"""Read-only learner suggestions from existing lesson and review records.

Durations are authored lesson estimates. This service never measures time,
records browsing, awards XP, changes unlocks, or assigns a CEFR level.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json

from app_errors import ServiceError


NOTICE = ("Οι χρόνοι μαθημάτων είναι εκτιμήσεις του περιεχομένου, όχι μέτρηση "
          "χρόνου μελέτης. Η πρόοδος αφορά αυτό το εισαγωγικό υλικό και δεν "
          "πιστοποιεί επίπεδο CEFR.")


def _instant(value):
    """Accept historic UTC ISO records without letting a damaged row break a page."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def _minutes(lesson):
    value = lesson.get("durationMinutes", 5)
    return value if type(value) is int and 1 <= value <= 60 else 5


class StudyPlanService:
    def __init__(self, db_factory, curriculum_fn, activity_day_fn, clock=None):
        self.db_factory = db_factory
        self.curriculum_fn = curriculum_fn
        self.activity_day = activity_day_fn
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def get(self, user_id):
        # A short, consistent read snapshot is separate from HTTP session touch.
        # No learner records or tracking tables are written by this service.
        with closing(self.db_factory()) as db, db:
            db.execute("BEGIN")
            if (not isinstance(user_id, str)
                    or not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone()):
                raise ServiceError(401, "Συνδέσου για να δεις το πλάνο σου.")
            curriculum = self.curriculum_fn(db=db)
            lessons = curriculum.get("lessons", []) if isinstance(curriculum, dict) else curriculum
            if not isinstance(lessons, list):
                raise ServiceError(503, "Το εκπαιδευτικό υλικό δεν είναι διαθέσιμο.")
            completions = db.execute(
                "SELECT lesson_id,xp,completed_at FROM completions WHERE user_id=?", (user_id,)).fetchall()
            checkpoints = {row["lesson_id"]: row for row in db.execute(
                "SELECT lesson_id,phase,position FROM lesson_checkpoints WHERE user_id=?", (user_id,))}
            review_rows = db.execute(
                """SELECT r.lesson_id,r.exercise_id,r.due_at FROM review_cards r
                   JOIN completions c ON c.user_id=r.user_id AND c.lesson_id=r.lesson_id
                   WHERE r.user_id=?""", (user_id,)).fetchall()
            profile_row = db.execute("SELECT data FROM profiles WHERE user_id=?", (user_id,)).fetchone()

        current = self.clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        today = self.activity_day(current)
        completed = {row["lesson_id"] for row in completions}
        by_id = {lesson["id"]: lesson for lesson in lessons}
        current_completed = completed.intersection(by_id)
        goal = 10
        if profile_row:
            try:
                candidate = json.loads(profile_row["data"]).get("goalMinutes")
                if type(candidate) is int and candidate in (5, 10, 15, 20, 30):
                    goal = candidate
            except (ValueError, TypeError, AttributeError):
                pass

        unlocked = set()
        prerequisites_complete = True
        for lesson in lessons:
            if prerequisites_complete:
                unlocked.add(lesson["id"])
            prerequisites_complete = prerequisites_complete and lesson["id"] in completed

        def brief(lesson):
            checkpoint = checkpoints.get(lesson["id"])
            saved = None
            if checkpoint and lesson["id"] not in completed and lesson["id"] in unlocked:
                phase, index = checkpoint["phase"], checkpoint["position"]
                content = lesson.get("cards" if phase == "cards" else "exercises", [])
                if phase in ("cards", "exercises") and type(index) is int and 0 <= index < len(content):
                    saved = {"phase": phase, "index": index}
            return {"id": lesson["id"], "title": lesson.get("title", lesson["id"]),
                    "durationMinutes": _minutes(lesson), "resume": saved is not None,
                    "checkpoint": saved}

        pending = [lesson for lesson in lessons if lesson["id"] not in completed]
        next_lesson = next((lesson for lesson in pending if lesson["id"] in unlocked), None)
        next_brief = brief(next_lesson) if next_lesson else None
        exercise_ids = {lesson["id"]: {exercise["id"] for exercise in lesson.get("exercises", [])}
                        for lesson in lessons}
        learned_reviews = [row for row in review_rows
                           if row["lesson_id"] in unlocked
                           and row["exercise_id"] in exercise_ids.get(row["lesson_id"], set())]
        due = sum(1 for row in learned_reviews if (instant := _instant(row["due_at"])) is not None
                  and instant <= current)

        if due:
            recommendation = {"kind": "review", "title": "Πρώτα μια μικρή επανάληψη",
                              "description": f"Έχουν προγραμματιστεί {due} ασκήσεις για τώρα. Κάνε πρώτα επανάληψη όσων έχεις ήδη μάθει.",
                              "route": "review", "lessonId": None, "dueCount": due}
        elif next_brief:
            recommendation = {"kind": "lesson", "title": "Συνέχισε από εκεί που έμεινες" if next_brief["resume"] else "Το επόμενο μικρό σου βήμα",
                              "description": ("Υπάρχει αποθηκευμένο σημείο συνέχειας στο μάθημα «" if next_brief["resume"] else "Επόμενο μάθημα: «")
                                  + next_brief["title"] + "».",
                              "route": "lesson/" + next_brief["id"], "lessonId": next_brief["id"], "dueCount": 0}
        else:
            recommendation = {"kind": "complete", "title": "Ολοκλήρωσες το διαθέσιμο υλικό",
                              "description": "Μπορείς να εξασκηθείς ξανά στις μαθημένες ασκήσεις. Η ολοκλήρωση αυτού του υλικού δεν πιστοποιεί επίπεδο.",
                              "route": "review", "lessonId": None, "dueCount": 0}

        suggested = []
        estimated_minutes = 0
        for lesson in pending:
            duration = _minutes(lesson)
            if suggested and estimated_minutes + duration > goal:
                break
            suggested.append(brief(lesson))
            estimated_minutes += duration
            if estimated_minutes >= goal:
                break

        start = today - timedelta(days=13)
        history = {day.isoformat(): {"date": day.isoformat(), "completedLessons": 0, "xp": 0}
                   for offset in range(14) for day in [start + timedelta(days=offset)]}
        for row in completions:
            instant = _instant(row["completed_at"])
            if instant is None or instant > current:
                continue
            day = self.activity_day(instant).isoformat()
            if day in history:
                history[day]["completedLessons"] += 1
                history[day]["xp"] += row["xp"]

        units = []
        for offset in range(0, len(lessons), 3):
            chunk = lessons[offset:offset + 3]
            learned = sum(lesson["id"] in completed for lesson in chunk)
            status = ("completed" if learned == len(chunk) else
                      "available" if any(lesson["id"] in unlocked and lesson["id"] not in completed
                                         for lesson in chunk) else "locked")
            units.append({"id": f"unit-{offset // 3 + 1}", "title": f"Ενότητα {offset // 3 + 1}",
                          "lessonIds": [lesson["id"] for lesson in chunk],
                          "completedLessons": learned, "totalLessons": len(chunk), "status": status,
                          "lessons": [{"id": lesson["id"], "title": lesson.get("title", lesson["id"]),
                                       "completed": lesson["id"] in completed, "unlocked": lesson["id"] in unlocked,
                                       "durationMinutes": _minutes(lesson)} for lesson in chunk]})

        return {"timezone": "Europe/Athens", "today": today.isoformat(), "generatedAt": current.isoformat(),
                "summary": {"completedLessons": len(current_completed), "totalLessons": len(lessons),
                            "xp": sum(row["xp"] for row in completions),
                            "totalExercises": sum(len(lesson.get("exercises", [])) for lesson in lessons),
                            "learnedExercises": sum(len(exercise_ids[key]) for key in current_completed),
                            "completionPercent": round(100 * len(current_completed) / len(lessons)) if lessons else 0,
                            "dueReviews": due, "goalMinutes": goal},
                "nextLesson": next_brief, "recommendation": recommendation,
                "suggestedSession": {"goalMinutes": goal, "lessons": suggested,
                                     "estimatedLessonMinutes": estimated_minutes, "includesReviews": bool(due),
                                     "note": "Πρόταση με βάση τον προσωπικό στόχο και τις εκτιμήσεις διάρκειας μαθημάτων. Δεν μετράμε ενεργά λεπτά. Η επανάληψη δεν έχει εκτίμηση χρόνου. Κάθε επόμενο μάθημα ξεκλειδώνει αφού ολοκληρώσεις το προηγούμενο."},
                "dailyActivity": list(history.values()), "units": units, "notice": NOTICE}
