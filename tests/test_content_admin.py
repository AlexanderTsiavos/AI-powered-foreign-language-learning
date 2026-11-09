"""Versioned content, publication isolation, and permanent usage guards."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from app_errors import ServiceError
from content_admin import ContentAdminService


ROOT = Path(__file__).resolve().parents[1]


class ContentAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.base = json.loads((ROOT / "data" / "curriculum.json").read_text())
        self.service = ContentAdminService(self.db, ROOT, lambda: deepcopy(self.base))
        with self.db() as db:
            db.executescript("""
                CREATE TABLE users(id TEXT PRIMARY KEY,role TEXT NOT NULL DEFAULT 'student');
                INSERT INTO users VALUES('admin','admin'),('student','student');
                CREATE TABLE completions(user_id TEXT REFERENCES users(id) ON DELETE CASCADE,lesson_id TEXT);
                CREATE TABLE lesson_checkpoints(user_id TEXT REFERENCES users(id) ON DELETE CASCADE,lesson_id TEXT);
                CREATE TABLE review_cards(user_id TEXT REFERENCES users(id) ON DELETE CASCADE,lesson_id TEXT);
                CREATE TABLE review_attempts(user_id TEXT REFERENCES users(id) ON DELETE CASCADE,lesson_id TEXT);
                CREATE TABLE assessment_attempt_lessons(attempt_id TEXT,lesson_id TEXT);
            """)
            self.service.initialize(db)

    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def assert_error(self, status, callback, *args, **kwargs):
        with self.assertRaises(ServiceError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)

    def draft(self, lesson=None, version=0, create=False):
        lesson = deepcopy(lesson or self.base["lessons"][0])
        return self.service.save_draft("admin", None if create else lesson["id"],
                                       {"lesson": lesson, "baseVersion": version}, create=create)["lesson"]

    def test_every_shipped_lesson_validates_with_local_assets_and_all_types(self):
        kinds = set()
        for lesson in self.base["lessons"]:
            self.assertEqual(self.service.validate_lesson(lesson), lesson)
            kinds.update(exercise["type"] for exercise in lesson["exercises"])
        self.assertEqual(kinds, {"translate", "gap", "multiple-choice", "word-order", "reading", "listening"})
        self.assertEqual(len(self.service.allowed_images), 3)

    def test_draft_stays_private_then_published_overlay_keeps_base_order(self):
        lesson = deepcopy(self.base["lessons"][0])
        lesson["title"] = "Νέος τίτλος"
        saved = self.draft(lesson)
        self.assertEqual(saved["version"], 1)
        self.assertEqual(saved["draft"], lesson)
        self.assertEqual(self.service.public_curriculum(self.base), self.base)
        self.assert_error(409, self.service.publish, "admin", lesson["id"], {"baseVersion": 0})
        published = self.service.publish("admin", lesson["id"], {"baseVersion": 1})["lesson"]
        self.assertIsNone(published["draft"])
        self.assertEqual(published["version"], 2)
        public = self.service.public_curriculum(self.base)
        self.assertEqual(public["lessons"][0], lesson)
        self.assertEqual([item["id"] for item in public["lessons"]], [item["id"] for item in self.base["lessons"]])
        self.assertNotEqual(self.base["lessons"][0]["title"], lesson["title"])
        self.assert_error(409, self.draft, lesson, 1)

    def test_new_lessons_append_once_in_publication_order_and_cannot_replace_ids(self):
        first = deepcopy(self.base["lessons"][0])
        first["id"] = "new-first"
        second = deepcopy(first)
        second["id"] = "new-second"
        self.draft(first, create=True)
        self.draft(second, create=True)
        self.assertEqual(len(self.service.public_curriculum(self.base)["lessons"]), len(self.base["lessons"]))
        self.service.publish("admin", second["id"], {"baseVersion": 1})
        self.service.publish("admin", first["id"], {"baseVersion": 1})
        self.assertEqual([lesson["id"] for lesson in self.service.public_curriculum(self.base)["lessons"]][-2:],
                         [second["id"], first["id"]])
        self.assert_error(409, self.draft, first, 0, True)
        self.assert_error(400, self.service.save_draft, "admin", "new-first",
                          {"lesson": second, "baseVersion": 2})
        self.assert_error(404, self.service.publish, "admin", "unknown", {"baseVersion": 0})

    def test_admin_authorization_is_checked_again_after_role_revocation(self):
        self.assert_error(403, self.service.overview, "student")
        self.assert_error(403, self.service.list_curriculum, "missing")
        self.assert_error(403, self.service.save_draft, "student", self.base["lessons"][0]["id"],
                          {"lesson": self.base["lessons"][0], "baseVersion": 0, "role": "admin"})
        self.assertIn("counts", self.service.overview("admin"))
        with self.db() as db:
            db.execute("UPDATE users SET role='student' WHERE id='admin'")
        self.assert_error(403, self.service.list_curriculum, "admin")

    def test_any_usage_permanently_freezes_all_lesson_changes_including_pending_drafts(self):
        tables = ("completions", "lesson_checkpoints", "review_cards", "review_attempts", "assessment_attempt_lessons")
        for index, table in enumerate(tables):
            with self.subTest(table=table):
                lesson = deepcopy(self.base["lessons"][index])
                lesson["title"] = "Προσχέδιο πριν από τη χρήση"
                self.draft(lesson)
                with self.db() as db:
                    db.execute(f"INSERT INTO {table} VALUES(?,?)", ("attempt" if table == "assessment_attempt_lessons" else "student", lesson["id"]))
                    db.execute(f"DELETE FROM {table}")
                self.assert_error(409, self.service.publish, "admin", lesson["id"], {"baseVersion": 1})
                self.assert_error(409, self.draft, lesson, 1)
                record = next(item for item in self.service.list_curriculum("admin")["lessons"] if item["id"] == lesson["id"])
                self.assertFalse(record["canChangeExercises"])
                self.assertIn("νέο", record["lockedReason"])

    def test_usage_guards_backfill_existing_records_and_survive_account_deletion(self):
        lesson_id = self.base["lessons"][0]["id"]
        with self.db() as db:
            db.execute("DROP TRIGGER content_usage_completions_insert")
            db.execute("INSERT INTO completions VALUES('student',?)", (lesson_id,))
            self.service.initialize(db)
            db.execute("DELETE FROM users WHERE id='student'")
        changed = deepcopy(self.base["lessons"][0])
        changed["exercises"][0]["answer"] = "changed"
        changed["exercises"][0]["acceptedAnswers"] = ["changed"]
        self.assert_error(409, self.draft, changed)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM completions").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM content_lesson_usage").fetchone()[0], 1)

    def test_validation_rejects_html_remote_assets_invalid_types_answers_and_payloads(self):
        baseline = self.base["lessons"][0]
        bad = []
        for key, value in (("title", "<img src=x>"), ("xp", True), ("durationMinutes", 61), ("id", "../lesson")):
            lesson = deepcopy(baseline)
            lesson[key] = value
            bad.append(lesson)
        lesson = deepcopy(baseline)
        lesson["cards"][0]["image"] = "https://example.com/image.svg"
        bad.append(lesson)
        for key, value in (("type", []), ("acceptedAnswers", ["other"]), ("prompt", "bad\x00text"), ("choices", ["a", "b"])):
            lesson = deepcopy(baseline)
            lesson["exercises"][0][key] = value
            bad.append(lesson)
        lesson = deepcopy(baseline)
        lesson["exercises"].append(deepcopy(lesson["exercises"][0]))
        bad.append(lesson)
        for index, lesson in enumerate(bad):
            with self.subTest(case=index):
                self.assert_error(400, self.service.validate_lesson, lesson)
        self.assert_error(400, self.service.save_draft, "admin", baseline["id"], {"lesson": baseline, "baseVersion": False})
        self.assert_error(400, self.service.save_draft, "admin", baseline["id"], {"lesson": baseline, "baseVersion": 0, "delete": True})

    def test_exercise_specific_validation_rejects_impossible_choices_and_tokens(self):
        lesson = deepcopy(self.base["lessons"][0])
        exercise = lesson["exercises"][0]
        exercise.update(type="multiple-choice", choices=["car", "CAR!"])
        exercise["acceptedAnswers"] = ["car"]
        self.assert_error(400, self.service.validate_lesson, lesson)
        exercise.update(type="word-order", tokens=["a", "boat"])
        exercise.pop("choices")
        exercise["answer"] = "a car"
        exercise["acceptedAnswers"] = ["a car"]
        self.assert_error(400, self.service.validate_lesson, lesson)
        exercise.update(type="reading")
        exercise.pop("tokens")
        self.assert_error(400, self.service.validate_lesson, lesson)

    def test_concurrent_saves_cannot_overwrite_another_revision(self):
        lessons = [deepcopy(self.base["lessons"][0]) for _ in range(2)]
        for index, lesson in enumerate(lessons):
            lesson["title"] = f"Σύγχρονο προσχέδιο {index}"
        def save(lesson):
            try:
                return self.draft(lesson)["draft"]["title"]
            except ServiceError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(save, lessons))
        self.assertEqual(results.count(409), 1)
        record = self.service.list_curriculum("admin")["lessons"][0]
        self.assertEqual(record["version"], 1)
        self.assertIn(record["draft"]["title"], results)


if __name__ == "__main__":
    unittest.main()
