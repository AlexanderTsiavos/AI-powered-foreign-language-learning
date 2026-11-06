"""Private, local support threads and in-app notifications.

There is no external delivery or automated support reply. Every admin response
requires an account whose current database role is ``admin``.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import secrets
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app_errors import ServiceError


CATEGORIES = frozenset({"account", "billing", "lesson", "technical", "suggestion"})
STATUSES = frozenset({"open", "answered", "closed"})
IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
PREFERENCE_KEYS = ("reminders", "achievements", "supportReplies")


def utc_now():
    return datetime.now(timezone.utc)


def activity_day(at=None):
    try:
        zone = ZoneInfo("Europe/Athens")
    except ZoneInfoNotFoundError:
        with (Path(__file__).parent / "data/timezones/Europe/Athens").open("rb") as tzfile:
            zone = ZoneInfo.from_file(tzfile, key="Europe/Athens")
    return (at or utc_now()).astimezone(zone).date()


def _fields(data, allowed, required=()):
    if (not isinstance(data, dict) or set(data) - set(allowed) - {"csrfToken"}
            or set(required) - set(data)):
        raise ServiceError(400, "Τα στοιχεία του αιτήματος δεν είναι έγκυρα.")


def _text(value, minimum, maximum, *, multiline=False):
    if not isinstance(value, str):
        raise ServiceError(400, "Συμπλήρωσε απλό κείμενο μέσα στα επιτρεπόμενα όρια.")
    value = value.replace("\r\n", "\n").strip()
    allowed_controls = "\n\t" if multiline else ""
    if (not minimum <= len(value) <= maximum
            or any(char in "<>" or (unicodedata.category(char).startswith("C")
                                   and char not in allowed_controls) for char in value)):
        raise ServiceError(400, "Χρησιμοποίησε απλό κείμενο, χωρίς HTML, μέσα στα επιτρεπόμενα όρια.")
    return value


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ServiceError(400, "Το αναγνωριστικό δεν είναι έγκυρο.")
    return value


class SupportService:
    def __init__(self, db_factory):
        self.db_factory = db_factory

    def initialize(self, db):
        statements = (
            """CREATE TABLE IF NOT EXISTS support_tickets (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                category TEXT NOT NULL CHECK(category IN ('account','billing','lesson','technical','suggestion')),
                subject TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','answered','closed')),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_day TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS support_messages (
                id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
                author_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                author_role TEXT NOT NULL CHECK(author_role IN ('student','admin')),
                message TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS notification_preferences (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                reminders INTEGER NOT NULL DEFAULT 1 CHECK(reminders IN (0,1)),
                achievements INTEGER NOT NULL DEFAULT 1 CHECK(achievements IN (0,1)),
                support_replies INTEGER NOT NULL DEFAULT 1 CHECK(support_replies IN (0,1))
            )""",
            """CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                event_key TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, link TEXT NOT NULL,
                created_at TEXT NOT NULL, read_at TEXT, UNIQUE(user_id,event_key)
            )""",
            "CREATE INDEX IF NOT EXISTS support_tickets_owner ON support_tickets(user_id,created_day)",
            "CREATE INDEX IF NOT EXISTS support_tickets_queue ON support_tickets(status,updated_at)",
            "CREATE INDEX IF NOT EXISTS support_messages_ticket ON support_messages(ticket_id)",
            "CREATE INDEX IF NOT EXISTS notifications_owner ON notifications(user_id,created_at)",
        )
        for statement in statements:
            db.execute(statement)

    @staticmethod
    def _authorize(db, user_id, *, admin=False):
        user = db.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if user is None:
            raise ServiceError(401, "Συνδέσου στον λογαριασμό σου για να συνεχίσεις.")
        if admin and user["role"] != "admin":
            raise ServiceError(403, "Η διαχείριση είναι διαθέσιμη μόνο σε εξουσιοδοτημένο διαχειριστή.")

    @staticmethod
    def _ticket(db, ticket_id, user_id, *, admin=False):
        _identifier(ticket_id)
        row = db.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()
        if row is None or (not admin and row["user_id"] != user_id):
            raise ServiceError(404, "Το αίτημα δεν βρέθηκε.")
        return row

    @staticmethod
    def _summary(row):
        return {"id": row["id"], "category": row["category"], "subject": row["subject"],
                "status": row["status"], "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    def _detail(self, db, row):
        result = self._summary(row)
        result["messages"] = [
            {"id": message["id"], "authorRole": message["author_role"],
             "message": message["message"], "createdAt": message["created_at"]}
            for message in db.execute("SELECT * FROM support_messages WHERE ticket_id=? ORDER BY rowid", (row["id"],))
        ]
        return result

    @staticmethod
    def _preferences(db, user_id):
        row = db.execute("SELECT * FROM notification_preferences WHERE user_id=?", (user_id,)).fetchone()
        return ({"reminders": bool(row["reminders"]), "achievements": bool(row["achievements"]),
                 "supportReplies": bool(row["support_replies"])} if row else dict.fromkeys(PREFERENCE_KEYS, True))

    @staticmethod
    def _notify(db, user_id, event_key, title, body, link, at):
        db.execute("""INSERT OR IGNORE INTO notifications
            (id,user_id,event_key,title,body,link,created_at) VALUES(?,?,?,?,?,?,?)""",
                   (secrets.token_hex(16), user_id, event_key, title, body, link, at))

    @staticmethod
    def _notification(row):
        return {"id": row["id"], "title": row["title"], "body": row["body"], "link": row["link"],
                "readAt": row["read_at"], "createdAt": row["created_at"]}

    def _notification_list(self, db, user_id):
        return {"items": [self._notification(row) for row in db.execute(
                    "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 50", (user_id,))],
                "unreadCount": db.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (user_id,)).fetchone()[0]}

    @staticmethod
    def _message_capacity(db, ticket_id, owner_id):
        if ticket_id and db.execute("SELECT COUNT(*) FROM support_messages WHERE ticket_id=?", (ticket_id,)).fetchone()[0] >= 20:
            raise ServiceError(429, "Το αίτημα έφτασε το όριο των 20 μηνυμάτων.")
        total = db.execute("""SELECT COUNT(*) FROM support_messages m
            JOIN support_tickets t ON t.id=m.ticket_id WHERE t.user_id=?""", (owner_id,)).fetchone()[0]
        if total >= 1000:
            raise ServiceError(429, "Ο λογαριασμός έφτασε το όριο αποθήκευσης μηνυμάτων υποστήριξης.")

    @staticmethod
    def _query_value(query, name, default):
        value = query.get(name, default)
        if isinstance(value, list):
            if len(value) != 1:
                raise ServiceError(400, "Οι παράμετροι της λίστας δεν είναι έγκυρες.")
            value = value[0]
        return value

    def _admin_list(self, db, query):
        if not isinstance(query, dict) or set(query) - {"status", "offset", "limit"}:
            raise ServiceError(400, "Οι παράμετροι της λίστας δεν είναι έγκυρες.")
        status = self._query_value(query, "status", "open")
        if not isinstance(status, str) or status not in STATUSES:
            raise ServiceError(400, "Επίλεξε έγκυρη κατάσταση αιτημάτων.")
        numbers = []
        for key, default, low, high in (("offset", "0", 0, 1000000), ("limit", "20", 1, 50)):
            raw = self._query_value(query, key, default)
            if (type(raw) not in (str, int) or not re.fullmatch(r"[0-9]{1,7}", str(raw))
                    or not low <= int(raw) <= high):
                raise ServiceError(400, "Η σελιδοποίηση δεν είναι έγκυρη.")
            numbers.append(int(raw))
        offset, limit = numbers
        rows = db.execute("SELECT * FROM support_tickets WHERE status=? ORDER BY updated_at DESC,rowid DESC LIMIT ? OFFSET ?", (status, limit, offset))
        return {"tickets": [self._summary(row) for row in rows], "offset": offset, "limit": limit,
                "total": db.execute("SELECT COUNT(*) FROM support_tickets WHERE status=?", (status,)).fetchone()[0]}

    def handle(self, method, path, user_id, data=None, query=None):
        ticket_path = re.fullmatch(r"/api/(admin/)?support/tickets/([0-9a-f]{32})(/messages)?", path)
        bases = {"/api/support/tickets", "/api/admin/support/tickets", "/api/notifications",
                 "/api/notifications/read", "/api/notifications/preferences"}
        if path not in bases and not ticket_path:
            return None
        admin = path.startswith("/api/admin/")
        with closing(self.db_factory()) as db, db:
            # Serialize count-and-insert operations, status changes and daily
            # reminders. Readers never acquire a writer lock unless needed.
            if method != "GET" or path == "/api/notifications":
                db.execute("BEGIN IMMEDIATE")
            self._authorize(db, user_id, admin=admin)
            if path == "/api/support/tickets":
                if method == "GET":
                    return {"tickets": [self._summary(row) for row in db.execute(
                        "SELECT * FROM support_tickets WHERE user_id=? ORDER BY updated_at DESC,rowid DESC", (user_id,))]}
                if method == "POST":
                    return self._create_ticket(db, user_id, data)
            elif path == "/api/admin/support/tickets" and method == "GET":
                return self._admin_list(db, {} if query is None else query)
            elif ticket_path:
                ticket = self._ticket(db, ticket_path.group(2), user_id, admin=admin)
                if method == "GET" and not ticket_path.group(3):
                    return {"ticket": self._detail(db, ticket)}
                if method == "POST" and ticket_path.group(3):
                    return self._reply(db, user_id, ticket, data, admin=admin)
                if method == "PUT" and not ticket_path.group(3) and not admin:
                    _fields(data, {"status"}, {"status"})
                    if not isinstance(data["status"], str) or data["status"] not in {"open", "closed"}:
                        raise ServiceError(400, "Επίλεξε άνοιγμα ή κλείσιμο του αιτήματος.")
                    db.execute("UPDATE support_tickets SET status=?,updated_at=? WHERE id=?", (data["status"], utc_now().isoformat(), ticket["id"]))
                    return {"ticket": self._detail(db, self._ticket(db, ticket["id"], user_id))}
            elif path == "/api/notifications" and method == "GET":
                self._due_reminder(db, user_id)
                return self._notification_list(db, user_id)
            elif path == "/api/notifications/read" and method == "POST":
                return self._read_notifications(db, user_id, data)
            elif path == "/api/notifications/preferences":
                if method == "GET":
                    return {"preferences": self._preferences(db, user_id)}
                if method == "PUT":
                    _fields(data, PREFERENCE_KEYS, PREFERENCE_KEYS)
                    if any(type(data[key]) is not bool for key in PREFERENCE_KEYS):
                        raise ServiceError(400, "Οι προτιμήσεις χρειάζονται τιμές αληθές ή ψευδές.")
                    db.execute("""INSERT INTO notification_preferences VALUES(?,?,?,?)
                        ON CONFLICT(user_id) DO UPDATE SET reminders=excluded.reminders,
                        achievements=excluded.achievements,support_replies=excluded.support_replies""",
                               (user_id, int(data["reminders"]), int(data["achievements"]), int(data["supportReplies"])))
                    return {"preferences": self._preferences(db, user_id)}
            raise ServiceError(405, "Αυτή η ενέργεια δεν υποστηρίζεται σε αυτή τη διαδρομή.")

    def _create_ticket(self, db, user_id, data):
        _fields(data, {"category", "subject", "message"}, {"category", "subject", "message"})
        category = data["category"]
        if not isinstance(category, str) or category not in CATEGORIES:
            raise ServiceError(400, "Επίλεξε έγκυρη κατηγορία αιτήματος.")
        subject = _text(data["subject"], 5, 120)
        message = _text(data["message"], 10, 4000, multiline=True)
        now = utc_now()
        day = activity_day(now).isoformat()
        if db.execute("SELECT COUNT(*) FROM support_tickets WHERE user_id=? AND created_day=?", (user_id, day)).fetchone()[0] >= 5:
            raise ServiceError(429, "Μπορείς να ανοίγεις έως 5 νέα αιτήματα την ημέρα, με ώρα Αθήνας.")
        self._message_capacity(db, None, user_id)
        ticket_id, at = secrets.token_hex(16), now.isoformat()
        db.execute("INSERT INTO support_tickets VALUES(?,?,?,?,?,?,?,?)", (ticket_id, user_id, category, subject, "open", at, at, day))
        db.execute("INSERT INTO support_messages VALUES(?,?,?,?,?,?)", (secrets.token_hex(16), ticket_id, user_id, "student", message, at))
        return {"ticket": self._detail(db, self._ticket(db, ticket_id, user_id))}

    def _reply(self, db, user_id, ticket, data, *, admin=False):
        _fields(data, {"message"}, {"message"})
        message = _text(data["message"], 10, 4000, multiline=True)
        if ticket["status"] == "closed":
            raise ServiceError(409, "Το αίτημα είναι κλειστό. Ο ιδιοκτήτης μπορεί να το ανοίξει ξανά.")
        self._message_capacity(db, ticket["id"], ticket["user_id"])
        message_id, at = secrets.token_hex(16), utc_now().isoformat()
        db.execute("INSERT INTO support_messages VALUES(?,?,?,?,?,?)", (message_id, ticket["id"], user_id, "admin" if admin else "student", message, at))
        db.execute("UPDATE support_tickets SET status=?,updated_at=? WHERE id=?", ("answered" if admin else "open", at, ticket["id"]))
        if admin and self._preferences(db, ticket["user_id"])["supportReplies"]:
            self._notify(db, ticket["user_id"], "support:" + message_id, "Απάντηση στο αίτημά σου",
                         "Υπάρχει νέα απάντηση στο αίτημα: " + ticket["subject"], "#support/" + ticket["id"], at)
        return {"ticket": self._detail(db, self._ticket(db, ticket["id"], user_id, admin=admin))}

    def _due_reminder(self, db, user_id):
        if not self._preferences(db, user_id)["reminders"]:
            return
        now = utc_now()
        # SQLite compares instants (including UTC offsets), rather than relying
        # on ISO text ordering, for review schedules upgraded from older data.
        due = db.execute("SELECT COUNT(*) FROM review_cards WHERE user_id=? AND julianday(due_at)<=julianday(?)", (user_id, now.isoformat())).fetchone()[0]
        if due:
            self._notify(db, user_id, "review:" + activity_day(now).isoformat(), "Ώρα για μια μικρή επανάληψη",
                         f"Έχεις {due} ασκήσεις έτοιμες για επανάληψη.", "#review", now.isoformat())

    def _read_notifications(self, db, user_id, data):
        _fields(data, {"ids", "all"})
        keys = set(data) - {"csrfToken"}
        at = utc_now().isoformat()
        if keys == {"all"} and data["all"] is True:
            db.execute("UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE user_id=?", (at, user_id))
        elif keys == {"ids"} and isinstance(data["ids"], list) and 1 <= len(data["ids"]) <= 50:
            ids = list(dict.fromkeys(_identifier(item) for item in data["ids"]))
            placeholders = ",".join("?" for _ in ids)
            owned = db.execute(f"SELECT COUNT(*) FROM notifications WHERE user_id=? AND id IN ({placeholders})", (user_id, *ids)).fetchone()[0]
            if owned != len(ids):
                raise ServiceError(404, "Η ειδοποίηση δεν βρέθηκε.")
            db.execute(f"UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE user_id=? AND id IN ({placeholders})", (at, user_id, *ids))
        else:
            raise ServiceError(400, "Επίλεξε τις ειδοποιήσεις που διάβασες.")
        return self._notification_list(db, user_id)

    def notify_completion(self, db, user_id, lesson):
        """Use the caller's completion transaction; never commit it here."""
        self._authorize(db, user_id)
        if self._preferences(db, user_id)["achievements"]:
            title = lesson.get("title") or lesson["id"]
            self._notify(db, user_id, "completion:" + lesson["id"], "Ολοκλήρωσες ένα μάθημα",
                         f"Μπράβο! Ολοκλήρωσες το μάθημα «{title}».", "#dashboard", utc_now().isoformat())

    def export_data(self, db, user_id):
        self._authorize(db, user_id)
        return {"supportTickets": [self._detail(db, row) for row in db.execute(
                    "SELECT * FROM support_tickets WHERE user_id=? ORDER BY created_at,rowid", (user_id,))],
                "notificationPreferences": self._preferences(db, user_id),
                "notifications": [self._notification(row) for row in db.execute(
                    "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at,rowid", (user_id,))]}
