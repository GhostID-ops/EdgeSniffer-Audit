//! Materialize receipt-backed public observations for one sealed forward cohort.
//!
//! This deliberately does not score pools, inspect outcomes, or invent F01-F13
//! values. It turns immutable producer receipts into a deterministic event ledger.

use std::{collections::{BTreeMap, BTreeSet, VecDeque}, env, fs, path::{Path, PathBuf}};

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use pump_exact_evidence_ledger::{
    canonical_state_v1::{jcs_bytes, sha256_hex},
    finalized_slot_inventory_v1::FinalizedSlotInventory,
    forward_capture_session_v1::ForwardCaptureSession,
    forward_cohort_receipts_v1::SlotReceiptBundle,
    forward_new_pool_cohort_v1::{
        seal_forward_new_pool_session_from_persisted_receipts, ForwardNewPoolSessionCohort,
    },
};
use serde::Serialize;
use serde_json::json;

const LEDGER_SCHEMA: &str = "EDGESNIFFER_FORWARD_NEW_POOL_OBSERVATION_LEDGER_V1";

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ObservationRow {
    cohort_sha256: String,
    pool: String,
    mint: String,
    relation_to_creation: String,
    slot: String,
    transaction_index: String,
    signature: String,
    event_id: String,
    instruction_path: String,
    semantic_kind: String,
    canonical_order: serde_json::Value,
    path_receipt_sha256: String,
    source_frame_sha256: String,
    evidence_disposition: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PreEntryStateRow {
    cohort_sha256: String,
    pool: String,
    mint: String,
    boundary_event_id: String,
    boundary_slot: String,
    boundary_transaction_index: String,
    boundary_instruction_path: String,
    boundary_semantic_kind: String,
    event_sequence_since_creation_before: String,
    pool_age_slots_before: String,
    prior_buy_event_count: String,
    prior_sell_event_count: String,
    prior_buy_quote_flow_raw: String,
    prior_sell_quote_flow_raw: String,
    prior_observed_pool_base_raw: Option<String>,
    prior_observed_pool_quote_raw: Option<String>,
    finalized_block_time_unix_seconds: Option<String>,
    finalized_event_time_unix_seconds: Option<String>,
    ever_observed_buy_before: bool,
    snapshot_validity: String,
    rolling_time_window_disposition: String,
    prior_buy_count_5s: Option<String>,
    prior_buy_count_20s: Option<String>,
    prior_buy_count_60s: Option<String>,
    prior_buy_quote_flow_5s_raw: Option<String>,
    prior_buy_quote_flow_20s_raw: Option<String>,
    prior_buy_quote_flow_60s_raw: Option<String>,
    prior_sell_count_60s: Option<String>,
    prior_sell_quote_flow_60s_raw: Option<String>,
    exact_amount_state_disposition: String,
    source_path_receipt_sha256: String,
}

#[derive(Clone, Debug)]
struct RawPumpSwapEvent {
    side: String,
    pool: String,
    event_time: i64,
    pool_base: u64,
    pool_quote: u64,
    pool_quote_delta: u64,
    block_time: Option<i64>,
}

#[derive(Clone, Debug)]
struct Flow {
    event_time: i64,
    side: String,
    quote_delta: u64,
}

#[derive(Default)]
struct PoolState {
    sequence: u64,
    buy_count: u64,
    sell_count: u64,
    creation_slot: Option<u64>,
    buy_flow: u64,
    sell_flow: u64,
    pool_base: Option<u64>,
    pool_quote: Option<u64>,
    last_event_time: Option<i64>,
    flows: VecDeque<Flow>,
}

const BUY_EVENT_DISC: [u8; 8] = [103, 244, 82, 31, 44, 245, 119, 119];
const SELL_EVENT_DISC: [u8; 8] = [62, 47, 55, 10, 165, 3, 220, 42];

fn u64_le(payload: &[u8], offset: usize) -> Result<u64> {
    let bytes: [u8; 8] = payload.get(offset..offset + 8).context("PUMPSWAP_EVENT_TRUNCATED")?.try_into().context("PUMPSWAP_EVENT_WORD")?;
    Ok(u64::from_le_bytes(bytes))
}

fn i64_le(payload: &[u8], offset: usize) -> Result<i64> {
    let bytes: [u8; 8] = payload.get(offset..offset + 8).context("PUMPSWAP_EVENT_TRUNCATED")?.try_into().context("PUMPSWAP_EVENT_WORD")?;
    Ok(i64::from_le_bytes(bytes))
}

fn decode_raw_pumpswap_event(encoded: &str, block_time: Option<i64>) -> Result<Option<RawPumpSwapEvent>> {
    let Ok(payload) = BASE64_STANDARD.decode(encoded.trim()) else { return Ok(None) };
    let side = if payload.starts_with(&BUY_EVENT_DISC) { "BUY" } else if payload.starts_with(&SELL_EVENT_DISC) { "SELL" } else { return Ok(None) };
    if payload.len() < 184 { bail!("PUMPSWAP_EVENT_LAYOUT_TOO_SHORT") }
    Ok(Some(RawPumpSwapEvent {
        side: side.into(), event_time: i64_le(&payload, 8)?, pool_base: u64_le(&payload, 48)?,
        pool_quote: u64_le(&payload, 56)?, pool_quote_delta: u64_le(&payload, 104)?,
        pool: bs58::encode(&payload[120..152]).into_string(), block_time,
    }))
}

fn rolling(flows: &VecDeque<Flow>, event_time: i64, seconds: i64, side: &str) -> Result<(u64, u64)> {
    let mut count = 0_u64;
    let mut amount = 0_u64;
    for flow in flows {
        let age = event_time.checked_sub(flow.event_time).context("EVENT_TIME_NONMONOTONIC")?;
        if age < 0 {
            bail!("EVENT_TIME_NONMONOTONIC")
        }
        if age <= seconds && flow.side == side {
            count += 1;
            amount = amount.checked_add(flow.quote_delta).context("ROLLING_FLOW_OVERFLOW")?;
        }
    }
    Ok((count, amount))
}

fn raw_events_for_slots(paths: &BTreeMap<u64, PathBuf>, wanted: &BTreeSet<(u64, String, usize)>) -> Result<BTreeMap<(u64, String, usize), RawPumpSwapEvent>> {
    let mut found = BTreeMap::new();
    for (slot, path) in paths {
        if !wanted.iter().any(|(candidate_slot, _, _)| candidate_slot == slot) { continue; }
        let root: serde_json::Value = serde_json::from_slice(&fs::read(path).with_context(|| format!("RAW_BLOCK_MISSING:{}", path.display()))?)?;
        let block_time = root.pointer("/result/blockTime").and_then(serde_json::Value::as_i64);
        let transactions = root.pointer("/result/transactions").and_then(serde_json::Value::as_array).context("RAW_BLOCK_TRANSACTIONS_MISSING")?;
        for tx in transactions {
            let signature = tx.pointer("/transaction/signatures/0").and_then(serde_json::Value::as_str).context("RAW_BLOCK_SIGNATURE_MISSING")?;
            let Some(logs) = tx.pointer("/meta/logMessages").and_then(serde_json::Value::as_array) else { continue; };
            for (log_index, log) in logs.iter().enumerate() {
                let Some(encoded) = log.as_str().and_then(|line| line.strip_prefix("Program data: ")) else { continue; };
                let Some(event) = decode_raw_pumpswap_event(encoded, block_time)? else { continue; };
                let key = (*slot, signature.to_owned(), log_index);
                if wanted.contains(&key) && found.insert(key, event).is_some() { bail!("RAW_PUMPSWAP_EVENT_DUPLICATE") }
            }
        }
    }
    if found.len() != wanted.len() { bail!("RAW_PUMPSWAP_EVENT_MISSING") }
    Ok(found)
}

fn number(value: &str, label: &str) -> Result<u64> {
    value.parse::<u64>().with_context(|| format!("{label}_INVALID"))
}

fn sort_key(row: &ObservationRow) -> Result<(u64, u64, String, String)> {
    Ok((number(&row.slot, "OBSERVATION_SLOT")?, number(&row.transaction_index, "OBSERVATION_TRANSACTION_INDEX")?, row.instruction_path.clone(), row.event_id.clone()))
}

fn write_atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    let tmp = path.with_extension("tmp");
    fs::write(&tmp, bytes)?;
    fs::rename(tmp, path)?;
    Ok(())
}

