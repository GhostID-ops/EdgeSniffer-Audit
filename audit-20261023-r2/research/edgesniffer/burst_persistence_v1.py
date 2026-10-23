"""Fixed, offline native-clock price diagnostic; no fills, fees or PnL invented."""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import random
import statistics

from ledger_v1 import SIZES
from transaction_evidence_v1 import digest

NS = 1_000_000_000
WINDOW = NS // 4
CONTRACT = Path(__file__).with_name('burst-persistence-contract-v1.json')


def price(event):
    q, t = event.get('virtual_quote_reserves'), event.get('virtual_token_reserves')
    return Fraction(q, t) if type(q) is int and q > 0 and type(t) is int and t > 0 else None


class Study:
    def __init__(self, population):
        self.population = population
        self.history = defaultdict(list)
        self.seeds = []
        self.seeded = set()
        self.seen = {}
        self.order = {}
        self.counts = Counter()
        self.start = self.end = None
        self.session = None

    def features(self, mint, now):
        hist = self.history[mint]
        if not hist or now-hist[0]['at'] < NS or hist[-1]['event'] is None:
            return None
        recent = [x for x in hist if now-WINDOW <= x['at'] <= now]
        before = [x for x in hist if x['at'] < now-WINDOW]
        if not recent or not before or any(x['event'] is None for x in recent):
            return None
        baseline = before[-1]
        if baseline['event'] is None or now-baseline['at'] > NS:
            return None
        if any(x['event'] is None for x in hist if x['at'] >= now-NS):
            return None
        events = [x['event'] for x in recent]
        buys = {e['user'] for e in events if e['side'] == 'buy'}
        flow = sum(e['sol_amount_lamports']*(1 if e['side'] == 'buy' else -1) for e in events)
        first, last = price(baseline['event']), price(events[-1])
        burst = len(events) >= 2 and all(e['side'] == 'buy' for e in events[-2:]) and len(buys) >= 2 and flow > 0 and last > first
        return {'burst': burst, 'baseline': baseline, 'last': recent[-1],
                'buyers': sorted(buys), 'net_flow_lamports': flow,
                'bucket': events[-1]['virtual_quote_reserves'].bit_length()-1}

    def consume(self, row):
        self.counts['input_transactions'] += 1
        if row.get('source_commitment') != 'processed' or row.get('finality_known_at_decision') is not False or not row.get('session_id'):
            raise ValueError('native processed session evidence required; historical recovery is not a native input')
        now = row.get('metadata_available_at_ns')
        if type(now) is not int or type(row.get('received_at_unix_ns')) is not int or now < row['received_at_unix_ns']:
            raise ValueError('invalid native clock; cannot certify this diagnostic')
        if self.end is not None and now < self.end:
            raise ValueError('native availability regressed; do not sort it away')
        if self.session is not None and row.get('session_id') != self.session:
            raise ValueError('sessions must be analyzed separately')
        self.session = row.get('session_id')
        self.start = now if self.start is None else self.start
        self.end = now
        sig = row.get('signature')
        if not sig:
            raise ValueError('missing signature')
        identity = {k: v for k, v in row.items() if k not in ('capture_line', 'metadata_available_at_ns', 'received_at_unix_ns')}
        h = digest(identity)
        if sig in self.seen:
            if self.seen[sig] != h:
                raise ValueError('conflicting signature evidence')
            self.counts['duplicate_transactions'] += 1
            return
        self.seen[sig] = h
        self.counts[row['execution_status']] += 1
        if row['execution_status'] != 'SUCCESS':
            return  # Reverted event logs NEVER count as executed flow or state.
        groups = defaultdict(list)
        for e in row.get('pump_trade_event_prefixes', []):
            groups[e['mint']].append(e)
        changed = []
        for mint, events in groups.items():
            event = events[0]
            order = (row.get('slot'), row.get('transaction_index'))
            order_ok = all(type(x) is int for x in order)
            reason = None
            if not order_ok or (mint in self.order and order < self.order[mint]):
                reason = 'late_or_unknown_canonical_order'
            elif len(events) != 1:
                reason = 'ambiguous_multi_event_same_mint'
            elif not row.get('instruction_identity_complete') or not event.get('signed_sol_flow_eligible'):
                reason = 'unknown_identity_or_non_sol_flow'
            elif event.get('mayhem') is not False:
                reason = 'mayhem_or_unknown'
            elif price(event) is None or event.get('side') not in ('buy', 'sell') or not event.get('user'):
                reason = 'invalid_event_state'
            if order_ok:
                self.order[mint] = max(order, self.order.get(mint, order))
            if reason:
                self.counts[reason] += 1
                event = None
            else:
                self.counts['eligible_events'] += 1
            point = {'at': now, 'signature': sig, 'event': event, 'reason': reason}
            self.history[mint].append(point)
            changed.append(mint)
        # The entire transaction becomes available together, not intermediate fills.
        for mint in sorted(changed):
            if mint in self.seeded:
                continue
            f = self.features(mint, now)
            if not f or not f['burst']:
                continue
            seed_id = digest([self.population, mint, sig, 'T02_T03_V1'])
            controls = []
            for other in sorted(self.history):
                if other == mint:
                    continue
                cf = self.features(other, now)
                if cf and not cf['burst'] and cf['bucket'] == f['bucket']:
                    controls.append(other)
            control = min(controls, key=lambda x: digest([seed_id, x])) if controls else None
            self.seeds.append({'seed_id': seed_id, 'mint': mint, 'seed_at_ns': now,
                               'signature': sig, 'initial_buyers': f['buyers'],
                               'baseline': f['baseline'], 'seed_state': f['last'],
                               'control_mint': control, 'control_candidates': len(controls),
                               'control_seed_state': self.features(control, now)['last'] if control else None})
            self.seeded.add(mint)

    def mark(self, mint, at):
        if at > self.end:
            return {'reason': 'right_censored', 'deadline_ns': at}
        hist = self.history.get(mint, [])
        i = bisect_right([p['at'] for p in hist], at)-1
        if i < 0:
            return {'reason': 'no_prior_state', 'deadline_ns': at}
        p = hist[i]
        reason = p['reason'] if p['event'] is None else 'stale_state' if at-p['at'] > NS else None
        return {'reason': reason, 'deadline_ns': at, 'state_at_ns': p['at'],
                'signature': p['signature'], 'event': p['event']}

    def confirm(self, seed):
        t = seed['seed_at_ns']
        mark = self.mark(seed['mint'], t+2*NS)
        if mark['reason']:
            return 'UNKNOWN', mark['reason']
        points = [p for p in self.history[seed['mint']] if t < p['at'] <= t+2*NS]
        if any(p['event'] is None for p in points):
            return 'UNKNOWN', 'invalid_confirmation_flow'
        buys = Counter()
        flow = 0
        for p in points:
            e = p['event']; amount = e['sol_amount_lamports']
            flow += amount*(1 if e['side'] == 'buy' else -1)
            if e['side'] == 'buy':
                buys[e['user']] += amount
        base = price(seed['baseline']['event'])
        initial = price(seed['seed_state']['event'])
        retained = price(mark['event']) >= base+(initial-base)/2
        breadth = len(set(buys)-set(seed['initial_buyers'])) >= 2
        balanced = bool(buys) and max(buys.values())*5 <= sum(buys.values())*3
        return ('CONFIRMED', None) if retained and flow > 0 and breadth and balanced else ('REJECTED', 'fixed_rule_not_met')

    def forward(self, mint, start, end):
        a, b = self.mark(mint, start), self.mark(mint, end)
        result = {'entry_mark': a, 'exit_mark': b, 'return_bps': None}
        if a['reason'] is None and b['reason'] is None:
            result['return_bps'] = float((price(b['event'])/price(a['event'])-1)*10000)
        return result

    def outcomes(self):
        rows = []
        for seed in self.seeds:
            t = seed['seed_at_ns']; mint = seed['mint']; control = seed['control_mint']
            confirmation, reason = self.confirm(seed)
            row = dict(seed, confirmation=confirmation, confirmation_reason=reason, arms={})
            row['continuation'] = {str(sec): self.forward(mint, t, t+sec*NS) for sec in (1, 2)}
            for arm, offset in (('burst', 1), ('persistence', 3)):
                start, end = t+offset*NS, t+(offset+16)*NS
                target = self.forward(mint, start, end)
                comparison = self.forward(control, start, end) if control else None
                admitted = arm == 'burst' or confirmation == 'CONFIRMED'
                policy_return = target['return_bps'] if admitted else 0.0 if confirmation == 'REJECTED' else None
                # Rejected persistence abstains for both legs: all-seed proxy, NOT PnL.
                control_return = None if comparison is None else comparison['return_bps'] if admitted else 0.0 if confirmation == 'REJECTED' else None
                row['arms'][arm] = {'decision_at_ns': start-NS, 'entry_at_ns': start, 'exit_at_ns': end,
                                    'admitted': admitted, 'target': target, 'control': comparison,
                                    'policy_return_bps': policy_return, 'control_policy_return_bps': control_return,
                                    'paired_excess_bps': policy_return-control_return if policy_return is not None and control_return is not None else None}
            rows.append(row)
        return rows


