import threading
import unittest
from context_requests_v1 import ContextRequests
from quote_context_adapter_v1 import snapshot_available_at
import test_state_continuity_v1 as fixtures
from state_continuity_v1 import checkpoint_context


class RequestTests(unittest.TestCase):
    def test_delayed_rpc_does_not_block_caller_and_queue_is_bounded(self):
        gate=threading.Event()
        def retrieve(addresses,plan):
            gate.wait(timeout=1)
            return {'request_at_ns':1,'response_available_at_ns':2,'addresses':addresses,'error':None}
        requests=ContextRequests(retrieve,clock=lambda:3)
        try:
            self.assertTrue(requests.submit('checkpoint',['a'],{}))
            self.assertTrue(requests.submit('bootstrap',['b']))
            self.assertFalse(requests.submit('checkpoint',['dropped']))
            self.assertEqual(len(requests.pending),2)
            # The caller can continue receiving without waiting for either RPC.
            self.assertEqual(requests.poll(),[])
            gate.set()
            for future in requests.pending.values():future.result(timeout=2)
            rows=requests.poll();self.assertEqual(len(rows),2)
            self.assertTrue(all(snapshot_available_at(r)==3 for r in rows))
            self.assertFalse(requests.pending)
        finally:
            gate.set();requests.finish()

    def test_rpc_error_is_preserved_as_published_missing_context(self):
        requests=ContextRequests(lambda a,p:{'request_at_ns':1,'response_available_at_ns':2,'error':'RPC_ERROR_1'},clock=lambda:3)
        try:
            requests.submit('bootstrap',['a'])
            requests.pending['bootstrap'].result(timeout=2)
            self.assertEqual(requests.poll()[0]['error'],'RPC_ERROR_1')
        finally:requests.finish()

    def test_publication_not_response_governs_admission(self):
        fixture=fixtures.ContinuityTests();fixture.setUp()
        row=fixture.row;row['observer_published_at_ns']=fixture.at+1
        result=checkpoint_context([(1,row)],fixture.mint,fixture.curve,fixture.at,fixtures.empty_index())
        self.assertEqual(result['reasons'],['NO_PRIOR_SYNCHRONIZED_READ_SET'])
        row['observer_published_at_ns']=row['response_available_at_ns']-1
        with self.assertRaises(ValueError):snapshot_available_at(row)


if __name__=='__main__':unittest.main()
