"""Role-protected, versioned curriculum drafts stored outside the source files.

Publication makes a snapshot available to learners; it is not pedagogical
certification. Lessons that have been used cannot be changed under the same ID.
"""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

from app_errors import ServiceError


IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z", re.ASCII)
EXERCISE_TYPES = frozenset({"translate", "gap", "multiple-choice", "word-order", "reading", "listening"})
INSTRUCTIONAL_IMAGES = ("/assets/car.svg", "/assets/book.svg", "/assets/apple.svg")
USAGE_TABLES = ("completions", "lesson_checkpoints", "review_cards", "review_attempts", "assessment_attempt_lessons")
REVIEW_NOTICE = "Η δημοσίευση δεν πιστοποιεί παιδαγωγικό έλεγχο. Έλεγξε το υλικό πριν από τη χρήση."
LOCKED_REASON = "Το μάθημα έχει χρησιμοποιηθεί. Δημιούργησε αντίγραφο με νέο αναγνωριστικό για αλλαγές."


def _now():
    return datetime.now(timezone.utc).isoformat()


def _normalize(value):
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return " ".join(value.rstrip(".!?,;:").split())


def _fields(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ServiceError(400, "Τα πεδία του μαθήματος δεν είναι έγκυρα ή λείπουν υποχρεωτικά πεδία.")


def _text(value, maximum, minimum=1):
    if (not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum
            or any(char in "<>" or (unicodedata.category(char).startswith("C") and char != "\n") for char in value)):
        raise ServiceError(400, "Χρησιμοποίησε απλό κείμενο μέσα στα επιτρεπόμενα όρια, χωρίς HTML ή χαρακτήρες ελέγχου.")
    return value.strip()


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ServiceError(400, "Το αναγνωριστικό χρειάζεται 1–64 μικρά λατινικά γράμματα, αριθμούς ή παύλες.")
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ServiceError(400, "Μία αριθμητική τιμή είναι έξω από τα επιτρεπόμενα όρια.")
    return value


def _text_list(value, minimum, maximum, text_limit):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ServiceError(400, "Η λίστα απαντήσεων, επιλογών ή λέξεων δεν έχει έγκυρο μέγεθος.")
    return [_text(item, text_limit) for item in value]


class ContentAdminService:
    def __init__(self, db_factory, root: Path, base_curriculum_loader):
        self.db_factory = db_factory
        self.root = Path(root)
        self.base_curriculum_loader = base_curriculum_loader
        assets = (self.root / "web" / "assets").resolve()
        self.allowed_images = tuple(path for path in INSTRUCTIONAL_IMAGES
                                    if (candidate := (self.root / "web" / path.lstrip("/")).resolve()).is_relative_to(assets)
                                    and candidate.is_file())

    @staticmethod
    def _table_exists(db, table):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    def initialize(self, db):
        # Do not seed copies of the shipped JSON: only edits create snapshots.
        db.execute("""CREATE TABLE IF NOT EXISTS content_lessons (
            lesson_id TEXT PRIMARY KEY, draft_json TEXT, published_json TEXT,
            revision INTEGER NOT NULL CHECK(revision >= 1), publication_order INTEGER UNIQUE,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT,
            CHECK(draft_json IS NOT NULL OR published_json IS NOT NULL)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS content_audit (
            id INTEGER PRIMARY KEY, actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
            lesson_id TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('create', 'save', 'publish')),
            revision INTEGER NOT NULL, created_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS content_lesson_usage (
            lesson_id TEXT PRIMARY KEY, first_used_at TEXT NOT NULL
        )""")
        self.install_usage_guards(db)

    def install_usage_guards(self, db):
        """Run again after optional feature migrations to guard their new tables.

        The ledger contains no user IDs and survives progress/account deletion,
        so previously learned content cannot later acquire a different meaning.
        """
        for table in USAGE_TABLES:
            if not self._table_exists(db, table):
                continue
            db.execute(f"INSERT OR IGNORE INTO content_lesson_usage SELECT lesson_id, ? FROM {table} WHERE lesson_id IS NOT NULL", (_now(),))
            for event in ("INSERT", "UPDATE OF lesson_id"):
                suffix = "insert" if event == "INSERT" else "update"
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS content_usage_{table}_{suffix}
                    AFTER {event} ON {table} BEGIN
                        INSERT OR IGNORE INTO content_lesson_usage(lesson_id,first_used_at)
                        VALUES(NEW.lesson_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
                    END""")

    @staticmethod
    def _authorize(db, user_id):
        row = db.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None or row["role"] != "admin":
            raise ServiceError(403, "Η διαχείριση είναι διαθέσιμη μόνο σε εξουσιοδοτημένο διαχειριστή.")

    @staticmethod
    def _base_lessons(base):
        return base.get("lessons", []) if isinstance(base, dict) else base

    def public_curriculum(self, base, db=None):
        """Overlay published snapshots without mutating or reordering the base."""
        if db is None:
            with closing(self.db_factory()) as connection:
                return self.public_curriculum(base, connection)
        rows = db.execute("SELECT lesson_id,published_json FROM content_lessons WHERE published_json IS NOT NULL ORDER BY publication_order,lesson_id").fetchall()
        snapshots = {row["lesson_id"]: json.loads(row["published_json"]) for row in rows}
        result = deepcopy(base)
        original = self._base_lessons(result)
        lessons = [snapshots.pop(lesson["id"], lesson) for lesson in original]
        lessons.extend(snapshots.values())
        if isinstance(result, dict):
            result["lessons"] = lessons
            return result
        return lessons

    def _is_used(self, db, lesson_id):
        if db.execute("SELECT 1 FROM content_lesson_usage WHERE lesson_id=?", (lesson_id,)).fetchone():
            return True
        # Defensive fallback if a feature table was created after initialization.
        return any(self._table_exists(db, table)
                   and db.execute(f"SELECT 1 FROM {table} WHERE lesson_id=? LIMIT 1", (lesson_id,)).fetchone()
                   for table in USAGE_TABLES)

    def _record(self, db, lesson_id, base_by_id, row=None):
        if row is None:
            row = db.execute("SELECT * FROM content_lessons WHERE lesson_id=?", (lesson_id,)).fetchone()
        published = json.loads(row["published_json"]) if row and row["published_json"] is not None else deepcopy(base_by_id.get(lesson_id))
        locked = self._is_used(db, lesson_id)
        return {"id": lesson_id, "published": published,
                "draft": json.loads(row["draft_json"]) if row and row["draft_json"] is not None else None,
                "version": row["revision"] if row else 0,
                "canChangeExercises": not locked, "lockedReason": LOCKED_REASON if locked else None}

    def _base_by_id(self):
        return {lesson["id"]: lesson for lesson in self._base_lessons(self.base_curriculum_loader())}

    def list_curriculum(self, user_id):
        with closing(self.db_factory()) as db, db:
            self._authorize(db, user_id)
            base = self._base_by_id()
            rows = db.execute("SELECT * FROM content_lessons ORDER BY COALESCE(publication_order,2147483647),created_at,lesson_id").fetchall()
            by_id = {row["lesson_id"]: row for row in rows}
            ids = list(base) + [row["lesson_id"] for row in rows if row["lesson_id"] not in base]
            return {"lessons": [self._record(db, lesson_id, base, by_id.get(lesson_id)) for lesson_id in ids],
                    "allowedImages": list(self.allowed_images), "contentReviewNotice": REVIEW_NOTICE}

    def overview(self, user_id):
        with closing(self.db_factory()) as db, db:
            self._authorize(db, user_id)
            def count(table):
                return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if self._table_exists(db, table) else 0
            curriculum = self.public_curriculum(self.base_curriculum_loader(), db)
            return {"counts": {"users": count("users"),
                               "students": db.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0],
                               "publishedLessons": len(self._base_lessons(curriculum)),
                               "draftLessons": db.execute("SELECT COUNT(*) FROM content_lessons WHERE draft_json IS NOT NULL").fetchone()[0],
                               "completions": count("completions"), "reviewCards": count("review_cards"),
                               "assessmentAttempts": count("assessment_attempts"), "supportTickets": count("support_tickets")},
                    "contentReviewNotice": REVIEW_NOTICE}

    def validate_lesson(self, data):
        required = {"id", "title", "subtitle", "durationMinutes", "xp", "objective", "cards", "exercises"}
        _fields(data, required, required)
        lesson = {"id": _identifier(data["id"]), "title": _text(data["title"], 120),
                  "subtitle": _text(data["subtitle"], 180), "durationMinutes": _integer(data["durationMinutes"], 1, 60),
                  "xp": _integer(data["xp"], 1, 100), "objective": _text(data["objective"], 500)}
        cards, exercises = data["cards"], data["exercises"]
        if not isinstance(cards, list) or not 1 <= len(cards) <= 12:
            raise ServiceError(400, "Το μάθημα χρειάζεται 1–12 κάρτες.")
        if not isinstance(exercises, list) or not 1 <= len(exercises) <= 20:
            raise ServiceError(400, "Το μάθημα χρειάζεται 1–20 ασκήσεις.")
        lesson["cards"] = []
        for card in cards:
            limits = {"word": 160, "translation": 240, "pronunciation": 240, "example": 600, "exampleTranslation": 900}
            _fields(card, {*limits, "image"}, {"word", "translation"})
            clean = {key: _text(card[key], maximum) for key, maximum in limits.items() if key in card}
            if "image" in card:
                if not isinstance(card["image"], str) or card["image"] not in self.allowed_images:
                    raise ServiceError(400, "Η εικόνα πρέπει να είναι μία από τις διαθέσιμες τοπικές εικόνες.")
                clean["image"] = card["image"]
            lesson["cards"].append(clean)
        lesson["exercises"] = []
        seen = set()
        for exercise in exercises:
            needed = {"id", "type", "prompt", "answer", "acceptedAnswers", "explanation"}
            _fields(exercise, needed | {"hint", "choices", "tokens", "passage", "audioText"}, needed)
            exercise_id = _identifier(exercise["id"])
            if exercise_id in seen:
                raise ServiceError(400, "Κάθε άσκηση χρειάζεται διαφορετικό αναγνωριστικό μέσα στο μάθημα.")
            seen.add(exercise_id)
            kind = exercise["type"]
            if not isinstance(kind, str) or kind not in EXERCISE_TYPES:
                raise ServiceError(400, "Ο τύπος της άσκησης δεν υποστηρίζεται.")
            clean = {"id": exercise_id, "type": kind, "prompt": _text(exercise["prompt"], 1000),
                     "answer": _text(exercise["answer"], 300),
                     "acceptedAnswers": _text_list(exercise["acceptedAnswers"], 1, 12, 300),
                     "explanation": _text(exercise["explanation"], 2000)}
            if clean["answer"] not in clean["acceptedAnswers"]:
                raise ServiceError(400, "Η βασική απάντηση πρέπει να περιλαμβάνεται στις αποδεκτές απαντήσεις.")
            if "hint" in exercise:
                clean["hint"] = _text(exercise["hint"], 500)
            conditional = {"choices": "multiple-choice", "tokens": "word-order", "passage": "reading", "audioText": "listening"}
            if any(key in exercise and kind != expected for key, expected in conditional.items()):
                raise ServiceError(400, "Τα πρόσθετα πεδία δεν αντιστοιχούν στον τύπο της άσκησης.")
            if kind == "multiple-choice":
                clean["choices"] = _text_list(exercise.get("choices"), 2, 8, 300)
                normalized = [_normalize(choice) for choice in clean["choices"]]
                accepted = {_normalize(answer) for answer in clean["acceptedAnswers"]}
                if (len(normalized) != len(set(normalized)) or clean["answer"] not in clean["choices"]
                        or sum(choice in accepted for choice in normalized) != 1):
                    raise ServiceError(400, "Οι επιλογές χρειάζονται μία μοναδική σωστή βασική απάντηση.")
            elif kind == "word-order":
                clean["tokens"] = _text_list(exercise.get("tokens"), 2, 20, 100)
                if (any(any(char.isspace() for char in token) for token in clean["tokens"])
                        or Counter(_normalize(clean["answer"]).split()) != Counter(_normalize(" ".join(clean["tokens"])).split())):
                    raise ServiceError(400, "Οι λέξεις της άσκησης πρέπει να σχηματίζουν τη βασική απάντηση.")
            elif kind == "reading":
                clean["passage"] = _text(exercise.get("passage"), 3000)
            elif kind == "listening":
                clean["audioText"] = _text(exercise.get("audioText"), 1000)
            lesson["exercises"].append(clean)
        if len(json.dumps(lesson, ensure_ascii=False).encode("utf-8")) > 30000:
            raise ServiceError(400, "Το μάθημα είναι πολύ μεγάλο. Χώρισέ το σε μικρότερα μαθήματα.")
        return lesson

    @staticmethod
    def _version(data, allowed):
        _fields(data, allowed, allowed)
        return _integer(data["baseVersion"], 0, 2147483647)

    @staticmethod
    def _check_version(row, expected):
        if expected != (row["revision"] if row else 0):
            raise ServiceError(409, "Το μάθημα άλλαξε από άλλη αποθήκευση. Ανανέωσε τη λίστα πριν συνεχίσεις.")

    @staticmethod
    def _audit(db, user_id, lesson_id, action, revision, at):
        db.execute("INSERT INTO content_audit(actor_id,lesson_id,action,revision,created_at) VALUES(?,?,?,?,?)",
                   (user_id, lesson_id, action, revision, at))

    def save_draft(self, user_id, lesson_id, data, create=False):
        with closing(self.db_factory()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._authorize(db, user_id)
            expected = self._version(data, {"lesson", "baseVersion"})
            lesson = self.validate_lesson(data["lesson"])
            if lesson_id is not None and _identifier(lesson_id) != lesson["id"]:
                raise ServiceError(400, "Το αναγνωριστικό του μαθήματος δεν μπορεί να αλλάξει.")
            lesson_id = lesson["id"]
            base = self._base_by_id()
            row = db.execute("SELECT * FROM content_lessons WHERE lesson_id=?", (lesson_id,)).fetchone()
            exists = row is not None or lesson_id in base
            if create and exists:
                raise ServiceError(409, "Υπάρχει ήδη μάθημα με αυτό το αναγνωριστικό.")
            if not create and not exists:
                raise ServiceError(404, "Το μάθημα δεν βρέθηκε.")
            self._check_version(row, expected)
            record = self._record(db, lesson_id, base, row)
            if not record["canChangeExercises"] and lesson != record["published"]:
                raise ServiceError(409, LOCKED_REASON)
            revision, at = expected + 1, _now()
            snapshot = json.dumps(lesson, ensure_ascii=False, separators=(",", ":"))
            if row:
                db.execute("UPDATE content_lessons SET draft_json=?,revision=?,updated_at=? WHERE lesson_id=?",
                           (snapshot, revision, at, lesson_id))
            else:
                db.execute("INSERT INTO content_lessons(lesson_id,draft_json,revision,created_at,updated_at) VALUES(?,?,?,?,?)",
                           (lesson_id, snapshot, revision, at, at))
            self._audit(db, user_id, lesson_id, "create" if create else "save", revision, at)
            return {"lesson": self._record(db, lesson_id, base)}

    def publish(self, user_id, lesson_id, data):
        with closing(self.db_factory()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._authorize(db, user_id)
            lesson_id = _identifier(lesson_id)
            expected = self._version(data, {"baseVersion"})
            row = db.execute("SELECT * FROM content_lessons WHERE lesson_id=?", (lesson_id,)).fetchone()
            if row is None:
                raise ServiceError(404, "Δεν βρέθηκε αποθηκευμένο προσχέδιο.")
            self._check_version(row, expected)
            if row["draft_json"] is None:
                raise ServiceError(409, "Αποθήκευσε πρώτα ένα προσχέδιο για δημοσίευση.")
            lesson = self.validate_lesson(json.loads(row["draft_json"]))
            if lesson["id"] != lesson_id:
                raise ServiceError(409, "Το αναγνωριστικό του προσχεδίου δεν συμφωνεί με το μάθημα.")
            base = self._base_by_id()
            record = self._record(db, lesson_id, base, row)
            if not record["canChangeExercises"] and lesson != record["published"]:
                raise ServiceError(409, LOCKED_REASON)
            order = row["publication_order"]
            if order is None:
                order = db.execute("SELECT COALESCE(MAX(publication_order),0)+1 FROM content_lessons").fetchone()[0]
            revision, at = expected + 1, _now()
            db.execute("""UPDATE content_lessons SET published_json=?,draft_json=NULL,revision=?,publication_order=?,updated_at=?,published_at=?
                          WHERE lesson_id=?""",
                       (json.dumps(lesson, ensure_ascii=False, separators=(",", ":")), revision, order, at, at, lesson_id))
            self._audit(db, user_id, lesson_id, "publish", revision, at)
            return {"lesson": self._record(db, lesson_id, base)}
