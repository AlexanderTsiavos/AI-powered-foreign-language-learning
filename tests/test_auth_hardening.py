"""Bounded anonymous state and one consistent new-account mailbox policy."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch

from app_errors import ServiceError
from email_addresses import is_mailbox, normalize_email
from email_auth import _email
from mailer import Mailer, _mailbox
from server import ANONYMOUS_SESSION_TTL, SESSION_TTL
from session_limits import AnonymousSessionQuota
from test_wsgi import WSGIClient
from wsgi import create_application


class MailboxPolicyTests(unittest.TestCase):
    def test_plain_ascii_addresses_have_one_shared_normalized_policy(self):
        for address in ('learner@example.test', 'learner+study@school.example.test',
                        "o'brien@example.test", 'first_last@example.test'):
            with self.subTest(address=address):
                self.assertTrue(is_mailbox(address))
                self.assertTrue(_mailbox(address))
                self.assertEqual(_email(address), address)
        self.assertEqual(normalize_email('  LEARNER+STUDY@EXAMPLE.TEST  '), 'learner+study@example.test')

    def test_unsupported_mailboxes_are_rejected_for_both_accounts_and_delivery(self):
        for address in ('a<@example.test', 'a\x00@example.test', '.name@example.test',
                        'name..second@example.test', 'name.@example.test',
                        'name@-example.test', 'name@example..test', 'Name <name@example.test>',
                        '"quoted"@example.test', 'όνομα@example.test', 'a' * 65 + '@example.test'):
            with self.subTest(address=address):
                self.assertFalse(is_mailbox(address))
                self.assertFalse(_mailbox(address))
                with self.assertRaises(ServiceError) as error:
                    _email(address)
                self.assertEqual(error.exception.status, 400)


class AnonymousQuotaTests(unittest.TestCase):
    def test_per_actor_and_global_bounds_expire_without_retaining_raw_addresses(self):
        now = [0]
        quota = AnonymousSessionQuota(per_actor=2, total=3, window=10, clock=lambda: now[0])
        quota.consume('192.0.2.1')
        quota.consume('192.0.2.1')
        with self.assertRaises(ServiceError):
            quota.consume('192.0.2.1')
        quota.consume('192.0.2.2')
        with self.assertRaises(ServiceError):
            quota.consume('192.0.2.3')
        self.assertEqual(len(quota._events), 3)
        self.assertEqual(len(quota._counts), 2)
        self.assertTrue(all(isinstance(key, bytes) and len(key) == 32 for key in quota._counts))
        now[0] = 10
        quota.consume('192.0.2.3')
        self.assertEqual(len(quota._events), 1)
        self.assertEqual(len(quota._counts), 1)

    def test_concurrent_quota_checks_cannot_exceed_the_actor_limit(self):
        quota = AnonymousSessionQuota(per_actor=5, total=20)

        def consume(_):
            try:
                quota.consume('192.0.2.1')
                return True
            except ServiceError:
                return False

        with ThreadPoolExecutor(max_workers=12) as executor:
            self.assertEqual(sum(executor.map(consume, range(30))), 5)
        self.assertEqual(len(quota._events), 5)


class AuthenticationHardeningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'accounts.sqlite3'
        self.app = create_application(self.path, environ={})
        self.addCleanup(self.app.close)

    def test_registration_rejects_mailboxes_unsupported_by_reset_or_delivery(self):
        client = WSGIClient(self.app)
        client.request('GET', '/api/session')
        for email in ('a<@example.test', '.name@example.test', 'a\x00@example.test',
                      'name@-example.test', 'name..second@example.test', 'όνομα@example.test'):
            result = client.mutate('POST', '/api/register', {'name': 'Fixture', 'email': email,
                                  'password': 'strong fixture password 42', 'ageConfirmed': True})
            self.assertEqual(result[0], 400)
        with self.app.state.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)

    def test_legacy_identifier_still_allows_sign_in_and_correction(self):
        client = WSGIClient(self.app)
        self.assertEqual(client.register()[0], 200)
        with self.app.state.db() as db:
            db.execute('UPDATE users SET email=?', ('a<@example.test',))
        self.assertEqual(client.mutate('POST', '/api/logout')[0], 200)
        result = client.mutate('POST', '/api/login', {'email': 'a<@example.test',
                               'password': 'a strong password 42'})
        self.assertEqual(result[0], 200)

    def test_fresh_anonymous_creation_is_limited_but_valid_cookies_are_not(self):
        self.app.state.anonymous_quota = AnonymousSessionQuota(per_actor=2, total=3)
        first = WSGIClient(self.app)
        self.assertEqual(first.request('GET', '/api/session')[0], 200)
        self.assertEqual(WSGIClient(self.app).request('GET', '/api/session')[0], 200)
        rejected = WSGIClient(self.app).request('GET', '/api/session')
        self.assertEqual(rejected[0], 429)
        self.assertNotIn('Set-Cookie', rejected[2])
        for _ in range(5):
            self.assertEqual(first.request('GET', '/api/session')[0], 200)
        with self.app.state.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 2)

    def test_anonymous_cookie_and_storage_expire_while_signed_in_ttl_stays_seven_days(self):
        anonymous = WSGIClient(self.app)
        response = anonymous.request('GET', '/api/session')
        self.assertIn(f'Max-Age={ANONYMOUS_SESSION_TTL}', response[2]['Set-Cookie'])
        old_cookie = anonymous.cookie
        signed_in = WSGIClient(self.app)
        response = signed_in.register()
        self.assertIn(f'Max-Age={SESSION_TTL}', response[2]['Set-Cookie'])
        with patch('server.time.time', return_value=time.time() + ANONYMOUS_SESSION_TTL + 1):
            self.assertEqual(anonymous.request('GET', '/api/session')[0], 200)
            self.assertNotEqual(anonymous.cookie, old_cookie)
            self.assertIsNotNone(signed_in.request('GET', '/api/session')[1]['user'])
        with self.app.state.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions WHERE user_id IS NULL').fetchone()[0], 1)

    def test_storage_cap_is_atomic_under_concurrent_creation_and_preserves_authenticated_sessions(self):
        signed_in = WSGIClient(self.app)
        signed_in.register()
        with patch('server.MAX_ANONYMOUS_SESSIONS', 4):
            with ThreadPoolExecutor(max_workers=8) as executor:
                statuses = list(executor.map(lambda _: WSGIClient(self.app).request('GET', '/api/session')[0], range(20)))
            self.assertEqual(statuses, [200] * 20)
            with self.app.state.db() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions WHERE user_id IS NULL').fetchone()[0], 4)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions WHERE user_id IS NOT NULL').fetchone()[0], 1)
            self.assertIsNotNone(signed_in.request('GET', '/api/session')[1]['user'])

    def test_logout_password_reset_and_deletion_do_not_fail_after_creation_quota_is_exhausted(self):
        self.app.state.anonymous_quota = AnonymousSessionQuota(per_actor=1, total=1)
        client = WSGIClient(self.app)
        self.assertEqual(client.register()[0], 200)
        self.assertEqual(WSGIClient(self.app).request('GET', '/api/session')[0], 429)
        self.assertIsNotNone(client.request('GET', '/api/session')[1]['user'])
        self.assertEqual(client.mutate('POST', '/api/logout')[0], 200)
        self.assertEqual(client.request('GET', '/api/session')[0], 200)

        messages = []
        mailer = Mailer.for_testing(messages.append)
        self.app.state.mailer.close()
        self.app.state.mailer = self.app.state.email_auth.mailer = mailer
        result = client.mutate('POST', '/api/account/password/reset/request', {'email': 'test@example.com'})
        self.assertEqual(result[0], 200)
        self.assertTrue(mailer.wait_idle())
        token = re.search(r'/#reset-password/([A-Za-z0-9_-]+)', messages[0].body)[1]
        result = client.mutate('POST', '/api/account/password/reset/confirm',
                               {'token': token, 'newPassword': 'replacement fixture password 42'})
        self.assertEqual(result[0], 200)
        self.assertIsNone(result[1]['user'])
        self.assertEqual(client.mutate('POST', '/api/login', {'email': 'test@example.com',
                         'password': 'replacement fixture password 42'})[0], 200)
        result = client.mutate('DELETE', '/api/account', {'password': 'replacement fixture password 42'})
        self.assertEqual(result[0], 200)
        self.assertIsNone(result[1]['user'])

    def test_upgrade_shortens_legacy_anonymous_expiry_and_applies_cap_without_touching_signed_in(self):
        client = WSGIClient(self.app)
        client.register()
        for _ in range(6):
            WSGIClient(self.app).request('GET', '/api/session')
        with self.app.state.db() as db:
            db.execute('UPDATE sessions SET expires=? WHERE user_id IS NULL', (time.time() + SESSION_TTL,))
            signed_in_expiry = db.execute('SELECT expires FROM sessions WHERE user_id IS NOT NULL').fetchone()[0]
        self.app.close()
        with patch('server.MAX_ANONYMOUS_SESSIONS', 4):
            replacement = create_application(self.path, environ={})
        try:
            with replacement.state.db() as db:
                rows = db.execute('SELECT expires FROM sessions WHERE user_id IS NULL').fetchall()
                self.assertEqual(len(rows), 4)
                self.assertTrue(all(row[0] <= time.time() + ANONYMOUS_SESSION_TTL for row in rows))
                self.assertEqual(db.execute('SELECT expires FROM sessions WHERE user_id IS NOT NULL').fetchone()[0], signed_in_expiry)
        finally:
            replacement.close()


if __name__ == '__main__':
    unittest.main()
