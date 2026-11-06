"""Private, server-graded practice checks using frozen published questions.

These checks measure a small sample of introductory A1 material. They do not
assign a CEFR level, award XP, or change completion/unlock state.
"""

import hashlib
import json
import time
import unicodedata
import uuid
from contextlib import closing
from datetime import datetime, timezone

from app_errors import ServiceError


ATTEMPT_TTL = 24 * 60 * 60
RETAINED_ATTEMPTS = 20
STARTS_PER_HOUR = 10
QUESTION_COUNT = 6
PUBLIC_QUESTION_FIELDS = ("prompt", "type", "choices", "tokens", "passage", "audioText")


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _normalized_answer(answer):
    # Same harmless text normalization as the lesson checker. Grading itself
    # always uses the authoritative callback supplied by the application.
    value = unicodedata.normalize("NFKC", answer).casefold().strip()
    return " ".join(value.rstrip(".!?,;:").split())


class AssessmentService:
    def __init__(self, db_factory, curriculum_fn, answer_matches):
        self.db_factory = db_factory
        self.curriculum_fn = curriculum_fn
        self.answer_matches = answer_matches

    @staticmethod
    def initialize(db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS assessment_attempts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('placement','unit')),
                unit_id TEXT,
                title TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                questions_json TEXT NOT NULL,
                request_hash TEXT,
                result_json TEXT,
                completed_at REAL
            );
            CREATE INDEX IF NOT EXISTS assessment_attempts_user_time
                ON assessment_attempts(user_id, created_at);
            CREATE TABLE IF NOT EXISTS assessment_attempt_lessons (
                attempt_id TEXT NOT NULL REFERENCES assessment_attempts(id) ON DELETE CASCADE,
                lesson_id TEXT NOT NULL,
                PRIMARY KEY(attempt_id, lesson_id)
            );
        """)

    @staticmethod
    def _require_user(db, user_id):
        if not isinstance(user_id, str) or not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise ServiceError(401, "Συνδέσου για να κάνεις τη δοκιμή.")

    def _lessons(self, db=None):
        curriculum = self.curriculum_fn(db=db) if db is not None else self.curriculum_fn()
        lessons = curriculum.get("lessons", []) if isinstance(curriculum, dict) else curriculum
        if not isinstance(lessons, list):
            raise ServiceError(503, "Το υλικό της δοκιμής δεν είναι διαθέσιμο.")
        return lessons

    @staticmethod
    def _units(lessons):
        return [{"id": f"unit-{offset // 3 + 1}",
                 "title": f"Έλεγχος ενότητας {offset // 3 + 1}",
                 "lessonIds": [lesson["id"] for lesson in lessons[offset:offset + 3]]}
                for offset in range(0, len(lessons) - 2, 3)]

    @staticmethod
    def _usable_exercises(lesson):
        return [exercise for exercise in lesson.get("exercises", [])
                if isinstance(exercise, dict) and isinstance(exercise.get("id"), str)
                and isinstance(exercise.get("prompt"), str)
                and any(isinstance(answer, str) and answer for answer in
                        AssessmentService._expected_answers(exercise))]

    @staticmethod
    def _expected_answers(exercise):
        answers = exercise.get("acceptedAnswers", exercise.get("answers", exercise.get("answer", [])))
        return [answers] if isinstance(answers, str) else answers if isinstance(answers, list) else []

    def _unit_questions(self, lessons, unit):
        # Spread questions across all three lessons before taking additional
        # items. Authored lessons may have different exercise counts.
        pools = [(lesson, self._usable_exercises(lesson)) for lesson in lessons
                 if lesson["id"] in unit["lessonIds"]]
        return [(lesson, exercises[index])
                for index in range(max((len(exercises) for _, exercises in pools), default=0))
                for lesson, exercises in pools if index < len(exercises)][:QUESTION_COUNT]

    def _diagnostic_questions(self, lessons):
        # Sample each side of the course independently, so later material is
        # included even when a course grows. Each side contributes three items.
        if len(lessons) < 2:
            return []
        split = len(lessons) // 2
        selected = []
        for half in (lessons[:split], lessons[split:]):
            pools = [(lesson, self._usable_exercises(lesson)) for lesson in half]
            pools = [(lesson, exercises) for lesson, exercises in pools if exercises]
            if not pools:
                return []
            if len(pools) >= 3:
                chosen = [pools[round(index * (len(pools) - 1) / 2)] for index in range(3)]
                selected.extend((lesson, exercises[index % len(exercises)])
                                for index, (lesson, exercises) in enumerate(chosen))
            else:
                candidates = [(lesson, exercises[index])
                              for index in range(max(len(exercises) for _, exercises in pools))
                              for lesson, exercises in pools if index < len(exercises)]
                if len(candidates) < 3:
                    return []
                selected.extend(candidates[:3])
        return selected

    def list(self, user_id):
        with closing(self.db_factory()) as db, db:
            self._require_user(db, user_id)
            lessons = self._lessons(db=db)
            completed = {row[0] for row in db.execute("SELECT lesson_id FROM completions WHERE user_id=?", (user_id,))}
            records = db.execute("SELECT kind,unit_id,result_json FROM assessment_attempts WHERE user_id=? AND result_json IS NOT NULL ORDER BY created_at DESC,rowid DESC", (user_id,)).fetchall()
        diagnostic = {"available": len(self._diagnostic_questions(lessons)) == QUESTION_COUNT,
                      "title": "Αρχική δοκιμή A1",
                      "description": "Έξι ερωτήσεις από το εισαγωγικό υλικό A1. Προτείνει επανάληψη, χωρίς πιστοποίηση επιπέδου.",
                      **self._history(records, "placement", None)}
        units = []
        for unit in self._units(lessons):
            learned = all(lesson_id in completed for lesson_id in unit["lessonIds"])
            enough_questions = len(self._unit_questions(lessons, unit)) == QUESTION_COUNT
            units.append({**unit, "available": learned and enough_questions,
                          "unavailableReason": (None if learned and enough_questions else
                              "Ολοκλήρωσε πρώτα και τα τρία μαθήματα της ενότητας." if not learned else
                              "Δεν υπάρχουν ακόμη έξι διαθέσιμες ερωτήσεις για αυτή την ενότητα."),
                          **self._history(records, "unit", unit["id"])})
        return {"diagnostic": diagnostic, "units": units, "retainedAttempts": RETAINED_ATTEMPTS}

    @staticmethod
    def _history(records, kind, unit_id):
        results = [json.loads(row["result_json"]) for row in records
                   if row["kind"] == kind and row["unit_id"] == unit_id]
        return {"bestScore": max((result["score"] for result in results), default=None),
                "lastResult": results[0] if results else None}

    def start(self, user_id, data):
        if not isinstance(data, dict) or data.get("kind") not in ("placement", "unit"):
            raise ServiceError(400, "Διάλεξε αρχική δοκιμή ή έλεγχο ενότητας.")
        kind = data["kind"]
        if kind == "unit" and (not isinstance(data.get("unitId"), str) or len(data["unitId"]) > 128):
            raise ServiceError(400, "Διάλεξε μια έγκυρη ενότητα.")
        if kind == "placement" and data.get("unitId") is not None:
            raise ServiceError(400, "Η αρχική δοκιμή δεν χρειάζεται ενότητα.")
        now = time.time()
        with closing(self.db_factory()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._require_user(db, user_id)
            recent = db.execute("SELECT COUNT(*) FROM assessment_attempts WHERE user_id=? AND created_at>?", (user_id, now - 3600)).fetchone()[0]
            if recent >= STARTS_PER_HOUR:
                raise ServiceError(429, "Έφτασες τις δέκα νέες δοκιμές ανά ώρα. Δοκίμασε αργότερα.")
            # Load after acquiring the write lock: publication cannot race the
            # snapshot and the content usage references inserted below.
            lessons = self._lessons(db=db)
            unit_id = None
            title = "Αρχική δοκιμή A1"
            if kind == "unit":
                unit = next((unit for unit in self._units(lessons) if unit["id"] == data["unitId"]), None)
                if not unit:
                    raise ServiceError(404, "Η ενότητα δεν βρέθηκε.")
                completed = {row[0] for row in db.execute("SELECT lesson_id FROM completions WHERE user_id=?", (user_id,))}
                if not all(lesson_id in completed for lesson_id in unit["lessonIds"]):
                    raise ServiceError(409, "Ολοκλήρωσε πρώτα και τα τρία μαθήματα της ενότητας.")
                unit_id, title = unit["id"], unit["title"]
                selected = self._unit_questions(lessons, unit)
            else:
                selected = self._diagnostic_questions(lessons)
            if len(selected) != QUESTION_COUNT:
                raise ServiceError(503, "Δεν υπάρχουν ακόμη έξι διαθέσιμες ερωτήσεις για αυτή τη δοκιμή.")
            questions = []
            for lesson, exercise in selected:
                snapshot = json.loads(json.dumps(exercise, ensure_ascii=False))
                questions.append({"id": str(uuid.uuid4()), "lessonId": lesson["id"], "exercise": snapshot})
            attempt_id = str(uuid.uuid4())
            expires = now + ATTEMPT_TTL
            db.execute("INSERT INTO assessment_attempts(id,user_id,kind,unit_id,title,created_at,expires_at,questions_json) VALUES(?,?,?,?,?,?,?,?)",
                       (attempt_id, user_id, kind, unit_id, title, now, expires,
                        json.dumps(questions, ensure_ascii=False)))
            db.executemany("INSERT INTO assessment_attempt_lessons(attempt_id,lesson_id) VALUES(?,?)",
                           [(attempt_id, lesson_id) for lesson_id in dict.fromkeys(question["lessonId"] for question in questions)])
            # At most ten starts occur per hour, so the latest twenty rows also
            # retain every row needed for the persistent one-hour rate limit.
            db.execute("DELETE FROM assessment_attempts WHERE user_id=? AND id NOT IN (SELECT id FROM assessment_attempts WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?)",
                       (user_id, user_id, RETAINED_ATTEMPTS))
        return {"attemptId": attempt_id, "kind": kind, "unitId": unit_id, "title": title,
                "expiresAt": _iso(expires), "totalQuestions": len(questions),
                "questions": [self._public_question(question) for question in questions]}

    @staticmethod
    def _public_question(question):
        exercise = question["exercise"]
        public = {field: exercise[field] for field in PUBLIC_QUESTION_FIELDS if field in exercise}
        public.setdefault("type", "translate")
        return {"id": question["id"], **public}

    @staticmethod
    def _submitted_answers(data, questions):
        answers = data.get("answers")
        if not isinstance(answers, list) or not 1 <= len(answers) <= 20 or len(answers) != len(questions):
            raise ServiceError(400, "Στείλε μία απάντηση για κάθε ερώτηση.")
        by_id = {}
        expected_ids = {question["id"] for question in questions}
        for item in answers:
            if not isinstance(item, dict):
                raise ServiceError(400, "Η μορφή των απαντήσεων δεν είναι έγκυρη.")
            question_id, answer = item.get("questionId"), item.get("answer")
            if (not isinstance(question_id, str) or question_id not in expected_ids or question_id in by_id
                    or not isinstance(answer, str) or len(answer) > 1000):
                raise ServiceError(400, "Στείλε μία έγκυρη απάντηση ανά ερώτηση, έως 1.000 χαρακτήρες.")
            by_id[question_id] = answer
        canonical = [[question["id"], _normalized_answer(by_id[question["id"]])] for question in questions]
        fingerprint = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        return by_id, fingerprint

    def submit(self, user_id, data):
        if not isinstance(data, dict) or not isinstance(data.get("attemptId"), str) or len(data["attemptId"]) > 128:
            raise ServiceError(400, "Η δοκιμή δεν είναι έγκυρη.")
        now = time.time()
        with closing(self.db_factory()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._require_user(db, user_id)
            attempt = db.execute("SELECT * FROM assessment_attempts WHERE id=? AND user_id=?", (data["attemptId"], user_id)).fetchone()
            if not attempt:
                raise ServiceError(404, "Η δοκιμή δεν βρέθηκε στον λογαριασμό σου.")
            questions = json.loads(attempt["questions_json"])
            answers, fingerprint = self._submitted_answers(data, questions)
            if attempt["result_json"] is not None:
                if fingerprint != attempt["request_hash"]:
                    raise ServiceError(409, "Αυτή η δοκιμή έχει ήδη βαθμολογηθεί με άλλες απαντήσεις.")
                return json.loads(attempt["result_json"])
            if attempt["expires_at"] <= now:
                raise ServiceError(410, "Η δοκιμή έληξε. Ξεκίνα μια νέα δοκιμή.")
            feedback = []
            for question in questions:
                exercise = question["exercise"]
                expected = exercise.get("answer")
                if not isinstance(expected, str):
                    expected = next(answer for answer in self._expected_answers(exercise) if isinstance(answer, str) and answer)
                feedback.append({"questionId": question["id"], "lessonId": question["lessonId"],
                                 "prompt": exercise["prompt"],
                                 "correct": bool(self.answer_matches(answers[question["id"]], exercise)),
                                 "expectedAnswer": expected, "explanation": exercise.get("explanation", "")})
            correct = sum(item["correct"] for item in feedback)
            score = round(correct * 100 / len(questions))
            recommendation = None
            if attempt["kind"] == "placement":
                first_incorrect = next((item for item in feedback if not item["correct"]), None)
                recommendation = {
                    "lessonId": first_incorrect["lessonId"] if first_incorrect else questions[0]["lessonId"],
                    "message": ("Ξεκίνα την επανάληψη από αυτό το μάθημα του εισαγωγικού A1."
                                if first_incorrect else "Πολύ καλή αρχή σε αυτό το μικρό δείγμα A1. Συνέχισε τα μαθήματα για εξάσκηση.")}
            result = {"attemptId": attempt["id"], "kind": attempt["kind"], "unitId": attempt["unit_id"],
                      "score": score, "correctCount": correct, "totalQuestions": len(questions),
                      "passed": score >= 80 if attempt["kind"] == "unit" else None,
                      "completedAt": _iso(now), "feedback": feedback, "recommendation": recommendation}
            db.execute("UPDATE assessment_attempts SET request_hash=?,result_json=?,completed_at=? WHERE id=? AND user_id=? AND result_json IS NULL",
                       (fingerprint, json.dumps(result, ensure_ascii=False), now, attempt["id"], user_id))
        return result

    def export(self, user_id, db=None):
        if db is None:
            with closing(self.db_factory()) as connection:
                return self.export(user_id, connection)
        self._require_user(db, user_id)
        records = db.execute("SELECT id,kind,unit_id,title,created_at,expires_at,result_json FROM assessment_attempts WHERE user_id=? ORDER BY created_at DESC,rowid DESC", (user_id,)).fetchall()
        # An unfinished attempt exports metadata only. The private snapshot and
        # fingerprint are never part of a learner's downloadable record.
        return {"retainedAttempts": RETAINED_ATTEMPTS,
                "attempts": [{"attemptId": row["id"], "kind": row["kind"], "unitId": row["unit_id"],
                              "title": row["title"], "startedAt": _iso(row["created_at"]),
                              "expiresAt": _iso(row["expires_at"]),
                              "result": json.loads(row["result_json"]) if row["result_json"] else None}
                             for row in records]}
