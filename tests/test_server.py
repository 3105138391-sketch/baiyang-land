import concurrent.futures
import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from api_limits import RequestLimits, LimitExceeded
from server import MyHandler, ReusableTCPServer, validate_chat, BadRequest


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/limits.sqlite'
        self.now = 100000
        self.limits = RequestLimits(self.path, lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def test_daily_cap_survives_restart_and_separates_image_budget(self):
        self.limits.daily['chat'] = 2
        for _ in range(2):
            self.limits.acquire('chat', 'ip'); self.limits.release('chat', 'ip')
        fresh = RequestLimits(self.path, lambda: self.now)
        fresh.daily['chat'] = 2
        with self.assertRaises(LimitExceeded): fresh.acquire('chat', 'other-ip')
        fresh.acquire('image', 'other-ip'); fresh.release('image', 'other-ip')
        self.now += 86400
        fresh.acquire('chat', 'ip'); fresh.release('chat', 'ip')

    def test_rate_limit_and_concurrency_release(self):
        self.limits.minute['chat'] = 2
        self.limits.acquire('chat', 'ip'); self.limits.acquire('chat', 'ip')
        with self.assertRaises(LimitExceeded): self.limits.acquire('image', 'ip')
        self.limits.release('chat', 'ip'); self.limits.release('chat', 'ip')
        with self.assertRaises(LimitExceeded): self.limits.acquire('chat', 'ip')
        self.now += 60
        self.limits.acquire('chat', 'ip')
        self.limits.release('chat', 'ip')
        self.assertEqual(self.limits.active['chat'], 0)

    def test_parallel_requests_cannot_overspend_daily_budget(self):
        self.limits.daily['chat'] = 5
        def attempt(n):
            try:
                self.limits.acquire('chat', str(n))
                self.limits.release('chat', str(n))
                return 1
            except LimitExceeded:
                return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(sum(pool.map(attempt, range(20))), 5)

    def test_older_timestamp_cannot_erase_newer_day_budget(self):
        self.limits.daily['chat'] = 1
        old = self.now
        self.now += 86400
        self.limits.acquire('chat', 'today'); self.limits.release('chat', 'today')
        self.now = old
        self.limits.acquire('chat', 'yesterday'); self.limits.release('chat', 'yesterday')
        self.now += 86400
        with self.assertRaises(LimitExceeded): self.limits.acquire('chat', 'today-again')


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        owner = self
        class FakeHandler(MyHandler):
            limits = RequestLimits(owner.tmp.name + '/limits.sqlite')
            def log_message(self, *args): pass
            def _post_json(self, url, payload, headers, timeout=60):
                owner.calls.append(payload)
                if owner.fail: raise TimeoutError('upstream secret should not leak')
                return 200, {'choices': [{'message': {'content': '剧情'}}]}
        self.handler = FakeHandler
        self.fail = False
        self.env = patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-only'})
        self.env.start()
        self.server = ReusableTCPServer(('127.0.0.1', 0), FakeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.env.stop(); self.tmp.cleanup()

    def request(self, body, path='/api/chat', headers=None, method='POST'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=3)
        conn.request(method, path, body, headers or {'Content-Type': 'application/json'})
        resp = conn.getresponse(); result = resp.status, dict(resp.getheaders()), resp.read()
        conn.close(); return result

    def payload(self, **extras):
        return json.dumps({'messages':[{'role':'user', 'content':'观察校园'}], **extras})

    def test_normal_request_is_bounded_and_health_available(self):
        status, _, body = self.request(self.payload())
        self.assertEqual(status, 200)
        self.assertEqual(self.calls[0]['model'], 'deepseek-chat')
        self.assertEqual(self.calls[0]['max_tokens'], 1500)
        self.assertEqual(self.request(None, '/healthz', method='GET')[0], 200)

    def test_reject_malformed_and_oversized_without_upstream(self):
        for body in ('{', '[]', self.payload(model='other-model'), self.payload(max_tokens=999999), self.payload(stream=True), self.payload(temperature=float('nan')), 'x' * 65537):
            self.assertEqual(self.request(body)[0], 400)
        self.assertEqual(self.calls, [])

    def test_input_content_and_image_validation(self):
        bad = [ {'messages':[]}, {'messages':[{'role':'tool','content':'x'}]},
               {'messages':[{'role':'user','content':'x'*8001}]},
               {'messages':[{'role':'user','content':'x'*8000}]*4} ]
        for payload in bad: self.assertEqual(self.request(json.dumps(payload))[0], 400)
        for payload in ({'scene':[]}, {'scene':''}, {'scene':'x'*801}, {'scene':'校园','model':'any'}):
            self.assertEqual(self.request(json.dumps(payload), '/api/image')[0], 400)
        self.assertEqual(self.calls, [])

    def test_rate_limit_http_retry_after_and_no_extra_upstream(self):
        self.handler.limits.minute['chat'] = 1
        self.assertEqual(self.request(self.payload())[0], 200)
        status, headers, body = self.request(self.payload())
        self.assertEqual(status, 429); self.assertGreater(int(headers['Retry-After']), 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.handler.limits.active['chat'], 0)

    def test_upstream_error_releases_slot_and_redacts_details(self):
        self.fail = True
        status, _, body = self.request(self.payload())
        self.assertEqual(status, 502); self.assertNotIn(b'secret', body)
        self.assertEqual(self.handler.limits.active['chat'], 0)
        self.fail = False
        self.assertEqual(self.request(self.payload())[0], 200)

    def test_proxy_identity_only_trusts_configured_peers(self):
        handler = object.__new__(MyHandler)
        handler.client_address = ('127.0.0.1', 12345)
        handler.headers = {'X-Forwarded-For': '1.1.1.1, 203.0.113.9'}
        with patch.dict(os.environ, {'TRUSTED_PROXY_IPS': ''}):
            self.assertEqual(handler.client_identity(), '127.0.0.1')
        with patch.dict(os.environ, {'TRUSTED_PROXY_IPS': '127.0.0.1'}):
            self.assertEqual(handler.client_identity(), '203.0.113.9')
            handler.headers = {'X-Forwarded-For': 'invalid'}
            self.assertEqual(handler.client_identity(), '127.0.0.1')

    def test_state_and_source_are_not_public(self):
        for path in ('/.state/request_limits.sqlite3', '/api_limits.py', '/.env', '/server.py'):
            self.assertEqual(self.request(None, path, method='GET')[0], 404)

if __name__ == '__main__': unittest.main()
