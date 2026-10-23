#!/usr/bin/env python3
"""Offline audit counterexamples. Runs supplied pure functions on synthetic rows.

No network, real trade data, credentials, or production outputs are used.
"""
from __future__ import annotations
import argparse
import json
import math
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root / 'scripts'))
    import runIndependentStrategyLabV1 as lab
    import runNativeLatencyBoundedStudyV1 as latency
    import materialize2CQFingerprintMarketWindowsV1 as broad
    import buildIndependentMarketControlsV1 as controls

    def state(vsol=31_000_000_000):
        return {'virtual_sol': vsol, 'virtual_token': 1_000_000_000_000,
                'real_sol': 5_000_000_000, 'real_token': 900_000_000_000}

    def native(ts, side, mint='audit-mint'):
        return {'mint': mint, 'side': side, 'local_receive_ts_ms': ts,
                'transaction_signature': f'sig-{mint}-{ts}', 'observation_id': f'obs-{mint}-{ts}',
                'route': 'PUMP_BONDING_CURVE_TOP_LEVEL', 'post_state': state(),
                'fee_basis_points': 125, 'protocol_fee_basis_points': 95,
                'creator_fee_basis_points': 30, 'native_economics_state_complete': True,
                'quote_lamports': 100_000_000,
                'raw_record': {'wallet': 'market-wallet', 'nativeKind': 'flow'}}

    def window(mint, ts, sig):
        return {'record_type': 'INDEPENDENT_MARKET_WINDOW', 'mint': mint,
                'signal_receive_ts_ms': ts, 'signal_signature': sig,
                'route': 'PUMP_BONDING_CURVE_TOP_LEVEL', 'entry_state': state(),
                'fee_basis_points': 125, 'protocol_fee_basis_points': 95,
                'creator_fee_basis_points': 30,
                'net_flow_sol': 1, 'price_change_bps': 20,
                'effective_quote_reserve_sol': 31,
                'lineage': {'source_receive_clock': True, 'entry_state_source': sig,
                            'entry_state_receive_ts_ms': ts + 1000},
                'observation_as_of_ts_ms': ts + 400_000, 'marks': []}

    results = {}
    with tempfile.TemporaryDirectory(prefix='edgesniffer_audit_governance_') as td:
        p = Path(td)
        fingerprint = p/'fingerprint.json'
        fingerprint.write_text(json.dumps({'derived_fingerprint': {'name': 'SYNTHETIC',
            'curve_phase_observations': {'virtual_sol_sol': {'p75': 46.171004438}}}}))
        tape = p/'tape.jsonl'
        tape.write_text(''.join(json.dumps(r)+'\n' for r in [
            native(1_000, 'buy'), native(3_000, 'sell'), native(301_000, 'sell'),
            native(302_000, 'sell')]))
        out = p/'windows.jsonl'
        report = broad.materialize(tape, fingerprint, out)
        w = json.loads(out.read_text().splitlines()[0])
        deadline = w['signal_receive_ts_ms'] + lab.ENTRY_DELAY_MS + 300_000
        mark = lab.first_at_or_after(w, deadline)
        assert mark is None
        results['strict_300s_window_truncation'] = {
            'source_has_mark_at_deadline': 302_000, 'deadline': deadline,
            'last_retained_mark': max(lab.timestamp_ms(m) for m in w['marks']),
            'observation_as_of': w['observation_as_of_ts_ms'],
            'matured': lab.horizon_matured(w, deadline), 'strict_exit_found': mark is not None,
            'materializer_report': report}

        original = [window('mint-a', 10_000, 'A'), window('mint-b', 12_000, 'B'),
                    window('mint-c', 14_000, 'C'), window('mint-d', 16_000, 'D')]
        source = p/'candidate_windows.jsonl'
        source.write_text(''.join(json.dumps(r)+'\n' for r in original))
        control_path, control_report = p/'controls.jsonl', p/'controls_report.json'
        controls.build(source, control_path, control_report)
        baseline = [json.loads(s) for s in control_path.read_text().splitlines()]
        original_sigs = {r['signal_signature'] for r in original}
        overlapping = [r['signal_signature'] for r in baseline if r['signal_signature'] in original_sigs]
        assert overlapping == ['B', 'D']
        results['control_self_inclusion'] = {
            'candidate_count': len(original), 'control_count': len(baseline),
            'controls_also_in_candidate_input': overlapping,
            'matched_pairs': [{'candidate': r['matched_candidate_signal_signature'],
                              'control': r['signal_signature']} for r in baseline],
            'note': 'The evaluator does not restrict candidate input to the left member or use pair IDs.'}

        contract = p/'contradictory_contract.json'
        contract.write_text(json.dumps({'entry_delay_ms': 9_999,
            'sizes_sol': [42], 'exit_horizons_seconds': [888],
            'maximum_checkpoint_delay_ms': [999], 'candidate_filters': ['NOT_A_REAL_ARM']}))
        empty = p/'empty.jsonl'; empty.write_text('')
        rendered = latency.evaluate(empty, contract)
        assert len(rendered['surface']) == 567
        assert rendered['study_contract']['entry_delay_ms'] == 9_999
        assert any('|0.1|' in r['strategy_id'] for r in rendered['surface'])
        results['preregistration_contract_not_enforced'] = {
            'declared_contract': rendered['study_contract'],
            'actual_first_strategy_id': rendered['surface'][0]['strategy_id'],
            'actual_surface_count': len(rendered['surface']),
            'actual_entry_delay_from_imported_code_ms': lab.ENTRY_DELAY_MS}

    results['search_surface_size'] = {
        'filters': len(lab.FILTERS), 'exits': len(lab.EXIT_RULES),
        'sizes': len(lab.NOTIONALS_SOL), 'stress_levels': len(lab.STRESS_BPS),
        'possible_cells': len(lab.FILTERS)*len(lab.EXIT_RULES)*len(lab.NOTIONALS_SOL)*len(lab.STRESS_BPS),
        'latency_study_cells': len(latency.FILTER_IDS)*len(latency.HORIZONS)*len(latency.NOTIONALS)*len(latency.STRESS_BPS)*len(latency.LATENCY_BUCKETS)}
    results['normal_approximation_effective_sample_size'] = {
        'formula': '(1.96+0.84)^2 * sigma^2 / delta^2; independent paired differences only, illustrative',
        'sigma_25pct_delta_2pct': math.ceil((1.96+.84)**2 * .25**2 / .02**2),
        'sigma_50pct_delta_2pct': math.ceil((1.96+.84)**2 * .5**2 / .02**2)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2)+'\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
