import importlib.util
from pathlib import Path
import unittest
import urllib.error

spec = importlib.util.spec_from_file_location('remote', Path(__file__).resolve().parents[1] / 'scripts/remote_workflow.py')
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)

class RemoteTests(unittest.TestCase):
    def test_claim_is_atomic_and_precedes_execution(self):
        calls=[]
        def api(*args):calls.append(args);return {}
        self.assertTrue(remote.claim_date('2026-10-08', 'abc', api))
        self.assertEqual(calls, [('/git/refs','POST',{'ref':'refs/tags/douyin-daily-2026-10-08','sha':'abc'})])
    def test_duplicate_is_not_send_permission(self):
        def api(*args):raise urllib.error.HTTPError('url',422,'exists',{},None)
        self.assertFalse(remote.claim_date('2026-10-08','abc',api))
    def test_permissions_failure_cannot_authorize_send(self):
        def api(*args):raise urllib.error.HTTPError('url',403,'forbidden',{},None)
        with self.assertRaises(urllib.error.HTTPError):remote.claim_date('2026-10-08','abc',api)
    def test_stale_or_invalid_requests_rejected(self):
        for request in [{'date':'2026-10-07','mode':'send','request_id':'ok'}, {'date':'2026-10-08','mode':'send','request_id':'../x'}, {'date':'2026-10-08','mode':'unknown','request_id':'ok'}]:
            with self.assertRaises(ValueError):remote.validate_request(request,'2026-10-08')

if __name__=='__main__':unittest.main()
