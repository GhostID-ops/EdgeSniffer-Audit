"""Offline opportunity/attempt census. No signing, RPC, submission or PnL.

The first observed buy per mint is a census seed, NOT a validated strategy.
File order is observation order; no future sorting/enrichment of decisions.
Native replay is retained but is never certified by this module.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

VERSION = 'OPPORTUNITY_ATTEMPT_CENSUS_V1'
SIZES = (100_000_000, 500_000_000, 1_000_000_000)
POLICY = {'version': VERSION, 'seed': 'FIRST_OBSERVED_BUY_PER_MINT',
          'entry_delay_ms': 1000, 'sizes_lamports': SIZES,
          'scope': 'coverage_diagnostic_only', 'teacher_used_for_selection': False}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def integer(value):
    return type(value) is int and value >= 0


class Ledger:
    def __init__(self, population_id):
        if not population_id.strip():
            raise ValueError('population_id required')
        self.population_id = population_id
        self.seen = {}
        self.mints = set()
        self.last_receipt = None
        self.counts = Counter()
        self.reasons = Counter()

    def consume(self, row, line):
        self.counts['input_rows'] += 1
        if not isinstance(row, dict):
            self.counts['malformed_rows'] += 1
            return [{'record_type': 'INPUT_REJECTION', 'line': line, 'reason': 'not_object'}]
        raw = row.get('raw_record') or {}
        if not isinstance(raw, dict):
            raw = {}
        identity = row.get('observation_id') or row.get('raw_record_identity')
        content_hash = digest(row)
        if identity:
            identity = str(identity)
            if identity in self.seen:
                if self.seen[identity] != content_hash:
                    self.counts['identity_conflicts'] += 1
                    return [{'record_type': 'INPUT_REJECTION', 'line': line,
                             'source_id': identity, 'reason': 'identity_content_conflict'}]
                self.counts['duplicate_rows'] += 1
                return []
            self.seen[identity] = content_hash
        ts = row.get('local_receive_ts_ms')
        if ts is None:
            ts = row.get('receive_ts_ms')
        late = integer(ts) and self.last_receipt is not None and ts < self.last_receipt
        if integer(ts):
            self.last_receipt = max(ts, self.last_receipt or ts)
        side = row.get('side') or raw.get('side')
        mint = row.get('mint') or raw.get('mint')
        if side != 'buy':
            self.counts['non_buy_rows'] += 1
            return []
        if not isinstance(mint, str) or not mint:
            self.counts['missing_mint_rows'] += 1
            return [{'record_type': 'INPUT_REJECTION', 'line': line, 'reason': 'missing_mint'}]
        if mint in self.mints:
            self.counts['repeat_mint_buy_rows'] += 1
            return []
        # Reserve the seed before evidence filtering: better later data cannot
        # replace an inconvenient first opportunity.
        self.mints.add(mint)
        self.counts['opportunities'] += 1
        reasons = []
        if not identity:
            reasons.append('missing_source_identity')
        if not integer(ts):
            reasons.append('missing_receipt_clock')
        if late:
            reasons.append('nonmonotonic_receipt_order')
        canonical = row.get('canonical_state') or {}
        if not isinstance(canonical, dict):
            canonical = {}
        route = row.get('route') or canonical.get('route')
        if route not in {'PUMP_CURVE', 'PUMP_BONDING_CURVE_TOP_LEVEL'}:
            reasons.append('unsupported_or_unresolved_route')
        execution = row.get('execution') or {}
        if not isinstance(execution, dict) or execution.get('success') is not True:
            reasons.append('successful_execution_unproven')
        if canonical.get('state_source') == 'NATIVE_SHRED_REPLAY' or canonical.get('schema') == 'PUMP_CURVE_NATIVE_REPLAY_STATE_V1':
            reasons.append('conditional_replay_state')
        # No trusted processed-state adapter is wired yet. A Boolean provided
        # by arbitrary input must never change this to economic admission.
        reasons.append('processed_state_adapter_not_validated')
        state = canonical.get('post_state') or row.get('state') or {}
        if not isinstance(state, dict) or not all(integer(state.get(k)) for k in
                ('virtual_sol', 'virtual_token', 'real_sol', 'real_token')):
            reasons.append('incomplete_reserves')
        if not all(integer(row.get(k)) for k in
                   ('fee_basis_points', 'protocol_fee_basis_points', 'creator_fee_basis_points')):
            reasons.append('missing_explicit_fee_fields')
        for key in ('state_available_at_ms', 'fee_available_at_ms', 'coverage_evidence_id'):
            if row.get(key) is None:
                reasons.append('missing_' + key)
        for key in ('state_available_at_ms', 'fee_available_at_ms'):
            value = row.get(key)
            if value is not None and (not integer(value) or not integer(ts) or value > ts):
                reasons.append('unavailable_at_decision_' + key)
        self.reasons.update(reasons)
        oid = digest([VERSION, self.population_id, mint])
        opportunity = {'record_type': 'OPPORTUNITY', 'opportunity_id': oid,
                       'mint': mint, 'decision_ready_ms': ts if integer(ts) else None,
                       'source_id': identity, 'source_sha256': content_hash, 'input_line': line,
                       'policy_sha256': digest(POLICY), 'population_id': self.population_id,
                       'status': 'BLOCKED_EVIDENCE', 'reasons': reasons,
                       'teacher_used_for_selection': False}
        attempts = [{'record_type': 'ATTEMPT', 'attempt_id': digest([oid, size]),
                     'opportunity_id': oid, 'notional_lamports': size,
                     'modeled_entry_not_before_ms': ts + 1000 if integer(ts) else None,
                     'status': 'NOT_ATTEMPTED_EVIDENCE_BLOCKED', 'reasons': reasons,
                     'submitted': False, 'realized_pnl_lamports': None}
                    for size in SIZES]
        self.counts['attempt_rows'] += len(attempts)
        return [opportunity, *attempts]


def run(source, output, population_id):
    output.mkdir(parents=True, exist_ok=False)  # immutable run; never overwrite
    ledger = Ledger(population_id)
    input_hash = hashlib.sha256()
    output_hash = hashlib.sha256()
    prefix_bytes = source.stat().st_size
    with source.open('rb') as incoming, (output / 'ledger.jsonl').open('xb') as outgoing:
        line = 0
        while incoming.tell() < prefix_bytes:
            blob = incoming.readline(prefix_bytes - incoming.tell())
            if not blob:
                raise ValueError('source truncated during census')
            line += 1
            ledger.counts['physical_lines'] += 1
            input_hash.update(blob)
            try:
                if not blob.endswith(b'\n'):
                    raise ValueError('unsealed trailing line')
                row = json.loads(blob)
                records = ledger.consume(row, line)
            except (ValueError, TypeError, OverflowError) as error:
                ledger.counts['invalid_json_or_value_rows'] += 1
                records = [{'record_type': 'INPUT_REJECTION', 'line': line,
                            'raw_sha256': hashlib.sha256(blob).hexdigest(),
                            'reason': type(error).__name__}]
            for record in records:
                encoded = (json.dumps(record, sort_keys=True, allow_nan=False)+'\n').encode()
                outgoing.write(encoded)
                output_hash.update(encoded)
    report = {'schema': VERSION, 'population_id': population_id, 'policy': POLICY,
              'policy_sha256': digest(POLICY), 'input_sha256': input_hash.hexdigest(),
              'input_prefix_bytes': prefix_bytes,
              'ledger_sha256': output_hash.hexdigest(), 'counts': dict(ledger.counts),
              'evidence_reasons': dict(ledger.reasons), 'economics_computed': False,
              'trading_enabled': False, 'sampling': 'first observed buy per mint in supplied file',
              'limitations': ['Not a burst strategy or opportunity-rate estimate.',
                              'No validated processed-state adapter or modeled fills.',
                              'No economic or profitability conclusion from blocked seeds.']}
    (output / 'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--population-id', required=True)
    a = p.parse_args()
    print(json.dumps(run(a.input, a.output, a.population_id), sort_keys=True))
