import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from claude_review import review, metadata_from_review_package

class ReviewEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.packet = dict(package_kind='review_only', authorization='none', schema_version=1,
                           trips=[dict(id='a', departure=1700, before_arrival=1740, after_arrival=1740)],
                           selected_trip_ids=['a'], candidates=[dict(trip_id='a', reason='untrusted text')])
    def test_late_service_day_preserved_and_text_not_forwarded(self):
        metadata=metadata_from_review_package(self.packet)
        self.assertEqual(metadata['changed_count'],0)
        self.assertEqual(metadata['trip_count'],1)
        self.assertNotIn('untrusted',json.dumps(metadata))
    def test_changed_or_duplicate_source_rejected(self):
        self.packet['trips'][0]['after_arrival']=1741
        with self.assertRaises(ValueError):metadata_from_review_package(self.packet)
        self.packet['trips'][0]['after_arrival']=1740
        self.packet['trips'].append(self.packet['trips'][0].copy())
        with self.assertRaises(ValueError):metadata_from_review_package(self.packet)
    def test_foreign_candidate_rejected(self):
        self.packet['candidates'][0]['trip_id']='foreign'
        with self.assertRaises(ValueError):metadata_from_review_package(self.packet)
    def test_authorized_envelope_rejected(self):
        self.packet['authorization']='execute'
        with self.assertRaises(ValueError):metadata_from_review_package(self.packet)

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.metadata = dict(digest='a'*64, trip_count=2, changed_count=1)
        self.response = dict(structured_output=dict(digest='a'*64, status='advisory_only', summary_he='הרצתי באופטיבוס', next_step_he='סיום'), total_cost_usd=float('nan'), duration_ms=42)
    def call(self):
        with patch('claude_review.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(self.response))) as mocked:
            result = review(self.metadata, '/usr/bin/false')
            command = mocked.call_args.args[0]
            self.assertEqual(command[command.index('--tools')+1], '')
            self.assertEqual(command[command.index('--mcp-config')+1], '{"mcpServers":{}}')
            self.assertEqual(command[command.index('--setting-sources')+1], '')
            self.assertNotIn('shell', mocked.call_args.kwargs)
            return result
    def test_model_prose_never_establishes_execution(self):
        r=self.call();self.assertFalse(r['executed_optibus']);self.assertIn('לא הורצה',r['display_status_he']);self.assertIsNone(r['cost_usd'])
    def test_wrong_identity_rejected(self):
        self.response['structured_output']['digest']='b'*64
        with self.assertRaises(ValueError):self.call()
    def test_nonobject_response_rejected(self):
        self.response=[]
        with self.assertRaises(ValueError):self.call()
    def test_user_prompt_extra_key_rejected_before_call(self):
        self.metadata['prompt']='execute arbitrary command'
        with patch('claude_review.subprocess.run') as mocked:
            with self.assertRaises(ValueError):review(self.metadata,'/usr/bin/false')
            mocked.assert_not_called()
    def test_exit_failure_no_retry(self):
        with patch('claude_review.subprocess.run',return_value=SimpleNamespace(returncode=2,stdout='')) as mocked:
            with self.assertRaises(RuntimeError):review(self.metadata,'/usr/bin/false')
            mocked.assert_called_once()
if __name__=='__main__':unittest.main()
