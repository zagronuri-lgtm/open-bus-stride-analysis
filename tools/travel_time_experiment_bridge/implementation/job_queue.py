"""Durable offline SQLite queue. No adapter, shell runner or live API exists here."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from bridge_contract import validate_job

class QueueError(ValueError):
    pass

TRANSITIONS = {'claimed': {'prepared', 'failed'}, 'prepared': {'blocked_adapter', 'failed'}}

class JobQueue:
    def __init__(self, path: str):
        self.path = str(path)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL,
                    digest TEXT UNIQUE NOT NULL, payload_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, state TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
                CREATE TABLE IF NOT EXISTS audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    previous_state TEXT, state TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
                CREATE INDEX IF NOT EXISTS jobs_state_created ON jobs(state, created_at);
                CREATE INDEX IF NOT EXISTS audit_job_seq ON audit(job_id, seq);
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _audit(db, job_id, old, new):
        db.execute('INSERT INTO audit(job_id,previous_state,state) VALUES(?,?,?)', (job_id, old, new))

    def submit(self, payload: dict, trusted_snapshot: dict) -> str:
        job = validate_job(payload, trusted_snapshot)
        with self._transaction() as db:
            existing = db.execute('SELECT job_id,digest FROM jobs WHERE run_id=?', (job.run_id,)).fetchone()
            if existing:
                if existing['digest'] != job.digest:
                    raise QueueError('run_id already bound to a different digest')
                return existing['job_id']
            job_id = uuid.uuid4().hex
            db.execute('INSERT INTO jobs(job_id,run_id,digest,payload_json,snapshot_json,state) VALUES(?,?,?,?,?,?)', (job_id, job.run_id, job.digest, job.payload_json, job.snapshot_json, 'queued'))
            self._audit(db, job_id, None, 'queued')
            return job_id

    @staticmethod
    def _stored_json(raw):
        # Canonical submissions never contain duplicate keys or nonfinite values.
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise QueueError('invalid stored JSON')
                result[key] = value
            return result

        def reject_constant(_):
            raise QueueError('invalid stored JSON')

        return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)

    def claim_next(self):
        """Claim the oldest intact item, quarantining corrupt rows atomically.

        failed_integrity is terminal: payload/snapshot/digest/run_id are retained
        unchanged for investigation. Only the fixed state is audited, never a
        raw validation exception. Even if every queued row is corrupt, returning
        None commits the quarantine and audit together. BEGIN IMMEDIATE prevents
        concurrent workers from claiming or quarantining the same row twice.
        """
        with self._transaction() as db:
            while True:
                row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
                if row is None:
                    return None
                try:
                    job = validate_job(self._stored_json(row['payload_json']),
                                       self._stored_json(row['snapshot_json']))
                    if job.digest != row['digest'] or job.run_id != row['run_id']:
                        raise QueueError('stored integrity mismatch')
                except (ValueError, TypeError, KeyError, RecursionError):
                    # Includes malformed JSON, ContractError and metadata mismatch.
                    # Storage/SQLite failures are not swallowed as data failures.
                    db.execute("UPDATE jobs SET state='failed_integrity' WHERE job_id=?", (row['job_id'],))
                    self._audit(db, row['job_id'], 'queued', 'failed_integrity')
                    continue
                db.execute("UPDATE jobs SET state='claimed' WHERE job_id=?", (row['job_id'],))
                self._audit(db, row['job_id'], 'queued', 'claimed')
                return dict(row) | {'state': 'claimed'}

    def transition(self, job_id: str, expected_state: str, new_state: str):
        if new_state not in TRANSITIONS.get(expected_state, set()):
            raise QueueError('forbidden transition')
        with self._transaction() as db:
            changed = db.execute('UPDATE jobs SET state=? WHERE job_id=? AND state=?', (new_state, job_id, expected_state)).rowcount
            if changed != 1:
                raise QueueError('missing job or stale state')
            self._audit(db, job_id, expected_state, new_state)

    def recover_inflight(self) -> int:
        """Call only after exclusive process restart; never while a worker is live."""
        with self._transaction() as db:
            rows = db.execute("SELECT job_id,state FROM jobs WHERE state IN ('claimed','prepared')").fetchall()
            for row in rows:
                db.execute("UPDATE jobs SET state='reconciliation_required' WHERE job_id=?", (row['job_id'],))
                self._audit(db, row['job_id'], row['state'], 'reconciliation_required')
            return len(rows)

    def get(self, job_id: str):
        with self._db() as db:
            row = db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            return dict(row) if row else None

    def audit(self, job_id: str):
        with self._db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM audit WHERE job_id=? ORDER BY seq', (job_id,))]
