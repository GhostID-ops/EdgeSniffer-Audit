import json
from pathlib import Path
from unittest.mock import patch

import materializeKefi13ArmFinalizedRpcRawV1 as subject


def contracts():
    root = Path("research/kefi-13-arm-program-v1")
    return (
        json.loads((root / "prospective-multivenue-20261019T025634Z/collection-manifest.json").read_text()),
        json.loads((root / "registry.json").read_text()),
        json.loads((root / "frozen-policies.json").read_text()),
    )


def raw(signature, programs):
    return {
        "schema": subject.RAW_SCHEMA,
        "captured_at_utc": "2026-10-19T02:59:00Z",
        "finalized": True,
        "observed_program_ids": programs,
        "observed_program_families": [],
        "candidate_arm_ids": [],
        "signature": signature,
        "slot": 123,
        "transaction_sha256": "a" * 64,
        "tracked_wallet": "teacher-wallet",
    }


def test_direct_pumpswap_materializes_and_ambiguous_is_quarantined():
    assert subject.resolve_arm({subject.PUMPSWAP}) == ("ARM07_PUMPSWAP_DIRECT", "PUMPSWAP_PROGRAM_ONLY_DIRECT")
    assert subject.resolve_arm({subject.PUMP, subject.PUMPSWAP}) == ("ARM08_PUMPSWAP_CPI", "PUMPSWAP_AND_PUMP_PROGRAM_CPI_ATTRIBUTION")
    assert subject.resolve_arm({subject.PUMP})[0] is None
    assert subject.resolve_arm({next(iter(subject.JUPITERS)), subject.ORCA})[0] == "ARM10_ROUTER_ORCA_WHIRLPOOL"


def test_arm08_route_resolution_uses_inner_cpi_evidence_when_top_level_ids_omit_pumpswap():
    row = raw("arm08-inner-cpi", [])
    row["transaction"] = {"meta": {}, "transaction": {"message": {"instructions": []}}}
    with patch.object(
        subject.route_adapters,
        "resolve_route",
        return_value={"underlyingVenues": [{"venue": "PUMPSWAP", "invocation": "CPI"}]},
    ):
        assert subject.resolve_arm_for_row(row) == (
            "ARM08_PUMPSWAP_CPI",
            "PUMPSWAP_ROUTE_EVIDENCE_CPI",
        )


def test_arm08_rejects_direct_pumpswap_even_if_candidate_label_includes_arm08():
    manifest, registry, policies = contracts()
    row = raw("arm08-direct", [subject.PUMP, subject.PUMPSWAP])
    row["tracked_wallet"] = "teacher-wallet"
    row["transaction"] = {"meta": {"err": None}, "transaction": {"message": {"instructions": []}}}
    observations, report = subject.materialize([row], manifest, registry, policies)
    assert observations == []
    assert report["ignored_event_reasons"]["ARM08_CPI_ROUTE_NOT_PROVEN"] == 1


def test_arm08_requires_exactly_one_wallet_buy_and_positive_settlement():
    manifest, registry, policies = contracts()
    row = raw("arm08-cpi", [subject.PUMP, subject.PUMPSWAP])
    row["tracked_wallet"] = "teacher-wallet"
    row["transaction"] = {"meta": {"err": None}, "transaction": {"message": {"instructions": []}}}
    with patch.object(
        subject.route_adapters,
        "resolve_route",
        return_value={"underlyingVenues": [{"venue": "PUMPSWAP", "invocation": "CPI"}]},
    ), patch.object(subject.pumpswap, "validate_transaction", return_value=[]):
        observations, report = subject.materialize([row], manifest, registry, policies)
    assert observations == []
    assert report["ignored_event_reasons"]["ARM08_NOT_EXACTLY_ONE_WALLET_BUY:0"] == 1


def test_non_pumpswap_resolved_venue_materializes_and_stays_outcome_blind():
    manifest, registry, policies = contracts()
    observations, report = subject.materialize([raw("router", [next(iter(subject.JUPITERS)), subject.ORCA])], manifest, registry, policies)
    assert observations == []
    assert report["quarantine"][0]["reason"] == "EXACT_VENUE_ENTRY_NOT_MATERIALIZED"


