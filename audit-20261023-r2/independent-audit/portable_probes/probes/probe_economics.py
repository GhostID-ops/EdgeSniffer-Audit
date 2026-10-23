#!/usr/bin/env python3
"""Offline adversarial probes. Imports audited pure helpers; no network or trading.

Run with --source pointing at the unpacked source root. All persisted probe
outputs are written to --output; temporary fixtures are auto-removed.
"""
from __future__ import annotations
import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

p = argparse.ArgumentParser()
p.add_argument('--source', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
sys.path.insert(0, str(a.source / 'scripts'))
import exactCurveEconomicsV1 as curve
import runIndependentStrategyLabV1 as lab
import materializeNativeReserveHorizonCheckpointsV1 as cp
import executableOpportunityCensusV1 as census

L = 1_000_000_000
FEES = {'fee_basis_points': 125, 'protocol_fee_basis_points': 95,
        'creator_fee_basis_points': 30}
S = {'virtual_sol': 30*L, 'virtual_token': 1_000_000_000_000_000,
     'real_sol': 10*L, 'real_token': 800_000_000_000_000}
def opportunity(**kw):
    return {'record_type': 'INDEPENDENT_MARKET_WINDOW', 'mint': 'synthetic-mint',
            'route': 'PUMP_CURVE', 'signal_receive_ts_ms': 1000,
            'entry_state': dict(S), 'net_flow_sol': .1, 'price_change_bps': 20,
            'effective_quote_reserve_sol': 30, 'observation_as_of_ts_ms': 400000,
            'marks': [], **FEES, **kw}
def mark(ts, state=None, **kw):
    return {'receive_ts_ms': ts, 'state': dict(state or S), **FEES, **kw}
out = {}

# A missing quote becomes an invisible opportunity, not a failed entry.
bad = opportunity(entry_state={**S, 'real_token': 1})
outcomes, surfaces, rejections = lab.evaluate_population([bad])
assert not outcomes and not surfaces and not rejections
out['failed_entry_disappears'] = {'input_signals': 1, 'outcome_rows': len(outcomes),
                                'surfaces': len(surfaces), 'rejections': rejections}

# Accounting keeps only the resolvable profitable row.
rows = [{'notional_sol': 1., 'status': 'ACTIONABLE', 'pnl_sol': 2.192002151}]
rows += [{'notional_sol': 1., 'status': 'MISSED_EXIT_MARK', 'pnl_sol': None} for _ in range(8)]
summ = lab.summary(rows)
assert summ['pnl_sol'] == 2.192002151 and summ['actionable'] == 1
out['unresolved_inventory_omitted'] = {
    'reported': summ, 'if_eight_unresolved_positions_are_total_losses_sol': 2.192002151-8,
    'note': 'Sensitivity bound only; does not assert that unresolved positions are worthless.'}

# Boundary checker verifies labels, not entry chronology.
future = opportunity(lineage={'source_receive_clock': True, 'entry_state_source': 'future',
                             'entry_state_receive_ts_ms': 999999})
assert lab.has_native_receipt_boundary(future)
out['future_entry_state_boundary_accepted'] = {'signal_ts_ms':1000,
    'intended_entry_ts_ms':2000, 'state_receive_ts_ms':999999,
    'accepted':lab.has_native_receipt_boundary(future)}

# Fee-field absence silently quotes an exit at zero protocol/creator fees.
buy = lab.quote_buy(opportunity(), 1., 0)
assert buy is not None
no_fees = {'receive_ts_ms':17000, 'state':dict(S)}
with_fees_pnl = lab.quote_sell(mark(17000), buy.token_out, 1., 0)
no_fees_pnl = lab.quote_sell(no_fees, buy.token_out, 1., 0)
assert no_fees_pnl > with_fees_pnl
out['missing_exit_fees_default_zero'] = {
    'fee_object':dataclasses.asdict(lab.fee_for(no_fees,0)),
    'pnl_125bps_sol':with_fees_pnl,'pnl_missing_fee_sol':no_fees_pnl,
    'overstatement_sol':no_fees_pnl-with_fees_pnl}

# Virtual reserve filter accepts zero real quote inventory.
empty = opportunity(entry_state={**S, 'real_sol':0})
empty_buy = lab.quote_buy(empty,.1,0)
reserve_filter = next(f for f in lab.FILTERS if f['id']=='RESERVE_GE_2_SOL')
assert lab.eligible(reserve_filter,empty,empty_buy,.1)
out['virtual_reserve_is_not_real_liquidity'] = {'virtual_quote_sol':30,
    'real_quote_sol':0,'reserve_ge_2_sol_passes':True}

# Historical-state quoting is not a self-consistent round trip. Its bias is
# not always upward: omitting our own prior buy can erase collateral and
# create artificial sell failures on a market that otherwise did not move.
roundtrips = []
for size in (.1,.5,1.):
    b = lab.quote_buy(empty,size,0)
    assert b is not None
    original_mark = mark(17000,{**S,'real_sol':0})
    rebased_mark = mark(17000,dataclasses.asdict(b.post_state))
    historical = lab.quote_sell(original_mark,b.token_out,size,0)
    rebased = lab.quote_sell(rebased_mark,b.token_out,size,0)
    assert historical is None and rebased is not None
    roundtrips.append({'notional_sol':size,'historical_unmodified_mark_pnl':historical,
                       'flat_market_self_consistent_roundtrip_pnl_sol':rebased})
out['own_buy_state_omission'] = roundtrips

# Independent partial legs reuse the same pre-sell liquidity state.
s = curve.CurveState(**S)
tokens = buy.token_out
f = curve.Fees(95,30)
first = curve.quote_exact_token_sell(s,tokens//2,0,f)
second_true = curve.quote_exact_token_sell(first.post_state,tokens-tokens//2,0,f)
second_reset = curve.quote_exact_token_sell(s,tokens-tokens//2,0,f)
out['partial_sells_reuse_liquidity'] = {
    'first_leg_receipts_lamports': first.immediate_quote_out,
    'second_leg_receipts_reused_state_lamports':second_reset.immediate_quote_out,
    'second_leg_receipts_updated_state_lamports':second_true.immediate_quote_out,
    'overstatement_sol':(second_reset.immediate_quote_out-second_true.immediate_quote_out)/L,
    'scope':'Static no-external-flow counterexample; actual partial paths require replay.'}
assert second_reset.immediate_quote_out > second_true.immediate_quote_out

# A carry exit changes retroactively when an arbitrarily late future mark is
# appended. A true at-deadline carry rule cannot make that choice.
rule = {'id':'CARRY_15S','kind':'FIXED_CARRY','horizon_seconds':15}
base = opportunity(marks=[mark(16000)])
late_state = {**S, 'virtual_sol':20*L,'virtual_token':1_500_000_000_000_000}
late = opportunity(marks=[mark(16000),mark(300000,late_state)])
r1 = lab.exit_result(base,buy,1.,rule,0,2000)
r2 = lab.exit_result(late,buy,1.,rule,0,2000)
assert r1['exit_reason'] == 'CARRY_FORWARD_LAST_NATIVE_STATE'
assert r2['exit_reason'] == 'FIXED_HORIZON'
assert r1['pnl_sol'] != r2['pnl_sol']
out['carry_rule_depends_on_future_append'] = {
    'deadline_ms':17000,'latest_before_deadline_ms':16000,'appended_future_mark_ms':300000,
    'before_future_append':r1,'after_future_append':r2}

# The checkpoint cursor can permanently consume updates before a window is
# published. The same final files produce different checkpoints by schedule.
def seed(ts):
    return {'bondingCurve':'curve-a','observedAtMs':ts,'native_receive_unix_ns':ts*1_000_000,
            'slot':10,'write_version':1,'account_txn_signature':'sig',
            'virtualSolReserves':S['virtual_sol'],'virtualTokenReserves':S['virtual_token'],
            'realSolReserves':S['real_sol'],'realTokenReserves':S['real_token'],
            'complete':False}
win = {'record_type':'INDEPENDENT_MARKET_WINDOW','fingerprint_id':'fp','mint':'mint-a',
       'signal_receive_ts_ms':1000,'signal_signature':'s','bonding_curve':'curve-a'}
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    args=SimpleNamespace(windows=root/'windows.jsonl',seeds=root/'seeds.jsonl',
                         output=root/'out.jsonl',state=root/'state.json',
                         start_at_receive_ms=None,horizons=(15,))
    args.windows.write_text('')
    args.seeds.write_text(json.dumps(seed(17000))+'\n')
    state_doc=cp.load_state(args.state); emitted=set()
    first_pass=cp.process_once(args,state_doc,emitted)
    args.windows.write_text(json.dumps(win)+'\n')
    second_pass=cp.process_once(args,state_doc,emitted)
    assert not args.output.read_text().strip()
    with args.seeds.open('a') as h:h.write(json.dumps(seed(60000))+'\n')
    cp.process_once(args,state_doc,emitted)
    incremental=json.loads(args.output.read_text())
    args.output=root/'clean.jsonl'; args.state=root/'clean-state.json'
    cp.process_once(args,cp.load_state(args.state),set())
    clean=json.loads(args.output.read_text())
    assert incremental['delay_after_deadline_ms']==43000
    assert clean['delay_after_deadline_ms']==0
    out['checkpoint_late_window_cursor_race']={
        'first_pass_unmatched_seed_rows':first_pass['unmatched_seed_rows'],
        'second_pass_checkpoints':second_pass['checkpoints_written'],
        'incremental_checkpoint_ms':incremental['checkpoint_receive_ts_ms'],
        'incremental_reported_delay_ms':incremental['delay_after_deadline_ms'],
        'clean_replay_checkpoint_ms':clean['checkpoint_receive_ts_ms'],
        'clean_replay_delay_ms':clean['delay_after_deadline_ms'],
        'same_final_window_and_seed_files':True}

# Checkpoint fees are imputed, including overriding an explicit zero.
w = {**win,'_checkpoint_curve':'curve-a','_window_id':'window-a'}
missing_fee=cp.make_checkpoint(w,15,seed(17000),S,17000)
zero_fee=cp.make_checkpoint(w,15,{**seed(17000),'fee_basis_points':0},S,17000)
assert missing_fee['fee_basis_points']==125 and zero_fee['fee_basis_points']==125
out['checkpoint_fee_imputation']={'absent_fee_output_bps':missing_fee['fee_basis_points'],
    'absent_fee_explicit_flag':missing_fee['fee_fields']['explicit_in_seed'],
    'explicit_zero_fee_output_bps':zero_fee['fee_basis_points'],
    'explicit_zero_fee_explicit_flag':zero_fee['fee_fields']['explicit_in_seed']}

# Integer-formula disagreement between fee subtraction used in native state
# reconstruction and the exact helper. Excludes new protocol transition changes.
fee_comparison=[]
for size in (.1,.5,1.):
    gross=round(size*L)
    q=curve.quote_exact_quote_in_buy(curve.CurveState(**S),gross,f)
    subtract=gross-gross*125//10000
    fee_comparison.append({'notional_sol':size,'curve_in_exact_lamports':q.curve_quote_in,
                           'curve_in_subtract_gross_125bps_lamports':subtract,
                           'difference_lamports':subtract-q.curve_quote_in})
out['gross_fee_subtraction_vs_exact_inverse'] = fee_comparison

# Different parts of the repository assign different meanings to the same
# `fee_basis_points` key: aggregate native fee versus TradeEvent protocol fee.
legacy_event={'event':{'fee_basis_points':95,'creator_fee_basis_points':30}}
out['fee_namespace_collision']={
    'trade_event_protocol_bps':95,'trade_event_creator_bps':30,
    'expected_total_under_trade_event_convention':125,
    'lab_inferred_total_bps':lab.fee_for(legacy_event,0).total_bps}
assert lab.fee_for(legacy_event,0).total_bps == 95

# Older N350 core does apply own-state mutations and sell landing delays.
# However, it also manufactures an ENDPOINT at the requested horizon without
# accepting any source-health watermark or proof that the forward tape is
# complete. This probe is an evidence-status test, not a profitability claim.
fee_event=SimpleNamespace(creator_fee=1,cashback=0,fee_basis_points=95,
    creator_fee_basis_points=30,cashback_fee_basis_points=0,buyback_fee_basis_points=0)
decision={'decisionMs':1000,'baseState':curve.CurveState(**{**S,'real_sol':0}),
          'feeEvent':fee_event,'futureIndex':0}
cell=census.evaluate_cell(decision,[],.1,'ONE_SECOND',1000,2000,1000,[0.0],[10.0])
assert cell['status']=='EXACT' and cell['counterfactualAttemptedTransactions']==0
out['historical_core_empty_future_still_exact']={k:cell[k] for k in
    ('status','entryMs','horizonMs','counterfactualAttemptedTransactions','policies')}

dest=a.output
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
print(json.dumps(out,indent=2,sort_keys=True))
