import copy
import http.client
import json
import os
import sqlite3
import socket
import time
from unittest.mock import patch
from contextlib import closing
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
import local_bridge as lb
CORE = SERVICE.parent / 'implementation'
core, _ = lb.load_core(CORE)

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.token = self.root / 'token'
        self.token.write_text('a' * 48)
        self.token.chmod(0o600)
        self.registry = self.root / 'registry.json'
        self.payload, self.snapshot = core.synthetic_fixture()
        self.write_registry()
        self.bridge = lb.Bridge(self.root / 'state', self.registry, self.token, CORE)
        self.server = lb.make_server(self.bridge)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def write_registry(self):
        self.registry.write_bytes(lb.encode({'schema_version': 1, 'snapshots': [self.snapshot]}))
        self.registry.chmod(0o600)

    def request(self, body=None, headers=None, method='POST', path='/jobs'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        h = {'Authorization': 'Bearer ' + 'a'*48, 'Content-Type': 'application/json'}
        if headers: h.update(headers)
        connection.request(method, path, body=lb.encode(self.payload) if body is None and method == 'POST' else body, headers=h)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_complete_preparation_and_private_artifacts(self):
        status, result = self.request()
        self.assertEqual(status, 202)
        self.assertEqual(result['counts'], {'total': 2, 'selected': 1, 'changed': 1})
        job_id = result['job_id']
        self.assertEqual(self.request()[1]['job_id'], job_id)
        self.assertEqual(self.bridge.worker_once()['state'], 'blocked_adapter')
        self.assertIsNone(self.bridge.worker_once())
        status, result = self.request(method='GET', path='/jobs/' + job_id)
        self.assertEqual(status, 200)
        self.assertEqual(set(result), {'state', 'counts', 'job_id'})
        folder = self.root / 'state' / 'artifacts' / job_id
        handoff = lb.strict_json((folder / 'handoff.json').read_bytes())
        self.assertEqual(len(handoff['payload']['trips']), 2)
        self.assertEqual([a['state'] for a in self.bridge.queue.audit(job_id)], ['queued','claimed','prepared','blocked_adapter'])
        for path in folder.iterdir():
            self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_unauthorized(self):
        self.assertEqual(self.request(headers={'Authorization':'Bearer bad'})[0], 401)

    def test_origin_denied(self):
        for origin in ('null', 'https://evil.test', 'http://localhost:3000'):
            self.assertEqual(self.request(headers={'Origin':origin})[0], 403)

    def test_host_denied(self):
        self.assertEqual(self.request(headers={'Host':'evil.test'})[0], 403)

    def test_json_duplicate_nonfinite_malformed(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{', b'[]'):
            self.assertEqual(self.request(raw)[0], 400)

    def test_client_snapshot_rejected(self):
        self.payload['snapshot'] = self.snapshot
        self.assertEqual(self.request()[0], 400)

    def test_changed_duplicate_run_rejected(self):
        self.assertEqual(self.request()[0], 202)
        self.payload['trips'][0]['after_arrival'] = self.payload['trips'][0]['before_arrival']
        self.assertEqual(self.request()[0], 400)

    def test_duplicate_trip(self):
        self.payload['trips'].append(copy.deepcopy(self.payload['trips'][0]))
        self.assertEqual(self.request()[0], 400)

    def test_stale_at_submission(self):
        self.snapshot['source']['revision'] = 'r2'
        self.write_registry()
        self.assertEqual(self.request()[0], 400)

    def test_stale_at_claim(self):
        result = self.request()[1]
        self.snapshot['source']['revision'] = 'r2'
        self.write_registry()
        self.assertEqual(self.bridge.worker_once()['failure_code'], 'stale_registry')
        self.assertFalse((self.root/'state'/'artifacts'/result['job_id']).exists())

    def test_changed_evidence_at_claim(self):
        self.request()
        self.snapshot['trips'][0]['evidence']['reason'] = 'different evidence'
        self.write_registry()
        self.assertEqual(self.bridge.worker_once()['failure_code'], 'stale_registry')

    def test_limits_and_content_type(self):
        self.assertEqual(self.request(b'', headers={'Content-Length': str(lb.MAX_BODY+1)})[0], 413)
        self.assertEqual(self.request(headers={'Content-Type':'text/plain'})[0], 415)

    def test_no_other_endpoint_or_path(self):
        self.assertEqual(self.request(path='/execute')[0], 404)
        self.assertEqual(self.request(method='GET',path='/jobs/../../token')[0], 404)

    def test_private_token_required(self):
        self.token.chmod(0o644)
        with self.assertRaises(ValueError): lb.Bridge(self.root/'other',self.registry,self.token,CORE)

    def test_symlink_token_rejected(self):
        link = self.root/'link'
        link.symlink_to(self.token)
        with self.assertRaises(OSError): lb.Bridge(self.root/'other',self.registry,link,CORE)

    def test_missing_auth_and_duplicate_headers(self):
        for headers, expected in [([],401), ([('Authorization','Bearer '+'a'*48),('Authorization','Bearer '+'a'*48)],401), ([('Authorization','Bearer '+'a'*48),('Host','evil.test')],403)]:
            connection = http.client.HTTPConnection('127.0.0.1',self.server.server_port)
            connection.putrequest('GET','/jobs/'+'0'*32)
            for k,v in headers: connection.putheader(k,v)
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status,expected)
            response.read()
            connection.close()

    def test_exact_allowed_origin(self):
        server = lb.make_server(self.bridge,allowed_origin='http://localhost:3000')
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            for origin,expected in [('http://localhost:3000',202),('http://localhost:3001',403)]:
                connection = http.client.HTTPConnection('127.0.0.1',server.server_port)
                connection.request('POST','/jobs',lb.encode(self.payload),{'Authorization':'Bearer '+'a'*48,'Content-Type':'application/json','Origin':origin})
                response = connection.getresponse()
                self.assertEqual(response.status,expected)
                response.read()
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_static_demo_has_no_secret_and_checks_host(self):
        for path in ('/', '/demo.js'):
            connection = http.client.HTTPConnection('127.0.0.1',self.server.server_port)
            connection.request('GET',path)
            response = connection.getresponse()
            self.assertEqual(response.status,200)
            self.assertIn("frame-ancestors 'none'",response.getheader('Content-Security-Policy'))
            self.assertNotIn(('a'*48).encode(),response.read())
            connection.close()
        self.assertEqual(self.request(method='GET',path='/',headers={'Host':'evil.test'})[0],403)

    def test_http_quarantined_malformed_payload_returns_safe_status(self):
        job_id = self.request()[1]['job_id']
        malformed = '{broken private evidence'
        with closing(sqlite3.connect(self.bridge.queue.path)) as db, db:
            db.execute('UPDATE jobs SET payload_json=? WHERE job_id=?', (malformed, job_id))
        self.assertIsNone(self.bridge.worker_once())
        status, result = self.request(method='GET', path='/jobs/' + job_id)
        self.assertEqual(status, 200)
        self.assertEqual(result, {'job_id': job_id, 'state': 'failed_integrity',
                                 'counts': None, 'failure_code': 'stored_integrity_failed'})
        self.assertEqual(self.bridge.queue.get(job_id)['payload_json'], malformed)
        self.assertNotIn(malformed, json.dumps(result))

    def test_http_corruption_before_claim_is_safe_and_read_only(self):
        job_id = self.request()[1]['job_id']
        for malformed in ('{private corrupt text', '{}', '[1]', 'null'):
            with self.subTest(malformed=malformed):
                with closing(sqlite3.connect(self.bridge.queue.path)) as db, db:
                    db.execute('UPDATE jobs SET payload_json=? WHERE job_id=?', (malformed, job_id))
                status, result = self.request(method='GET', path='/jobs/' + job_id)
                self.assertEqual(status, 200)
                self.assertEqual(result, {'job_id': job_id, 'state': 'queued',
                                         'counts': None, 'failure_code': 'stored_integrity_failed'})
                self.assertEqual(self.bridge.queue.get(job_id)['payload_json'], malformed)
                self.assertEqual(len(self.bridge.queue.audit(job_id)), 1)

    def test_database_permission_check_never_reads_contents(self):
        db = Path(self.bridge.queue.path)
        original = lb.private_read
        def guarded_read(path, *args, **kwargs):
            self.assertNotEqual(Path(path), db, 'database contents must not be read for permissions')
            return original(path, *args, **kwargs)
        with patch.object(lb, 'private_read', side_effect=guarded_read):
            reopened = lb.Bridge(self.root/'state', self.registry, self.token, CORE)
        self.assertIsNone(reopened.worker_once())
        # Metadata validation of a large sparse file never opens a read stream.
        sparse = self.root/'large.sqlite'
        with sparse.open('wb') as stream:
            stream.truncate(2 * 1024**3)
        sparse.chmod(0o600)
        with patch.object(lb.os, 'read', side_effect=AssertionError('read forbidden')), \
             patch.object(lb.os, 'fdopen', side_effect=AssertionError('stream forbidden')):
            with lb.private_fd(sparse):
                pass
        db.chmod(0o644)
        with self.assertRaises(ValueError):
            lb.Bridge(self.root/'state', self.registry, self.token, CORE)

    def test_slow_headers_bounded_and_normal_request_recovers(self):
        server = lb.make_server(self.bridge, max_workers=2, connection_timeout=0.3)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        sockets = []
        try:
            for _ in range(2):
                sock = socket.create_connection(('127.0.0.1',server.server_port), timeout=2)
                sock.sendall(b'GET / HTTP/1.1\r\nHost: ')
                sockets.append(sock)
            deadline = time.monotonic() + 1
            while server.slots._value and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(server.slots._value, 0)
            excess = socket.create_connection(('127.0.0.1',server.server_port), timeout=2)
            sockets.append(excess)
            self.assertEqual(excess.recv(1), b'')
            for sock in sockets[:2]:
                self.assertEqual(sock.recv(1), b'')
            deadline = time.monotonic() + 1
            while server.slots._value < 2 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(server.slots._value, 2)
            connection = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
            connection.request('GET','/')
            response = connection.getresponse()
            self.assertEqual(response.status,200)
            response.read()
            connection.close()
        finally:
            for sock in sockets: sock.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_trickling_headers_have_absolute_deadline(self):
        server = lb.make_server(self.bridge, connection_timeout=0.25)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        sock = socket.create_connection(('127.0.0.1',server.server_port),timeout=2)
        started = time.monotonic()
        sock.sendall(b'GET / HTTP/1.1\r\nHost: ')
        def trickle():
            for _ in range(20):
                time.sleep(0.05)
                try: sock.sendall(b'x')
                except OSError: return
        sender = threading.Thread(target=trickle,daemon=True)
        sender.start()
        try:
            self.assertEqual(sock.recv(1),b'')
            self.assertLess(time.monotonic()-started,1.5)
        finally:
            sock.close()
            sender.join(timeout=2)
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cli_submit(self):
        payload = self.root/'payload.json'
        payload.write_bytes(lb.encode(self.payload))
        proc = subprocess.run([sys.executable,str(SERVICE/'local_bridge.py'),'submit','--token-file',str(self.token),'--port',str(self.server.server_port),'--payload',str(payload)],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['state'],'queued')
        self.assertNotIn('a'*48,proc.stdout+proc.stderr)

if __name__ == '__main__': unittest.main()
