"""Assessment authorization, trusted snapshots, bounded history, and grading."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sqlite3
import tempfile
import time
import unittest

from app_errors import ServiceError
from assessment import AssessmentService, RETAINED_ATTEMPTS
from content_admin import ContentAdminService
from server import answer_matches


ROOT = Path(__file__).resolve().parents[1]


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.base = json.loads((ROOT / "data" / "curriculum.json").read_text())
        self.content = ContentAdminService(self.db, ROOT, lambda: deepcopy(self.base))
        self.service = AssessmentService(self.db,
            lambda db=None: self.content.public_curriculum(deepcopy(self.base), db=db), answer_matches)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE users(id TEXT PRIMARY KEY,role TEXT NOT NULL DEFAULT 'student');
                INSERT INTO users VALUES('admin','admin'),('student','student'),('other','student');
                CREATE TABLE completions(
                    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,lesson_id TEXT,
                    xp INTEGER NOT NULL,completed_at TEXT NOT NULL,PRIMARY KEY(user_id,lesson_id));
                CREATE TABLE profiles(user_id TEXT REFERENCES users(id) ON DELETE CASCADE,data TEXT);
                INSERT INTO profiles VALUES('student','{"level":"A1"}');
            """)
            self.content.initialize(db)
            self.service.initialize(db)
            self.content.install_usage_guards(db)

    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def assert_error(self, status, callback, *args, **kwargs):
        with self.assertRaises(ServiceError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)

    def start(self, **options):
        return self.service.start("student", {"kind": "placement", **options})

    def answers(self, attempt, wrong=()):
        with self.db() as db:
            snapshots = json.loads(db.execute("SELECT questions_json FROM assessment_attempts WHERE id=?", (attempt["attemptId"],)).fetchone()[0])
        return [{"questionId": item["id"], "answer": "incorrect answer" if index in wrong else item["exercise"]["answer"]}
                for index, item in enumerate(snapshots)]

    def complete(self, count, user="student"):
        with self.db() as db:
            for lesson in self.base["lessons"][:count]:
                db.execute("INSERT OR IGNORE INTO completions VALUES(?,?,?,?)", (user, lesson["id"], lesson["xp"], "2026-01-01T00:00:00+00:00"))

    def test_placement_serves_six_questions_without_answers_and_exports_metadata_only(self):
        attempt = self.start()
        self.assertEqual(attempt["totalQuestions"], 6)
        self.assertEqual(len({item["id"] for item in attempt["questions"]}), 6)
        for question in attempt["questions"]:
            self.assertTrue(question["prompt"])
            for private in ("answer", "acceptedAnswers", "explanation", "hint", "lessonId"):
                self.assertNotIn(private, question)
        exported = self.service.export("student")
        self.assertEqual(exported["attempts"][0]["attemptId"], attempt["attemptId"])
        self.assertIsNone(exported["attempts"][0]["result"])
        for private in ("questions_json", "request_hash", "acceptedAnswers", "questions"):
            self.assertNotIn(private, json.dumps(exported))
        self.assertEqual(self.service.export("other")["attempts"], [])

    def test_server_grade_ignores_forged_score_without_xp_completion_or_level_change(self):
        attempt = self.start()
        result = self.service.submit("student", {"attemptId": attempt["attemptId"], "answers": self.answers(attempt, (1, 4)),
                                                "score": 100, "passed": True, "level": "C2", "xp": 10000})
        self.assertEqual((result["correctCount"], result["score"]), (4, 67))
        self.assertIsNone(result["passed"])
        self.assertIsNotNone(result["recommendation"]["lessonId"])
        self.assertEqual(sum(item["correct"] for item in result["feedback"]), 4)
        self.assertTrue(all(item["expectedAnswer"] and item["explanation"] for item in result["feedback"]))
        self.assertNotIn("level", result)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM completions").fetchone()[0], 0)
            self.assertEqual(json.loads(db.execute("SELECT data FROM profiles WHERE user_id='student'").fetchone()[0]), {"level": "A1"})
        history = self.service.list("student")["diagnostic"]
        self.assertEqual(history["bestScore"], 67)
        self.assertEqual(history["lastResult"], result)
        self.assertIsNone(self.service.list("other")["diagnostic"]["lastResult"])

    def test_submit_retry_is_normalized_order_independent_and_changed_answers_conflict(self):
        attempt = self.start()
        answers = self.answers(attempt)
        original = self.service.submit("student", {"attemptId": attempt["attemptId"], "answers": answers})
        equivalent = [{"questionId": item["questionId"], "answer": "  " + item["answer"].upper().rstrip(".!?,;:") + "!  "}
                      for item in reversed(answers)]
        with self.db() as db:
            db.execute("UPDATE assessment_attempts SET expires_at=0 WHERE id=?", (attempt["attemptId"],))
        self.assertEqual(self.service.submit("student", {"attemptId": attempt["attemptId"], "answers": equivalent}), original)
        answers[0]["answer"] = "different"
        self.assert_error(409, self.service.submit, "student", {"attemptId": attempt["attemptId"], "answers": answers})
        self.assertEqual(original["score"], 100)

    def test_attempts_are_private_to_the_authenticated_user(self):
        attempt = self.start()
        payload = {"attemptId": attempt["attemptId"], "answers": self.answers(attempt)}
        self.assert_error(404, self.service.submit, "other", payload)
        self.assert_error(401, self.service.submit, "missing", payload)
        self.assert_error(401, self.service.start, "missing", {"kind": "placement"})
        self.assert_error(401, self.service.list, "missing")
        self.assert_error(401, self.service.export, "missing")
        self.assertIsNone(self.service.list("other")["diagnostic"]["bestScore"])

    def test_units_need_three_real_completions_and_do_not_award_extra_xp(self):
        self.assertFalse(self.service.list("student")["units"][0]["available"])
        self.assert_error(409, self.start, kind="unit", unitId="unit-1")
        self.complete(3, user="other")
        self.complete(2)
        self.assert_error(409, self.service.start, "student", {"kind": "unit", "unitId": "unit-1", "completedLessons": [lesson["id"] for lesson in self.base["lessons"][:3]]})
        self.complete(3)
        self.assertTrue(self.service.list("student")["units"][0]["available"])
        attempt = self.start(kind="unit", unitId="unit-1")
        self.assertEqual(len(attempt["questions"]), 6)
        result = self.service.submit("student", {"attemptId": attempt["attemptId"], "answers": self.answers(attempt)})
        self.assertTrue(result["passed"])
        self.assertIsNone(result["recommendation"])
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*),SUM(xp) FROM completions WHERE user_id='student'").fetchone()[:],
                             (3, sum(lesson["xp"] for lesson in self.base["lessons"][:3])))

    def test_unit_pool_supports_uneven_lesson_sizes_and_hides_insufficient_pool(self):
        self.base["lessons"][1]["exercises"] = self.base["lessons"][1]["exercises"][:2]
        self.base["lessons"][2]["exercises"] = self.base["lessons"][2]["exercises"][:1]
        self.complete(3)
        self.assertTrue(self.service.list("student")["units"][0]["available"])
        self.assertEqual(len(self.start(kind="unit", unitId="unit-1")["questions"]), 6)
        for lesson in self.base["lessons"][:3]:
            lesson["exercises"] = lesson["exercises"][:1]
        unit = self.service.list("student")["units"][0]
        self.assertFalse(unit["available"])
        self.assertIn("έξι", unit["unavailableReason"])
        self.assert_error(503, self.service.start, "student", {"kind": "unit", "unitId": "unit-1"})

    def test_attempt_snapshot_ignores_unpublished_drafts_and_freezes_used_lesson(self):
        changed = deepcopy(self.base["lessons"][0])
        changed["exercises"][0]["answer"] = "new unpublished answer"
        changed["exercises"][0]["acceptedAnswers"] = ["new unpublished answer"]
        self.content.save_draft("admin", changed["id"], {"lesson": changed, "baseVersion": 0})
        attempt = self.start()
        answers = self.answers(attempt)
        self.assertEqual(answers[0]["answer"], "car")
        self.assert_error(409, self.content.publish, "admin", changed["id"], {"baseVersion": 1})
        # Even if the shipped file is externally changed, an in-flight attempt
        # grades against its immutable stored snapshot, never the current file.
        self.base["lessons"][0] = changed
        result = self.service.submit("student", {"attemptId": attempt["attemptId"], "answers": answers})
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["feedback"][0]["expectedAnswer"], "car")

    def test_expired_unfinished_attempt_cannot_submit_or_expose_correct_answers(self):
        attempt = self.start()
        with self.db() as db:
            db.execute("UPDATE assessment_attempts SET expires_at=? WHERE id=?", (time.time() - 1, attempt["attemptId"]))
        self.assert_error(410, self.service.submit, "student", {"attemptId": attempt["attemptId"], "answers": self.answers(attempt)})
        with self.db() as db:
            self.assertIsNone(db.execute("SELECT result_json FROM assessment_attempts").fetchone()[0])

    def test_malformed_questions_answers_and_kinds_are_rejected(self):
        for payload in ({}, {"kind": "C2"}, {"kind": "placement", "unitId": "unit-1"}, {"kind": "unit", "unitId": []}):
            self.assert_error(400, self.service.start, "student", payload)
        self.assert_error(404, self.service.start, "student", {"kind": "unit", "unitId": "unit-99"})
        attempt = self.start()
        answers = self.answers(attempt)
        bad = [answers[:-1], answers + [answers[0]], [answers[0]] * 6,
               [{"questionId": "foreign", "answer": "car"}, *answers[1:]],
               [{"questionId": answers[0]["questionId"], "answer": 1}, *answers[1:]],
               [{"questionId": answers[0]["questionId"], "answer": "a" * 1001}, *answers[1:]]]
        for invalid in bad:
            self.assert_error(400, self.service.submit, "student", {"attemptId": attempt["attemptId"], "answers": invalid})

    def test_hourly_start_limit_is_persistent(self):
        for _ in range(10):
            self.start()
        reloaded = AssessmentService(self.db, lambda db=None: self.content.public_curriculum(self.base, db=db), answer_matches)
        self.assert_error(429, reloaded.start, "student", {"kind": "placement"})
        self.assertIsNotNone(reloaded.start("other", {"kind": "placement"})["attemptId"])

    def test_concurrent_submit_retries_store_one_result(self):
        attempt = self.start()
        payload = {"attemptId": attempt["attemptId"], "answers": self.answers(attempt)}
        with ThreadPoolExecutor(max_workers=3) as workers:
            results = list(workers.map(lambda _: self.service.submit("student", payload), range(3)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(results[0]["score"], 100)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assessment_attempts WHERE completed_at IS NOT NULL").fetchone()[0], 1)

    def test_concurrent_publication_and_start_cannot_change_snapshot_answers(self):
        changed = deepcopy(self.base["lessons"][0])
        changed["exercises"][0]["answer"] = "new published answer"
        changed["exercises"][0]["acceptedAnswers"] = ["new published answer"]
        self.content.save_draft("admin", changed["id"], {"lesson": changed, "baseVersion": 0})
        with ThreadPoolExecutor(max_workers=2) as workers:
            started = workers.submit(self.start)
            published = workers.submit(self.content.publish, "admin", changed["id"], {"baseVersion": 1})
            attempt = started.result()
            try:
                published.result()
                publication_won = True
            except ServiceError as error:
                self.assertEqual(error.status, 409)
                publication_won = False
        answers = self.answers(attempt)
        self.assertEqual(answers[0]["answer"], "new published answer" if publication_won else "car")
        self.assertEqual(self.service.submit("student", {"attemptId": attempt["attemptId"], "answers": answers})["score"], 100)

    def test_only_latest_twenty_attempts_remain_and_deleted_accounts_cascade(self):
        started = []
        for _ in range(RETAINED_ATTEMPTS + 2):
            started.append(self.start()["attemptId"])
            # Simulate distinct hours without changing process/global clocks.
            with self.db() as db:
                db.execute("UPDATE assessment_attempts SET created_at=created_at-3601 WHERE user_id='student'")
        retained = self.service.export("student")["attempts"]
        self.assertEqual(len(retained), RETAINED_ATTEMPTS)
        self.assertEqual({attempt["attemptId"] for attempt in retained}, set(started[-RETAINED_ATTEMPTS:]))
        self.service.start("other", {"kind": "placement"})
        with self.db() as db:
            db.execute("DELETE FROM users WHERE id='student'")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assessment_attempts WHERE user_id='student'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assessment_attempt_lessons WHERE attempt_id NOT IN (SELECT id FROM assessment_attempts)").fetchone()[0], 0)
            self.assertGreater(db.execute("SELECT COUNT(*) FROM content_lesson_usage").fetchone()[0], 0)
        self.assertEqual(len(self.service.export("other")["attempts"]), 1)


if __name__ == "__main__":
    unittest.main()
