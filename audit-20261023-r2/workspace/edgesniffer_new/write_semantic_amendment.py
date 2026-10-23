#!/usr/bin/env python3
import gzip
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path('/var/lib/8ycb-canonical')
ORDINALS = [140, 141, 142, 148]
TARGET = '8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ'

def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]

def main():
    ledger = {int(r['canonical_ordinal']): r for r in read_jsonl(ROOT / 'launch-identity-ledger.jsonl')}
    tape = read_jsonl(ROOT / 'pump-tape' / 'evidence.jsonl')
    artifacts = {int(r['canonical_ordinal']): r for r in read_jsonl(ROOT / 'economics' / 'canonical-economic-artifact.jsonl')}
    signals = {}
    v5 = Path('/var/lib/8ycb-prospective-shadow-v5-netcup/qualified-signals.jsonl')
    if v5.exists():
        for r in read_jsonl(v5):
            signals[r.get('trigger_signature')] = r
    v4_outcomes = {}
    v4 = Path('/mnt/HC_Volume_106775198/edgesniffer-derived/8ycb-prospective-shadow-v4/outcome_events.jsonl.gz')
    if v4.exists():
        with gzip.open(v4, 'rt', encoding='utf-8') as f:
            for line in f:
                try: r = json.loads(line)
                except ValueError: continue
                v4_outcomes[r.get('signature')] = r
    v4_signals = {}
    v4_signal_path = Path('/mnt/HC_Volume_106775198/edgesniffer-derived/8ycb-prospective-shadow-v4/signal_events.jsonl.gz')
    if v4_signal_path.exists():
        with gzip.open(v4_signal_path, 'rt', encoding='utf-8') as f:
            for line in f:
                try: r = json.loads(line)
                except ValueError: continue
                v4_signals[r.get('signature')] = r
    evidence = {}
    for n in ORDINALS:
        t = ledger[n]
        rows = [r for r in tape if int(r.get('canonical_ordinal', -1)) == n]
        atomic = next(r for r in rows if r.get('evidence_kind') == 'TRADE_EVENT' and r.get('transaction_signature') == t['signature'] and r.get('wallet') == TARGET and r.get('side') == 'buy')
        sig = signals.get(t['signature'], {})
        old = v4_outcomes.get(t['signature'], {})
        old_signal = v4_signals.get(t['signature'], {})
        old_primary = next((r for r in old.get('scenario_rows', []) if r.get('lane') == 'ACTUAL_SHADOW_SIGNAL_READY' and r.get('first_sellable_ms_after_entry') == 178 and not r.get('adverse_entry_stress')), None)
        primary = artifacts[n].get('primary')
        src_ns = int(t.get('source_receive_ns') or atomic.get('source_received_ns') or 0)
        dt = datetime.fromtimestamp(src_ns / 1e9, timezone.utc).astimezone(timezone(timedelta(hours=-4))).isoformat() if src_ns else None
        evidence[str(n)] = {
            'ordinal': n,
            'mint': t['mint'],
            'signature': t['signature'],
            'launch_time_edt': dt,
            'raw_buy_instruction_discriminator_hex': '66063d1201daebea',
            'raw_buy_instruction_data_hex': '66063d1201daebea2eea2a92b75800000001b2c40000000001',
            'instruction_token_amount_raw': 97545454545454,
            'actual_sol_spent_to_curve_lamports': 3000000000,
            'instruction_quote_amount_lamports': 3000000000,
            'max_sol_cost_lamports': 3300000000,
            'slippage_cap_lamports': 300000000,
            'slippage_cap_pct_of_executed_quote': 10.0,
            'pump_fee_component_lamports': 28500000,
            'network_fee_lamports': 1010000,
            'canonical_post_virtual_sol_reserves': atomic.get('virtual_sol_reserves_after'),
            'canonical_initial_virtual_sol_reserves': 30000000000,
            'legacy_v4_input_lamports': (old_signal.get('atomic_initial_buy') or {}).get('quote_lamports'),
            'v5_signal_max_cost_lamports': (sig.get('atomic_buy') or {}).get('quote_lamports'),
            'canonical_input_lamports': atomic.get('quote_lamports'),
            'canonical_input_source': 'canonical Pump TRADE_EVENT quote + reserve delta + finalized transaction transfer',
            'legacy_v4_input_source': 'V4 signal_events.jsonl.gz atomic_initial_buy.quote_lamports',
            'old_v4_net_roi_pct': old_primary.get('net_roi_pct') if old_primary else None,
            'correct_canonical_net_roi_pct': primary.get('net_roi_pct') if primary else None,
            'correct_canonical_exit': primary.get('exit_reason') if primary else None,
            'canonical_event_count': len(rows),
        }
    amendment = {
        'schema': '8ycb-canonical-semantic-correction-amendment-v1',
        'version': '8YCB_CANONICAL_ATOMIC_QUOTE_SEMANTICS_V1',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'methodology_change': False,
        'historical_artifacts_overwritten': False,
        'finding': 'Pump BUY instruction second u64 is max_sol_cost, not executed curve quote.',
        'mapping': {
            'executed_curve_quote_lamports': 3000000000,
            'max_sol_cost_lamports': 3300000000,
            'slippage_cap_lamports': 300000000,
            'reserve_delta_lamports': 3000000000,
            'curve_transfer_lamports': 3000000000,
            'pump_fee_transfers_lamports': [14250000, 14250000],
            'entry_input_for_frozen_follower': 'executed_curve_quote_lamports / canonical quote_lamports = 3.000 SOL',
        },
        'scientifically_correct_entry_input': '3.000 SOL executed curve quote; never max_sol_cost 3.300 SOL',
        'legacy_v4_semantically_compatible': False,
        'canonical_input_correct': True,
        'semantic_correction_applied': True,
        'evidence': evidence,
        'controls': {
            str(n): {
                'legacy_v4_net_roi_pct': evidence[str(n)]['old_v4_net_roi_pct'],
                'correct_canonical_net_roi_pct': evidence[str(n)]['correct_canonical_net_roi_pct'],
                'delta_pct_points': (evidence[str(n)]['correct_canonical_net_roi_pct'] - evidence[str(n)]['old_v4_net_roi_pct']) if evidence[str(n)]['old_v4_net_roi_pct'] is not None else None,
            } for n in [140, 141, 142]
        },
        'new_launches': {
            'frontier_before': 147,
            'frontier_after': 148,
            'count': 1,
            'ordinal': 148,
            'pipeline': 'identity -> tape -> V5 shadow -> frozen horizon -> canonical economic artifact',
        },
        'submission': {'count': 0, 'enabled': False},
    }
    (ROOT / 'economics' / 'semantic-correction-amendment-v1.json').write_text(json.dumps(amendment, sort_keys=True, indent=2) + '\n')
    report = json.loads((ROOT / 'canonical-report.json').read_text())
    report['semantic_correction_amendment'] = 'economics/semantic-correction-amendment-v1.json'
    report['scientifically_correct_entry_input'] = '3.000 SOL executed curve quote'
    report['legacy_v4_input_semantically_compatible'] = False
    report['canonical_input_semantically_correct'] = True
    report['semantic_correction_applied'] = True
    report['post_147_processing'] = evidence['148']
    (ROOT / 'economics' / 'semantic-correction-report-v1.json').write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'amendment': str(ROOT / 'economics' / 'semantic-correction-amendment-v1.json'), 'report': str(ROOT / 'economics' / 'semantic-correction-report-v1.json'), 'new_launch': evidence['148'], 'controls': amendment['controls']}, sort_keys=True, indent=2))

if __name__ == '__main__':
    main()
