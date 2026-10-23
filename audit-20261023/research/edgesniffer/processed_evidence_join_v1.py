"""Offline identity/availability diagnostic, NOT an execution-state adapter.

Retains every normalized receipt, including gaps and duplicates. Only exact
signature/slot/curve account matches are considered. Never infers success,
executed flow, current state, fees, market coverage or fills from a match.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from ledger_v1 import Ledger, digest, integer

VERSION = 'PROCESSED_EVIDENCE_JOIN_DIAGNOSTIC_V1'
ACCOUNT_KIND = 'rustNativePumpCurveAccountState'
RECEIPT_KINDS = {'rustNativePumpCurveTransactionReceipt', 'rustNativeTargetTransactionReceipt'}
RESERVES = ('virtualSolReserves', 'virtualTokenReserves', 'realSolReserves', 'realTokenReserves')


def positive(value):
    return type(value) is int and value > 0


def text_id(value):
    return isinstance(value, str) and bool(value.strip())


def snapshot_tail(source, target, max_bytes):
    """Copy a bounded complete-line range, never mutate the source or overwrite.

    Hash exactly the copied bytes. Drop partial first/last lines explicitly.
    Open once; appended bytes beyond fstat boundary are not consumed.
    """
    import os
    with source.open('rb') as incoming:
        stat = os.fstat(incoming.fileno())
        end = stat.st_size
        start = max(0, end - max_bytes)
        incoming.seek(start)
        if start:
            incoming.seek(start - 1)
            if incoming.read(1) != b'\n':
                incoming.readline(end - start)
            start = incoming.tell()
        data = incoming.read(end - start)
        if len(data) != end - start:
            raise ValueError('source truncated during snapshot')
    last_newline = data.rfind(b'\n') + 1
    sealed = data[:last_newline]
    with target.open('xb') as outgoing:
        outgoing.write(sealed)
    return {'source': str(source), 'source_prefix_bytes': end,
            'source_device': stat.st_dev, 'source_inode': stat.st_ino,
            'start_byte': start, 'end_byte': start + len(sealed),
            'unsealed_tail_bytes_excluded': len(data) - len(sealed),
            'snapshot_bytes': len(sealed), 'sha256': hashlib.sha256(sealed).hexdigest()}


def rows(path, counts):
    with path.open('rb') as source:
        for line, blob in enumerate(source, 1):
            counts['physical_lines'] += 1
            try:
                row = json.loads(blob)
                if not isinstance(row, dict):
                    raise ValueError('not object')
                digest(row)  # reject NaN/Infinity before indexing
            except (ValueError, TypeError):
                counts['invalid_rows'] += 1
                yield line, None
                continue
            yield line, row


class EvidenceIndex:
    def __init__(self):
        self.accounts = defaultdict(list)
        self.receipts = defaultdict(list)
        self.kinds = Counter()
        self.keys = defaultdict(Counter)
        self.last_receive_ns = None
        self.nonmonotonic_accounts = 0

    def add(self, row, line):
        kind = str(row.get('kind', 'missing'))
        self.kinds[kind] += 1
        if kind in RECEIPT_KINDS:
            self.keys[kind].update(row.keys())
            key = (row.get('signature'), row.get('slot'), row.get('nativeReceiveUnixNs'))
            if text_id(key[0]) and integer(key[1]) and positive(key[2]):
                self.receipts[key].append((row, line))
        if kind != ACCOUNT_KIND:
            return
        ts = row.get('nativeReceiveUnixNs')
        late = positive(ts) and self.last_receive_ns is not None and ts < self.last_receive_ns
        if positive(ts):
            self.last_receive_ns = max(ts, self.last_receive_ns or ts)
        if late:
            self.nonmonotonic_accounts += 1
        key = (row.get('accountTxnSignature'), row.get('slot'), row.get('bondingCurve'))
        if text_id(key[0]) and integer(key[1]) and text_id(key[2]):
            self.accounts[key].append((row, line, late))

    def join(self, receipt, line):
        result = {'schema': VERSION, 'receipt_line': line, 'receipt_sha256': digest(receipt),
                  'economic_admission': False, 'execution_success': None,
                  'executed_flow': None, 'status': 'UNRESOLVED', 'reasons': [],
                  'state_ref': None, 'raw_receipt_refs': []}
        reasons = result['reasons']
        sig, slot, curve = (receipt.get(k) for k in ('signature', 'slot', 'bonding_curve'))
        ts = receipt.get('native_receive_unix_ns')
        if not (text_id(sig) and integer(slot) and text_id(curve)):
            reasons.append('missing_exact_signature_slot_curve')
        if not positive(ts):
            reasons.append('invalid_receipt_clock')
        if reasons:
            return result
        boundary = ts + 1_000_000_000
        result['receipt_ns'] = ts
        result['entry_boundary_ns'] = boundary
        # Derived receipts must be traceable to the original event, not merely
        # trusted because their source label says Yellowstone.
        originals = self.receipts.get((sig, slot, ts), [])
        for original, original_line in originals:
            if (original.get('kind') == receipt.get('kind')
                    and original.get('bondingCurve') == curve
                    and original.get('mint') == receipt.get('mint')
                    and original.get('side') == receipt.get('side')
                    and original.get('instructionIndex') == receipt.get('instruction_index')):
                result['raw_receipt_refs'].append({'line': original_line, 'sha256': digest(original)})
        if not result['raw_receipt_refs']:
            reasons.append('original_receipt_not_in_sample_or_not_equal')
        candidates = self.accounts.get((sig, slot, curve), [])
        eligible = []
        invalid_clock = False
        for account, account_line, late in candidates:
            received, decoded = account.get('nativeReceiveUnixNs'), account.get('decodeCompleteUnixNs')
            if not positive(received) or not positive(decoded) or decoded < received:
                invalid_clock = True
                continue
            if max(received, decoded) <= boundary:
                eligible.append((account, account_line, late))
        if not eligible:
            reasons.append('invalid_account_clock' if invalid_clock else
                           'account_after_boundary' if candidates else 'account_not_in_sample')
            return result
        if invalid_clock:
            reasons.append('invalid_account_clock')
            return result
        # Distinct writes cannot be collapsed by choosing the last/highest
        # writeVersion: whole-transaction and fork semantics are not proven.
        identities = defaultdict(list)
        for account, account_line, late in eligible:
            version = account.get('writeVersion')
            if not integer(version):
                reasons.append('ambiguous_or_missing_write_version')
                return result
            identities[version].append((account, account_line, late))
        if len(identities) != 1:
            reasons.append('ambiguous_or_missing_write_version')
            return result
        payloads = {digest({key: a.get(key) for key in (*RESERVES, 'complete', 'tokenTotalSupply')})
                    for a, _, _ in eligible}
        if len(payloads) != 1:
            reasons.append('conflicting_account_payloads')
            return result
        # Earliest physically retained observation; no timestamp sorting.
        account, account_line, late = eligible[0]
        if any(flag for _, _, flag in eligible):
            reasons.append('nonmonotonic_account_receipt')
        if not all(integer(account.get(k)) for k in RESERVES) or not all(
                positive(account.get(k)) for k in RESERVES[:2]):
            reasons.append('invalid_reserves')
        if account.get('complete') is not False:
            reasons.append('complete_or_unknown_phase')
        if reasons:
            return result
        result['status'] = 'IDENTITY_AND_CLOCK_MATCH_ONLY'
        result['state_ref'] = {'line': account_line, 'sha256': digest(account),
                               'write_version': account['writeVersion'],
                               'available_ns': max(account['nativeReceiveUnixNs'], account['decodeCompleteUnixNs'])}
        # Intentionally not a current-state quote: other transactions may have
        # changed this curve before entry. Clock-domain/fork/coverage unknown.
        return result


def ledger_input(receipt):
    """Keep observation seeds without upgrading them to state or execution proof."""
    ts = receipt.get('native_receive_unix_ns')
    return {'observation_id': digest(['normalized_receipt_v1', receipt.get('signature'),
                                     receipt.get('slot'), receipt.get('bonding_curve'),
                                     receipt.get('instruction_index')]),
            'mint': receipt.get('mint'), 'side': receipt.get('side'),
            'local_receive_ts_ms': (ts + 999_999) // 1_000_000 if positive(ts) else None,
            'route': 'PUMP_CURVE' if text_id(receipt.get('bonding_curve')) else None,
            'source_receipt_sha256': digest(receipt)}


def run(source_dir, output, population_id, account_max_bytes=64*1024*1024, sealed_inputs=False):
    output.mkdir(parents=True, exist_ok=False)
    specs = [('accounts', 'accounts.jsonl' if sealed_inputs else 'native-pump-account-state-events.jsonl', account_max_bytes),
             ('receipts', 'receipts.jsonl' if sealed_inputs else 'native-yellowstone-pump-transaction-receipts-v1.jsonl', 32*1024*1024)]
    manifest = {name: snapshot_tail(source_dir/file, output/(name+'.jsonl'), limit)
                for name, file, limit in specs}
    index = EvidenceIndex()
    source_counts, receipt_counts = Counter(), Counter()
    for line, row in rows(output/'accounts.jsonl', source_counts):
        if row is not None:
            index.add(row, line)
    ledger = Ledger(population_id)
    statuses, reasons, receipt_kinds, receipt_fields = Counter(), Counter(), Counter(), Counter()
    seen = set()
    with (output/'joins.jsonl').open('x', encoding='utf-8') as joins, \
            (output/'ledger.jsonl').open('x', encoding='utf-8') as ledger_file:
        for line, row in rows(output/'receipts.jsonl', receipt_counts):
            if row is None:
                joined = {'receipt_line': line, 'status': 'INVALID_RECEIPT', 'economic_admission': False}
                records = []
            else:
                receipt_kinds[str(row.get('kind', 'missing'))] += 1
                for field in ('signature', 'slot', 'bonding_curve', 'mint', 'side',
                              'native_receive_unix_ns', 'instruction_index'):
                    if row.get(field) is not None:
                        receipt_fields[field] += 1
                try:
                    joined = index.join(row, line)
                    records = ledger.consume(ledger_input(row), line)
                except (TypeError, ValueError):
                    joined = {'receipt_line': line, 'status': 'INVALID_RECEIPT', 'economic_admission': False}
                    records = []
                row_hash = digest(row)
                joined['duplicate_receipt'] = row_hash in seen
                seen.add(row_hash)
            statuses[joined['status']] += 1
            reasons.update(joined.get('reasons', []))
            joins.write(json.dumps(joined, sort_keys=True)+'\n')
            for record in records:
                ledger_file.write(json.dumps(record, sort_keys=True)+'\n')
    hashes = {name: hashlib.sha256((output/name).read_bytes()).hexdigest()
              for name in ('joins.jsonl', 'ledger.jsonl')}
    code_paths = (Path(__file__), Path(__file__).with_name('ledger_v1.py'))
    report = {'schema': VERSION, 'population_id': population_id, 'inputs': manifest,
              'implementation_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in code_paths},
              'output_hashes': hashes, 'source_counts': dict(source_counts),
              'source_kinds': dict(index.kinds),
              'raw_receipt_keys': {k: dict(v) for k, v in index.keys.items()},
              'nonmonotonic_account_rows': index.nonmonotonic_accounts,
              'receipt_counts': dict(receipt_counts),
              'receipt_kinds': dict(receipt_kinds), 'receipt_nonnull_fields': dict(receipt_fields),
              'join_statuses': dict(statuses),
              'join_reasons': dict(reasons), 'ledger_counts': dict(ledger.counts),
              'ledger_reasons': dict(ledger.reasons), 'economic_admissions': 0,
              'economics_computed': False, 'trading_enabled': False,
              'limitations': ['Bounded retained-file sample, not subscription or market coverage.',
                              'Clock comparisons are diagnostic; common clock epoch/fork not established.',
                              'An account match is not proof of successful swap or executed amounts.',
                              'Matched transaction post-state is not necessarily current state at entry.',
                              'Future-dated account evidence cannot rescue an earlier boundary.',
                              'Backdated appended evidence requires a new snapshot/version, never overwrites old results.',
                              'Receipt kinds/route metadata may be historical or wallet-conditioned.',
                              'Fees, current-state availability, failed attempts and own-state economics remain unproven.']}
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--population-id', required=True)
    parser.add_argument('--account-max-bytes', type=int, default=64*1024*1024)
    parser.add_argument('--sealed-inputs', action='store_true', help='Read accounts.jsonl/receipts.jsonl from a prior run')
    args = parser.parse_args()
    if args.account_max_bytes <= 0:
        parser.error('--account-max-bytes must be positive')
    print(json.dumps(run(args.source_dir, args.output, args.population_id, args.account_max_bytes, args.sealed_inputs), sort_keys=True))