def test_clmm_resolution_does_not_admit_a_row_without_exact_wallet_acquisition():
    manifest, registry, policies = contracts()
    row = raw("clmm", [next(iter(subject.JUPITERS)), subject.RAYDIUM_CLMM])
    row["transaction"] = {"meta": {"err": None}, "transaction": {"message": {"instructions": []}}}
    observations, report = subject.materialize([row], manifest, registry, policies)
    assert observations == []
    assert report["quarantined_rows"] == 0
    assert report["ignored_event_reasons"]["RAYDIUM_CLMM_EXACT_ENTRY_NOT_MATERIALIZED:validated_transitions=0:wallet_acquisitions=0"] == 1


def test_materializer_is_deterministic():
    manifest, registry, policies = contracts()
    rows = [raw("direct", [subject.PUMPSWAP])]
    left, _ = subject.materialize(rows, manifest, registry, policies)
    right, _ = subject.materialize(rows, manifest, registry, policies)
    assert left == right


def test_pump_fee_distribution_is_quarantined_without_trade_assignment():
    manifest, registry, policies = contracts()
    row = raw("fee-distribution", [subject.PUMP])
    row["transaction"] = {
        "meta": {
            "logMessages": [
                "Program log: Instruction: DistributeFeeToHolders",
                "Program data: not-a-trade-event",
            ],
            "innerInstructions": [],
        },
        "transaction": {"message": {"accountKeys": [], "instructions": []}},
    }
    row["transaction_sha256"] = subject.sha256(row["transaction"])
    observations, report = subject.materialize([row], manifest, registry, policies)
    assert observations == []
    assert report["quarantine"][0]["reason"] == "PUMP_PROGRAM_NON_TRADE_OR_UNSUPPORTED_EXACT_ACTIVITY"
    assert report["quarantine"][0]["pump_activity"]["trade_event_count"] == 0


def test_launchlab_exact_tracked_buy_is_materialized():
    manifest, registry, policies = contracts()
    row = raw("launchlab-buy", [subject.LAUNCHLAB])
    row["transaction"] = {}
    transition = {
        "payer": "teacher-wallet",
        "side": "BUY",
        "baseMint": "base-mint",
        "quoteMint": subject.WSOL if hasattr(subject, "WSOL") else "quote-mint",
        "poolState": "pool-state",
    }
    with patch.object(subject.launchlab, "validate_transaction", return_value=[transition]):
        observations, report = subject.materialize([row], manifest, registry, policies)
    assert len(observations) == 1
    assert observations[0]["arm_id"] == "ARM05_RAYDIUM_LAUNCHLAB"
    assert report["by_arm"]["ARM05_RAYDIUM_LAUNCHLAB"] == 1


def test_cpmm_exact_tracked_buy_is_materialized_as_protocol_native_arm():
    manifest, registry, policies = contracts()
    row = raw("cpmm-buy", [subject.RAYDIUM_CPMM])
    row["transaction"] = {}
    transition = {
        "payer": "teacher-wallet",
        "outputOwner": "teacher-wallet",
        "outputMint": "base-mint",
        "inputMint": "quote-mint",
        "pool": "cpmm-pool",
    }
    with patch.object(subject.raydium_cpmm, "validate_transaction", return_value=[transition]):
        observations, report = subject.materialize([row], manifest, registry, policies)
    assert len(observations) == 1
    assert observations[0]["arm_id"] == "ARM12_OTHER_DEX_PROTOCOL_NATIVE"
    assert observations[0]["state"]["decoded_event"]["venue_transition"] == transition


def test_non_sol_pump_is_admitted_only_from_exact_quote_native_entry():
    manifest, registry, policies = contracts()
    row = raw("non-sol-pump-buy", [subject.PUMP])
    row["transaction"] = {}
    entry = {
        "candidate_signature": row["signature"],
        "event_index": 0,
        "mint": "quote-native-mint",
        "quote_mint": "quote-mint",
        "side": "BUY",
        "exact_transition_validated": True,
    }
    with patch.object(subject, "decoded_non_sol_pump_entry", return_value=([entry], [])):
        observations, report = subject.materialize([row], manifest, registry, policies)
    assert len(observations) == 1
    assert observations[0]["arm_id"] == "ARM04_NON_SOL_PUMP_V2_QUOTE_NATIVE"
    assert report["by_arm"]["ARM04_NON_SOL_PUMP_V2_QUOTE_NATIVE"] == 1
