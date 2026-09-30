import copy
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from bridge_contract import ContractError, synthetic_fixture, validate_job
from job_queue import JobQueue, QueueError

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.p, self.s = synthetic_fixture()

    def test_complete_output_and_24_plus(self):
        result = validate_job(self.p, self.s)
        self.assertEqual(len(result.digest), 64)
        self.assertEqual(self.p['trips'][0]['departure'], 1500)
        self.assertEqual(self.p['trips'][1]['after_arrival'], 640)

    def test_all_source_identity_components(self):
        for key in self.p['source']:
            with self.subTest(key=key):
                p = copy.deepcopy(self.p)
                p['source'][key] = 'b'*64 if key == 'sha256' else 'other'
                with self.assertRaises(ContractError): validate_job(p, self.s)

    def test_context_and_versions(self):
        for key in ('branch', 'day_type', 'season', 'engine_version', 'policy_version', 'service_dates'):
            with self.subTest(key=key):
                p = copy.deepcopy(self.p)
                p[key] = ['2026-10-01'] if key == 'service_dates' else 'other'
                with self.assertRaises(ContractError): validate_job(p, self.s)

    def test_date_order_normalized_before_comparison(self):
        dates = ['2026-09-30', '2026-10-01']
        self.p['service_dates'] = dates.copy()
        self.s['service_dates'] = dates.copy()
        self.s['trips'][0]['evidence']['verified_service_dates'] = dates.copy()
        digest = validate_job(self.p, self.s).digest
        self.p['service_dates'].reverse()
        self.assertEqual(digest, validate_job(self.p, self.s).digest)

    def test_invalid_numeric(self):
        for value in (True, False, 1545.0, float('nan'), float('inf'), -1, 4321, '1545', None):
            with self.subTest(value=value):
                p = copy.deepcopy(self.p)
                p['trips'][0]['after_arrival'] = value
                with self.assertRaises(ContractError): validate_job(p, self.s)

    def test_trip_identity_and_departure(self):
        for key in ('id', 'operator', 'makat', 'line_number', 'direction', 'alternative', 'departure', 'before_arrival'):
            with self.subTest(key=key):
                p = copy.deepcopy(self.p)
                p['trips'][0][key] = 1501 if key in ('departure', 'before_arrival') else 'other'
                with self.assertRaises(ContractError): validate_job(p, self.s)

    def test_added_deleted_duplicate(self):
        for action in ('add', 'delete', 'duplicate'):
            p = copy.deepcopy(self.p)
            if action == 'delete': p['trips'].pop()
            else:
                p['trips'].append(copy.deepcopy(p['trips'][0]))
                if action == 'add': p['trips'][-1]['id'] = 'added'
            with self.assertRaises(ContractError): validate_job(p, self.s)

    def test_selection_and_freeze(self):
        for selection in ([], ['late', 'late'], ['unknown']):
            p = copy.deepcopy(self.p)
            p['selection_trip_ids'] = selection
            with self.assertRaises(ContractError): validate_job(p, self.s)
        self.assertEqual(self.p['trips'][1]['makat'], '10277')
        self.assertEqual(self.p['trips'][1]['line_number'], '277')
        self.p['selection_trip_ids'].append('frozen')
        self.p['trips'][1]['after_arrival'] += 5
        with self.assertRaisesRegex(ContractError, 'frozen'): validate_job(self.p, self.s)

    def test_review_and_blocked_evidence(self):
        for status in ('review', 'blocked'):
            self.s['trips'][0]['evidence']['status'] = status
            with self.assertRaises(ContractError): validate_job(self.p, self.s)
        self.p['trips'][0]['after_arrival'] = 1540
        validate_job(self.p, self.s)  # Unchanged review/blocked trips retained.

    def test_exact_recommendation_and_no_decrease(self):
        for value in (1490, 1539, 1544, 1546):
            self.p['trips'][0]['after_arrival'] = value
            with self.assertRaises(ContractError): validate_job(self.p, self.s)

    def test_target_date_and_day(self):
        for key, value in [('verified_service_dates', ['2026-10-01']), ('verified_day_type', 'Saturday')]:
            s = copy.deepcopy(self.s)
            s['trips'][0]['evidence'][key] = value
            with self.assertRaises(ContractError): validate_job(self.p, s)

    def test_browser_cannot_supply_evidence_or_unknown_keys(self):
        for key in ('approved', 'evidence', 'secret', 'command'):
            p = copy.deepcopy(self.p)
            p[key] = True
            with self.assertRaises(ContractError): validate_job(p, self.s)
        self.p['trips'][0]['evidence'] = self.s['trips'][0]['evidence']
        with self.assertRaises(ContractError): validate_job(self.p, self.s)

    def test_invalid_dates_and_schema(self):
        for value in (['2026-02-30'], ['2026-9-30'], [], ['2026-09-30']*2):
            p = copy.deepcopy(self.p)
            p['service_dates'] = value
            with self.assertRaises(ContractError): validate_job(p, self.s)
        self.p['schema_version'] = True
        with self.assertRaises(ContractError): validate_job(self.p, self.s)

    def test_digest_binds_snapshot_evidence_and_normalizes_order(self):
        first = validate_job(self.p, self.s)
        self.p['trips'].reverse()
        self.s['trips'].reverse()
        self.assertEqual(first.digest, validate_job(self.p, self.s).digest)
        self.s['trips'][0]['evidence']['reason'] = 'different evidence'
        self.assertNotEqual(first.digest, validate_job(self.p, self.s).digest)

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'queue.sqlite')
        self.q = JobQueue(self.path)
        self.p, self.s = synthetic_fixture()

    def tearDown(self): self.tmp.cleanup()

    def test_durable_idempotency_and_run_conflict(self):
        jid = self.q.submit(self.p, self.s)
        self.assertEqual(jid, JobQueue(self.path).submit(self.p, self.s))
        self.s['trips'][0]['evidence']['reason'] = 'new evidence'
        with self.assertRaises(QueueError): self.q.submit(self.p, self.s)
        self.assertEqual(len(self.q.audit(jid)), 1)

    def test_concurrent_submit_and_claim(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _: self.q.submit(self.p, self.s), range(20)))
        self.assertEqual(len(set(ids)), 1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: self.q.claim_next(), range(20)))
        self.assertEqual(sum(c is not None for c in claims), 1)
        self.assertEqual([r['state'] for r in self.q.audit(ids[0])], ['queued', 'claimed'])

    def test_terminal_blocked_adapter_and_forbidden_success(self):
        jid = self.q.submit(self.p, self.s)
        with self.assertRaises(QueueError): self.q.transition(jid, 'queued', 'prepared')
        self.q.claim_next()
        self.q.transition(jid, 'claimed', 'prepared')
        with self.assertRaises(QueueError): self.q.transition(jid, 'claimed', 'failed')
        with self.assertRaises(QueueError): self.q.transition(jid, 'prepared', 'succeeded')
        self.q.transition(jid, 'prepared', 'blocked_adapter')
        self.assertEqual(self.q.get(jid)['state'], 'blocked_adapter')
        with self.assertRaises(QueueError): self.q.transition(jid, 'blocked_adapter', 'queued')

    def test_recovery_requires_reconciliation_not_retry(self):
        for state in ('claimed', 'prepared'):
            p = copy.deepcopy(self.p)
            p['run_id'] = state
            jid = self.q.submit(p, self.s)
            self.q.claim_next()
            if state == 'prepared': self.q.transition(jid, 'claimed', 'prepared')
        restarted = JobQueue(self.path)
        self.assertEqual(restarted.recover_inflight(), 2)
        self.assertEqual(restarted.recover_inflight(), 0)
        self.assertIsNone(restarted.claim_next())
        for row in (self.q.get(jid),):
            self.assertEqual(row['state'], 'reconciliation_required')

    def test_storage_tampering_is_blocked(self):
        jid = self.q.submit(self.p, self.s)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE jobs SET digest=? WHERE job_id=?", ('b'*64, jid))
        self.assertIsNone(self.q.claim_next())
        self.assertEqual(self.q.get(jid)['state'], 'failed_integrity')
        self.assertEqual([r['state'] for r in self.q.audit(jid)], ['queued', 'failed_integrity'])

    def test_failed_is_terminal(self):
        jid = self.q.submit(self.p, self.s)
        self.q.claim_next()
        self.q.transition(jid, 'claimed', 'failed')
        self.assertEqual(self.q.get(jid)['state'], 'failed')
        self.assertEqual(self.q.recover_inflight(), 0)

if __name__ == '__main__': unittest.main()
