import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from context_capture_quality_v1 import checked_account,run
from run_context_capture_v1 import snapshot_row


class ContextCaptureTests(unittest.TestCase):
    def test_account_hash_and_identity_are_required(self):
        a={'pubkey':'a','owner':'p','slot':1,'write_version':2,'data_base64':base64.b64encode(b'x').decode(),
           'data_sha256':hashlib.sha256(b'x').hexdigest()}
        self.assertEqual(checked_account(a),1)
        with self.assertRaises(ValueError):checked_account(dict(a,data_sha256='bad'))
        with self.assertRaises(ValueError):checked_account(dict(a,owner=''))

    def test_rpc_clock_remains_later_and_no_continuity_claim(self):
        r=snapshot_row(['mint'],10,20,{'context':{'slot':1}},None)
        self.assertEqual(r['response_available_at_ns'],20)
        self.assertIsNone(r['chain_execution_timestamp']);self.assertFalse(r['continuity_certified'])

    def test_quality_retains_missing_mints_and_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);src=p/'src';src.mkdir()
            (src/'manifest.json').write_text(json.dumps({'fee_account_candidate':'fee'}))
            (src/'completion.json').write_text(json.dumps({'exit_code':0}))
            rows=[{'kind':'MINT_DISCOVERED','mint':'a','available_at_ns':1},
                  {'kind':'MINT_DISCOVERED','mint':'b','available_at_ns':2}, {'kind':'GAP'}]
            (src/'capture.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            response={'context':{'slot':1},'value':[{'owner':'token','data':['eA==','base64']} ]}
            (src/'bootstrap.jsonl').write_text(json.dumps(snapshot_row(['a'],2,3,response,None))+'\n')
            r=run(src,p/'out');self.assertEqual(r['mints_without_raw_bootstrap'],['b'])
            self.assertEqual(r['capture_counts']['GAP'],1);self.assertFalse(r['quote_certification'])


if __name__=='__main__':unittest.main()
