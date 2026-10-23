"""Offline capture quality and finalized-reference audit. No historical backdating."""
import argparse
import base64
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import time

from transaction_evidence_v1 import PUMP, normalize, digest
from recover_receipts_v1 import rpc, read_endpoint
from processed_evidence_join_v1 import snapshot_tail


def in_scope(tx):
    message = (tx.get('transaction') or {}).get('message') or {}
    loaded = (tx.get('meta') or {}).get('loadedAddresses') or {}
    return PUMP in (message.get('accountKeys') or []) + (loaded.get('writable') or []) + (loaded.get('readonly') or [])


def reconcile_slot(expected, observed):
    wanted = {(x['transaction']['signatures'][0]) for x in expected if in_scope(x)}
    return {'expected_pump_signatures': len(wanted), 'observed_expected': len(wanted & observed),
            'missing_signatures': sorted(wanted-observed),
            'observed_not_in_finalized_block': sorted(observed-wanted)}


def compare_metadata(native, finalized):
    return {'execution_status_equal':native['execution_status']==finalized['execution_status'],
            'resolved_accounts_equal':native['resolved_account_keys']==finalized['resolved_account_keys'],
            'lamport_balances_equal':native['lamport_balance_deltas']==finalized['lamport_balance_deltas'],
            'fee_equal':native['fee_lamports']==finalized['fee_lamports'],
            'event_payloads_equal':[e['raw_event_base64'] for e in native['pump_trade_event_prefixes']]==
                                   [e['raw_event_base64'] for e in finalized['pump_trade_event_prefixes']]}


