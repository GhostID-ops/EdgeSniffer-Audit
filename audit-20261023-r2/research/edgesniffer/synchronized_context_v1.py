"""Off-path, signal-blind-rule context scheduler. No execution or future inputs.

Signals/controls use the unchanged native Study; checkpoints do not affect them.
Each selected target/control read set contains curve, mint, global, fee and Clock
in ONE getMultipleAccounts call. This is a bank observation, not a fill or proof
of continuous delivery. Responses after a boundary are never used at it.
"""
from burst_persistence_v1 import Study, NS
from transaction_evidence_v1 import normalize
from account_boundary_audit_v1 import derive_curves
from quote_context_adapter_v1 import FEE, GLOBAL, CLOCK


def native_view(row, line):
    view = normalize(row['transaction'])
    view.update(capture_line=line, received_at_unix_ns=row['received_at_unix_ns'],
                metadata_available_at_ns=row['normalized_at_unix_ns'],
                session_id=row['session_id'], source_commitment='processed',
                finality_known_at_decision=False)
    return view


class Scheduler:
    def __init__(self, population, signal_seconds, web3_module, derive=derive_curves, pre_signal=False):
        self.study = Study(population)
        self.signal_seconds = signal_seconds
        self.web3_module, self.derive = web3_module, derive
        self.mappings = {}
        self.start = None
        self.max_slot = 0
        self.seed_logs = []
        self.gaps = []
        self.disabled = False
        self.pre_signal = pre_signal
        self.sequence = 0
        self.last_received = None

    def consume(self, row, line, observed_at_ns):
        if 'sequence' in row:
            if row['sequence'] != self.sequence+1:
                self.gaps.append({'line':line,'kind':'SEQUENCE_GAP','at':observed_at_ns});self.disabled=True
            self.sequence=row['sequence']
        if 'received_at_unix_ns' in row:
            if self.last_received is not None and row['received_at_unix_ns']<self.last_received:
                self.gaps.append({'line':line,'kind':'CLOCK_REGRESSION','at':observed_at_ns});self.disabled=True
            self.last_received=row['received_at_unix_ns']
        if row.get('kind') in ('GAP', 'CONTEXT_SCOPE_LIMIT'):
            self.gaps.append({'line': line, 'kind': row['kind'], 'at': observed_at_ns})
            # Do not bridge feature windows across transport reconnects. Preserve
            # the capture, but stop generating research signals for this run.
            self.disabled = True
        if row.get('kind')=='ACCOUNT' and not self.disabled:
            self.max_slot=max(self.max_slot,row['account']['slot'])
        if row.get('kind') != 'TRANSACTION' or self.disabled:
            return []
        view = native_view(row, line)
        if self.start is None:
            self.start = view['metadata_available_at_ns']
        self.max_slot = max(self.max_slot, view['slot'])
        before = len(self.study.seeds)
        self.study.consume(view)
        new = self.study.seeds[before:]
        logs = []
        for seed in new:
            log = {'seed_id': seed['seed_id'], 'mint': seed['mint'],
                   'control_mint': seed['control_mint'],
                   'seed_at_ns': seed['seed_at_ns'],
                   'scheduler_observed_at_ns': observed_at_ns,
                   'observer_lag_ns': observed_at_ns-seed['seed_at_ns'],
                   'eligible_signal_period': seed['seed_at_ns'] <= self.start+self.signal_seconds*NS}
            self.seed_logs.append(log); logs.append(log)
        return logs

    def request_plan(self, now):
        if self.disabled:
            return []
        active = [s for s in self.seed_logs if s['eligible_signal_period'] and
                  s['seed_at_ns'] <= now <= s['seed_at_ns']+20*NS]
        mints = {m for s in active for m in (s['mint'], s['control_mint']) if m}
        # Acquire decision inputs BEFORE a burst, using an outcome/teacher-blind
        # rolling native activity universe. This changes acquisition only, not
        # signal rules, controls or opportunity accounting.
        candidates = set()
        if self.pre_signal and self.start is not None and now <= self.start+self.signal_seconds*NS:
            candidates = {m for m,h in self.study.history.items() if h and h[-1]['event'] is not None
                          and 0 <= now-h[-1]['at'] <= NS}
            mints |= candidates
        missing = mints-self.mappings.keys()
        if missing:
            self.mappings.update(self.derive(missing, self.web3_module)['mappings'])
        # Whole target/control pairs stay together; never split a read set across
        # requests/banks. Round-robin is unnecessary at this bounded scope, but
        # every batch is recorded and subsequent late responses stay missing.
        batches = []; addresses = [FEE, GLOBAL, CLOCK]; seeds = []
        for s in active:
            pair = [x for m in (s['mint'], s['control_mint']) if m
                    for x in (m, self.mappings[m]['curve'])]
            extra = [x for x in pair if x not in addresses]
            if len(set(addresses+extra)) > 100:
                batches.append({'addresses': addresses, 'seed_ids': seeds,
                                'minimum_native_slot': self.max_slot})
                addresses = [FEE, GLOBAL, CLOCK]; seeds = []
            addresses = list(dict.fromkeys(addresses+pair)); seeds.append(s['seed_id'])
        covered = set(addresses)
        covered.update(x for b in batches for x in b['addresses'])
        for mint in sorted(candidates):
            if mint in covered:continue
            pair = [mint,self.mappings[mint]['curve']]
            if len(set(addresses+pair))>100:
                batches.append({'addresses':addresses,'seed_ids':seeds,
                                'minimum_native_slot':self.max_slot})
                addresses=[FEE,GLOBAL,CLOCK];seeds=[]
            addresses=list(dict.fromkeys(addresses+pair));covered.update(pair)
        if len(addresses)>3:
            batches.append({'addresses': addresses, 'seed_ids': seeds,
                            'minimum_native_slot': self.max_slot})
        return batches
