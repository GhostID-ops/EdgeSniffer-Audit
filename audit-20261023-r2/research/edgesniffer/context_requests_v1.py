"""Bounded off-path RPC workers. No unbounded queue or receive-path HTTP."""
from concurrent.futures import ThreadPoolExecutor
import time


class ContextRequests:
    def __init__(self, retrieve, clock=time.time_ns):
        self.retrieve, self.clock = retrieve, clock
        self.pool = ThreadPoolExecutor(max_workers=2,thread_name_prefix='research-context')
        self.pending = {}

    def available(self, lane):
        return lane not in self.pending

    def submit(self, lane, addresses, plan=None):
        if lane not in ('checkpoint','bootstrap'):raise ValueError('unknown bounded lane')
        if not self.available(lane):return False
        # At most one outstanding request per lane. No stale work queue.
        self.pending[lane]=self.pool.submit(self.retrieve,list(addresses),plan)
        return True

    def poll(self):
        rows=[]
        for lane,future in list(self.pending.items()):
            if future.done():
                row=future.result()
                # Response completion and publication to the research observer
                # are distinct. The latter, later clock governs replay admission.
                row['observer_published_at_ns']=self.clock()
                row['request_lane']=lane
                rows.append(row);del self.pending[lane]
        return rows

    def finish(self):
        self.pool.shutdown(wait=True)
        return self.poll()