def run(source, output, account_source=None, endpoint=None, block_limit=16):
    output.mkdir(parents=True, exist_ok=False)
    capture_manifest = snapshot_tail(source, output/'capture.jsonl', 1024*1024*1024)
    counts, kinds, gaps, statuses = Counter(), Counter(), Counter(), Counter()
    source_slots, processed_slots, observed, views = set(), set(), defaultdict(set), []
    last_seq = None; last_clock = None; current_session = None
    started = ended = None; connected = False; reconnect_start = None; reconnects = []
    observed_unique = set()
    with (output/'transactions.jsonl').open('x') as out, (output/'capture.jsonl').open() as incoming:
        for line_no, line in enumerate(incoming, 1):
            try:
                row = json.loads(line)
            except ValueError:
                counts['malformed_lines'] += 1; continue
            kind = row.get('kind'); kinds[str(kind)] += 1
            if kind == 'SESSION_START':
                current_session = row['session_id']; started = row['available_at_ns']; last_seq = None
            if kind == 'SESSION_END':
                ended = row['available_at_ns']
            if kind == 'GAP':
                reconnect_start = reconnect_start or row['available_at_ns']; connected = False
            if kind == 'CONNECTED':
                connected = True
                if reconnect_start is not None:
                    reconnects.append({'start_ns': reconnect_start, 'end_ns': row['available_at_ns']})
                    reconnect_start = None
            if row.get('sequence') is not None:
                seq = row['sequence']
                if seq != (last_seq or 0)+1:
                    counts['writer_sequence_discontinuities'] += 1
                last_seq = seq
                received = row.get('received_at_unix_ns')
                normalized = row.get('normalized_at_unix_ns')
                if type(received) is not int or type(normalized) is not int or normalized < received:
                    counts['invalid_native_clock_rows'] += 1
                else:
                    if last_clock is not None and received < last_clock:
                        counts['wall_clock_regressions'] += 1
                    last_clock = received
                try:
                    raw = base64.b64decode(row['raw_protobuf_base64'], validate=True)
                    if hashlib.sha256(raw).hexdigest() != row['raw_sha256']:
                        counts['raw_hash_mismatches'] += 1
                except (KeyError, ValueError, TypeError):
                    counts['raw_payload_unavailable'] += 1
            if kind == 'SLOT' and type(row.get('slot')) is int:
                source_slots.add(row['slot'])
                if (row.get('slot_state') or {}).get('status') == 0:
                    processed_slots.add(row['slot'])
            if kind != 'TRANSACTION':
                continue
            counts['transaction_frames'] += 1
            tx = row.get('transaction')
            if not isinstance(tx, dict):
                counts['transaction_body_unavailable'] += 1
                continue
            try:
                view = normalize(tx)
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                counts['normalization_errors'] += 1
                out.write(json.dumps({'capture_line': line_no, 'error': type(exc).__name__, 'execution_status': 'UNKNOWN'})+'\n')
                continue
            signature, slot = view['signature'], view['slot']
            identity = (slot, signature)
            if identity in observed_unique:
                counts['duplicate_transaction_frames'] += 1
            observed_unique.add(identity)
            if signature:
                observed[slot].add(signature)
            statuses[view['execution_status']] += 1
            gaps.update(view['gaps'])
            counts['instruction_identity_complete'] += view['instruction_identity_complete']
            counts['transactions_with_pump_instructions'] += view['pump_instruction_count'] > 0
            counts['pump_instruction_count'] += view['pump_instruction_count']
            counts['inner_pump_instruction_count'] += sum(i['program_id'] == PUMP and i['inner_index'] is not None for i in view['instructions'])
            counts['trade_event_prefixes'] += len(view['pump_trade_event_prefixes'])
            counts['successful_trade_event_prefixes'] += sum(e['realized_swap_amounts_eligible'] for e in view['pump_trade_event_prefixes'])
            counts['signed_sol_flow_events'] += sum(e['signed_sol_flow_eligible'] for e in view['pump_trade_event_prefixes'])
            counts['transactions_with_signed_sol_flow'] += any(e['signed_sol_flow_eligible'] for e in view['pump_trade_event_prefixes'])
            counts['event_quote_layout_unknown'] += sum(not e['quote_layout_decoded'] for e in view['pump_trade_event_prefixes'])
            view.update(capture_line=line_no, received_at_unix_ns=row.get('received_at_unix_ns'),
                        metadata_available_at_ns=row.get('normalized_at_unix_ns'),
                        session_id=row.get('session_id'), source_commitment='processed',
                        finality_known_at_decision=False)
            views.append(view)
            out.write(json.dumps(view)+'\n')
    if reconnect_start is not None:
        reconnects.append({'start_ns': reconnect_start, 'end_ns': ended})
    account_manifest = None; account_index = defaultdict(list); account_stats = Counter()
    if account_source:
        account_manifest = snapshot_tail(account_source, output/'accounts.jsonl', 128*1024*1024)
        for line_no, line in enumerate((output/'accounts.jsonl').read_text().splitlines(), 1):
            try:
                row = json.loads(line)
            except ValueError:
                account_stats['invalid_rows'] += 1; continue
            if row.get('kind') == 'rustNativePumpCurveAccountState' and row.get('accountTxnSignature'):
                account_index[(row['slot'], row['accountTxnSignature'])].append((line_no,row))
    with (output/'account-joins.jsonl').open('x') as out:
        for view in views:
            candidates = account_index[(view['slot'], view['signature'])]
            matches = [(line,row) for line,row in candidates if row.get('bondingCurve') in view['resolved_account_keys']]
            deadline = view['metadata_available_at_ns']+1_000_000_000 if type(view.get('metadata_available_at_ns')) is int else None
            timely = [(line,row) for line,row in matches if deadline and type(row.get('decodeCompleteUnixNs')) is int
                      and type(row.get('nativeReceiveUnixNs')) is int
                      and row['nativeReceiveUnixNs'] <= row['decodeCompleteUnixNs'] <= deadline]
            account_stats['transaction_rows'] += 1
            account_stats['signature_slot_account_matches'] += bool(matches)
            account_stats['matches_by_one_second_boundary'] += bool(timely)
            account_stats[view['execution_status']+'_denominator'] += 1
            account_stats[view['execution_status']+'_identity_match'] += bool(matches)
            if any(e['signed_sol_flow_eligible'] for e in view['pump_trade_event_prefixes']):
                account_stats['SOL_FLOW_denominator'] += 1
                account_stats['SOL_FLOW_identity_match'] += bool(matches)
                account_stats['SOL_FLOW_timely_match'] += bool(timely)
            out.write(json.dumps({'signature': view['signature'], 'slot': view['slot'],
                                  'account_refs': [{'line':line,'sha256':digest(row),'curve':row['bondingCurve'],
                                                    'available_ns':row.get('decodeCompleteUnixNs')} for line,row in matches],
                                  'timely_matches': len(timely), 'economic_state_certified': False})+'\n')
    # Exclude boundary slots to avoid counting intentionally partial capture as loss.
    # Late finalized/root notifications can name slots before subscription began;
    # only processed notifications/transactions define the observed interval.
    ordered = sorted(processed_slots | set(observed))
    interior = ordered[1:-1]
    selected = sorted(interior, key=lambda slot: digest(['coverage-v1', slot]))[:block_limit] if endpoint else []
    reconciled, reference, parity = Counter(), [], Counter()
    native_by_id={(v['slot'],v['signature']):v for v in views}
    with (output/'finalized-reference.jsonl').open('x') as out:
        for slot in selected:
            queried = time.time_ns()
            try:
                block, error = rpc(endpoint, 'getBlock', [slot, {'encoding':'json','commitment':'finalized',
                    'transactionDetails':'full','rewards':False,'maxSupportedTransactionVersion':1}])
            except Exception as exc:
                block,error=None,type(exc).__name__
            available = time.time_ns()
            result = reconcile_slot(block['transactions'], observed[slot]) if isinstance(block,dict) and isinstance(block.get('transactions'),list) else None
            if result:
                reconciled['resolved_blocks'] += 1
                reconciled['expected_pump_signatures'] += result['expected_pump_signatures']
                reconciled['observed_expected'] += result['observed_expected']
                reconciled['missing_transactions'] += len(result['missing_signatures'])
                reconciled['observed_not_finalized'] += len(result['observed_not_in_finalized_block'])
                for entry in block['transactions']:
                    signature=(entry.get('transaction') or {}).get('signatures',[None])[0]
                    native=native_by_id.get((slot,signature))
                    if native is not None and in_scope(entry):
                        final=normalize(dict(entry,slot=slot),signature)
                        parity['compared_transactions']+=1
                        for name,equal in compare_metadata(native,final).items():
                            parity[name+'_matches' if equal else name+'_mismatches']+=1
            else:
                reconciled['unresolved_blocks'] += 1
            reference.append({'slot':slot,'result':result,'error':error})
            out.write(json.dumps({'slot':slot,'request_at_ns':queried,'reference_available_at_ns':available,
                                  'raw_block':block,'error':error,'result':result,
                                  'usable_at_original_decision':False})+'\n')
            time.sleep(0.2)
    numbers=sorted(source_slots)
    report = {'schema':'PUMP_CAPTURE_QUALITY_V1','input':capture_manifest,'account_input':account_manifest,
              'session_start_ns':started,'session_end_ns':ended,'kinds':dict(kinds),'counts':dict(counts),
              'execution_statuses':dict(statuses),'decoder_gaps':dict(gaps),'account_join_counts':dict(account_stats),
              'reconnect_gaps':reconnects,'observed_slot_count':len(numbers),
              'slot_numeric_discontinuities':sum(b>a+1 for a,b in zip(numbers,numbers[1:])),
              'finalized_sample_size':len(selected),'finalized_comparison':dict(reconciled),
              'native_vs_finalized_metadata':dict(parity),
              'account_join_rates':{status:account_stats[status+'_identity_match']/account_stats[status+'_denominator']
                                    for status in ('SUCCESS','FAILED','SOL_FLOW') if account_stats[status+'_denominator']},
              'reference_results':reference,'global_capture_completeness':'UNKNOWN',
              'independent_scope':'Pump program transactions including CPI and failed/unknown execution; no teacher or mint shortlist',
              'economic_admission':False,
              'limitations':['Numeric slot gaps may be skipped slots, not necessarily capture loss.',
                             'Finalized comparison is only for the preselected interior slot sample, not the whole stream.',
                             'Unresolved reference blocks are unknown, not zero missing.',
                             'Later finality/reference data are never decision-time features.',
                             'Account joins prove identity/clock association, not entry-state continuity/fee validity.',
                             'Trade-event prefix amounts preserve unknown fee tails; transaction deltas are not swap fills.',
                             'Missing metadata/decodes remain in the denominator. No PnL evaluated.']}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--accounts',type=Path);p.add_argument('--rpc-env',type=Path);p.add_argument('--rpc-key',default='SECURE_RPC_URL')
    p.add_argument('--block-limit',type=int,default=16)
    a=p.parse_args()
    if not 0<=a.block_limit<=64:p.error('bounded audit block limit must be 0..64')
    print(json.dumps(run(a.input,a.output,a.accounts,read_endpoint(a.rpc_env,a.rpc_key) if a.rpc_env else None,a.block_limit)))
