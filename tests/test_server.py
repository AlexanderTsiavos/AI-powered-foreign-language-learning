"""HTTP integration tests: use a temporary SQLite DB and an ephemeral TCP port."""

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from server import activity_day, create_server, password_hash
from teacher import TeacherError, TeacherService


class ActivityDayTests(unittest.TestCase):
    def test_athens_midnight_uses_local_calendar_day(self):
        before_midnight = datetime(2026, 11, 3, 21, 30, tzinfo=timezone.utc)
        after_midnight = datetime(2026, 11, 3, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(activity_day(before_midnight), date(2026, 11, 3))
        self.assertEqual(activity_day(after_midnight), date(2026, 11, 4))

    def test_bundled_timezone_when_system_database_is_missing(self):
        with patch("server.ZoneInfo", side_effect=ZoneInfoNotFoundError("missing system tzdata")) as zone:
            zone.from_file.side_effect = ZoneInfo.from_file
            self.assertEqual(activity_day(datetime(2026, 11, 3, 22, 30, tzinfo=timezone.utc)), date(2026, 11, 4))
            # Summer time is UTC+3; the fallback must preserve DST, not a fixed offset.
            self.assertEqual(activity_day(datetime(2026, 7, 3, 21, 30, tzinfo=timezone.utc)), date(2026, 7, 4))
            self.assertEqual(zone.from_file.call_count, 2)


class Client:
    def __init__(self, port):
        self.port, self.cookie, self.csrf = port, "", ""

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        request_headers = {"Cookie": self.cookie}
        if body is not None:
            body = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        connection.request(method, path, body, request_headers)
        response = connection.getresponse()
        content = response.read()
        result_headers = dict(response.getheaders())
        if "Set-Cookie" in result_headers:
            self.cookie = result_headers["Set-Cookie"].split(";", 1)[0]
        if response.getheader("Content-Type", "").startswith("application/json"):
            content = json.loads(content)
            if isinstance(content, dict) and "csrfToken" in content:
                self.csrf = content["csrfToken"]
        status = response.status
        connection.close()
        return status, content, result_headers

    def mutate(self, method, path, body=None):
        return self.request(method, path, {**(body or {}), "csrfToken": self.csrf})

    def register(self, email="test@example.com"):
        self.request("GET", "/api/session")
        return self.mutate("POST", "/api/register", {"name": "Δοκιμή", "email": email, "password": "a strong password 42", "ageConfirmed": True})


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "web").mkdir()
        (self.root / "web" / "index.html").write_text("<html lang='el'>Academy</html>")
        (self.root / "data").mkdir()
        self.curriculum = {"lessons": [
            {"id": "first-words", "xp": 30, "cards": [{"word": "car"}, {"word": "book"}, {"word": "water"}], "exercises": [
                {"id": "car", "prompt": "Αυτοκίνητο;", "type": "translate", "answer": "car", "acceptedAnswers": ["car", "a car"], "explanation": "Car σημαίνει αυτοκίνητο.", "hint": "Αρχίζει από c.", "choices": ["car", "cat"], "tokens": ["a", "car"], "passage": "This is a car.", "audioText": "car"},
                {"id": "book", "answer": "book"}, {"id": "water", "answer": "water"}]},
            {"id": "greetings", "xp": 30, "cards": [{"word": "hello"}], "exercises": [{"id": "hello", "answer": "hello"}]}
        ]}
        (self.root / "data" / "curriculum.json").write_text(json.dumps(self.curriculum))
        self.db_path = self.root / "private" / "test.sqlite3"
        self.server = create_server("127.0.0.1", 0, self.db_path, self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = Client(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def complete_first(self, client=None):
        return (client or self.client).mutate("POST", "/api/progress", {"lessonId": "first-words", "answers": ["CAR!", "book", "water"]})

    def checkpoint(self, **changes):
        return {"lessonId": "first-words", "phase": "cards", "index": 0, "answers": [], "correct": False, **changes}

    def review(self, **changes):
        return {"lessonId": "first-words", "exerciseId": "car", "answer": "car", "attemptId": "attempt-0001", **changes}

    def restart_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.server = create_server("127.0.0.1", 0, self.db_path, self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client.port = self.server.server_port

    def another_device(self, email="test@example.com", password="a strong password 42"):
        client = Client(self.server.server_port)
        client.request("GET", "/api/session")
        self.assertEqual(client.mutate("POST", "/api/login", {"email": email, "password": password})[0], 200)
        return client

    def test_health_and_public_content(self):
        status, data, _ = self.client.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(self.client.request("GET", "/api/curriculum")[1], self.curriculum)
        self.assertIn(b"Academy", self.client.request("GET", "/")[1])

    def test_register_cookie_and_hashed_credentials(self):
        status, data, headers = self.client.register()
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["email"], "test@example.com")
        self.assertIsNone(data["profile"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        with self.server.db() as db:
            stored = db.execute("SELECT password_hash FROM users").fetchone()[0]
            self.assertTrue(stored.startswith("scrypt$"))
            self.assertNotIn("a strong password", stored)
            tokens = [r[0] for r in db.execute("SELECT token_hash FROM sessions")]
            self.assertNotIn(self.client.cookie.split("=", 1)[1], tokens)

    def test_adult_gate_and_password_rules(self):
        self.client.request("GET", "/api/session")
        payload = {"name": "Test", "email": "test@example.com", "password": "1234567890"}
        self.assertEqual(self.client.mutate("POST", "/api/register", payload)[0], 400)
        self.assertEqual(self.client.mutate("POST", "/api/register", {**payload, "ageConfirmed": True, "password": "short"})[0], 400)

    def test_csrf_and_origin_rejection(self):
        self.client.request("GET", "/api/session")
        self.assertEqual(self.client.request("POST", "/api/logout", {})[0], 403)
        self.assertEqual(self.client.request("POST", "/api/logout", {"csrfToken": "α"})[0], 403)
        self.assertEqual(self.client.request("POST", "/api/logout", {"csrfToken": self.client.csrf}, {"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.client.request("POST", "/api/logout", {"csrfToken": self.client.csrf}, {"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_login_logout_and_session_rotation(self):
        self.client.register()
        old_cookie, old_csrf = self.client.cookie, self.client.csrf
        self.assertEqual(self.client.mutate("POST", "/api/logout")[0], 200)
        self.assertNotEqual(self.client.cookie, old_cookie)
        self.assertNotEqual(self.client.csrf, old_csrf)
        login = {"email": "test@example.com", "password": "wrong password"}
        self.assertEqual(self.client.mutate("POST", "/api/login", login)[0], 401)
        login["password"] = "a strong password 42"
        self.assertEqual(self.client.mutate("POST", "/api/login", login)[0], 200)
        self.assertIsNotNone(self.client.request("GET", "/api/session")[1]["user"])
        stale = Client(self.server.server_port)
        stale.cookie = old_cookie
        self.assertIsNone(stale.request("GET", "/api/session")[1]["user"])

    def test_profile_validation_and_persistence(self):
        self.client.register()
        profile = {"nativeLanguage": "el", "targetLanguage": "en", "goalMinutes": 15, "reason": "work", "ageGroup": "adult", "level": "A1"}
        self.assertEqual(self.client.mutate("PUT", "/api/profile", profile)[1]["profile"], profile)
        self.assertEqual(self.client.request("GET", "/api/session")[1]["profile"], profile)
        self.assertEqual(self.client.mutate("PUT", "/api/profile", {**profile, "targetLanguage": "fr"})[0], 400)
        self.assertEqual(self.client.mutate("PUT", "/api/profile", {**profile, "goalMinutes": True})[0], 400)

    def test_account_endpoints_require_authentication_and_csrf(self):
        self.client.request("GET", "/api/session")
        operations = [
            ("PUT", "/api/account/profile", {"name": "Νέο όνομα"}),
            ("POST", "/api/account/password", {"currentPassword": "a strong password 42", "newPassword": "different strong password"}),
            ("DELETE", "/api/account/sessions", {"sessionId": "a"*24}),
            ("POST", "/api/account/sessions/revoke-others", {}),
        ]
        self.assertEqual(self.client.request("GET", "/api/account/sessions")[0], 401)
        for method, path, data in operations:
            self.assertEqual(self.client.mutate(method, path, data)[0], 401)
        self.client.register()
        for method, path, data in operations:
            self.assertEqual(self.client.request(method, path, data)[0], 403)

    def test_account_name_edit_is_trimmed_persistent_and_isolated(self):
        self.client.register()
        other = Client(self.server.server_port)
        other.register("other@example.com")
        for invalid in (None, [], " ", "x", "x"*81):
            self.assertEqual(self.client.mutate("PUT", "/api/account/profile", {"name": invalid})[0], 400)
        updated = self.client.mutate("PUT", "/api/account/profile", {"name": "  Αλέξανδρος  "})
        self.assertEqual(updated[0], 200)
        self.assertEqual(updated[1]["user"]["name"], "Αλέξανδρος")
        self.assertEqual(self.client.request("GET", "/api/session")[1]["user"]["name"], "Αλέξανδρος")
        self.assertEqual(other.request("GET", "/api/session")[1]["user"]["name"], "Δοκιμή")
        self.restart_server()
        self.assertEqual(self.client.request("GET", "/api/session")[1]["user"]["name"], "Αλέξανδρος")

    def test_password_change_validates_rotates_and_revokes_every_old_session(self):
        self.client.register()
        self.complete_first()
        device = self.another_device()
        old_cookie, old_csrf = self.client.cookie, self.client.csrf
        credentials = {"currentPassword": "a strong password 42", "newPassword": "new strong password 99"}
        for data in ({**credentials, "currentPassword": []}, {**credentials, "newPassword": "short"},
                     {**credentials, "newPassword": "x"*257}, {**credentials, "newPassword": credentials["currentPassword"]}):
            self.assertEqual(self.client.mutate("POST", "/api/account/password", data)[0], 400)
        self.assertEqual(self.client.mutate("POST", "/api/account/password", {**credentials, "currentPassword": "wrong password"})[0], 401)
        changed = self.client.mutate("POST", "/api/account/password", credentials)
        self.assertEqual(changed[0], 200)
        self.assertIsNotNone(changed[1]["user"])
        self.assertEqual(changed[1]["progress"]["xp"], 30)
        self.assertNotEqual((self.client.cookie, self.client.csrf), (old_cookie, old_csrf))
        self.assertNotEqual(self.client.cookie, old_cookie)
        self.assertNotEqual(self.client.csrf, old_csrf)
        self.assertEqual(self.client.request("PUT", "/api/account/profile", {"name": "Changed", "csrfToken": old_csrf})[0], 403)
        stale = Client(self.server.server_port)
        stale.cookie = old_cookie
        self.assertIsNone(stale.request("GET", "/api/session")[1]["user"])
        self.assertIsNone(device.request("GET", "/api/session")[1]["user"])
        self.assertEqual(len(self.client.request("GET", "/api/account/sessions")[1]["sessions"]), 1)
        self.assertEqual(device.mutate("POST", "/api/login", {"email": "test@example.com", "password": credentials["currentPassword"]})[0], 401)
        self.assertEqual(device.mutate("POST", "/api/login", {"email": "test@example.com", "password": credentials["newPassword"]})[0], 200)
        with self.server.db() as db:
            stored = db.execute("SELECT password_hash FROM users").fetchone()[0]
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn(credentials["newPassword"], stored)

    def test_password_change_wrong_current_is_rate_limited(self):
        self.client.register()
        for _ in range(15):
            self.assertEqual(self.client.mutate("POST", "/api/account/password", {"currentPassword": "incorrect", "newPassword": "new strong password"})[0], 401)
        self.assertEqual(self.client.mutate("POST", "/api/account/password", {"currentPassword": "incorrect", "newPassword": "new strong password"})[0], 429)
        self.assertIsNotNone(self.client.request("GET", "/api/session")[1]["user"])

    def test_session_metadata_is_safe_and_revoke_is_owner_scoped(self):
        self.client.register()
        device = self.another_device()
        other = Client(self.server.server_port)
        other.register("other@example.com")
        sessions = self.client.request("GET", "/api/account/sessions")[1]["sessions"]
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sum(item["current"] for item in sessions), 1)
        for item in sessions:
            self.assertEqual(set(item), {"id", "current", "createdAt", "lastSeenAt", "expiresAt"})
            self.assertEqual(len(item["id"]), 24)
        exported = self.client.request("GET", "/api/export")[1]
        self.assertEqual(exported["sessions"], sessions)
        with self.server.db() as db:
            secret_values = [row[key] for row in db.execute("SELECT token_hash,csrf FROM sessions") for key in ("token_hash", "csrf")]
        for secret in secret_values:
            self.assertNotIn(secret, json.dumps(exported))
            self.assertNotIn(secret, json.dumps(sessions))
        current_id = next(item["id"] for item in sessions if item["current"])
        device_id = next(item["id"] for item in sessions if not item["current"])
        foreign_id = other.request("GET", "/api/account/sessions")[1]["sessions"][0]["id"]
        for session_id, expected in ((current_id, 409), (foreign_id, 404), ("x"*24, 404), ([], 400)):
            self.assertEqual(self.client.mutate("DELETE", "/api/account/sessions", {"sessionId": session_id})[0], expected)
        self.assertEqual(self.client.mutate("DELETE", "/api/account/sessions", {"sessionId": device_id})[1], {"revoked": True})
        self.assertIsNone(device.request("GET", "/api/session")[1]["user"])
        self.assertIsNotNone(other.request("GET", "/api/session")[1]["user"])
        self.assertEqual(self.client.request("GET", "/api/account/sessions")[1]["sessions"][0]["id"], current_id)

    def test_revoke_others_retains_current_session_and_other_accounts(self):
        self.client.register()
        first, second = self.another_device(), self.another_device()
        other = Client(self.server.server_port)
        other.register("other@example.com")
        cookie, csrf = self.client.cookie, self.client.csrf
        response = self.client.mutate("POST", "/api/account/sessions/revoke-others")
        self.assertEqual(response[0], 200)
        self.assertEqual(response[1]["revoked"], 2)
        self.assertEqual((self.client.cookie, self.client.csrf), (cookie, csrf))
        self.assertIsNotNone(response[1]["user"])
        self.assertIsNone(first.request("GET", "/api/session")[1]["user"])
        self.assertIsNone(second.request("GET", "/api/session")[1]["user"])
        self.assertIsNotNone(other.request("GET", "/api/session")[1]["user"])
        self.assertEqual(self.client.mutate("POST", "/api/account/sessions/revoke-others")[1]["revoked"], 0)

    def test_session_activity_is_throttled_and_assets_do_not_touch_database(self):
        self.client.register()
        start = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
        original = (start-timedelta(minutes=2)).isoformat()
        with self.server.db() as db:
            db.execute("UPDATE sessions SET last_seen_at=?", (original,))
        with patch("server.utc_now", return_value=start):
            self.client.request("GET", "/")
            with self.server.db() as db:
                self.assertEqual(db.execute("SELECT last_seen_at FROM sessions").fetchone()[0], original)
            self.client.request("GET", "/api/session")
        with patch("server.utc_now", return_value=start+timedelta(seconds=30)):
            self.client.request("GET", "/api/session")
        with self.server.db() as db:
            self.assertEqual(db.execute("SELECT last_seen_at FROM sessions").fetchone()[0], start.isoformat())
        # A write API that opens its own transaction must still work when the touch is due.
        with patch("server.utc_now", return_value=start+timedelta(minutes=2)):
            self.assertEqual(self.client.mutate("PUT", "/api/lesson-state", self.checkpoint())[0], 200)
        with self.server.db() as db:
            self.assertEqual(db.execute("SELECT last_seen_at FROM sessions").fetchone()[0], (start+timedelta(minutes=2)).isoformat())

    def test_legacy_session_schema_migration_preserves_cookies_and_is_idempotent(self):
        self.client.register()
        device = self.another_device()
        with self.server.db() as db:
            db.executescript("""
                ALTER TABLE sessions RENAME TO sessions_with_metadata;
                CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id) ON DELETE CASCADE,csrf TEXT NOT NULL,expires REAL NOT NULL);
                INSERT INTO sessions SELECT token_hash,user_id,csrf,expires FROM sessions_with_metadata;
                DROP TABLE sessions_with_metadata;
            """)
        self.restart_server()
        device.port = self.server.server_port
        self.assertIsNotNone(self.client.request("GET", "/api/session")[1]["user"])
        self.assertIsNotNone(device.request("GET", "/api/session")[1]["user"])
        metadata = self.client.request("GET", "/api/account/sessions")[1]["sessions"]
        self.assertEqual(len(metadata), 2)
        self.assertEqual(len({item["id"] for item in metadata}), 2)
        self.server.initialize_db()
        self.assertEqual(self.client.request("GET", "/api/account/sessions")[1]["sessions"], metadata)

    def test_teacher_hooks_require_auth_csrf_and_preserve_service_errors(self):
        self.server.teacher = Mock()
        self.server.teacher.status.return_value = {"available": False, "provider": "disabled", "mode": "disabled", "message": "Δεν έχει ενεργοποιηθεί."}
        self.client.request("GET", "/api/session")
        self.assertEqual(self.client.request("GET", "/api/teacher/status")[0], 401)
        self.assertEqual(self.client.mutate("POST", "/api/teacher", {"message": "Hello"})[0], 401)
        self.server.teacher.reply.assert_not_called()
        self.server.teacher.status.assert_not_called()
        registered = self.client.register()[1]
        self.assertEqual(self.client.request("GET", "/api/teacher/status")[1]["available"], False)
        self.assertEqual(self.client.request("POST", "/api/teacher", {"message": "Hello"})[0], 403)
        self.server.teacher.reply.assert_not_called()
        self.server.teacher.reply.return_value = {"message": "Γεια σου!", "boardAction": None, "provider": "ollama", "mode": "local-ai"}
        result = self.client.mutate("POST", "/api/teacher", {"message": "Hello", "consent": True})
        self.assertEqual(result[0], 200)
        args = self.server.teacher.reply.call_args.args
        self.assertEqual(args[0], registered["user"]["id"])
        self.assertEqual(args[1]["message"], "Hello")
        self.assertEqual(args[2], self.curriculum)
        self.server.teacher.reply.side_effect = TeacherError(503, "Το τοπικό μοντέλο δεν είναι διαθέσιμο.")
        unavailable = self.client.mutate("POST", "/api/teacher", {"message": "Hello", "consent": True})
        self.assertEqual(unavailable, (503, {"error": "Το τοπικό μοντέλο δεν είναι διαθέσιμο."}, unavailable[2]))

    def test_disabled_teacher_http_has_no_invented_ai_response(self):
        self.client.register()
        self.server.teacher = TeacherService()
        self.assertFalse(self.client.request("GET", "/api/teacher/status")[1]["available"])
        status, data, _ = self.client.mutate("POST", "/api/teacher", {"message": "Hello", "consent": True})
        self.assertEqual(status, 503)
        self.assertIn("error", data)
        self.assertNotIn("message", data)

    def test_progress_requires_auth_and_correct_answers(self):
        self.client.request("GET", "/api/session")
        self.assertEqual(self.complete_first()[0], 401)
        self.client.register()
        body = {"lessonId": "first-words", "answers": ["wrong", "book", "water"]}
        self.assertEqual(self.client.mutate("POST", "/api/progress", body)[0], 422)
        self.assertEqual(self.client.mutate("POST", "/api/progress", {"lessonId": "first-words", "answers": []})[0], 400)
        self.assertEqual(self.client.request("GET", "/api/session")[1]["progress"]["xp"], 0)

    def test_sequential_unlock_and_idempotent_rewards(self):
        self.client.register()
        second = {"lessonId": "greetings", "answers": ["hello"]}
        self.assertEqual(self.client.mutate("POST", "/api/progress", second)[0], 409)
        status, data, _ = self.complete_first()
        self.assertEqual(status, 200)
        self.assertEqual(data["progress"]["xp"], 30)
        self.assertEqual(data["progress"]["streak"], 1)
        self.assertEqual(self.complete_first()[1]["progress"]["xp"], 30)
        self.assertEqual(self.client.mutate("POST", "/api/progress", second)[1]["progress"]["xp"], 60)

    def test_accepted_alternative_and_normalization(self):
        self.client.register()
        result = self.client.mutate("POST", "/api/progress", {"lessonId": "first-words", "answers": ["  A CAR. ", "BOOK", "water!"]})
        self.assertEqual(result[0], 200)
        result = self.client.mutate("POST", "/api/progress", {"lessonId": "first-words", "answers": ["car,", "book:", "water;"]})
        self.assertEqual(result[0], 200)

    def test_account_isolation_and_second_device(self):
        self.client.register()
        self.complete_first()
        other = Client(self.server.server_port)
        other.register("other@example.com")
        self.assertEqual(other.request("GET", "/api/session")[1]["progress"]["xp"], 0)
        device = Client(self.server.server_port)
        device.request("GET", "/api/session")
        result = device.mutate("POST", "/api/login", {"email": "test@example.com", "password": "a strong password 42"})
        self.assertEqual(result[1]["progress"]["xp"], 30)

    def test_export_and_account_deletion(self):
        self.client.register()
        self.complete_first()
        self.client.mutate("PUT", "/api/lesson-state", self.checkpoint(lessonId="greetings"))
        self.client.mutate("POST", "/api/review", self.review())
        export = self.client.request("GET", "/api/export")
        self.assertEqual(export[0], 200)
        self.assertEqual(export[1]["progress"]["xp"], 30)
        self.assertNotIn("csrfToken", export[1])
        self.assertNotIn("password_hash", json.dumps(export[1]))
        self.assertEqual(len(export[1]["lessonCheckpoints"]), 1)
        self.assertEqual(export[1]["lessonCheckpoints"][0]["lessonId"], "greetings")
        self.assertEqual(len(export[1]["reviews"]), 3)
        self.assertEqual(len(export[1]["reviewAttempts"]), 1)
        self.assertEqual(self.client.mutate("DELETE", "/api/account", {"password": "wrong"})[0], 401)
        self.assertEqual(self.client.mutate("DELETE", "/api/account", {"password": "a strong password 42"})[1]["user"], None)
        with self.server.db() as db:
            for table in ("users", "completions", "profiles", "activities", "lesson_checkpoints", "review_cards", "review_attempts"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual(self.client.request("GET", "/api/export")[0], 401)

    def test_checkpoint_auth_csrf_unlock_and_read_does_not_write(self):
        self.client.request("GET", "/api/session")
        self.assertEqual(self.client.request("GET", "/api/lesson-state?lessonId=first-words")[0], 401)
        self.assertEqual(self.client.mutate("PUT", "/api/lesson-state", self.checkpoint())[0], 401)
        self.client.register()
        self.assertEqual(self.client.request("PUT", "/api/lesson-state", self.checkpoint())[0], 403)
        for query in ("", "?lessonId=", "?lessonId=first-words&lessonId=greetings"):
            self.assertEqual(self.client.request("GET", "/api/lesson-state" + query)[0], 400)
        self.assertEqual(self.client.request("GET", "/api/lesson-state?lessonId=missing")[0], 404)
        self.assertEqual(self.client.request("GET", "/api/lesson-state?lessonId=greetings")[0], 409)
        self.assertEqual(self.client.mutate("PUT", "/api/lesson-state", self.checkpoint(lessonId="greetings"))[0], 409)
        self.assertEqual(self.client.request("GET", "/api/lesson-state?lessonId=first-words")[1], {"checkpoint": None})
        with self.server.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM lesson_checkpoints").fetchone()[0], 0)

    def test_checkpoint_shape_and_answers_are_server_validated(self):
        self.client.register()
        invalid = [
            self.checkpoint(phase="done"), self.checkpoint(index=True), self.checkpoint(index=-1),
            self.checkpoint(index=3), self.checkpoint(answers=["car"]), self.checkpoint(correct=True),
            self.checkpoint(correct=0), self.checkpoint(phase="exercises", index=3),
            self.checkpoint(phase="exercises", index=1),
            self.checkpoint(phase="exercises", index=0, correct=True),
            self.checkpoint(phase="exercises", index=1, answers=["wrong"]),
            self.checkpoint(phase="exercises", index=1, answers=[42]),
            self.checkpoint(phase="exercises", index=1, answers=["a"*1001]),
        ]
        for checkpoint in invalid:
            with self.subTest(checkpoint=checkpoint):
                self.assertIn(self.client.mutate("PUT", "/api/lesson-state", checkpoint)[0], (400, 422))
        self.assertEqual(self.client.request("GET", "/api/lesson-state?lessonId=first-words")[1], {"checkpoint": None})
        self.assertEqual(self.client.request("GET", "/api/session")[1]["progress"]["xp"], 0)
        self.assertEqual(self.client.request("GET", "/api/review")[1]["items"], [])

    def test_checkpoint_restores_other_device_isolates_and_clears_on_completion(self):
        self.client.register()
        checkpoint = self.checkpoint(phase="exercises", index=1, answers=["A CAR."], correct=False)
        self.assertEqual(self.client.mutate("PUT", "/api/lesson-state", checkpoint)[1], {"checkpoint": checkpoint})
        other = Client(self.server.server_port)
        other.register("other@example.com")
        self.assertEqual(other.request("GET", "/api/lesson-state?lessonId=first-words")[1], {"checkpoint": None})
        device = Client(self.server.server_port)
        device.request("GET", "/api/session")
        device.mutate("POST", "/api/login", {"email": "test@example.com", "password": "a strong password 42"})
        self.assertEqual(device.request("GET", "/api/lesson-state?lessonId=first-words")[1], {"checkpoint": checkpoint})
        confirmed = self.checkpoint(phase="exercises", index=1, answers=["car", "book"], correct=True)
        self.assertEqual(device.mutate("PUT", "/api/lesson-state", confirmed)[1], {"checkpoint": confirmed})
        self.complete_first(device)
        self.assertEqual(self.client.request("GET", "/api/lesson-state?lessonId=first-words")[1], {"checkpoint": None})
        # A deliberate partial repeat may be saved, but completion clears it without more XP.
        self.client.mutate("PUT", "/api/lesson-state", self.checkpoint(index=1))
        self.assertEqual(self.complete_first()[1]["progress"]["xp"], 30)
        self.assertEqual(self.client.request("GET", "/api/lesson-state?lessonId=first-words")[1], {"checkpoint": None})

    def test_migration_backfills_old_completed_lessons_idempotently(self):
        self.client.register()
        self.complete_first()
        with self.server.db() as db:
            completion = db.execute("SELECT completed_at FROM completions").fetchone()[0]
            db.executescript("DROP TABLE review_attempts; DROP TABLE review_cards; DROP TABLE lesson_checkpoints;")
        self.restart_server()
        review = self.client.request("GET", "/api/review")[1]
        self.assertEqual(len(review["items"]), 3)
        expected_due = (datetime.fromisoformat(completion) + timedelta(days=1)).isoformat()
        self.assertTrue(all(item["dueAt"] == expected_due for item in review["items"]))
        self.assertEqual(self.client.request("GET", "/api/session")[1]["progress"]["xp"], 30)
        self.server.initialize_db()
        with self.server.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM review_cards").fetchone()[0], 3)

    def test_checkpoint_and_review_schedule_survive_server_restart(self):
        self.client.register()
        self.complete_first()
        checkpoint = self.checkpoint(lessonId="greetings")
        self.client.mutate("PUT", "/api/lesson-state", checkpoint)
        result = self.client.mutate("POST", "/api/review", self.review(answer="wrong", attemptId="restart-0001"))[1]
        self.restart_server()
        self.assertEqual(self.client.request("GET", "/api/lesson-state?lessonId=greetings")[1], {"checkpoint": checkpoint})
        car = next(item for item in self.client.request("GET", "/api/review")[1]["items"] if item["exerciseId"] == "car")
        self.assertEqual(car["dueAt"], result["nextDueAt"])
        replay = self.client.mutate("POST", "/api/review", self.review(answer="wrong", attemptId="restart-0001"))[1]
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["nextDueAt"], result["nextDueAt"])

    def test_review_is_authenticated_learned_only_and_isolated(self):
        self.client.request("GET", "/api/session")
        self.assertEqual(self.client.request("GET", "/api/review")[0], 401)
        self.assertEqual(self.client.mutate("POST", "/api/review", self.review())[0], 401)
        self.client.register()
        self.assertEqual(self.client.request("GET", "/api/review")[1], {"items": [], "dueCount": 0})
        self.assertEqual(self.client.mutate("POST", "/api/review", self.review())[0], 409)
        self.complete_first()
        payload = self.client.request("GET", "/api/review")[1]
        self.assertEqual(len(payload["items"]), 3)
        self.assertEqual(payload["dueCount"], 0)
        car = next(item for item in payload["items"] if item["exerciseId"] == "car")
        self.assertEqual(car["acceptedAnswers"], ["car", "a car"])
        self.assertEqual({key: car[key] for key in ("choices", "tokens", "passage", "audioText")},
                         {"choices": ["car", "cat"], "tokens": ["a", "car"], "passage": "This is a car.", "audioText": "car"})
        self.assertEqual(car["stage"], 0)
        other = Client(self.server.server_port)
        other.register("other@example.com")
        self.assertEqual(other.request("GET", "/api/review")[1]["items"], [])
        self.assertEqual(other.mutate("POST", "/api/review", self.review())[0], 409)
        self.assertEqual(self.client.mutate("POST", "/api/review", self.review(lessonId="greetings", exerciseId="hello"))[0], 409)
        self.assertEqual(self.client.mutate("POST", "/api/review", self.review(exerciseId="missing"))[0], 404)

    def test_review_scheduling_due_ladder_and_early_correct_practice(self):
        self.client.register()
        start = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        with patch("server.utc_now", return_value=start):
            self.complete_first()
        initial_due = (start + timedelta(days=1)).isoformat()
        with patch("server.utc_now", return_value=start + timedelta(hours=6)):
            early = self.client.mutate("POST", "/api/review", self.review())[1]
        self.assertTrue(early["correct"])
        self.assertTrue(early["earlyPractice"])
        self.assertFalse(early["scheduleAdvanced"])
        self.assertEqual((early["stage"], early["nextDueAt"]), (0, initial_due))
        current = start + timedelta(days=1)
        for index, days in enumerate((1, 3, 7, 14, 30, 30), 1):
            with self.subTest(index=index), patch("server.utc_now", return_value=current):
                before = self.client.request("GET", "/api/review")[1]
                self.assertGreaterEqual(before["dueCount"], 1)
                result = self.client.mutate("POST", "/api/review", self.review(attemptId=f"stage-{index:04}"))[1]
            self.assertTrue(result["scheduleAdvanced"])
            self.assertFalse(result["earlyPractice"])
            self.assertEqual(result["stage"], min(index, 5))
            self.assertEqual(result["nextDueAt"], (current + timedelta(days=days)).isoformat())
            current += timedelta(days=days)
        self.assertEqual(self.client.request("GET", "/api/session")[1]["progress"]["xp"], 30)

    def test_wrong_review_resets_even_early_and_retry_keeps_due_without_raw_answers(self):
        self.client.register()
        self.complete_first()
        current = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        with self.server.db() as db:
            db.execute("UPDATE review_cards SET stage=3,due_at=? WHERE exercise_id='car'", ((current + timedelta(days=7)).isoformat(),))
        wrong = self.review(answer="RAW_WRONG_ANSWER_NOT_RETAINED", attemptId="wrong-0001")
        with patch("server.utc_now", return_value=current):
            result = self.client.mutate("POST", "/api/review", wrong)[1]
        self.assertFalse(result["correct"])
        self.assertTrue(result["earlyPractice"])
        self.assertEqual(result["stage"], 0)
        self.assertEqual(result["nextDueAt"], (current + timedelta(minutes=10)).isoformat())
        with patch("server.utc_now", return_value=current + timedelta(hours=1)):
            duplicate = self.client.mutate("POST", "/api/review", wrong)[1]
        self.assertEqual(duplicate, {**result, "duplicate": True})
        with self.server.db() as db:
            card = db.execute("SELECT stage,due_at FROM review_cards WHERE exercise_id='car'").fetchone()
            attempt = dict(db.execute("SELECT * FROM review_attempts").fetchone())
        self.assertEqual(card["due_at"], result["nextDueAt"])
        self.assertNotIn(wrong["answer"], json.dumps(attempt))
        self.assertEqual(self.client.mutate("POST", "/api/review", {**wrong, "answer": "car"})[0], 409)

    def test_review_concurrent_retry_advances_once_and_repeat_completion_preserves_schedule(self):
        self.client.register()
        self.complete_first()
        current = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
        with self.server.db() as db:
            db.execute("UPDATE review_cards SET due_at=?", ((current-timedelta(minutes=1)).isoformat(),))

        def send_same_attempt(_):
            client = Client(self.server.server_port)
            client.cookie, client.csrf = self.client.cookie, self.client.csrf
            return client.mutate("POST", "/api/review", self.review(attemptId="concurrent-0001"))

        with patch("server.utc_now", return_value=current), ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(send_same_attempt, range(2)))
        self.assertTrue(all(response[0] == 200 for response in responses))
        self.assertEqual(sorted(response[1]["duplicate"] for response in responses), [False, True])
        self.assertTrue(all(response[1]["stage"] == 1 for response in responses))
        next_due = responses[0][1]["nextDueAt"]
        self.complete_first()
        item = next(item for item in self.client.request("GET", "/api/review")[1]["items"] if item["exerciseId"] == "car")
        self.assertEqual((item["stage"], item["dueAt"]), (1, next_due))

    def test_review_malformed_input_csrf_and_bounded_retry_history(self):
        self.client.register()
        self.complete_first()
        self.assertEqual(self.client.request("POST", "/api/review", self.review())[0], 403)
        for invalid in (self.review(answer=42), self.review(answer="a"*1001), self.review(attemptId="x"), self.review(exerciseId=[])):
            self.assertEqual(self.client.mutate("POST", "/api/review", invalid)[0], 400)
        with patch("server.REVIEW_ATTEMPT_LIMIT", 2):
            for index in range(3):
                self.assertEqual(self.client.mutate("POST", "/api/review", self.review(attemptId=f"bounded-{index:04}"))[0], 200)
        with self.server.db() as db:
            retained = {row[0] for row in db.execute("SELECT attempt_id FROM review_attempts")}
        self.assertEqual(retained, {"bounded-0001", "bounded-0002"})
        self.assertTrue(self.client.mutate("POST", "/api/review", self.review(attemptId="bounded-0002"))[1]["duplicate"])

    def test_static_traversal_and_security_headers(self):
        for path in ("/../server.py", "/%2e%2e/server.py", "/.git/config", "/private/test.sqlite3", "/server.py", "/assets/%2f../../server.py"):
            self.assertEqual(self.client.request("GET", path)[0], 404, path)
        headers = self.client.request("GET", "/")[2]
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_bad_json_and_limits(self):
        self.client.request("GET", "/api/session")
        self.assertEqual(self.client.request("POST", "/api/login", {"value": "a" * 33000})[0], 413)
        self.assertEqual(self.client.request("POST", "/api/login", {"csrfToken": self.client.csrf, "email": [], "password": []})[0], 400)

    def test_rate_limit(self):
        self.client.request("GET", "/api/session")
        for _ in range(15):
            self.assertEqual(self.client.mutate("POST", "/api/login", {"email": "missing@example.com", "password": "wrong password"})[0], 401)
        self.assertEqual(self.client.mutate("POST", "/api/login", {"email": "missing@example.com", "password": "wrong password"})[0], 429)


class FeatureIntegrationTests(unittest.TestCase):
    """Exercise new services through authenticated HTTP using the real course."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        self.repo = Path(__file__).resolve().parents[1]
        self.server = create_server("127.0.0.1", 0, self.db_path, self.repo)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = Client(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def grant_admin(self):
        user = self.client.register()[1]["user"]
        with self.server.db() as db:
            db.execute("UPDATE users SET role='admin' WHERE id=?", (user["id"],))
        return user

    def assessment_answers(self, attempt):
        with self.server.db() as db:
            questions = json.loads(db.execute("SELECT questions_json FROM assessment_attempts WHERE id=?", (attempt["attemptId"],)).fetchone()[0])
        return [{"questionId": question["id"], "answer": question["exercise"]["answer"]} for question in questions]

    def test_live_admin_role_cannot_be_supplied_by_browser_and_all_features_require_login(self):
        for path in ("/api/admin/overview", "/api/admin/curriculum", "/api/assessments", "/api/notifications", "/api/support/tickets"):
            self.assertEqual(self.client.request("GET", path)[0], 401, path)
        self.client.request("GET", "/api/session")
        registered = self.client.mutate("POST", "/api/register", {"name": "Μαθητής", "email": "test@example.com", "password": "a strong password 42", "ageConfirmed": True, "role": "admin", "isAdmin": True})
        self.assertEqual(registered[0], 200)
        self.assertFalse(registered[1]["user"]["isAdmin"])
        self.assertEqual(self.client.request("GET", "/api/admin/overview")[0], 403)
        self.assertEqual(self.client.mutate("POST", "/api/admin/lessons", {"role": "admin"})[0], 403)
        with self.server.db() as db:
            db.execute("UPDATE users SET role='admin' WHERE id=?", (registered[1]["user"]["id"],))
        self.assertTrue(self.client.request("GET", "/api/session")[1]["user"]["isAdmin"])
        overview = self.client.request("GET", "/api/admin/overview")
        self.assertEqual(overview[0], 200)
        self.assertEqual(overview[1]["counts"]["publishedLessons"], 9)
        self.assertNotIn("test@example.com", json.dumps(overview[1]))
        with self.server.db() as db:
            db.execute("UPDATE users SET role='student'")
        self.assertFalse(self.client.request("GET", "/api/session")[1]["user"]["isAdmin"])
        self.assertEqual(self.client.request("GET", "/api/admin/curriculum")[0], 403)

    def test_admin_draft_publish_http_csrf_stale_versions_and_permanent_checkpoint_lock(self):
        self.grant_admin()
        base = self.client.request("GET", "/api/curriculum")[1]
        lesson = json.loads(json.dumps(base["lessons"][0]))
        lesson["title"] = "Αποθηκευμένος τίτλος"
        path = "/api/admin/lessons/" + lesson["id"]
        self.assertEqual(self.client.request("PUT", path, {"lesson": lesson, "baseVersion": 0})[0], 403)
        saved = self.client.mutate("PUT", path, {"lesson": lesson, "baseVersion": 0})
        self.assertEqual(saved[0], 200, saved[1])
        self.assertEqual(saved[1]["lesson"]["version"], 1)
        self.assertEqual(self.client.request("GET", "/api/curriculum")[1], base)
        self.assertEqual(self.client.request("GET", "/data/curriculum.json")[1], base)
        self.assertEqual(self.client.mutate("POST", path + "/publish", {"baseVersion": 0})[0], 409)
        self.assertEqual(self.client.mutate("POST", path + "/publish", {"baseVersion": 1})[0], 200)
        public = self.client.request("GET", "/api/curriculum")[1]
        self.assertEqual(public["lessons"][0]["title"], lesson["title"])
        self.assertEqual(self.client.request("GET", "/data/curriculum.json")[1], public)
        checkpoint = {"lessonId": lesson["id"], "phase": "cards", "index": 0, "answers": [], "correct": False}
        self.assertEqual(self.client.mutate("PUT", "/api/lesson-state", checkpoint)[0], 200)
        lesson["title"] = "Αλλαγή μετά τη χρήση"
        self.assertEqual(self.client.mutate("PUT", path, {"lesson": lesson, "baseVersion": 2})[0], 409)
        copied = json.loads(json.dumps(lesson))
        copied["id"] = "extra-lesson"
        self.assertEqual(self.client.mutate("POST", "/api/admin/lessons", {"lesson": copied, "baseVersion": 0})[0], 200)
        self.assertEqual(len(self.client.request("GET", "/api/curriculum")[1]["lessons"]), 9)
        self.assertEqual(self.client.mutate("POST", "/api/admin/lessons/extra-lesson/publish", {"baseVersion": 1})[0], 200)
        self.assertEqual([item["id"] for item in self.client.request("GET", "/api/curriculum")[1]["lessons"]],
                         [item["id"] for item in base["lessons"]] + ["extra-lesson"])

    def test_assessment_http_grading_retry_isolation_export_and_delete(self):
        user = self.client.register()[1]["user"]
        self.assertEqual(self.client.request("POST", "/api/assessments/start", {"kind": "placement"})[0], 403)
        attempt = self.client.mutate("POST", "/api/assessments/start", {"kind": "placement"})[1]
        answers = self.assessment_answers(attempt)
        other = Client(self.server.server_port)
        other.register("other@example.com")
        self.assertEqual(other.mutate("POST", "/api/assessments/submit", {"attemptId": attempt["attemptId"], "answers": answers})[0], 404)
        answers[0]["answer"] = "wrong"
        payload = {"attemptId": attempt["attemptId"], "answers": answers, "score": 100, "xp": 9999}
        graded = self.client.mutate("POST", "/api/assessments/submit", payload)
        self.assertEqual(graded[0], 200, graded[1])
        self.assertEqual(graded[1]["score"], 83)
        self.assertIsNone(graded[1]["passed"])
        self.assertEqual(self.client.mutate("POST", "/api/assessments/submit", payload)[1], graded[1])
        answers[0]["answer"] = "changed"
        self.assertEqual(self.client.mutate("POST", "/api/assessments/submit", payload)[0], 409)
        exported = self.client.request("GET", "/api/export")[1]
        self.assertEqual(exported["progress"]["xp"], 0)
        self.assertEqual(exported["progress"]["completedLessons"], [])
        self.assertEqual(exported["attempts"][0]["result"], graded[1])
        self.assertEqual(other.request("GET", "/api/export")[1]["attempts"], [])
        self.assertNotIn("questions_json", json.dumps(exported))
        self.assertEqual(self.client.mutate("DELETE", "/api/account", {"password": "a strong password 42"})[0], 200)
        with self.server.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assessment_attempts WHERE user_id=?", (user["id"],)).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assessment_attempt_lessons").fetchone()[0], 0)
            self.assertGreater(db.execute("SELECT COUNT(*) FROM content_lesson_usage").fetchone()[0], 0)

    def test_completion_notifies_only_on_first_successful_completion(self):
        self.client.register()
        lesson = self.server.lessons()[0]
        payload = {"lessonId": lesson["id"], "answers": [exercise["answer"] for exercise in lesson["exercises"]]}
        with patch.object(self.server.support, "notify_completion", wraps=self.server.support.notify_completion) as notification:
            self.assertEqual(self.client.mutate("POST", "/api/progress", {**payload, "answers": ["wrong"] * len(payload["answers"])})[0], 422)
            notification.assert_not_called()
            self.assertEqual(self.client.mutate("POST", "/api/progress", payload)[0], 200)
            self.assertEqual(self.client.mutate("POST", "/api/progress", payload)[0], 200)
            self.assertEqual(notification.call_count, 1)


class UserRoleMigrationTests(unittest.TestCase):
    def test_legacy_users_and_passwords_survive_student_role_migration_and_registration(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "legacy.sqlite3"
            stored_password = password_hash("a strong password 42")
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE users(id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at TEXT NOT NULL)")
                db.execute("INSERT INTO users VALUES(?,?,?,?,?)", ("legacy", "Παλαιός", "old@example.com", stored_password, "2025-01-01"))
            server = create_server("127.0.0.1", 0, path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server.initialize_db()
                with server.db() as db:
                    row = db.execute("SELECT * FROM users WHERE id='legacy'").fetchone()
                    self.assertEqual((row["role"], row["password_hash"], row["name"]), ("student", stored_password, "Παλαιός"))
                client = Client(server.server_port)
                client.request("GET", "/api/session")
                login = client.mutate("POST", "/api/login", {"email": "old@example.com", "password": "a strong password 42"})
                self.assertEqual(login[0], 200)
                self.assertEqual(login[1]["user"]["id"], "legacy")
                self.assertFalse(login[1]["user"]["isAdmin"])
                fresh = Client(server.server_port)
                self.assertEqual(fresh.register("fresh@example.com")[0], 200)
                with server.db() as db:
                    self.assertEqual([row[0] for row in db.execute("SELECT DISTINCT role FROM users")], ["student"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
