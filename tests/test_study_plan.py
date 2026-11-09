"""Actual private progress drives suggestions; no extra tracking or fake minutes."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app_errors import ServiceError
from server import activity_day, create_server
from test_server import Client
from test_wsgi import WSGIClient
from wsgi import create_application


def lesson(number, minutes=5):
    return {"id": f"lesson-{number}", "title": f"Μάθημα {number}", "durationMinutes": minutes,
            "xp": 30, "cards": [{"word": "car"}, {"word": "book"}, {"word": "water"}],
            "exercises": [{"id": "car", "answer": "car"}, {"id": "book", "answer": "book"},
                          {"id": "water", "answer": "water"}]}


class StudyPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "checkout"
        (self.root / "data").mkdir(parents=True)
        (self.root / "web").mkdir()
        self.curriculum = {"lessons": [lesson(i) for i in range(1, 7)]}
        self.write_course()
        self.path = Path(self.temp.name) / "private" / "academy.sqlite3"
        with patch.dict(os.environ, {"ACADEMY_MAIL_MODE": "disabled", "ACADEMY_AI_PROVIDER": "disabled"}):
            self.app = create_application(self.path, self.root, environ={})
        self.state = self.app.state
        self.service = self.state.study_plan
        self.now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.service.clock = lambda: self.now
        self.client = WSGIClient(self.app)
        self.user_id = self.client.register()[1]["user"]["id"]

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def write_course(self):
        (self.root / "data" / "curriculum.json").write_text(json.dumps(self.curriculum), encoding="utf-8")

    def complete(self, number, client=None):
        result = (client or self.client).mutate("POST", "/api/progress", {
            "lessonId": f"lesson-{number}", "answers": ["car", "book", "water"]})
        self.assertEqual(result[0], 200, result[1])
        return result

    def saved_completion(self, number, at, xp=30, user_id=None):
        with self.state.db() as db:
            db.execute("INSERT INTO completions VALUES(?,?,?,?)", (
                user_id or self.user_id, f"lesson-{number}", xp,
                at.isoformat() if isinstance(at, datetime) else at))

    def test_authenticated_http_and_no_store_no_account_selector(self):
        anonymous = WSGIClient(self.app)
        status, body, headers = anonymous.request("GET", "/api/study-plan")
        self.assertEqual(status, 401)
        self.assertEqual(headers["Cache-Control"], "no-store")
        status, body, headers = self.client.request("GET", "/api/study-plan?userId=someone-else")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(body["summary"]["completedLessons"], 0)
        self.assertEqual(body["summary"]["totalLessons"], 6)
        self.assertEqual(body["summary"]["totalExercises"], 18)
        self.assertNotIn("csrfToken", body)
        for private in ("password_hash", "acceptedAnswers", "token_hash", "email"):
            self.assertNotIn(private, json.dumps(body))
        status, body, _ = self.client.request("HEAD", "/api/study-plan")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_actual_completions_update_counts_and_current_next_lesson(self):
        self.complete(1)
        plan = self.service.get(self.user_id)
        self.assertEqual(plan["summary"]["xp"], 30)
        self.assertEqual(plan["summary"]["completedLessons"], 1)
        self.assertEqual(plan["summary"]["learnedExercises"], 3)
        self.assertEqual(plan["summary"]["completionPercent"], 17)
        self.assertEqual(plan["nextLesson"]["id"], "lesson-2")
        self.assertEqual(plan["recommendation"]["kind"], "lesson")
        self.assertEqual(plan["recommendation"]["route"], "lesson/lesson-2")
        self.assertEqual(plan["units"][0]["completedLessons"], 1)
        self.assertEqual([x["unlocked"] for x in plan["units"][0]["lessons"]], [True, True, False])
        self.assertEqual(plan["units"][1]["status"], "locked")

    def test_resume_is_safe_metadata_and_does_not_complete(self):
        status, _, _ = self.client.mutate("PUT", "/api/lesson-state", {
            "lessonId": "lesson-1", "phase": "exercises", "index": 1,
            "answers": ["car"], "correct": False})
        self.assertEqual(status, 200)
        plan = self.service.get(self.user_id)
        self.assertEqual(plan["nextLesson"]["checkpoint"], {"phase": "exercises", "index": 1})
        self.assertTrue(plan["nextLesson"]["resume"])
        self.assertEqual(plan["summary"]["xp"], 0)
        self.assertEqual(plan["summary"]["completedLessons"], 0)
        self.assertNotIn("answers", json.dumps(plan))

    def test_locked_or_completed_checkpoint_cannot_redirect_recommendation(self):
        self.saved_completion(3, self.now)
        with self.state.db() as db:
            for number in (3, 6):
                db.execute("INSERT INTO lesson_checkpoints VALUES(?,?,?,?,?,?,?)", (
                    self.user_id, f"lesson-{number}", "cards", 1, "[]", 0, self.now.isoformat()))
        plan = self.service.get(self.user_id)
        self.assertEqual(plan["nextLesson"]["id"], "lesson-1")
        self.assertFalse(plan["nextLesson"]["resume"])
        self.assertEqual(plan["recommendation"]["route"], "lesson/lesson-1")
        self.assertFalse(plan["units"][1]["lessons"][-1]["unlocked"])

    def test_due_review_priority_boundary_and_only_valid_learned_exercises(self):
        self.complete(1)
        with self.state.db() as db:
            db.execute("UPDATE review_cards SET due_at=? WHERE user_id=? AND exercise_id='car'", (
                self.now.isoformat(), self.user_id))
            db.execute("INSERT INTO review_cards VALUES(?,?,?,?,?,NULL)", (
                self.user_id, "lesson-6", "car", 0, (self.now - timedelta(days=1)).isoformat()))
            db.execute("INSERT INTO review_cards VALUES(?,?,?,?,?,NULL)", (
                self.user_id, "lesson-1", "deleted-question", 0, (self.now - timedelta(days=1)).isoformat()))
        plan = self.service.get(self.user_id)
        self.assertEqual(plan["summary"]["dueReviews"], 1)
        self.assertEqual(plan["recommendation"]["kind"], "review")
        self.assertEqual(plan["recommendation"]["route"], "review")
        self.assertTrue(plan["suggestedSession"]["includesReviews"])
        self.assertEqual(plan["nextLesson"]["id"], "lesson-2")
        self.now -= timedelta(microseconds=1)
        self.assertEqual(self.service.get(self.user_id)["summary"]["dueReviews"], 0)

    def test_two_accounts_cannot_read_each_others_activity_or_checkpoint(self):
        other = WSGIClient(self.app)
        other_id = other.register("other@example.test")[1]["user"]["id"]
        self.complete(1)
        self.client.mutate("PUT", "/api/lesson-state", {
            "lessonId": "lesson-2", "phase": "cards", "index": 1, "answers": [], "correct": False})
        self.saved_completion(2, self.now, user_id=self.user_id)
        plan = other.request("GET", "/api/study-plan?userId=" + self.user_id)[1]
        self.assertEqual(plan["summary"]["xp"], 0)
        self.assertEqual(plan["summary"]["completedLessons"], 0)
        self.assertEqual(plan["summary"]["dueReviews"], 0)
        self.assertEqual(plan["nextLesson"]["id"], "lesson-1")
        self.assertFalse(plan["nextLesson"]["resume"])
        self.assertEqual(sum(day["completedLessons"] for day in plan["dailyActivity"]), 0)
        self.assertNotEqual(self.user_id, other_id)

    def test_course_append_preserves_old_progress_xp_and_partial_unit(self):
        for number in range(1, 7):
            self.complete(number)
        previous = self.service.get(self.user_id)
        self.assertIsNone(previous["nextLesson"])
        self.assertEqual(previous["recommendation"]["kind"], "complete")
        self.curriculum["lessons"].append(lesson(7))
        self.write_course()
        plan = self.service.get(self.user_id)
        self.assertEqual(plan["summary"]["xp"], previous["summary"]["xp"])
        self.assertEqual(plan["summary"]["completedLessons"], 6)
        self.assertEqual(plan["summary"]["totalLessons"], 7)
        self.assertEqual(plan["nextLesson"]["id"], "lesson-7")
        self.assertEqual(plan["units"][-1]["totalLessons"], 1)
        self.assertEqual(plan["units"][-1]["status"], "available")

    def test_daily_history_uses_athens_midnight_and_fourteen_calendar_days(self):
        self.now = datetime(2026, 11, 9, 23, tzinfo=timezone.utc)
        self.saved_completion(1, datetime(2026, 11, 9, 21, 59, tzinfo=timezone.utc), xp=10)
        self.saved_completion(2, datetime(2026, 11, 9, 22, tzinfo=timezone.utc), xp=40)
        self.saved_completion(3, self.now - timedelta(days=15))
        self.saved_completion(4, self.now + timedelta(minutes=1))
        plan = self.service.get(self.user_id)
        self.assertEqual(plan["today"], "2026-11-10")
        self.assertEqual(len(plan["dailyActivity"]), 14)
        self.assertEqual(plan["dailyActivity"][0]["date"], "2026-10-28")
        self.assertEqual(plan["dailyActivity"][-2], {"date": "2026-11-09", "completedLessons": 1, "xp": 10})
        self.assertEqual(plan["dailyActivity"][-1], {"date": "2026-11-10", "completedLessons": 1, "xp": 40})
        self.assertEqual(sum(item["completedLessons"] for item in plan["dailyActivity"]), 2)

    def test_summer_and_dst_end_keep_local_days_and_bundled_timezone(self):
        self.now = datetime(2026, 10, 25, 22, tzinfo=timezone.utc)
        self.saved_completion(1, datetime(2026, 10, 24, 21, 30, tzinfo=timezone.utc))
        self.saved_completion(2, datetime(2026, 10, 25, 21, 30, tzinfo=timezone.utc))
        with patch("server.ZoneInfo", side_effect=ZoneInfoNotFoundError("no system data")) as zone:
            zone.from_file.side_effect = ZoneInfo.from_file
            plan = self.service.get(self.user_id)
        self.assertEqual(plan["today"], "2026-10-26")
        self.assertEqual(plan["dailyActivity"][-2]["completedLessons"], 2)
        self.assertEqual(plan["dailyActivity"][-1]["completedLessons"], 0)
        self.assertEqual(len({row["date"] for row in plan["dailyActivity"]}), 14)
        self.assertEqual(activity_day(datetime(2026, 7, 9, 21, 30, tzinfo=timezone.utc)).isoformat(), "2026-07-10")

    def test_goal_drives_estimates_without_time_or_unlock_claims(self):
        status, _, _ = self.client.mutate("PUT", "/api/profile", {"goalMinutes": 15, "reason": "travel"})
        self.assertEqual(status, 200)
        plan = self.service.get(self.user_id)
        suggested = plan["suggestedSession"]
        self.assertEqual(suggested["goalMinutes"], 15)
        self.assertEqual(suggested["estimatedLessonMinutes"], 15)
        self.assertEqual([row["id"] for row in suggested["lessons"]], ["lesson-1", "lesson-2", "lesson-3"])
        self.assertFalse(plan["units"][0]["lessons"][1]["unlocked"])
        self.assertIn("δεν πιστοποιεί", plan["notice"])
        self.assertIn("Δεν μετράμε", suggested["note"])
        self.assertNotIn("studiedMinutes", json.dumps(plan))

    def test_service_only_reads_existing_records_and_deleted_user_is_rejected(self):
        self.complete(1)
        with self.state.db() as db:
            before = list(db.iterdump())
        self.service.get(self.user_id)
        self.service.get(self.user_id)
        with self.state.db() as db:
            self.assertEqual(list(db.iterdump()), before)
            db.execute("DELETE FROM users WHERE id=?", (self.user_id,))
        with self.assertRaises(ServiceError) as error:
            self.service.get(self.user_id)
        self.assertEqual(error.exception.status, 401)


class StudyPlanDevelopmentHTTPTests(unittest.TestCase):
    def test_tcp_transport_uses_same_private_route(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"ACADEMY_MAIL_MODE": "disabled", "ACADEMY_AI_PROVIDER": "disabled"}):
                server = create_server("127.0.0.1", 0, Path(directory) / "academy.sqlite3")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = Client(server.server_port)
                self.assertEqual(client.request("GET", "/api/study-plan")[0], 401)
                self.assertEqual(client.register()[0], 200)
                status, body, headers = client.request("GET", "/api/study-plan")
                self.assertEqual(status, 200)
                self.assertEqual(body["summary"]["totalLessons"], len(server.lessons()))
                self.assertEqual(body["summary"]["xp"], 0)
                self.assertEqual(headers["Cache-Control"], "no-store")
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