fn main() -> Result<()> {
    let root = PathBuf::from(env::var("FORWARD_CAPTURE_SESSION_ROOT").context("FORWARD_CAPTURE_SESSION_ROOT")?);
    let output_root = PathBuf::from(env::var("FORWARD_NEW_POOL_MATERIALIZATION_ROOT").context("FORWARD_NEW_POOL_MATERIALIZATION_ROOT")?);
    if output_root.exists() { bail!("FORWARD_MATERIALIZATION_ROOT_ALREADY_EXISTS") }

    let persisted: ForwardNewPoolSessionCohort = serde_json::from_slice(&fs::read(root.join("forward-new-pool-cohort.json")).context("FORWARD_NEW_POOL_COHORT_MISSING")?)?;
    persisted.validate()?;
    let recomputed = seal_forward_new_pool_session_from_persisted_receipts(&root, &persisted.cohort_id)?;
    if recomputed != persisted { bail!("FORWARD_MATERIALIZATION_COHORT_RECOMPUTE_MISMATCH") }

    let admissions = persisted.pools.iter().map(|pool| (pool.pool.clone(), pool)).collect::<BTreeMap<_, _>>();
    let session = ForwardCaptureSession::verify_persisted(&root)?;
    let mut rows = Vec::new();
    let mut raw_paths = BTreeMap::<u64, PathBuf>::new();
    for segment in &session.segments {
        let segment_root = Path::new(&segment.root);
        let inventory = FinalizedSlotInventory::read_verified(segment_root)?;
        for entry in inventory.entries.iter().filter(|entry| entry.disposition == "PRODUCED_RECONCILED") {
            let bundle = SlotReceiptBundle::read(segment_root, number(&entry.slot, "INVENTORY_SLOT")?)?;
            for path in bundle.paths {
                if path.disposition != "PATH_RESOLVED_QUARANTINED_UNIVERSE_INCOMPLETE" { continue; }
                let Some(admission) = admissions.get(&path.pool) else { continue; };
                rows.push(ObservationRow {
                    cohort_sha256: persisted.cohort_sha256.clone(), pool: path.pool, mint: admission.mint.clone(),
                    relation_to_creation: if path.event_id == admission.event_id { "POOL_CREATION".into() } else { "POST_CREATION_PUBLIC_EVENT".into() },
                    slot: path.slot, transaction_index: path.transaction_index, signature: path.signature, event_id: path.event_id,
                    instruction_path: path.instruction_path, semantic_kind: path.semantic_kind, canonical_order: path.canonical_order,
                    path_receipt_sha256: path.receipt_sha256, source_frame_sha256: path.source_frame_sha256, evidence_disposition: path.disposition,
                });
                raw_paths.insert(number(&entry.slot, "INVENTORY_SLOT")?, segment_root.join(format!("raw-rpc-getBlock-{}.json", entry.slot)));
            }
        }
    }
    rows.sort_by_key(|row| sort_key(row).expect("validated receipt order"));
    let mut creations = BTreeMap::<String, usize>::new();
    for row in &rows { if row.relation_to_creation == "POOL_CREATION" { *creations.entry(row.pool.clone()).or_default() += 1; } }
    for pool in admissions.keys() { if creations.get(pool) != Some(&1) { bail!("FORWARD_MATERIALIZATION_CREATION_PATH_MISSING_OR_AMBIGUOUS") } }

    let wanted = rows.iter().filter(|row| matches!(row.semantic_kind.as_str(), "PUMPSWAP_BUY" | "PUMPSWAP_SELL")).map(|row| {
        let (_, log_index) = row.event_id.rsplit_once(':').context("OBSERVATION_EVENT_ID_INVALID")?;
        Ok((number(&row.slot, "OBSERVATION_SLOT")?, row.signature.clone(), number(log_index, "OBSERVATION_LOG_INDEX")? as usize))
    }).collect::<Result<BTreeSet<_>>>()?;
    let raw_events = raw_events_for_slots(&raw_paths, &wanted)?;

    fs::create_dir_all(&output_root)?;
    let ledger = rows.iter().map(serde_json::to_string).collect::<Result<Vec<_>, _>>()?.join("\n");
    write_atomic(&output_root.join("observations.jsonl"), ledger.as_bytes())?;
    let mut states = Vec::new();
    let mut per_pool = BTreeMap::<String, PoolState>::new();
    for row in &rows {
        let raw = if matches!(row.semantic_kind.as_str(), "PUMPSWAP_BUY" | "PUMPSWAP_SELL") {
            let (_, log_index) = row.event_id.rsplit_once(':').context("OBSERVATION_EVENT_ID_INVALID")?;
            let event = raw_events.get(&(number(&row.slot, "STATE_SLOT")?, row.signature.clone(), number(log_index, "OBSERVATION_LOG_INDEX")? as usize)).context("RAW_EVENT_BINDING_MISSING")?;
            if event.pool != row.pool || (event.side == "BUY") != (row.semantic_kind == "PUMPSWAP_BUY") || (event.side == "SELL") != (row.semantic_kind == "PUMPSWAP_SELL") { bail!("RAW_EVENT_PATH_SEMANTIC_MISMATCH") }
            Some(event)
        } else { None };
        let state = per_pool.entry(row.pool.clone()).or_default();
        if row.relation_to_creation == "POOL_CREATION" {
            if state.creation_slot.replace(number(&row.slot, "STATE_SLOT")?).is_some() { bail!("POOL_CREATION_DUPLICATE") }
        }
        let creation_slot = state.creation_slot.context("STATE_POOL_CREATION_MISSING")?;
        let age_slots = number(&row.slot, "STATE_SLOT")?.checked_sub(creation_slot).context("STATE_SLOT_BEFORE_CREATION")?;
        let windows = if let Some(raw) = raw {
            if raw.event_time < 0 || state.last_event_time.is_some_and(|last| raw.event_time < last) { bail!("FINALIZED_EVENT_TIME_NONMONOTONIC") }
            let (buy_5_count, buy_5_flow) = rolling(&state.flows, raw.event_time, 5, "BUY")?;
            let (buy_20_count, buy_20_flow) = rolling(&state.flows, raw.event_time, 20, "BUY")?;
            let (buy_60_count, buy_60_flow) = rolling(&state.flows, raw.event_time, 60, "BUY")?;
            let (sell_60_count, sell_60_flow) = rolling(&state.flows, raw.event_time, 60, "SELL")?;
            Some((buy_5_count, buy_5_flow, buy_20_count, buy_20_flow, buy_60_count, buy_60_flow, sell_60_count, sell_60_flow))
        } else { None };
        states.push(PreEntryStateRow {
            cohort_sha256: row.cohort_sha256.clone(), pool: row.pool.clone(), mint: row.mint.clone(),
            boundary_event_id: row.event_id.clone(), boundary_slot: row.slot.clone(),
            boundary_transaction_index: row.transaction_index.clone(), boundary_instruction_path: row.instruction_path.clone(),
            boundary_semantic_kind: row.semantic_kind.clone(), event_sequence_since_creation_before: state.sequence.to_string(),
            pool_age_slots_before: age_slots.to_string(), prior_buy_event_count: state.buy_count.to_string(),
            prior_sell_event_count: state.sell_count.to_string(), ever_observed_buy_before: state.buy_count > 0,
            prior_buy_quote_flow_raw: state.buy_flow.to_string(), prior_sell_quote_flow_raw: state.sell_flow.to_string(),
            prior_observed_pool_base_raw: state.pool_base.map(|value| value.to_string()), prior_observed_pool_quote_raw: state.pool_quote.map(|value| value.to_string()),
            finalized_block_time_unix_seconds: raw.and_then(|event| event.block_time).map(|value| value.to_string()),
            finalized_event_time_unix_seconds: raw.map(|event| event.event_time.to_string()),
            snapshot_validity: "PRE_EVENT_CANONICAL_STATE_AVAILABLE".into(),
            rolling_time_window_disposition: if windows.is_some() { "AVAILABLE_FROM_FINALIZED_EVENT_TIMESTAMP_SECONDS" } else { "NOT_APPLICABLE_POOL_CREATION" }.into(),
            prior_buy_count_5s: windows.map(|value| value.0.to_string()), prior_buy_quote_flow_5s_raw: windows.map(|value| value.1.to_string()),
            prior_buy_count_20s: windows.map(|value| value.2.to_string()), prior_buy_quote_flow_20s_raw: windows.map(|value| value.3.to_string()),
            prior_buy_count_60s: windows.map(|value| value.4.to_string()), prior_buy_quote_flow_60s_raw: windows.map(|value| value.5.to_string()),
            prior_sell_count_60s: windows.map(|value| value.6.to_string()), prior_sell_quote_flow_60s_raw: windows.map(|value| value.7.to_string()),
            exact_amount_state_disposition: if raw.is_some() { "AVAILABLE_FROM_FINALIZED_PUMPSWAP_EVENT" } else { "NOT_APPLICABLE_POOL_CREATION" }.into(),
            source_path_receipt_sha256: row.path_receipt_sha256.clone(),
        });
        state.sequence += 1;
        if let Some(raw) = raw {
            if raw.side == "BUY" { state.buy_count += 1; state.buy_flow = state.buy_flow.checked_add(raw.pool_quote_delta).context("BUY_FLOW_OVERFLOW")?; }
            if raw.side == "SELL" { state.sell_count += 1; state.sell_flow = state.sell_flow.checked_add(raw.pool_quote_delta).context("SELL_FLOW_OVERFLOW")?; }
            state.pool_base = Some(raw.pool_base); state.pool_quote = Some(raw.pool_quote); state.last_event_time = Some(raw.event_time);
            state.flows.push_back(Flow { event_time: raw.event_time, side: raw.side.clone(), quote_delta: raw.pool_quote_delta });
            while state.flows.front().is_some_and(|flow| raw.event_time - flow.event_time > 60) { state.flows.pop_front(); }
        }
    }
    let state_ledger = states.iter().map(serde_json::to_string).collect::<Result<Vec<_>, _>>()?.join("\n");
    write_atomic(&output_root.join("pre-entry-state.jsonl"), state_ledger.as_bytes())?;
    let manifest = json!({
        "schema": LEDGER_SCHEMA, "cohortId": persisted.cohort_id, "cohortSha256": persisted.cohort_sha256,
        "captureSessionSha256": persisted.capture_session_sha256, "sourceInstanceId": persisted.source_instance_id,
        "continuityEpoch": persisted.continuity_epoch, "coverageMode": persisted.coverage_mode,
        "poolCount": admissions.len().to_string(), "observationCount": rows.len().to_string(),
        "rowsSha256": sha256_hex(&jcs_bytes(&serde_json::to_value(&rows)?)?),
        "preEntryStateCount": states.len().to_string(),
        "preEntryStateSha256": sha256_hex(&jcs_bytes(&serde_json::to_value(&states)?)?),
        "featureDisposition": "PRE_EVENT_STATE_WITH_FINALIZED_AMOUNT_AND_SECOND_LEVEL_ROLLING_WINDOWS", "economicOutcomeAccess": "NOT_ACCESSED",
        "sourceCohortPath": root.join("forward-new-pool-cohort.json").display().to_string(),
    });
    write_atomic(&output_root.join("materialization-manifest.json"), &jcs_bytes(&manifest)?)?;
    println!("{}", serde_json::to_string(&manifest)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{rolling, Flow};
    use std::collections::VecDeque;

    #[test]
    fn rolling_windows_use_only_prior_events() {
        let flows = VecDeque::from([
            Flow { event_time: 100, side: "BUY".into(), quote_delta: 7 },
            Flow { event_time: 106, side: "SELL".into(), quote_delta: 11 },
            Flow { event_time: 115, side: "BUY".into(), quote_delta: 13 },
        ]);
        assert_eq!(rolling(&flows, 120, 5, "BUY").unwrap(), (1, 13));
        assert_eq!(rolling(&flows, 120, 20, "BUY").unwrap(), (2, 20));
        assert_eq!(rolling(&flows, 120, 60, "SELL").unwrap(), (1, 11));
    }

    #[test]
    fn rolling_windows_reject_backward_event_time() {
        let flows = VecDeque::from([Flow { event_time: 101, side: "BUY".into(), quote_delta: 1 }]);
        assert!(rolling(&flows, 100, 60, "BUY").is_err());
    }
}