def summary(values):
    valid = [v for v in values if v is not None]
    return {'observations': len(valid), 'missing': len(values)-len(valid),
            'mean_bps': statistics.mean(valid) if valid else None,
            'median_bps': statistics.median(valid) if valid else None,
            'positive_fraction': sum(v > 0 for v in valid)/len(valid) if valid else None}


def uncertainty(pairs):
    blocks = defaultdict(list)
    for t, value in pairs:
        if value is not None:
            blocks[t//(20*NS)].append(value)
    result = {'nonempty_20s_blocks': len(blocks), 'descriptive_only': True,
              'warning': 'Few periods and reused control mints limit inference; not a validation interval', 'bootstrap_95_percent_bps': None}
    if len(blocks) >= 2:
        rng = random.Random(8431); groups = list(blocks.values()); estimates = []
        for _ in range(2000):
            draw = [v for group in rng.choices(groups, k=len(groups)) for v in group]
            estimates.append(statistics.mean(draw))
        estimates.sort(); result['bootstrap_95_percent_bps'] = [estimates[49], estimates[1949]]
    return result


def report(study, rows):
    use = Counter(r['control_mint'] for r in rows if r['control_mint'])
    result = {'opportunities': len(rows), 'unique_signal_mints': len({r['mint'] for r in rows}),
              'unique_control_mints': len(use), 'control_max_reuse': max(use.values(), default=0),
              'without_control': sum(r['control_mint'] is None for r in rows),
              'confirmation': dict(Counter(r['confirmation'] for r in rows)),
              'counts': dict(study.counts), 'duration_seconds': (study.end-study.start)/NS if study.end else 0,
              'arms': {}, 'net_profitability': 'NOT_ESTIMATED: reserve-price marks are not fills; fees/impact/inclusion not certified'}
    for arm in ('burst', 'persistence'):
        a = [r['arms'][arm] for r in rows]
        result['arms'][arm] = {'admitted': sum(x['admitted'] for x in a),
            'target_all_seed_abstention_proxy': summary([x['policy_return_bps'] for x in a]),
            'control_all_seed_abstention_proxy': summary([x['control_policy_return_bps'] for x in a]),
            'paired_excess': summary([x['paired_excess_bps'] for x in a]),
            'admitted_target_only': summary([x['target']['return_bps'] for x in a if x['admitted']]),
            'admitted_paired_excess_only': summary([x['paired_excess_bps'] for x in a if x['admitted']]),
            'known_abstentions': sum(not x['admitted'] and x['policy_return_bps'] == 0 for x in a),
            'missing_target_marks': dict(Counter(f"{which}:{x['target'][which]['reason']}" for x in a for which in ('entry_mark', 'exit_mark') if x['target'][which]['reason'])),
            'uncertainty': uncertainty([(r['seed_at_ns'], r['arms'][arm]['paired_excess_bps']) for r in rows])}
        if not any(x['admitted'] and x['paired_excess_bps'] is not None for x in a):
            result['arms'][arm]['uncertainty']['bootstrap_95_percent_bps'] = None
            result['arms'][arm]['uncertainty']['warning'] = 'No admitted matched outcomes; abstention zeros do not estimate a trading effect'
    differences = [r['arms']['persistence']['policy_return_bps']-r['arms']['burst']['policy_return_bps']
                   if all(r['arms'][a]['policy_return_bps'] is not None for a in ('burst', 'persistence')) else None for r in rows]
    result['persistence_minus_burst_same_seed_proxy'] = summary(differences)
    result['continuation'] = {str(s): summary([r['continuation'][str(s)]['return_bps'] for r in rows]) for s in (1, 2)}
    result['interpretation'] = 'Means use observed subsets only; missingness can be informative. No whole-population edge estimate, no net PnL, no reliable small-sample validation.'
    return result


def run(source, output, population):
    output.mkdir(parents=True, exist_ok=False)
    study = Study(population); sha = hashlib.sha256()
    with source.open('rb') as incoming:
        for raw in incoming:
            sha.update(raw); study.consume(json.loads(raw))
    rows = study.outcomes()
    with (output/'opportunities.jsonl').open('x') as out, (output/'attempts.jsonl').open('x') as attempts:
        for row in rows:
            out.write(json.dumps(row, sort_keys=True)+'\n')
            for arm in ('burst', 'persistence'):
                for size in SIZES:
                    attempts.write(json.dumps({'seed_id': row['seed_id'], 'arm': arm, 'size_lamports': size,
                        'entry_at_ns': row['arms'][arm]['entry_at_ns'], 'admitted': row['arms'][arm]['admitted'],
                        'confirmation': row['confirmation'], 'execution_status': 'NOT_SUBMITTED_RESEARCH_ONLY',
                        'net_pnl_sol': None, 'economics_status': 'BLOCKED_EXECUTABLE_QUOTE_ACCOUNTING'})+'\n')
    result = report(study, rows)
    result.update(population=population, input_sha256=sha.hexdigest(), contract=json.loads(CONTRACT.read_text()),
                  contract_sha256=hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
                  implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    result['artifacts'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob('*.jsonl'))}
    (output/'report.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True, type=Path); p.add_argument('--output', required=True, type=Path)
    p.add_argument('--population', required=True)
    args = p.parse_args()
    print(json.dumps(run(args.source, args.output, args.population), indent=2))
