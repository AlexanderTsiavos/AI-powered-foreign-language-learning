"""Private learned cards, persistent idempotent bookmarks and safe migrations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app_errors import ServiceError
from server import create_server
from test_server import Client
from test_wsgi import WSGIClient
from wsgi import create_application


def lesson(number):
    words = ("car", "book", "water") if number == 1 else ("mother", "father", "sister")
    return {"id": f"lesson-{number}", "title": f"Μάθημα {number}", "xp": 30,
            "cards": [{"word": word, "translation": f"Μετάφραση {word}",
                       "example": f"This is {word}.", "exampleTranslation": "Ένα παράδειγμα.",
                       **({"pronunciation": "καρ"} if word == "car" else {}),
                       "privateExample": "do-not-copy-arbitrary-properties"} for word in words],
            "exercises": [{"id": word, "answer": word, "acceptedAnswers": [word]}
                          for word in words]}


class LearnerLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "checkout"
        (self.root / "web").mkdir(parents=True)
        (self.root / "data").mkdir()
        self.curriculum = {"lessons": [lesson(1), lesson(2)]}
        self.write_course()
        self.path = Path(self.temp.name) / "private" / "academy.sqlite3"
        with patch.dict(os.environ, {"ACADEMY_MAIL_MODE": "disabled", "ACADEMY_AI_PROVIDER": "disabled"}):
            self.app = create_application(self.path, self.root, environ={})
        self.state = self.app.state
        self.service = self.state.library
        self.client = WSGIClient(self.app)
        self.user_id = self.client.register("library@example.test")[1]["user"]["id"]

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def write_course(self):
        (self.root / "data/curriculum.json").write_text(json.dumps(self.curriculum), encoding="utf-8")

    def complete(self, number=1, client=None):
        item = self.curriculum["lessons"][number - 1]
        result = (client or self.client).mutate("POST", "/api/progress", {
            "lessonId": item["id"], "answers": [exercise["answer"] for exercise in item["exercises"]]})
        self.assertEqual(result[0], 200, result[1])

    def bookmark(self, value=True, index=0, client=None, **extra):
        return (client or self.client).mutate("PUT", "/api/library/bookmarks", {
            "lessonId": "lesson-1", "cardIndex": index, "bookmarked": value, **extra})

    def bookmark_rows(self, user_id=None):
        with self.state.db() as db:
            return [tuple(row) for row in db.execute(
                "SELECT lesson_id,card_index,created_at FROM learner_bookmarks WHERE user_id=? "
                "ORDER BY lesson_id,card_index", (user_id or self.user_id,))]

    def test_authentication_no_store_and_no_unlearned_cards(self):
        anonymous = WSGIClient(self.app)
        self.assertEqual(anonymous.request("GET", "/api/library")[0], 401)
        anonymous.request("GET", "/api/session")
        self.assertEqual(self.bookmark(client=anonymous)[0], 401)
        status, data, headers = self.client.request("GET", "/api/library")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(data["cards"], [])
        self.assertEqual(data["summary"], {"learnedCards": 0, "bookmarkedCards": 0, "completedLessons": 0})
        self.assertIn("δεν δίνει πόντους", data["notice"])
        status, body, _ = self.client.request("HEAD", "/api/library")
        self.assertEqual((status, body), (200, b""))

    def test_completed_lesson_card_order_whitelist_and_optional_pronunciation(self):
        self.complete()
        data = self.service.get(self.user_id)
        self.assertEqual([card["word"] for card in data["cards"]], ["car", "book", "water"])
        self.assertEqual([card["cardIndex"] for card in data["cards"]], [0, 1, 2])
        self.assertEqual(data["summary"]["completedLessons"], 1)
        self.assertEqual(data["summary"]["learnedCards"], 3)
        self.assertEqual(data["cards"][0]["pronunciation"], "καρ")
        self.assertNotIn("pronunciation", data["cards"][1])
        self.assertEqual(set(data["cards"][1]), {
            "lessonId", "cardIndex", "lessonTitle", "word", "translation",
            "example", "exampleTranslation", "bookmarked"})
        serialized = json.dumps(data)
        for private in ("privateExample", "acceptedAnswers", "csrfToken", "password_hash", "mother"):
            self.assertNotIn(private, serialized)

    def test_course_order_does_not_follow_completion_timestamp_or_bookmark_order(self):
        self.complete()
        self.complete(2)
        with self.state.db() as db:
            db.execute("UPDATE completions SET completed_at='2000-01-01T00:00:00+00:00' WHERE lesson_id='lesson-2'")
        self.assertEqual(self.bookmark(index=2)[0], 200)
        cards = self.service.get(self.user_id)["cards"]
        self.assertEqual([card["lessonId"] for card in cards], ["lesson-1"] * 3 + ["lesson-2"] * 3)
        self.assertTrue(cards[2]["bookmarked"])
        self.assertFalse(cards[0]["bookmarked"])

    def test_bookmarks_idempotently_preserve_timestamp_and_do_not_change_learning(self):
        self.complete()
        before = self.client.request("GET", "/api/session")[1]["progress"]
        reviews = self.client.request("GET", "/api/review")[1]
        stamp = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.service.clock = lambda: stamp
        first = self.bookmark()
        self.assertEqual(first[0], 200)
        rows = self.bookmark_rows()
        self.assertEqual(len(rows), 1)
        self.service.clock = lambda: stamp + timedelta(hours=2)
        second = self.bookmark()
        self.assertEqual(second[1], first[1])
        self.assertEqual(self.bookmark_rows(), rows)
        self.assertEqual(second[1]["summary"]["bookmarkedCards"], 1)
        self.assertEqual(self.bookmark(False)[1]["summary"]["bookmarkedCards"], 0)
        self.assertEqual(self.bookmark(False)[0], 200)
        self.assertEqual(self.bookmark_rows(), [])
        self.assertEqual(self.client.request("GET", "/api/session")[1]["progress"], before)
        self.assertEqual(self.client.request("GET", "/api/review")[1], reviews)

    def test_csrf_and_locked_lessons_cannot_create_bookmarks(self):
        self.assertEqual(self.bookmark()[0], 409)
        self.complete()
        self.assertEqual(self.client.request("PUT", "/api/library/bookmarks", {
            "lessonId": "lesson-1", "cardIndex": 0, "bookmarked": True})[0], 403)
        result = self.client.mutate("PUT", "/api/library/bookmarks", {
            "lessonId": "lesson-2", "cardIndex": 0, "bookmarked": True})
        self.assertEqual(result[0], 409)
        self.assertEqual(self.bookmark_rows(), [])

    def test_invalid_indexes_state_and_unknown_fields_are_rejected_without_writes(self):
        self.complete()
        for index in (True, False, -1, 3, "0", None, 1.5):
            with self.subTest(index=index):
                self.assertEqual(self.bookmark(index=index)[0], 400)
        for state in (0, 1, "true", None):
            with self.subTest(state=state):
                self.assertEqual(self.bookmark(value=state)[0], 400)
        self.assertEqual(self.bookmark(userId="another-user")[0], 400)
        self.assertEqual(self.client.mutate("PUT", "/api/library/bookmarks", {"lessonId": "lesson-1"})[0], 400)
        self.assertEqual(self.bookmark_rows(), [])

    def test_account_query_and_body_cannot_select_another_users_data(self):
        other = WSGIClient(self.app)
        other_id = other.register("other-library@example.test")[1]["user"]["id"]
        self.complete(client=other)
        self.assertEqual(self.bookmark(client=other)[0], 200)
        self.assertEqual(self.client.request("GET", "/api/library?userId=" + other_id)[1]["cards"], [])
        self.assertEqual(self.bookmark(userId=other_id)[0], 400)
        self.assertEqual(self.bookmark_rows(), [])
        self.assertEqual(len(self.bookmark_rows(other_id)), 1)
        self.complete()
        self.assertEqual(self.bookmark(False)[1]["summary"]["bookmarkedCards"], 0)
        self.assertEqual(other.request("GET", "/api/library")[1]["summary"]["bookmarkedCards"], 1)

    def test_export_contains_only_own_bookmark_metadata_and_delete_cascades(self):
        self.complete()
        self.bookmark(index=1)
        other = WSGIClient(self.app)
        other_id = other.register("retained-library@example.test")[1]["user"]["id"]
        self.complete(client=other)
        self.bookmark(client=other)
        export = self.client.request("GET", "/api/export")[1]["learnerLibrary"]
        self.assertEqual(set(export), {"bookmarks"})
        self.assertEqual(len(export["bookmarks"]), 1)
        self.assertEqual(export["bookmarks"][0]["cardIndex"], 1)
        self.assertEqual(set(export["bookmarks"][0]), {"lessonId", "cardIndex", "createdAt"})
        result = self.client.mutate("DELETE", "/api/account", {"password": "a strong password 42"})
        self.assertEqual(result[0], 200)
        self.assertEqual(self.bookmark_rows(), [])
        self.assertEqual(len(self.bookmark_rows(other_id)), 1)
        self.assertEqual(self.client.request("GET", "/api/library")[0], 401)

    def test_initialization_upgrades_existing_accounts_and_survives_restart(self):
        self.complete()
        with self.state.db() as db:
            db.execute("DROP TABLE learner_bookmarks")
        self.state.initialize_db()
        self.assertEqual(self.client.request("GET", "/api/library")[1]["summary"]["learnedCards"], 3)
        self.bookmark()
        with self.state.db() as db:
            self.service.initialize(db)
        self.app.close()
        self.app = create_application(self.path, self.root, environ={})
        self.state = self.app.state
        self.service = self.state.library
        # Keep the old valid cookie against the new service instance.
        fresh = WSGIClient(self.app)
        fresh.cookie, fresh.csrf = self.client.cookie, self.client.csrf
        result = fresh.request("GET", "/api/library")
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]["summary"]["bookmarkedCards"], 1)
        self.assertEqual(fresh.request("GET", "/api/session")[1]["progress"]["xp"], 30)

    def test_library_uses_current_authoritative_published_content(self):
        self.complete()
        self.bookmark()
        # Simulate an already-authoritative snapshot from the curriculum provider;
        # the library stores no copied words, translations or answers.
        source = self.state.curriculum
        self.service.curriculum_fn = lambda db=None: {
            "lessons": [{**source(db=db)["lessons"][0],
                         "cards": [{"word": "current word", "translation": "τρέχουσα λέξη"}]}]}
        data = self.service.get(self.user_id)
        self.assertEqual(data["cards"][0]["word"], "current word")
        self.assertTrue(data["cards"][0]["bookmarked"])
        self.assertNotIn("privateExample", json.dumps(data))

    def test_repeated_library_reads_write_no_tracking_records(self):
        self.complete()
        self.bookmark()
        with self.state.db() as db:
            before = list(db.iterdump())
        for _ in range(3):
            self.service.get(self.user_id)
        with self.state.db() as db:
            self.assertEqual(list(db.iterdump()), before)
        with self.assertRaises(ServiceError) as caught:
            self.service.get("deleted-user")
        self.assertEqual(caught.exception.status, 401)

    def test_concurrent_replayed_sets_remain_one_bookmark(self):
        self.complete()

        def set_one(_):
            with self.state.db() as db:
                return self.service.set_bookmark(self.user_id,
                    {"lessonId": "lesson-1", "cardIndex": 0, "bookmarked": True}, db=db)

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(set_one, range(8)))
        self.assertEqual(len(self.bookmark_rows()), 1)
        self.assertTrue(all(result["summary"]["bookmarkedCards"] == 1 for result in results))

    def test_development_http_uses_same_library_auth_csrf_and_bookmark_contract(self):
        server = create_server("127.0.0.1", 0, Path(self.temp.name) / "http.sqlite3", self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = Client(server.server_port)
            self.assertEqual(client.request("GET", "/api/library")[0], 401)
            self.assertEqual(client.register("tcp-library@example.test")[0], 200)
            self.complete(client=client)
            result = self.bookmark(client=client)
            self.assertEqual(result[0], 200)
            self.assertEqual(result[1]["summary"]["bookmarkedCards"], 1)
            self.assertEqual(result[2]["Cache-Control"], "no-store")
            self.assertTrue(client.request("GET", "/api/library")[1]["cards"][0]["bookmarked"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
