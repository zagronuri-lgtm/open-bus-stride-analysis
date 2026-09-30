"""Corrupt rows cannot starve intact work; quarantine retains forensic bytes."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from bridge_contract import synthetic_fixture
from job_queue import JobQueue, QueueError


class QueueIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'queue.sqlite')
        self.q = JobQueue(self.path)
        self.payload, self.snapshot = synthetic_fixture()

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self, run):
        payload = copy.deepcopy(self.payload)
        payload['run_id'] = run
        return self.q.submit(payload, self.snapshot)

    def corrupt(self, job_id, field, value):
        assert field in ('digest', 'run_id', 'payload_json', 'snapshot_json')
        with sqlite3.connect(self.path) as db:
            db.execute(f'UPDATE jobs SET {field}=? WHERE job_id=?', (value, job_id))
        return self.q.get(job_id)

    def assert_quarantined(self, job_id, before):
        after = JobQueue(self.path).get(job_id)
        self.assertEqual(after, dict(before, state='failed_integrity'))
        audit = self.q.audit(job_id)
        self.assertEqual([r['state'] for r in audit], ['queued', 'failed_integrity'])
        self.assertEqual(audit[1]['previous_state'], 'queued')
        self.assertEqual(set(audit[1]), {'seq', 'job_id', 'previous_state', 'state', 'created_at'})

    def test_corrupt_head_does_not_block_next_valid_job(self):
        bad = self.submit('bad')
        good = self.submit('good')
        before = self.corrupt(bad, 'digest', 'b' * 64)
        self.assertEqual(self.q.claim_next()['job_id'], good)
        self.assert_quarantined(bad, before)
        self.assertIsNone(self.q.claim_next())

    def test_only_corrupt_commits_quarantine_and_audit(self):
        bad = self.submit('bad')
        before = self.corrupt(bad, 'payload_json', '{broken private evidence')
        self.assertIsNone(self.q.claim_next())
        self.assert_quarantined(bad, before)
        self.assertIsNone(JobQueue(self.path).claim_next())
        self.assertEqual(len(self.q.audit(bad)), 2)

    def test_corruption_classes_are_quarantined_and_preserved(self):
        invalid_contract = copy.deepcopy(self.payload)
        invalid_contract['trips'][0]['after_arrival'] = -1
        cases = [('payload_json', json.dumps(invalid_contract)),
                 ('snapshot_json', '{'), ('snapshot_json', 'null'),
                 ('run_id', 'mismatched-run'), ('digest', 'bad-digest'),
                 ('payload_json', '{"x":1,"x":2}'),
                 ('snapshot_json', '{"value":NaN}'),
                 ('payload_json', '[' * 2000 + ']' * 2000)]
        for i, (field, value) in enumerate(cases):
            with self.subTest(field=field, index=i):
                bad = self.submit(f'bad-{i}')
                before = self.corrupt(bad, field, value)
                good = self.submit(f'good-{i}')
                self.assertEqual(self.q.claim_next()['job_id'], good)
                self.assert_quarantined(bad, before)

    def test_terminal_quarantine_has_no_transition_or_recovery(self):
        bad = self.submit('bad')
        self.corrupt(bad, 'snapshot_json', '[]')
        self.assertIsNone(self.q.claim_next())
        for destination in ('queued', 'claimed', 'prepared', 'failed', 'blocked_adapter'):
            with self.assertRaises(QueueError):
                self.q.transition(bad, 'failed_integrity', destination)
        self.assertEqual(self.q.recover_inflight(), 0)
        self.assertEqual(self.q.get(bad)['state'], 'failed_integrity')

    def test_concurrent_claims_quarantine_once_and_claim_valid_once(self):
        bad = self.submit('bad')
        before = self.corrupt(bad, 'digest', 'b' * 64)
        good_ids = {self.submit(f'good-{i}') for i in range(5)}
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: self.q.claim_next(), range(16)))
        claimed_ids = [row['job_id'] for row in claims if row is not None]
        self.assertEqual(set(claimed_ids), good_ids)
        self.assertEqual(len(claimed_ids), len(good_ids))
        self.assert_quarantined(bad, before)

    def test_audit_failure_rolls_back_quarantine(self):
        bad = self.submit('bad')
        before = self.corrupt(bad, 'digest', 'b' * 64)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TRIGGER fail_integrity_audit BEFORE INSERT ON audit
                WHEN NEW.state='failed_integrity'
                BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END""")
        with self.assertRaises(sqlite3.DatabaseError):
            self.q.claim_next()
        self.assertEqual(self.q.get(bad), before)
        self.assertEqual(len(self.q.audit(bad)), 1)


if __name__ == '__main__':
    unittest.main()
