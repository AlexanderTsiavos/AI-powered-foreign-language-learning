"""Behavior and isolation checks against real temporary SQLite databases."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app_errors import ServiceError
from support import SupportService, activity_day


NOW = datetime(2026, 11, 6, 21, 59, tzinfo=timezone.utc)
PREFS = {"reminders": True, "achievements": True, "supportReplies": True}
TICKET = {"category": "technical", "subject": "Πρόβλημα στο μάθημα", "message": "Δεν μπορώ να συνεχίσω στο επόμενο βήμα."}


class SupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "private.sqlite3"
        self.service = SupportService(self.database)
        with closing(self.database()) as db, db:
            db.executescript("""
                CREATE TABLE users(id TEXT PRIMARY KEY, role TEXT NOT NULL, password_hash TEXT);
                CREATE TABLE review_cards(user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                    lesson_id TEXT, exercise_id TEXT, due_at TEXT);
                INSERT INTO users VALUES('alice','student','secret-alice-hash');
                INSERT INTO users VALUES('bob','student','secret-bob-hash');
                INSERT INTO users VALUES('staff','admin','secret-staff-hash');
            """)
            self.service.initialize(db)
        self.clock = patch("support.utc_now", return_value=NOW)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.temp.cleanup()

    def database(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def call(self, method, path, user="alice", data=None, query=None):
        return self.service.handle(method, path, user, data=data, query=query)

    def create(self, user="alice", **changes):
        return self.call("POST", "/api/support/tickets", user, {**TICKET, **changes})["ticket"]

    def reply(self, ticket, user="alice", *, admin=False, message="Προσθέτω χρήσιμες πληροφορίες για το πρόβλημα."):
        prefix = "/api/admin" if admin else "/api"
        return self.call("POST", f"{prefix}/support/tickets/{ticket['id']}/messages", user, {"message": message})["ticket"]

    def assert_error(self, status, method, path, user="alice", data=None, query=None):
        with self.assertRaises(ServiceError) as caught:
            self.call(method, path, user, data, query)
        self.assertEqual(caught.exception.status, status)
        self.assertTrue(caught.exception.message)

    def preferences(self, user="alice", **changes):
        return self.call("PUT", "/api/notifications/preferences", user, {**PREFS, **changes})["preferences"]

    def complete(self, user="alice", lesson_id="lesson-one", title="Πρώτο μάθημα"):
        with closing(self.database()) as db, db:
            self.service.notify_completion(db, user, {"id": lesson_id, "title": title})

    def due(self, user="alice", at=None):
        with closing(self.database()) as db, db:
            db.execute("INSERT INTO review_cards VALUES(?,?,?,?)", (user, "learned", "one", (at or NOW - timedelta(minutes=1)).isoformat()))

    def test_initialize_is_idempotent_and_unknown_routes_are_not_claimed(self):
        ticket = self.create()
        with closing(self.database()) as db, db:
            self.service.initialize(db)
        self.assertEqual(self.call("GET", "/api/support/tickets")["tickets"][0]["id"], ticket["id"])
        self.assertIsNone(self.call("GET", "/api/unrelated"))
        self.assert_error(405, "DELETE", "/api/support/tickets")

    def test_ticket_create_and_thread_are_private_and_server_assigns_status_role_ids(self):
        ticket = self.create(message="Μια απλή περιγραφή\nμε δεύτερη γραμμή.")
        self.assertRegex(ticket["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(ticket["status"], "open")
        self.assertEqual(ticket["messages"][0]["authorRole"], "student")
        self.assertEqual(ticket["messages"][0]["message"], "Μια απλή περιγραφή\nμε δεύτερη γραμμή.")
        path = f"/api/support/tickets/{ticket['id']}"
        self.assertEqual(self.call("GET", path)["ticket"], ticket)
        self.assertEqual(self.call("GET", "/api/support/tickets", "bob"), {"tickets": []})
        for method, suffix, body in (("GET", "", None), ("POST", "/messages", {"message": "Απαγορευμένη απάντηση."}),
                                      ("PUT", "", {"status": "closed"})):
            self.assert_error(404, method, path + suffix, "bob", body)
        self.assert_error(401, "GET", "/api/support/tickets", "deleted-user")
        self.assert_error(400, "POST", "/api/support/tickets", data={**TICKET, "status": "answered"})

    def test_ticket_input_bounds_categories_plain_text_and_no_attachments(self):
        invalid = [{"category": []}, {"category": "other"}, {"subject": "abcd"}, {"subject": "x" * 121},
                   {"subject": "Hello\nworld"}, {"message": "123456789"}, {"message": "x" * 4001},
                   {"message": 10}, {"message": "<script>alert(1)</script>"}, {"message": "Invisible\x00text"},
                   {"attachments": ["private-file"]}, {"role": "admin"}]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assert_error(400, "POST", "/api/support/tickets", data={**TICKET, **changes})
        self.assertEqual(self.call("GET", "/api/support/tickets")["tickets"], [])
        self.assertEqual(len(self.create(subject="x" * 120, message="x" * 4000)["messages"][0]["message"]), 4000)

    def test_status_transitions_require_owner_reopening_and_actual_admin_reply(self):
        ticket = self.create()
        path = f"/api/support/tickets/{ticket['id']}"
        answered = self.reply(ticket, "staff", admin=True)
        self.assertEqual(answered["status"], "answered")
        self.assertEqual(answered["messages"][-1]["authorRole"], "admin")
        self.assertEqual(self.reply(ticket)["status"], "open")
        self.call("PUT", path, data={"status": "closed"})
        self.assert_error(409, "POST", path + "/messages", data={"message": "Δεν επιτρέπεται τώρα."})
        self.assert_error(409, "POST", "/api/admin/support/tickets/" + ticket["id"] + "/messages", "staff", {"message": "Δεν επιτρέπεται τώρα."})
        self.assert_error(400, "PUT", path, data={"status": "answered"})
        self.call("PUT", path, data={"status": "open"})
        self.assertEqual(len(self.reply(ticket)["messages"]), 4)

    def test_staff_routes_recheck_database_role_and_ignore_client_role_claims(self):
        ticket = self.create()
        path = "/api/admin/support/tickets/" + ticket["id"]
        self.assert_error(403, "GET", "/api/admin/support/tickets")
        self.assert_error(403, "GET", path)
        self.assert_error(403, "POST", path + "/messages", data={"message": "Μια ψεύτικη απάντηση.", "role": "admin"})
        self.assertEqual(self.call("GET", path, "staff")["ticket"]["id"], ticket["id"])
        with closing(self.database()) as db, db:
            db.execute("UPDATE users SET role='student' WHERE id='staff'")
        self.assert_error(403, "POST", path + "/messages", "staff", {"message": "Ο ρόλος έχει αφαιρεθεί."})
        self.assertEqual(len(self.call("GET", "/api/support/tickets/" + ticket["id"])["ticket"]["messages"]), 1)

    def test_admin_queue_status_and_bounded_pagination(self):
        first, second = self.create(), self.create("bob")
        self.reply(first, "staff", admin=True)
        queue = self.call("GET", "/api/admin/support/tickets", "staff", query={"status": ["answered"], "offset": ["0"], "limit": ["1"]})
        self.assertEqual((queue["total"], queue["offset"], queue["limit"]), (1, 0, 1))
        self.assertEqual(queue["tickets"][0]["id"], first["id"])
        self.assertEqual(self.call("GET", "/api/admin/support/tickets", "staff")["tickets"][0]["id"], second["id"])
        for query in ({"limit": "51"}, {"limit": "0"}, {"limit": True}, {"offset": "-1"},
                      {"offset": "1000001"}, {"status": []}, {"status": "everything"}, {"limit": ["1", "2"]}):
            self.assert_error(400, "GET", "/api/admin/support/tickets", "staff", query=query)

    def test_daily_ticket_quota_uses_athens_midnight(self):
        for _ in range(5):
            self.create()
        self.assert_error(429, "POST", "/api/support/tickets", data=TICKET)
        self.create("bob")
        with patch("support.utc_now", return_value=NOW + timedelta(minutes=2)):
            self.assertEqual(self.create()["status"], "open")
        self.assertEqual(len(self.call("GET", "/api/support/tickets")["tickets"]), 6)

    def test_parallel_ticket_requests_cannot_exceed_daily_quota(self):
        def attempt(_):
            try:
                self.create()
                return 200
            except ServiceError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(attempt, range(8)))
        self.assertEqual(statuses.count(200), 5)
        self.assertEqual(statuses.count(429), 3)

    def test_twenty_messages_includes_initial_and_admin_replies(self):
        ticket = self.create()
        for index in range(19):
            self.reply(ticket, "staff" if index % 2 else "alice", admin=bool(index % 2))
        self.assert_error(429, "POST", f"/api/support/tickets/{ticket['id']}/messages", data={"message": "Το όριο συμπληρώθηκε."})
        self.assert_error(429, "POST", f"/api/admin/support/tickets/{ticket['id']}/messages", "staff", {"message": "Το όριο συμπληρώθηκε."})
        self.assertEqual(len(self.call("GET", "/api/support/tickets/" + ticket["id"])["ticket"]["messages"]), 20)

    def test_account_storage_bound_also_counts_messages_from_admin(self):
        # Historical threads, each within its own quota, total exactly 1,000.
        counts = [20] * 49 + [19, 1]
        with closing(self.database()) as db, db:
            for index, count in enumerate(counts):
                ticket_id = f"{index + 1:032x}"
                historical_day = (date(2025, 1, 1) + timedelta(days=index)).isoformat()
                historical_time = historical_day + "T12:00:00+00:00"
                db.execute("INSERT INTO support_tickets VALUES(?,?,?,?,?,?,?,?)", (ticket_id, "alice", "account", "Παλαιό αίτημα", "open", historical_time, historical_time, historical_day))
                for message in range(count):
                    db.execute("INSERT INTO support_messages VALUES(?,?,?,?,?,?)", (f"{10000 + index * 100 + message:032x}", ticket_id, "staff", "admin", "Μια προηγούμενη απάντηση.", historical_time))
        self.assert_error(429, "POST", "/api/support/tickets", data=TICKET)
        self.assert_error(429, "POST", f"/api/support/tickets/{50:032x}/messages", data={"message": "Περισσότερες πληροφορίες."})
        self.create("bob")

    def test_preferences_are_strict_booleans_and_per_user(self):
        self.assertEqual(self.call("GET", "/api/notifications/preferences")["preferences"], PREFS)
        for invalid in (0, 1, "false", None, [], {}):
            self.assert_error(400, "PUT", "/api/notifications/preferences", data={**PREFS, "reminders": invalid})
        self.assert_error(400, "PUT", "/api/notifications/preferences", data={"reminders": False})
        self.assertEqual(self.preferences(reminders=False, achievements=False, supportReplies=False), dict.fromkeys(PREFS, False))
        self.assertEqual(self.call("GET", "/api/notifications/preferences", "bob")["preferences"], PREFS)

    def test_false_preferences_suppress_all_three_new_notification_types(self):
        self.preferences(reminders=False, achievements=False, supportReplies=False)
        self.due()
        self.complete()
        self.reply(self.create(), "staff", admin=True)
        self.assertEqual(self.call("GET", "/api/notifications"), {"items": [], "unreadCount": 0})

    def test_completion_is_idempotent_and_uses_caller_transaction(self):
        self.complete()
        self.complete()
        items = self.call("GET", "/api/notifications")["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["link"], "#dashboard")
        self.assertIn("Πρώτο μάθημα", items[0]["body"])
        with closing(self.database()) as db:
            db.execute("BEGIN IMMEDIATE")
            self.service.notify_completion(db, "alice", {"id": "rollback-lesson"})
            db.rollback()
        self.assertEqual(len(self.call("GET", "/api/notifications")["items"]), 1)
        self.assertEqual(self.call("GET", "/api/notifications", "bob")["unreadCount"], 0)

    def test_reminders_require_due_cards_and_are_once_per_athens_day(self):
        self.due("bob")
        self.due(at=NOW + timedelta(days=1))
        self.assertEqual(self.call("GET", "/api/notifications")["items"], [])
        self.due()
        first = self.call("GET", "/api/notifications")
        self.assertEqual(first["unreadCount"], 1)
        self.assertEqual(first["items"][0]["link"], "#review")
        self.assertEqual(self.call("GET", "/api/notifications"), first)
        with patch("support.utc_now", return_value=NOW + timedelta(minutes=2)):
            self.assertEqual(self.call("GET", "/api/notifications")["unreadCount"], 2)
        self.preferences(reminders=False)
        self.assertEqual(self.call("GET", "/api/notifications")["unreadCount"], 2)

    def test_review_due_offsets_are_compared_as_instants(self):
        self.due(at=datetime(2026, 11, 7, 0, 30, tzinfo=timezone(timedelta(hours=3))))
        self.assertEqual(self.call("GET", "/api/notifications")["unreadCount"], 1)

    def test_admin_reply_only_notifies_owner_and_never_marks_it_read(self):
        ticket = self.create()
        self.reply(ticket, "staff", admin=True)
        result = self.call("GET", "/api/notifications")
        self.assertEqual(result["unreadCount"], 1)
        self.assertEqual(result["items"][0]["link"], "#support/" + ticket["id"])
        self.assertIsNone(result["items"][0]["readAt"])
        self.assertEqual(self.call("GET", "/api/notifications", "staff")["items"], [])
        self.assertEqual(self.call("GET", "/api/notifications", "bob")["items"], [])

    def test_notification_mark_read_is_private_atomic_and_idempotent(self):
        self.complete()
        self.complete("bob")
        own = self.call("GET", "/api/notifications")["items"][0]["id"]
        other = self.call("GET", "/api/notifications", "bob")["items"][0]["id"]
        self.assert_error(404, "POST", "/api/notifications/read", data={"ids": [own, other]})
        self.assertEqual(self.call("GET", "/api/notifications")["unreadCount"], 1)
        result = self.call("POST", "/api/notifications/read", data={"ids": [own, own]})
        read_at = result["items"][0]["readAt"]
        self.assertEqual(result["unreadCount"], 0)
        with patch("support.utc_now", return_value=NOW + timedelta(hours=1)):
            self.assertEqual(self.call("POST", "/api/notifications/read", data={"all": True})["items"][0]["readAt"], read_at)
        self.assertEqual(self.call("GET", "/api/notifications", "bob")["unreadCount"], 1)
        for data in ({"all": 1}, {"all": False}, {"ids": []}, {"ids": [None]}, {"ids": [own] * 51}, {"all": True, "ids": [own]}):
            self.assert_error(400, "POST", "/api/notifications/read", data=data)

    def test_notification_list_is_bounded_but_unread_count_and_read_all_include_older_items(self):
        for index in range(55):
            self.complete(lesson_id=f"lesson-{index}")
        result = self.call("GET", "/api/notifications")
        self.assertEqual((len(result["items"]), result["unreadCount"]), (50, 55))
        self.assertEqual(self.call("POST", "/api/notifications/read", data={"all": True})["unreadCount"], 0)
        self.assert_error(405, "POST", "/api/notifications", data={"title": "Free reward", "xp": 1000})

    def test_export_is_own_data_without_credentials_and_user_deletion_cascades(self):
        own, other = self.create(), self.create("bob")
        self.reply(own, "staff", admin=True)
        self.complete()
        self.complete("bob")
        self.preferences(reminders=False)
        with closing(self.database()) as db:
            exported = self.service.export_data(db, "alice")
        self.assertEqual([ticket["id"] for ticket in exported["supportTickets"]], [own["id"]])
        self.assertEqual(len(exported["supportTickets"][0]["messages"]), 2)
        self.assertFalse(exported["notificationPreferences"]["reminders"])
        text = json.dumps(exported)
        for secret in ("password", "secret-", "author_id", "bob", other["id"]):
            self.assertNotIn(secret, text)
        with closing(self.database()) as db, db:
            db.execute("DELETE FROM users WHERE id='alice'")
            for table in ("support_tickets", "notification_preferences", "notifications"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id='alice'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM support_messages WHERE ticket_id=?", (own["id"],)).fetchone()[0], 0)
        self.assertEqual(self.call("GET", "/api/support/tickets", "bob")["tickets"][0]["id"], other["id"])

    def test_deleting_admin_retains_actual_replies_in_owners_thread(self):
        ticket = self.create()
        self.reply(ticket, "staff", admin=True)
        with closing(self.database()) as db, db:
            db.execute("DELETE FROM users WHERE id='staff'")
        messages = self.call("GET", "/api/support/tickets/" + ticket["id"])["ticket"]["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["authorRole"], "admin")


class SupportCalendarTests(unittest.TestCase):
    def test_athens_day_and_bundled_timezone_preserve_winter_and_summer_offsets(self):
        self.assertEqual(activity_day(NOW), date(2026, 11, 6))
        self.assertEqual(activity_day(NOW + timedelta(minutes=2)), date(2026, 11, 7))
        with patch("support.ZoneInfo", side_effect=ZoneInfoNotFoundError("missing tzdata")) as zone:
            zone.from_file.side_effect = ZoneInfo.from_file
            self.assertEqual(activity_day(NOW + timedelta(minutes=2)), date(2026, 11, 7))
            self.assertEqual(activity_day(datetime(2026, 7, 6, 21, 30, tzinfo=timezone.utc)), date(2026, 7, 7))


if __name__ == "__main__":
    unittest.main()
