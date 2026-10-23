"""Population explanation only. Intent/error activity is never executed flow."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

from transaction_evidence_v1 import PUMP, digest, b58decode

NAMES = ('buy', 'buy_v2', 'buy_v3', 'buy_exact_sol_in', 'buy_exact_quote_in_v2',
         'buy_exact_quote_in_v3', 'sell', 'sell_v2')
DISCS = {hashlib.sha256(('global:'+n).encode()).digest()[:8].hex(): n for n in NAMES}


def classify(row):
    pump = [i for i in row['instructions'] if i['program_id'] == PUMP]
    intent = []
    for i in pump:
        name = DISCS.get(i['discriminator_hex'])
        if name and i['identity_resolved'] and len(b58decode(i['data_base58'])) >= 24:
            intent.append((name, 'buy' if name.startswith('buy') else 'sell'))
    sides = {s for _, s in intent}
    side = next(iter(sides)) if len(sides) == 1 else 'mixed' if sides else 'unknown'
    route_ix = [i for i in pump if i['discriminator_hex'] in DISCS] or pump
    route = 'no_pump_instruction_observed' if not pump else 'direct_and_cpi' if any(i['inner_index'] is None for i in route_ix) and any(i['inner_index'] is not None for i in route_ix) else 'direct' if any(i['inner_index'] is None for i in route_ix) else 'cpi'
    return pump, intent, side, route


def correlation(a, b):
    if len(a) < 3: return None
    ma, mb = sum(a)/len(a), sum(b)/len(b)
    da, db = [x-ma for x in a], [x-mb for x in b]
    den = math.sqrt(sum(x*x for x in da)*sum(x*x for x in db))
    return sum(x*y for x,y in zip(da,db))/den if den else None


def lines(path, mode='r'):
    with path.open(mode) as incoming:
        yield from incoming


def run(source, reference, output):
    output.mkdir(parents=True, exist_ok=False)
    labels = {}
    if reference:
        for line in lines(reference):
            ref = json.loads(line)
            for tx in (ref.get('raw_block') or {}).get('transactions', []):
                labels[tx['transaction']['signatures'][0]] = (tx['meta']['err'], ref['reference_available_at_ns'])
    counts = Counter(); errors = Counter(); routes = Counter(); sides = Counter(); instructions = Counter()
    wallets = Counter(); mints = Counter(); payloads = defaultdict(set); seen = set()
    error_labels = defaultdict(Counter); bins = defaultdict(Counter); native_mints = set(); mentions = Counter()
    sha = hashlib.sha256(); first = None; last = None
    for raw in lines(source, 'rb'):
        sha.update(raw); row = json.loads(raw); counts['frames'] += 1
        sig = row['signature']; now = row['metadata_available_at_ns']
        if sig in seen:
            counts['repeated_signature_frames'] += 1; continue
        seen.add(sig); status = row['execution_status']; counts[status] += 1
        first = now if first is None else first; last = now
        sec = (now-first)//1_000_000_000; bins[sec][status] += 1
        events = row['pump_trade_event_prefixes']
        # Only identifiers already known at this metadata boundary; never a future-mint join.
        native_mints.update(e['mint'] for e in events)
        if status == 'SUCCESS':
            for e in events:
                if e['signed_sol_flow_eligible']:
                    bins[sec]['successful_sol_events'] += 1
                    bins[sec]['successful_buy_events' if e['side'] == 'buy' else 'successful_sell_events'] += 1
                    bins[sec]['net_executed_sol_lamports'] += e['sol_amount_lamports']*(1 if e['side'] == 'buy' else -1)
            continue
        if status != 'FAILED': continue
        pump, intent, side, route = classify(row)
        sides[side] += 1; routes[route] += 1
        instructions.update(n for n,_ in intent)
        if not intent: counts['failed_without_known_pump_trade_intent'] += 1
        counts['failed_with_pump_trade_event'] += bool(events)
        mints.update(set(e['mint'] for e in events))
        candidates = native_mints & set(row['resolved_account_keys'])
        mentions.update(candidates)
        counts['failed_with_native_known_mint_account_mention'] += bool(candidates)
        counts['failed_with_multiple_known_mint_mentions'] += len(candidates) > 1
        payer = row['resolved_account_keys'][0] if row['resolved_account_keys'] else 'unknown'
        wallets[payer] += 1
        error = json.dumps(row['execution_error'], sort_keys=True)
        errors[error] += 1
        if sig in labels:
            label, available = labels[sig]
            error_labels[error][json.dumps(label, sort_keys=True)] += 1
            counts['failed_with_later_reference_label'] += 1
            if available <= now: counts['unexpected_reference_clock'] += 1
        structural = [(i['program_id'], i['accounts'], i['data_base58']) for i in (pump or row['instructions'])]
        payloads[(payer, digest(structural))].add(sig)
    second_rows = [dict(second=i, **bins[i]) for i in range(max(bins)+1)] if bins else []
    failure = [b.get('FAILED', 0) for b in second_rows]
    success = [b.get('successful_sol_events', 0) for b in second_rows]
    result = {'schema': 'FAILED_TRANSACTION_POPULATION_DIAGNOSTIC_V1', 'input_sha256': sha.hexdigest(),
        'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'reference_sha256': hashlib.sha256(reference.read_bytes()).hexdigest() if reference else None,
        'counts': dict(counts), 'failure_fraction': counts['FAILED']/len(seen) if seen else None,
        'failure_routes': dict(routes), 'failure_intent': dict(sides), 'failure_instruction_counts': dict(instructions),
        'error_breakdown': [{'native_error': json.loads(e), 'count': n,
                             'later_reference_labels': dict(error_labels[e])} for e,n in errors.most_common()],
        'payer_concentration': {'unique': len(wallets), 'top1_count': sum(n for _,n in wallets.most_common(1)),
                               'top10_count': sum(n for _,n in wallets.most_common(10)), 'top20': wallets.most_common(20)},
        'event_identified_failed_mints': {'unique': len(mints), 'top20': mints.most_common(20)},
        'native_known_mint_mentions_NOT_intent_attribution': {'unique': len(mentions), 'top20': mentions.most_common(20)},
        'retry_like_groups': {'definition': 'same payer and observed Pump instruction payload/accounts (or entire observed instruction list if none); different signatures; not proof of retries or a single actor',
                              'groups_ge2': sum(len(s)>1 for s in payloads.values()),
                              'transactions_in_groups_ge2': sum(len(s) for s in payloads.values() if len(s)>1),
                              'largest_group': max((len(s) for s in payloads.values()), default=0)},
        'temporal_diagnostic': {'one_second_bins': len(second_rows),
            'failure_vs_same_second_successful_sol_event_count_correlation': correlation(failure, success),
            'failure_vs_next_second_successful_sol_event_count_correlation': correlation(failure[:-1], success[1:]),
            'interpretation': 'Descriptive market-wide counts, not controlled prediction. No reliable mint price/liquidity attribution for most failures; no volatility or net-return claim.'},
        'causality': {'native_known': 'status, raw error bytes, observed instruction identity/intent, account mentions, metadata availability clock',
                     'later_known': 'Human-readable labels from retained finalized block references; label mapping is explanatory only, not injected into original decisions',
                     'failed_flow': 'ZERO executed swap flow; events in reverted transactions remain attempted context, not fills'},
        'next': 'Prioritize validated native mint/intent attribution only if failure population is diverse enough; preregister a controlled failure-intensity experiment before testing returns. Burst/persistence stays primary.'}
    (output/'seconds.jsonl').write_text(''.join(json.dumps(r, sort_keys=True)+'\n' for r in second_rows))
    (output/'report.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--source', type=Path, required=True)
    p.add_argument('--reference', type=Path); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); print(json.dumps(run(a.source, a.reference, a.output), indent=2))
