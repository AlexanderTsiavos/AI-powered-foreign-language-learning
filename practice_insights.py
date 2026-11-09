"""Private practice suggestions derived only from retained review results.

This service does not store answers, add tracking, award XP, change schedules or
measure a language level. A missing/expired history item is not evidence that a
learner has mastered it.
"""

from contextlib import closing
from datetime import datetime, timezone
import json

from app_errors import ServiceError


NOTICE = ("Ασκήσεις με τελευταία λανθασμένη απάντηση στο διατηρημένο ιστορικό "
          "επαναλήψεων. Δεν περιλαμβάνονται λάθη πρώτης μελέτης και δεν "
          "πιστοποιείται επίπεδο.")


def _instant(value):
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value)
        return (instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None
                else instant.astimezone(timezone.utc))
    except (ValueError, OverflowError):
        return None


class PracticeInsightsService:
    def __init__(self, db_factory, curriculum_fn, *, attempt_limit=1000, clock=None):
        if type(attempt_limit) is not int or not 1 <= attempt_limit <= 1000:
            raise ValueError("Invalid retained review history limit")
        self.db_factory = db_factory
        self.curriculum_fn = curriculum_fn
        self.attempt_limit = attempt_limit
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def get(self, user_id):
        # Snapshot only existing data. A request never creates an account,
        # history row, progress record or a separate analytics identifier.
        with closing(self.db_factory()) as db, db:
            db.execute("BEGIN")
            if (not isinstance(user_id, str) or not user_id
                    or not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone()):
                raise ServiceError(401, "Συνδέσου για να δεις την εξάσκησή σου.")
            curriculum = self.curriculum_fn(db=db)
            lessons = curriculum.get("lessons", []) if isinstance(curriculum, dict) else curriculum
            if not isinstance(lessons, list):
                raise ServiceError(503, "Το εκπαιδευτικό υλικό δεν είναι διαθέσιμο.")
            # Joining both records ensures suggestions are actionable through
            # the existing review endpoint, even after historical data changes.
            learned = {(row["lesson_id"], row["exercise_id"]) for row in db.execute(
                """SELECT r.lesson_id,r.exercise_id FROM review_cards r
                   JOIN completions c ON c.user_id=r.user_id AND c.lesson_id=r.lesson_id
                   WHERE r.user_id=?""", (user_id,))}
            attempts = db.execute(
                """SELECT rowid AS history_row,lesson_id,exercise_id,result,created_at
                   FROM review_attempts WHERE user_id=?
                   ORDER BY created_at DESC,rowid DESC LIMIT ?""",
                (user_id, self.attempt_limit)).fetchall()

        current = self.clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        published = {(lesson["id"], exercise["id"])
                     for lesson in lessons if isinstance(lesson, dict)
                     and isinstance(lesson.get("id"), str)
                     for exercise in lesson.get("exercises", [])
                     if isinstance(exercise, dict) and isinstance(exercise.get("id"), str)}
        allowed = learned.intersection(published)
        latest = {}
        for row in attempts:
            key = (row["lesson_id"], row["exercise_id"])
            if key not in allowed:
                continue
            at = _instant(row["created_at"])
            if at is None or at > current:
                continue
            try:
                result = json.loads(row["result"])
            except (ValueError, TypeError):
                continue
            if not isinstance(result, dict) or type(result.get("correct")) is not bool:
                continue
            order = (at, row["history_row"])
            if key not in latest or order > latest[key]["order"]:
                latest[key] = {"correct": result["correct"], "order": order}

        wrong = [(key, value) for key, value in latest.items() if not value["correct"]]
        wrong.sort(key=lambda item: item[1]["order"], reverse=True)
        items = [{"lessonId": key[0], "exerciseId": key[1],
                  "lastAnsweredAt": value["order"][0].isoformat()}
                 for key, value in wrong]
        return {"items": items,
                "summary": {"needsPracticeCount": len(items), "reviewedExerciseCount": len(latest)},
                "scope": "retained-review-history", "notice": NOTICE}
