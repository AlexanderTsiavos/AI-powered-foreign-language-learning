"""Account email flows through both HTTP transports using an in-memory mail sink."""

import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from mailer import Mailer
from server import create_server
from test_server import Client
from test_wsgi import WSGIClient
from wsgi import create_application


class AccountEmailFlows:
    transport = "wsgi"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "checkout"
        (self.root / "data").mkdir(parents=True)
        (self.root / "data" / "curriculum.json").write_text('{"lessons":[]}')
        self.db_path = Path(self.temp.name) / "private" / "academy.sqlite3"
        self.messages = []
        self.mailer = Mailer.for_testing(self.messages.append)
        with patch("server.Mailer.from_env", return_value=self.mailer):
            if self.transport == "http":
                self.runtime = create_server("127.0.0.1", 0, self.db_path, self.root)
                self.thread = threading.Thread(target=self.runtime.serve_forever, daemon=True)
                self.thread.start()
            else:
                self.runtime = create_application(self.db_path, self.root, environ={})
        self.client = self.new_client()

    def tearDown(self):
        if self.transport == "http":
            self.runtime.shutdown()
            self.runtime.server_close()
            self.thread.join()
        else:
            self.runtime.close()
        self.temp.cleanup()

    def new_client(self):
        if self.transport == "http":
            return Client(self.runtime.server_port)
        return WSGIClient(self.runtime)

    def token(self, route, recipient=None):
        self.assertTrue(self.mailer.wait_idle(3), "fake mail transport did not drain")
        for message in reversed(self.messages):
            if recipient is not None and message.recipient != recipient:
                continue
            match = re.search(r"/#" + route + r"/([A-Za-z0-9_-]+)", message.body)
            if match:
                return match.group(1)
        self.fail("No requested account link in fake mail sink")

    def login_device(self, email="test@example.com", password="a strong password 42"):
        client = self.new_client()
        client.request("GET", "/api/session")
        self.assertEqual(client.mutate("POST", "/api/login", {"email": email, "password": password})[0], 200)
        return client

    def test_verification_requires_csrf_token_purpose_and_does_not_sign_in(self):
        self.assertEqual(self.client.request("GET", "/api/account/email/status")[1], {"available": True})
        self.assertEqual(self.client.register()[0], 200)
        self.assertFalse(self.client.request("GET", "/api/session")[1]["user"]["emailVerified"])
        self.assertEqual(self.client.request("POST", "/api/account/email/verification", {})[0], 403)
        self.assertEqual(self.client.mutate("POST", "/api/account/email/verification")[0], 200)
        token = self.token("verify-email")
        anonymous = self.new_client()
        anonymous.request("GET", "/api/session")
        self.assertEqual(anonymous.request("POST", "/api/account/email/verify", {"token": token})[0], 403)
        self.assertEqual(anonymous.mutate("POST", "/api/account/password/reset/confirm", {"token": token, "newPassword": "new strong password 42"})[0], 410)
        self.assertEqual(anonymous.mutate("POST", "/api/account/email/verify", {"token": token})[0], 200)
        self.assertIsNone(anonymous.request("GET", "/api/session")[1]["user"])
        self.assertTrue(self.client.request("GET", "/api/session")[1]["user"]["emailVerified"])
        self.assertEqual(anonymous.mutate("POST", "/api/account/email/verify", {"token": token})[0], 410)

    def test_reset_is_generic_revokes_all_sessions_and_rotates_anonymous_cookie(self):
        self.client.register()
        other = self.login_device()
        known = self.client.mutate("POST", "/api/account/password/reset/request", {"email": "test@example.com"})
        unknown = self.client.mutate("POST", "/api/account/password/reset/request", {"email": "absent@example.com"})
        self.assertEqual(known[0], 200)
        self.assertEqual(known[1], unknown[1])
        token = self.token("reset-password")
        old_cookie, old_csrf = self.client.cookie, self.client.csrf
        confirmed = self.client.mutate("POST", "/api/account/password/reset/confirm", {"token": token, "newPassword": "new strong password 42"})
        self.assertEqual(confirmed[0], 200)
        self.assertTrue(confirmed[1]["sessionRevoked"])
        self.assertIsNone(confirmed[1]["user"])
        self.assertNotEqual(self.client.cookie, old_cookie)
        self.assertNotEqual(self.client.csrf, old_csrf)
        self.assertIsNone(other.request("GET", "/api/session")[1]["user"])
        self.assertEqual(self.client.mutate("POST", "/api/account/password/reset/confirm", {"token": token, "newPassword": "another strong password"})[0], 410)
        self.assertEqual(self.client.mutate("POST", "/api/login", {"email": "test@example.com", "password": "a strong password 42"})[0], 401)
        self.assertIsNotNone(self.login_device(password="new strong password 42").request("GET", "/api/session")[1]["user"])

    def test_email_change_confirms_new_address_before_update_and_revokes_devices(self):
        self.client.register()
        other = self.login_device()
        request = {"currentPassword": "a strong password 42", "newEmail": "new@example.com"}
        self.assertEqual(self.client.mutate("POST", "/api/account/email/change", {**request, "currentPassword": "wrong"})[0], 401)
        self.assertEqual(self.client.mutate("POST", "/api/account/email/change", request)[0], 200)
        self.assertEqual(self.client.request("GET", "/api/session")[1]["user"]["email"], "test@example.com")
        token = self.token("confirm-email-change", "new@example.com")
        self.assertEqual(self.client.mutate("POST", "/api/account/email/verify", {"token": token})[0], 410)
        old_cookie = self.client.cookie
        confirmed = self.client.mutate("POST", "/api/account/email/change/confirm", {"token": token})
        self.assertEqual(confirmed[0], 200)
        self.assertTrue(confirmed[1]["sessionRevoked"])
        self.assertIsNone(confirmed[1]["user"])
        self.assertNotEqual(self.client.cookie, old_cookie)
        self.assertIsNone(other.request("GET", "/api/session")[1]["user"])
        self.assertEqual(self.client.mutate("POST", "/api/account/email/change/confirm", {"token": token})[0], 410)
        signed_in = self.login_device(email="new@example.com").request("GET", "/api/session")[1]
        self.assertTrue(signed_in["user"]["emailVerified"])
        self.assertTrue(self.mailer.wait_idle(3))
        notices = [message for message in self.messages if message.recipient == "test@example.com"]
        self.assertEqual(len(notices), 1)
        self.assertNotIn(token, notices[0].body)

    def test_export_contains_pending_metadata_without_link_tokens_or_rate_keys(self):
        self.client.register()
        self.client.mutate("POST", "/api/account/email/verification")
        token = self.token("verify-email")
        response = self.client.request("GET", "/api/export")
        self.assertEqual(response[0], 200)
        pending = response[1]["emailAccount"]["pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(set(pending[0]), {"purpose", "createdAt", "expiresAt"})
        exported = json.dumps(response[1])
        self.assertNotIn(token, exported)
        for field in ("token_hash", "digest", "password_hash", "rate_salt", "key_hash"):
            self.assertNotIn(field, exported)


class WSGIEmailFlowTests(AccountEmailFlows, unittest.TestCase):
    transport = "wsgi"


class DevHTTPEmailFlowTests(AccountEmailFlows, unittest.TestCase):
    transport = "http"


if __name__ == "__main__":
    unittest.main()
