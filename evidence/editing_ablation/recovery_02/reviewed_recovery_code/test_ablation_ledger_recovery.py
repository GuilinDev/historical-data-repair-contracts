import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
import resume_ablation_ledger as r

class RecoveryTests(unittest.TestCase):
    def test_retry_io_not_network(self):
        with patch.object(r,'ORIGINAL_ATOMIC',side_effect=[PermissionError(),None]) as write,patch.object(r.time,'sleep'):
            r.resilient_json('a',{});self.assertEqual(write.call_count,2)
    def test_permission_failure_bounded(self):
        with patch.object(r,'ORIGINAL_ATOMIC',side_effect=PermissionError()) as write,patch.object(r.time,'sleep'):
            with self.assertRaises(PermissionError):r.resilient_json('a',{})
            self.assertEqual(write.call_count,9)
    def test_other_error_not_retried(self):
        with patch.object(r,'ORIGINAL_ATOMIC',side_effect=ValueError()) as write:
            with self.assertRaises(ValueError):r.resilient_json('a',{})
            self.assertEqual(write.call_count,1)
    def test_first_request_exact_rekey_only(self):
        payload={'messages':['frozen']};sha=r.hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest()
        client=Mock();adapter=r.ContinuationClient(client,sha);target=pathlib.Path(r.EID)/r.SEED
        adapter.request(payload,target);self.assertEqual(client.request.call_args.args[1].name,r.CONTINUATION)
        adapter.request(payload,target.with_name('explore_16'));self.assertEqual(client.request.call_args.args[1].name,'explore_16')
        with self.assertRaises(AssertionError):r.ContinuationClient(client,sha).request({'messages':['changed']},target)
    def test_gateway_restore_no_source_reset_or_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=pathlib.Path(tmp);(folder/'candidate').mkdir();(folder/'candidate/changed.py').write_text('x=3')
            (folder/'applied_edits.json').write_text('[{"path":"changed.py"}]')
            (folder/'check_01').mkdir();(folder/'check_02').mkdir()
            data=Mock();data.tasks={'task':{'allowed':['changed.py']}}
            gateway=r.restore_gateway(data,{'family':'task','arm':'exact'},folder)
            self.assertEqual(gateway.checks,2);self.assertEqual(len(gateway.edits),1)
            self.assertEqual((gateway.source/'changed.py').read_text(),'x=3')
    def test_real_reconstruction_matches_reserved_request(self):
        out=r.ROOT/'results/editing_ablation/20261101_v1';plan=r.read(out/'plan.json')
        row=next(x for x in plan['episodes'] if x['episode']==r.EID)
        payload=r.reconstruct_request(out/'episodes'/r.EID,row,plan['tools'][row['arm']])
        event=next(x for x in r.read(out/'budget_10usd.json')['requests'] if x['destination']==f'episodes/{r.EID}/{r.SEED}')
        self.assertEqual(r.hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest(),event['request_sha256'])

if __name__=='__main__':unittest.main()
