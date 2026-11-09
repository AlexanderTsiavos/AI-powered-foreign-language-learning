"""Private learned-card library and idempotent bookmarks, without extra XP."""

from contextlib import closing
from datetime import datetime, timezone

from app_errors import ServiceError


NOTICE = ("Το λεξικό περιλαμβάνει κάρτες από μαθήματα που έχεις ολοκληρώσει. "
          "Η αποθήκευση λέξεων δεν δίνει πόντους και δεν αλλάζει τις επαναλήψεις σου.")
CARD_FIELDS = ("word", "translation", "example", "exampleTranslation")


class LearnerLibraryService:
    def __init__(self, db_factory, curriculum_fn, clock=None):
        self.db_factory = db_factory
        self.curriculum_fn = curriculum_fn
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def initialize(db):
        db.execute("""CREATE TABLE IF NOT EXISTS learner_bookmarks (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            lesson_id TEXT NOT NULL,
            card_index INTEGER NOT NULL CHECK(card_index >= 0),
            created_at TEXT NOT NULL,
            PRIMARY KEY(user_id,lesson_id,card_index)
        )""")

    @staticmethod
    def _require_user(db, user_id):
        if (not isinstance(user_id, str)
                or not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone()):
            raise ServiceError(401, "Συνδέσου για να δεις το προσωπικό λεξικό σου.")

    def _learned_lessons(self, db, user_id):
        curriculum = self.curriculum_fn(db=db)
        lessons = curriculum.get("lessons", []) if isinstance(curriculum, dict) else curriculum
        if not isinstance(lessons, list):
            raise ServiceError(503, "Το εκπαιδευτικό υλικό δεν είναι διαθέσιμο.")
        completed = {row[0] for row in db.execute(
            "SELECT lesson_id FROM completions WHERE user_id=?", (user_id,))}
        return [lesson for lesson in lessons if lesson["id"] in completed]

    def _payload(self, db, user_id):
        self._require_user(db, user_id)
        lessons = self._learned_lessons(db, user_id)
        saved = {(row[0], row[1]) for row in db.execute(
            "SELECT lesson_id,card_index FROM learner_bookmarks WHERE user_id=?", (user_id,))}
        cards = []
        for lesson in lessons:
            for index, card in enumerate(lesson.get("cards", [])):
                item = {"lessonId": lesson["id"], "cardIndex": index,
                        "lessonTitle": lesson.get("title", lesson["id"]),
                        **{field: card.get(field, "") if isinstance(card.get(field, ""), str) else ""
                           for field in CARD_FIELDS},
                        "bookmarked": (lesson["id"], index) in saved}
                pronunciation = card.get("pronunciation")
                if isinstance(pronunciation, str) and pronunciation.strip():
                    item["pronunciation"] = pronunciation
                cards.append(item)
        return {"cards": cards,
                "summary": {"learnedCards": len(cards),
                            "bookmarkedCards": sum(card["bookmarked"] for card in cards),
                            "completedLessons": len(lessons)},
                "notice": NOTICE}

    def get(self, user_id):
        # A consistent read snapshot, with no new browsing or learning records.
        with closing(self.db_factory()) as db, db:
            db.execute("BEGIN")
            return self._payload(db, user_id)

    def set_bookmark(self, user_id, data, *, db):
        if not isinstance(data, dict) or set(data) != {"lessonId", "cardIndex", "bookmarked"}:
            raise ServiceError(400, "Επίλεξε έγκυρη κάρτα και κατάσταση αποθήκευσης.")
        lesson_id, index, bookmarked = data["lessonId"], data["cardIndex"], data["bookmarked"]
        if (not isinstance(lesson_id, str) or not 1 <= len(lesson_id) <= 128
                or type(index) is not int or index < 0 or type(bookmarked) is not bool):
            raise ServiceError(400, "Επίλεξε έγκυρη κάρτα και κατάσταση αποθήκευσης.")
        # Serialize validation and write with account deletion and content changes.
        # The HTTP transport owns this transaction and commits after the payload.
        db.execute("BEGIN IMMEDIATE")
        self._require_user(db, user_id)
        lesson = next((lesson for lesson in self._learned_lessons(db, user_id)
                       if lesson["id"] == lesson_id), None)
        if lesson is None:
            raise ServiceError(409, "Ολοκλήρωσε πρώτα το μάθημα για να αποθηκεύσεις τις λέξεις του.")
        if index >= len(lesson.get("cards", [])):
            raise ServiceError(400, "Η κάρτα δεν βρέθηκε σε αυτό το μάθημα.")
        if bookmarked:
            stamp = self.clock()
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            db.execute("INSERT OR IGNORE INTO learner_bookmarks VALUES(?,?,?,?)",
                       (user_id, lesson_id, index, stamp.astimezone(timezone.utc).isoformat()))
        else:
            db.execute("DELETE FROM learner_bookmarks WHERE user_id=? AND lesson_id=? AND card_index=?",
                       (user_id, lesson_id, index))
        return self._payload(db, user_id)

    @staticmethod
    def export_data(db, user_id):
        return {"bookmarks": [{"lessonId": row[0], "cardIndex": row[1], "createdAt": row[2]}
                              for row in db.execute(
                                  "SELECT lesson_id,card_index,created_at FROM learner_bookmarks "
                                  "WHERE user_id=? ORDER BY created_at,lesson_id,card_index", (user_id,))]}
