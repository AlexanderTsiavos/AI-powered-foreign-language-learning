"""Account-link persistence and security tests with an in-memory fake mail sink."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest

from app_errors import ServiceError
from email_auth import EmailAuthService
from mailer import Mailer


def hash_password(value):
    return "test-hash:" + hashlib.sha256(value.encode()).hexdigest()


class EmailAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "accounts.sqlite3"
        self.now = 2000000000.0
        self.messages = []
        self.mailer = Mailer.for_testing(self.messages.append)
        self.addCleanup(self.mailer.close)
        self.service = EmailAuthService(self.db, self.mailer, hash_password,
                                        lambda password, stored: hash_password(password) == stored,
                                        clock=lambda: self.now)
        with closing(self.db()) as db, db:
            db.executescript("""CREATE TABLE users(id TEXT PRIMARY KEY,name TEXT,email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,created_at TEXT);
                CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id) ON DELETE CASCADE);""")
            db.executemany("INSERT INTO users VALUES(?,?,?,?,?)", [
                ("alice", "Alice", "alice@example.test", hash_password("initial-password"), "2026-01-01"),
                ("bob", "Bob", "bob@example.test", hash_password("other-password"), "2026-01-01")])
            db.executemany("INSERT INTO sessions VALUES(?,?)", [("session-a", "alice"), ("session-b", "alice"), ("session-c", "bob")])
            self.service.initialize(db)

    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def token(self, route=None):
        self.assertTrue(self.mailer.wait_idle())
        for message in reversed(self.messages):
            match = re.search(r"/#([a-z-]+)/([A-Za-z0-9_-]+)", message.body)
            if match and (not route or match[1] == route):
                return match[2]
        self.fail("No queued token mail found")

    def error(self, status, callback, *args, **kwargs):
        with self.assertRaises(ServiceError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)

    def test_migration_preserves_users_and_is_idempotent(self):
        with closing(self.db()) as db, db:
            self.service.initialize(db)
            self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 2)
            self.assertIsNone(db.execute("SELECT email_verified_at FROM users WHERE id='alice'").fetchone()[0])

    def test_disabled_requests_create_no_tokens_or_fake_mail(self):
        disabled = Mailer.from_env({})
        self.addCleanup(disabled.close)
        service = EmailAuthService(self.db, disabled, hash_password, lambda *args: True)
        self.error(503, service.request_verification, "alice")
        self.error(503, service.request_password_reset, "alice@example.test")
        self.error(503, service.request_email_change, "alice", "initial-password", "new@example.test")
        with closing(self.db()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM email_auth_tokens").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM email_auth_rates").fetchone()[0], 0)

    def test_verification_consumes_once_without_changing_sessions(self):
        self.service.request_verification("alice", "127.0.0.1")
        token = self.token("verify-email")
        self.assertTrue(self.service.verify(token)["ok"])
        self.error(410, self.service.verify, token)
        with closing(self.db()) as db:
            self.assertTrue(db.execute("SELECT email_verified_at FROM users WHERE id='alice'").fetchone()[0])
            self.assertEqual(db.execute("SELECT count(*) FROM sessions WHERE user_id='alice'").fetchone()[0], 2)
        self.service.request_verification("alice", "127.0.0.1")
        self.assertTrue(self.mailer.wait_idle())
        self.assertEqual(len(self.messages), 1)

    def test_raw_token_only_in_memory_mail_not_db_export_or_logs(self):
        self.service.request_verification("alice", "192.0.2.10")
        token = self.token()
        with closing(self.db()) as db:
            dump = "\n".join(db.iterdump())
            exported = json.dumps(self.service.export_data(db, "alice"))
            digest = db.execute("SELECT digest FROM email_auth_tokens").fetchone()[0]
        self.assertNotIn(token, dump)
        self.assertNotIn(token, exported)
        self.assertNotIn(digest, exported)
        self.assertNotIn("key_hash", exported)
        self.assertNotIn("192.0.2.10", dump)
        self.assertEqual(len(digest), 64)
        self.assertIn("expiresAt", exported)

    def test_delivery_starts_after_database_commit(self):
        observed = []

        def inspect_committed(message):
            with closing(self.db()) as db:
                observed.append(db.execute("SELECT count(*) FROM email_auth_tokens").fetchone()[0])

        self.mailer._transport = inspect_committed
        self.service.request_verification("alice", "actor")
        self.assertTrue(self.mailer.wait_idle())
        self.assertEqual(observed, [1])

    def test_generic_known_and_unknown_reset_response_and_dummy_job(self):
        known = self.service.request_password_reset("alice@example.test", "actor")
        unknown = self.service.request_password_reset("nobody@example.test", "actor")
        self.assertEqual(known, unknown)
        self.assertNotIn("sent", known)
        self.assertTrue(self.mailer.wait_idle())
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.mailer._counts["dummy"], 1)
        with closing(self.db()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM email_auth_tokens").fetchone()[0], 1)

    def test_reset_changes_hash_revokes_all_own_sessions_and_never_verifies_email(self):
        self.service.request_password_reset("alice@example.test", "actor")
        token = self.token("reset-password")
        result = self.service.confirm_password_reset(token, "replacement-password")
        self.assertTrue(result["sessionRevoked"])
        with closing(self.db()) as db:
            user = db.execute("SELECT * FROM users WHERE id='alice'").fetchone()
            self.assertEqual(user["password_hash"], hash_password("replacement-password"))
            self.assertIsNone(user["email_verified_at"])
            self.assertEqual(db.execute("SELECT count(*) FROM sessions WHERE user_id='alice'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM sessions WHERE user_id='bob'").fetchone()[0], 1)
        self.error(410, self.service.confirm_password_reset, token, "another-password")

    def test_reset_validation_does_not_consume_token(self):
        self.service.request_password_reset("alice@example.test", "actor")
        token = self.token()
        for value in (None, "", "short", "x" * 257):
            self.error(400, self.service.confirm_password_reset, token, value)
        self.assertTrue(self.service.confirm_password_reset(token, "replacement-password")["ok"])

    def test_wrong_purpose_expiry_and_target_snapshot_are_rejected(self):
        self.service.request_verification("alice", "actor")
        token = self.token()
        self.error(410, self.service.confirm_password_reset, token, "replacement-password")
        self.now += 86400
        self.error(410, self.service.verify, token)
        self.now += 3601
        self.service.request_password_reset("alice@example.test", "actor")
        reset = self.token("reset-password")
        with closing(self.db()) as db, db:
            db.execute("UPDATE users SET email='other@example.test' WHERE id='alice'")
        self.error(410, self.service.confirm_password_reset, reset, "replacement-password")

    def test_manual_password_change_invalidates_previous_links(self):
        self.service.request_password_reset("alice@example.test", "actor")
        token = self.token()
        with closing(self.db()) as db, db:
            db.execute("UPDATE users SET password_hash=? WHERE id='alice'", (hash_password("manually-changed"),))
        self.error(410, self.service.confirm_password_reset, token, "replacement-password")

    def test_concurrent_reset_confirm_only_one_succeeds(self):
        self.service.request_password_reset("alice@example.test", "actor")
        token = self.token()

        def confirm(_):
            try:
                self.service.confirm_password_reset(token, "replacement-password")
                return 200
            except ServiceError as exc:
                return exc.status

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(confirm, range(2))), [200, 410])

    def test_email_change_waits_for_confirmation_then_revokes_sessions_and_notifies_old_email(self):
        self.service.request_email_change("alice", "initial-password", "NEW@example.test", "actor")
        token = self.token("confirm-email-change")
        with closing(self.db()) as db:
            self.assertEqual(db.execute("SELECT email FROM users WHERE id='alice'").fetchone()[0], "alice@example.test")
        result = self.service.confirm_email_change(token)
        self.assertTrue(result["sessionRevoked"])
        self.assertTrue(self.mailer.wait_idle())
        with closing(self.db()) as db:
            user = db.execute("SELECT * FROM users WHERE id='alice'").fetchone()
            self.assertEqual(user["email"], "new@example.test")
            self.assertTrue(user["email_verified_at"])
            self.assertEqual(db.execute("SELECT count(*) FROM sessions WHERE user_id='alice'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT email FROM users WHERE id='bob'").fetchone()[0], "bob@example.test")
        notice = self.messages[-1]
        self.assertEqual(notice.recipient, "alice@example.test")
        self.assertNotIn(token, notice.body)
        self.assertNotIn("/#", notice.body)
        self.error(410, self.service.confirm_email_change, token)

    def test_email_change_checks_password_and_duplicate_without_creating_token(self):
        self.error(401, self.service.request_email_change, "alice", "wrong-password", "new@example.test", "actor")
        self.error(409, self.service.request_email_change, "alice", "initial-password", "bob@example.test", "actor")
        self.error(409, self.service.request_email_change, "alice", "initial-password", "alice@example.test", "actor")
        with closing(self.db()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM email_auth_tokens").fetchone()[0], 0)

    def test_email_change_rechecks_target_unique_at_confirm(self):
        self.service.request_email_change("alice", "initial-password", "new@example.test", "actor")
        token = self.token()
        with closing(self.db()) as db, db:
            db.execute("UPDATE users SET email='new@example.test' WHERE id='bob'")
        self.error(409, self.service.confirm_email_change, token)
        with closing(self.db()) as db:
            self.assertEqual(db.execute("SELECT email FROM users WHERE id='alice'").fetchone()[0], "alice@example.test")
            self.assertEqual(db.execute("SELECT count(*) FROM sessions WHERE user_id='alice'").fetchone()[0], 2)

    def test_durable_email_and_actor_quotas_cover_unknown_addresses(self):
        for _ in range(5):
            self.service.request_password_reset("unknown@example.test", "192.0.2.1")
        another_service = EmailAuthService(self.db, self.mailer, hash_password, lambda *args: False, clock=lambda: self.now)
        self.error(429, another_service.request_password_reset, "unknown@example.test", "192.0.2.2")
        for index in range(5):
            self.service.request_password_reset(f"unknown{index}@example.test", "192.0.2.1")
        self.error(429, self.service.request_password_reset, "different@example.test", "192.0.2.1")
        self.now += 3601
        self.assertTrue(self.service.request_password_reset("unknown@example.test", "192.0.2.1")["ok"])

    def test_full_queue_creates_no_new_token_and_does_not_invalidate_existing(self):
        self.service.request_verification("alice", "actor")
        token = self.token()
        reserved = [self.mailer.reserve() for _ in range(100)]
        self.error(503, self.service.request_verification, "alice", "actor")
        with closing(self.db()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM email_auth_tokens").fetchone()[0], 1)
        for reservation in reserved:
            reservation.cancel()
        self.assertTrue(self.service.verify(token)["ok"])

    def test_failed_delivery_preserves_previous_usable_token(self):
        self.service.request_verification("alice", "actor")
        original = self.token()
        self.mailer._transport = lambda message: (_ for _ in ()).throw(RuntimeError("provider unavailable"))
        self.service.request_verification("alice", "actor")
        self.assertTrue(self.mailer.wait_idle())
        self.assertTrue(self.service.verify(original)["ok"])

    def test_account_delete_cascades_tokens_and_export_does_not_include_other_user(self):
        self.service.request_verification("alice", "actor")
        self.service.request_verification("bob", "actor")
        self.assertTrue(self.mailer.wait_idle())
        with closing(self.db()) as db, db:
            own = self.service.export_data(db, "alice")
            self.assertEqual(len(own["pending"]), 1)
            db.execute("DELETE FROM users WHERE id='alice'")
            self.assertEqual(db.execute("SELECT count(*) FROM email_auth_tokens WHERE user_id='alice'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM email_auth_tokens WHERE user_id='bob'").fetchone()[0], 1)

    def test_valid_confirmation_still_works_when_mail_is_later_disabled(self):
        self.service.request_email_change("alice", "initial-password", "new@example.test", "actor")
        token = self.token()
        self.service.mailer = Mailer.from_env({})
        self.addCleanup(self.service.mailer.close)
        self.assertTrue(self.service.confirm_email_change(token)["ok"])
