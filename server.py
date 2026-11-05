#!/usr/bin/env python3
"""Local pilot server. Python 3.12+, SQLite, no third party runtime packages."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.cookies
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from teacher import TeacherError, TeacherService

ROOT = Path(__file__).resolve().parent
COOKIE = "academy_session"
SESSION_TTL = 7 * 24 * 60 * 60
MAX_BODY = 32 * 1024
REVIEW_INTERVAL_DAYS = (1, 3, 7, 14, 30)
REVIEW_ATTEMPT_LIMIT = 1000
SESSION_TOUCH_SECONDS = 60


def now_iso() -> str:
    return utc_now().isoformat()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def activity_day(at: datetime | None = None) -> date:
    """The Greek pilot uses Athens calendar days; account timezone settings are future work."""
    try:
        local_zone = ZoneInfo("Europe/Athens")
    except ZoneInfoNotFoundError:
        # Windows Python may have no IANA database. Use the unchanged bundled TZif.
        with (ROOT / "data" / "timezones" / "Europe" / "Athens").open("rb") as tzfile:
            local_zone = ZoneInfo.from_file(tzfile, key="Europe/Athens")
    return (at or datetime.now(timezone.utc)).astimezone(local_zone).date()


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
    return f"scrypt${salt}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        _, salt, _ = stored.split("$")
        return hmac.compare_digest(password_hash(password, salt), stored)
    except (ValueError, TypeError):
        return False


def normalize_answer(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return " ".join(value.rstrip(".!?,;:").split())


def accepted_answers(exercise):
    answers = exercise.get("acceptedAnswers", exercise.get("answers", exercise.get("answer", [])))
    return [answers] if isinstance(answers, str) else answers


def answer_matches(answer, exercise) -> bool:
    return (isinstance(answer, str) and len(answer) <= 1000
            and any(normalize_answer(answer) == normalize_answer(expected)
                    for expected in accepted_answers(exercise) if isinstance(expected, str)))


class APIError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


class AcademyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, db_path: Path, root: Path = ROOT, secure_cookies: bool = False):
        self.db_path, self.root = Path(db_path), Path(root)
        self.secure_cookies = secure_cookies
        self.rate_lock = threading.Lock()
        self.auth_attempts: dict[str, list[float]] = {}
        self.dummy_password = password_hash(secrets.token_urlsafe(32))
        self.teacher = TeacherService.from_env()
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.initialize_db()
        super().__init__(address, AcademyHandler)

    def db(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize_db(self):
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                    csrf TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS completions (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    lesson_id TEXT NOT NULL, xp INTEGER NOT NULL, completed_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, lesson_id)
                );
                CREATE TABLE IF NOT EXISTS activities (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    day TEXT NOT NULL, PRIMARY KEY(user_id, day)
                );
                CREATE TABLE IF NOT EXISTS lesson_checkpoints (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    lesson_id TEXT NOT NULL, phase TEXT NOT NULL, position INTEGER NOT NULL,
                    answers TEXT NOT NULL, correct INTEGER NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, lesson_id)
                );
                CREATE TABLE IF NOT EXISTS review_cards (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    lesson_id TEXT NOT NULL, exercise_id TEXT NOT NULL,
                    stage INTEGER NOT NULL, due_at TEXT NOT NULL, last_reviewed TEXT,
                    PRIMARY KEY(user_id, lesson_id, exercise_id)
                );
                CREATE TABLE IF NOT EXISTS review_attempts (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    attempt_id TEXT NOT NULL, lesson_id TEXT NOT NULL, exercise_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, attempt_id)
                );
            """)
            # Retain legacy cookies while adding independent, non-secret device IDs.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
            for column in ("public_id", "created_at", "last_seen_at"):
                if column not in columns:
                    db.execute(f"ALTER TABLE sessions ADD COLUMN {column} TEXT")
            legacy_sessions = db.execute("SELECT * FROM sessions WHERE public_id IS NULL OR created_at IS NULL OR last_seen_at IS NULL").fetchall()
            for row in legacy_sessions:
                created_at = row["created_at"] or datetime.fromtimestamp(max(0, row["expires"]-SESSION_TTL), timezone.utc).isoformat()
                db.execute("UPDATE sessions SET public_id=?,created_at=?,last_seen_at=? WHERE token_hash=?",
                           (row["public_id"] or secrets.token_urlsafe(18), created_at,
                            row["last_seen_at"] or created_at, row["token_hash"]))
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS sessions_public_id ON sessions(public_id)")
            # Upgrade existing pilot databases without changing their users or XP.
            completions = db.execute("SELECT user_id,lesson_id,completed_at FROM completions").fetchall()
            if completions:
                lessons = {lesson["id"]: lesson for lesson in self.lessons()}
                for completion in completions:
                    lesson = lessons.get(completion["lesson_id"])
                    if lesson:
                        self.seed_reviews(db, completion["user_id"], lesson, completion["completed_at"])
        os.chmod(self.db_path, 0o600)

    def curriculum(self):
        path = self.root / "data" / "curriculum.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise APIError(503, "Το εκπαιδευτικό υλικό δεν είναι διαθέσιμο.") from exc

    def lessons(self):
        curriculum = self.curriculum()
        return curriculum.get("lessons", []) if isinstance(curriculum, dict) else curriculum

    def exercise_map(self, lesson):
        exercises = lesson.get("exercises", [])
        ids = [exercise.get("id") for exercise in exercises]
        if (not exercises or any(not isinstance(item, str) or not 1 <= len(item) <= 128 for item in ids)
                or len(set(ids)) != len(ids)):
            raise APIError(503, "Το εκπαιδευτικό υλικό χρειάζεται έλεγχο μοναδικών ασκήσεων.")
        return dict(zip(ids, exercises))

    def seed_reviews(self, db, user_id, lesson, completed_at):
        due_at = (datetime.fromisoformat(completed_at) + timedelta(days=1)).isoformat()
        for exercise_id in self.exercise_map(lesson):
            db.execute("INSERT OR IGNORE INTO review_cards VALUES(?,?,?,?,?,NULL)",
                       (user_id, lesson["id"], exercise_id, 0, due_at))

    def rate_limit(self, keys):
        current = time.monotonic()
        with self.rate_lock:
            self.auth_attempts = {key: [t for t in values if current - t < 900]
                                  for key, values in self.auth_attempts.items()
                                  if values and current - values[-1] < 900}
            if any(len(self.auth_attempts.get(key, [])) >= 15 for key in keys):
                raise APIError(429, "Πολλές προσπάθειες. Δοκίμασε ξανά σε 15 λεπτά.")
            for key in keys:
                self.auth_attempts.setdefault(key, []).append(current)


class AcademyHandler(BaseHTTPRequestHandler):
    server: AcademyServer
    server_version = "AcademyPilot/1"
    sys_version = ""

    def log_message(self, fmt, *args):
        # Deliberately omit paths, request bodies, cookies and credentials.
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def do_HEAD(self):
        self.dispatch("HEAD")

    def do_OPTIONS(self):
        self.json_response(405, {"error": "Η μέθοδος δεν υποστηρίζεται."})

    def common_headers(self, is_json=False):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Cache-Control", "no-store" if is_json else "no-cache")
        if self.server.secure_cookies:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        if getattr(self, "new_cookie", None) is not None:
            cookie = f"{COOKIE}={self.new_cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}"
            if self.server.secure_cookies:
                cookie += "; Secure"
            self.send_header("Set-Cookie", cookie)

    def json_response(self, status, payload, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.common_headers(True)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def dispatch(self, method):
        self.new_cookie = None
        try:
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                self.api(method, path)
            elif path == "/data/curriculum.json" and method in ("GET", "HEAD"):
                self.json_response(200, self.server.curriculum())
            elif method in ("GET", "HEAD"):
                self.static(path)
            else:
                raise APIError(405, "Η μέθοδος δεν υποστηρίζεται.")
        except APIError as exc:
            self.json_response(exc.status, {"error": exc.message})
        except TeacherError as exc:
            self.json_response(exc.status, {"error": exc.message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except (sqlite3.Error, OSError, ValueError):
            self.json_response(500, {"error": "Παρουσιάστηκε προσωρινό σφάλμα. Δοκίμασε ξανά."})

    def body(self):
        if self.headers.get_content_type() != "application/json":
            raise APIError(415, "Απαιτούνται δεδομένα JSON.")
        if self.headers.get("Transfer-Encoding"):
            raise APIError(400, "Μη υποστηριζόμενη μεταφορά δεδομένων.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError(400, "Μη έγκυρο μέγεθος αιτήματος.")
        if length <= 0 or length > MAX_BODY:
            raise APIError(413, "Το αίτημα είναι πολύ μεγάλο ή κενό.")
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            raise APIError(400, "Μη έγκυρα δεδομένα.")
        if not isinstance(data, dict):
            raise APIError(400, "Μη έγκυρα δεδομένα.")
        return data

    def session(self, db, create=False):
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie[COOKIE].value if COOKIE in cookie else ""
        except http.cookies.CookieError:
            token = ""
        row = db.execute("SELECT * FROM sessions WHERE token_hash=? AND expires>?",
                         (token_digest(token), time.time())).fetchone() if token else None
        if row is None and create:
            row = self.new_session(db)
        return row

    def new_session(self, db, user_id=None, old_hash=None):
        if old_hash:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (old_hash,))
        db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        digest = token_digest(token)
        created_at = now_iso()
        db.execute("""INSERT INTO sessions(token_hash,user_id,csrf,expires,public_id,created_at,last_seen_at)
                   VALUES(?,?,?,?,?,?,?)""", (digest, user_id, csrf, time.time()+SESSION_TTL,
                                             secrets.token_urlsafe(18), created_at, created_at))
        self.new_cookie = token
        return db.execute("SELECT * FROM sessions WHERE token_hash=?", (digest,)).fetchone()

    def touch_session(self, db, session):
        if session and session["user_id"]:
            last_seen = datetime.fromisoformat(session["last_seen_at"])
            current = utc_now()
            if (current-last_seen).total_seconds() >= SESSION_TOUCH_SECONDS:
                db.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (current.isoformat(), session["token_hash"]))

    def session_metadata(self, db, session):
        rows = db.execute("SELECT public_id,token_hash,created_at,last_seen_at,expires FROM sessions WHERE user_id=? AND expires>? ORDER BY created_at DESC,public_id",
                          (session["user_id"], time.time()))
        return [{"id": row["public_id"], "current": row["token_hash"] == session["token_hash"],
                 "createdAt": row["created_at"], "lastSeenAt": row["last_seen_at"],
                 "expiresAt": datetime.fromtimestamp(row["expires"], timezone.utc).isoformat()}
                for row in rows]

    def require_user(self, session):
        if session is None or not session["user_id"]:
            raise APIError(401, "Χρειάζεται να συνδεθείς πρώτα.")
        return session["user_id"]

    def protect_mutation(self, session, data):
        origin = self.headers.get("Origin")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise APIError(403, "Το αίτημα απορρίφθηκε για λόγους ασφαλείας.")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or parsed.netloc != self.headers.get("Host"):
                raise APIError(403, "Το αίτημα απορρίφθηκε για λόγους ασφαλείας.")
        token = data.get("csrfToken", self.headers.get("X-CSRF-Token", ""))
        if not session or not isinstance(token, str) or not token.isascii() or not hmac.compare_digest(token, session["csrf"]):
            raise APIError(403, "Η συνεδρία έληξε. Ανανέωσε τη σελίδα και δοκίμασε ξανά.")

    def payload(self, db, session):
        user_id = session["user_id"]
        user = db.execute("SELECT id,name,email FROM users WHERE id=?", (user_id,)).fetchone() if user_id else None
        profile = db.execute("SELECT data FROM profiles WHERE user_id=?", (user_id,)).fetchone() if user_id else None
        completions = db.execute("SELECT lesson_id,xp,completed_at FROM completions WHERE user_id=? ORDER BY completed_at", (user_id,)).fetchall() if user_id else []
        activities = db.execute("SELECT day FROM activities WHERE user_id=? ORDER BY day DESC", (user_id,)).fetchall() if user_id else []
        days = {r["day"] for r in activities}
        today = activity_day()
        cursor = today if today.isoformat() in days else today - timedelta(days=1)
        streak = 0
        while cursor.isoformat() in days:
            streak += 1
            cursor -= timedelta(days=1)
        return {"user": dict(user) if user else None,
                "profile": json.loads(profile["data"]) if profile else None,
                "progress": {"completedLessons": [r["lesson_id"] for r in completions],
                             "xp": sum(r["xp"] for r in completions), "streak": streak,
                             "lastActivity": activities[0]["day"] if activities else None},
                "csrfToken": session["csrf"]}

    def api(self, method, path):
        if method == "GET" and path == "/api/health":
            with self.server.db() as db:
                db.execute("SELECT 1").fetchone()
            self.json_response(200, {"status": "ok", "teacherMode": "scripted-demo"})
            return
        if method == "GET" and path == "/api/curriculum":
            self.json_response(200, self.server.curriculum())
            return
        response_headers = None
        with self.server.db() as db:
            session = None
            if method == "GET" and path == "/api/session":
                session = self.session(db, create=True)
                result = self.payload(db, session)
            elif method == "GET" and path == "/api/lesson-state":
                session = self.session(db)
                user_id = self.require_user(session)
                query = parse_qs(urlsplit(self.path).query)
                lesson_ids = query.get("lessonId", [])
                if len(lesson_ids) != 1:
                    raise APIError(400, "Επίλεξε ένα μάθημα.")
                lesson = self.find_lesson(db, user_id, lesson_ids[0])
                row = db.execute("SELECT * FROM lesson_checkpoints WHERE user_id=? AND lesson_id=?",
                                 (user_id, lesson["id"])).fetchone()
                result = {"checkpoint": self.checkpoint_payload(row) if row else None}
            elif method == "GET" and path == "/api/review":
                session = self.session(db)
                user_id = self.require_user(session)
                result = self.review_payload(db, user_id)
            elif method == "GET" and path == "/api/account/sessions":
                session = self.session(db)
                self.require_user(session)
                result = {}
            elif method == "GET" and path == "/api/teacher/status":
                session = self.session(db)
                self.require_user(session)
                result = self.server.teacher.status()
            elif method == "GET" and path == "/api/export":
                session = self.session(db)
                user_id = self.require_user(session)
                result = self.payload(db, session)
                result.pop("csrfToken")
                result["exportedAt"] = now_iso()
                result["activityDays"] = [r[0] for r in db.execute("SELECT day FROM activities WHERE user_id=? ORDER BY day", (user_id,))]
                result["lessonResults"] = [dict(r) for r in db.execute("SELECT lesson_id,xp,completed_at FROM completions WHERE user_id=? ORDER BY completed_at", (user_id,))]
                result["lessonCheckpoints"] = [{**self.checkpoint_payload(r), "updatedAt": r["updated_at"]}
                                               for r in db.execute("SELECT * FROM lesson_checkpoints WHERE user_id=? ORDER BY updated_at", (user_id,))]
                result["reviews"] = self.review_payload(db, user_id)["items"]
                result["reviewAttempts"] = [{"attemptId": r["attempt_id"], "lessonId": r["lesson_id"],
                                             "exerciseId": r["exercise_id"], "result": json.loads(r["result"]),
                                             "createdAt": r["created_at"]}
                                            for r in db.execute("SELECT * FROM review_attempts WHERE user_id=? ORDER BY created_at", (user_id,))]
                response_headers = {"Content-Disposition": 'attachment; filename="academy-data.json"'}
            elif method in ("POST", "PUT", "DELETE"):
                data = self.body()
                session = self.session(db)
                self.protect_mutation(session, data)
                result = self.mutate(db, session, method, path, data)
            else:
                raise APIError(404, "Η διαδρομή δεν βρέθηκε.")
            self.touch_session(db, session)
            if method == "GET" and path in ("/api/account/sessions", "/api/export"):
                result["sessions"] = self.session_metadata(db, session)
        self.json_response(200, result, response_headers)

    def mutate(self, db, session, method, path, data):
        if method == "POST" and path in ("/api/register", "/api/login"):
            email = data.get("email", "")
            password = data.get("password", "")
            if not isinstance(email, str) or not isinstance(password, str):
                raise APIError(400, "Συμπλήρωσε έγκυρα στοιχεία σύνδεσης.")
            email = email.strip().casefold()
            self.server.rate_limit((f"ip:{self.client_address[0]}", f"email:{email[:254]}"))
            if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(password) > 256:
                raise APIError(400, "Συμπλήρωσε έγκυρο email και κωδικό.")
            if path == "/api/register":
                name = data.get("name", "")
                if not isinstance(name, str) or not 2 <= len(name.strip()) <= 80 or len(password) < 10:
                    raise APIError(400, "Χρειάζεται όνομα 2–80 χαρακτήρων και κωδικός τουλάχιστον 10 χαρακτήρων.")
                if data.get("ageConfirmed") is not True:
                    raise APIError(400, "Η δοκιμαστική έκδοση απευθύνεται μόνο σε ενηλίκους 18+.")
                user_id = secrets.token_hex(16)
                try:
                    db.execute("INSERT INTO users VALUES(?,?,?,?,?)", (user_id, name.strip(), email, password_hash(password), now_iso()))
                except sqlite3.IntegrityError:
                    raise APIError(409, "Δεν ήταν δυνατή η εγγραφή με αυτά τα στοιχεία. Δοκίμασε σύνδεση.")
            else:
                user = db.execute("SELECT id,password_hash FROM users WHERE email=?", (email,)).fetchone()
                valid = password_matches(password, user["password_hash"] if user else self.server.dummy_password)
                if not user or not valid:
                    raise APIError(401, "Το email ή ο κωδικός δεν είναι σωστός.")
                user_id = user["id"]
            session = self.new_session(db, user_id, session["token_hash"])
            return self.payload(db, session)
        if method == "POST" and path == "/api/logout":
            session = self.new_session(db, old_hash=session["token_hash"])
            return self.payload(db, session)
        user_id = self.require_user(session)
        if method == "POST" and path == "/api/teacher":
            return self.server.teacher.reply(user_id, data, self.server.curriculum())
        if method == "PUT" and path == "/api/account/profile":
            name = data.get("name")
            if not isinstance(name, str) or not 2 <= len(name.strip()) <= 80:
                raise APIError(400, "Το όνομα πρέπει να έχει 2–80 χαρακτήρες.")
            db.execute("UPDATE users SET name=? WHERE id=?", (name.strip(), user_id))
            return self.payload(db, session)
        if method == "POST" and path == "/api/account/password":
            current_password, new_password = data.get("currentPassword"), data.get("newPassword")
            if (not isinstance(current_password, str) or len(current_password) > 256
                    or not isinstance(new_password, str) or not 10 <= len(new_password) <= 256):
                raise APIError(400, "Ο νέος κωδικός πρέπει να έχει 10–256 χαρακτήρες.")
            self.server.rate_limit((f"password-user:{user_id}", f"password-ip:{self.client_address[0]}"))
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT 1 FROM sessions WHERE token_hash=? AND expires>?", (session["token_hash"], time.time())).fetchone()
            if not active:
                raise APIError(401, "Η συνεδρία έληξε. Συνδέσου ξανά.")
            stored = db.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
            if not stored or not password_matches(current_password, stored["password_hash"]):
                raise APIError(401, "Ο τρέχων κωδικός δεν είναι σωστός.")
            if new_password == current_password:
                raise APIError(400, "Επίλεξε διαφορετικό νέο κωδικό.")
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash(new_password), user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            fresh_session = self.new_session(db, user_id)
            return self.payload(db, fresh_session)
        if method == "DELETE" and path == "/api/account/sessions":
            session_id = data.get("sessionId")
            if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{24}", session_id):
                raise APIError(400, "Μη έγκυρη συνεδρία.")
            row = db.execute("SELECT token_hash FROM sessions WHERE public_id=? AND user_id=?", (session_id, user_id)).fetchone()
            if not row:
                raise APIError(404, "Η συνεδρία δεν βρέθηκε.")
            if row["token_hash"] == session["token_hash"]:
                raise APIError(409, "Για την τρέχουσα συσκευή χρησιμοποίησε Αποσύνδεση.")
            db.execute("DELETE FROM sessions WHERE public_id=? AND user_id=?", (session_id, user_id))
            return {"revoked": True}
        if method == "POST" and path == "/api/account/sessions/revoke-others":
            count = db.execute("SELECT COUNT(*) FROM sessions WHERE user_id=? AND token_hash<>? AND expires>?",
                               (user_id, session["token_hash"], time.time())).fetchone()[0]
            db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user_id, session["token_hash"]))
            return {"revoked": count, **self.payload(db, session)}
        if method == "PUT" and path == "/api/profile":
            goal = data.get("goalMinutes", 10)
            reason = data.get("reason", "travel")
            if data.get("nativeLanguage", "el") != "el" or data.get("targetLanguage", "en") != "en":
                raise APIError(400, "Η πρώτη έκδοση υποστηρίζει Ελληνικά προς Αγγλικά.")
            if type(goal) is not int or goal not in (5, 10, 15, 20, 30) or not isinstance(reason, str) or len(reason) > 80:
                raise APIError(400, "Επίλεξε έγκυρο ημερήσιο στόχο.")
            if data.get("ageGroup", "adult") != "adult":
                raise APIError(400, "Η δοκιμαστική έκδοση απευθύνεται μόνο σε ενηλίκους 18+.")
            profile = {"nativeLanguage": "el", "targetLanguage": "en", "goalMinutes": goal,
                       "reason": reason, "ageGroup": "adult", "level": "A1"}
            db.execute("INSERT INTO profiles VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data", (user_id, json.dumps(profile, ensure_ascii=False)))
            return self.payload(db, session)
        if method == "POST" and path == "/api/progress":
            return self.complete_lesson(db, session, data)
        if method == "PUT" and path == "/api/lesson-state":
            return self.save_checkpoint(db, user_id, data)
        if method == "POST" and path == "/api/review":
            return self.review_answer(db, user_id, data)
        if method == "DELETE" and path == "/api/account":
            password = data.get("password", "")
            self.server.rate_limit((f"delete:{user_id}",))
            stored = db.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
            if not isinstance(password, str) or len(password) > 256 or not password_matches(password, stored[0]):
                raise APIError(401, "Ο κωδικός δεν είναι σωστός.")
            db.execute("DELETE FROM users WHERE id=?", (user_id,))
            session = self.new_session(db)
            return self.payload(db, session)
        raise APIError(404, "Η διαδρομή δεν βρέθηκε.")

    def complete_lesson(self, db, session, data):
        lesson_id, answers = data.get("lessonId"), data.get("answers")
        if not isinstance(lesson_id, str) or not isinstance(answers, list) or len(answers) > 100:
            raise APIError(400, "Μη έγκυρες απαντήσεις μαθήματος.")
        # Serialize completion with checkpoint saves and review schedule creation.
        db.execute("BEGIN IMMEDIATE")
        user_id = session["user_id"]
        lesson = self.find_lesson(db, user_id, lesson_id)
        exercises = lesson.get("exercises", [])
        if not exercises or len(answers) != len(exercises):
            raise APIError(400, "Ολοκλήρωσε όλες τις ασκήσεις πριν συνεχίσεις.")
        for answer, exercise in zip(answers, exercises):
            if not answer_matches(answer, exercise):
                raise APIError(422, "Κάποια απάντηση χρειάζεται διόρθωση. Δοκίμασε ξανά.")
        xp = lesson.get("xp", 30)
        xp = xp if type(xp) is int and 0 < xp <= 1000 else 30
        completed_at = now_iso()
        inserted = db.execute("INSERT OR IGNORE INTO completions VALUES(?,?,?,?)", (user_id, lesson_id, xp, completed_at)).rowcount
        if inserted:
            self.server.seed_reviews(db, user_id, lesson, completed_at)
        db.execute("DELETE FROM lesson_checkpoints WHERE user_id=? AND lesson_id=?", (user_id, lesson_id))
        db.execute("INSERT OR IGNORE INTO activities VALUES(?,?)", (user_id, activity_day().isoformat()))
        return self.payload(db, session)

    def find_lesson(self, db, user_id, lesson_id, learned=False):
        if not isinstance(lesson_id, str) or not 1 <= len(lesson_id) <= 128:
            raise APIError(400, "Μη έγκυρο μάθημα.")
        lessons = self.server.lessons()
        lesson = next((item for item in lessons if item.get("id") == lesson_id), None)
        if not lesson:
            raise APIError(404, "Το μάθημα δεν βρέθηκε.")
        completed = {row[0] for row in db.execute("SELECT lesson_id FROM completions WHERE user_id=?", (user_id,))}
        if learned and lesson_id not in completed:
            raise APIError(409, "Ολοκλήρωσε πρώτα το μάθημα για να κάνεις επανάληψη.")
        if any(item["id"] not in completed for item in lessons[:lessons.index(lesson)]):
            raise APIError(409, "Ολοκλήρωσε πρώτα το προηγούμενο μάθημα.")
        return lesson

    @staticmethod
    def checkpoint_payload(row):
        return {"lessonId": row["lesson_id"], "phase": row["phase"], "index": row["position"],
                "answers": json.loads(row["answers"]), "correct": bool(row["correct"])}

    def save_checkpoint(self, db, user_id, data):
        # Last confirmed transition wins. No completion, XP or schedules are created here.
        db.execute("BEGIN IMMEDIATE")
        lesson = self.find_lesson(db, user_id, data.get("lessonId"))
        phase, position = data.get("phase"), data.get("index")
        answers, correct = data.get("answers"), data.get("correct")
        if (phase not in ("cards", "exercises") or type(position) is not int or position < 0
                or not isinstance(answers, list) or type(correct) is not bool):
            raise APIError(400, "Μη έγκυρο σημείο συνέχισης μαθήματος.")
        if phase == "cards":
            if position >= len(lesson.get("cards", [])) or answers or correct:
                raise APIError(400, "Μη έγκυρο σημείο θεωρίας.")
        else:
            exercises = lesson.get("exercises", [])
            if position >= len(exercises) or len(answers) != position + int(correct):
                raise APIError(400, "Μη έγκυρο σημείο ασκήσεων.")
            if any(not answer_matches(answer, exercise) for answer, exercise in zip(answers, exercises)):
                raise APIError(422, "Το σημείο συνέχισης περιέχει απάντηση που χρειάζεται διόρθωση.")
        db.execute("""INSERT INTO lesson_checkpoints VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(user_id,lesson_id) DO UPDATE SET phase=excluded.phase,
                   position=excluded.position, answers=excluded.answers,
                   correct=excluded.correct, updated_at=excluded.updated_at""",
                   (user_id, lesson["id"], phase, position, json.dumps(answers, ensure_ascii=False), int(correct), now_iso()))
        row = db.execute("SELECT * FROM lesson_checkpoints WHERE user_id=? AND lesson_id=?", (user_id, lesson["id"])).fetchone()
        return {"checkpoint": self.checkpoint_payload(row)}

    def review_payload(self, db, user_id):
        lessons = {lesson["id"]: lesson for lesson in self.server.lessons()}
        current = utc_now()
        items = []
        rows = db.execute("""SELECT r.* FROM review_cards r JOIN completions c
                           ON c.user_id=r.user_id AND c.lesson_id=r.lesson_id
                           WHERE r.user_id=? ORDER BY r.due_at,r.lesson_id,r.exercise_id""", (user_id,))
        for row in rows:
            lesson = lessons.get(row["lesson_id"])
            exercise = self.server.exercise_map(lesson).get(row["exercise_id"]) if lesson else None
            if not exercise:
                continue
            item = {"lessonId": row["lesson_id"], "exerciseId": row["exercise_id"],
                          "prompt": exercise.get("prompt", ""), "answer": exercise.get("answer", ""),
                          "acceptedAnswers": accepted_answers(exercise), "type": exercise.get("type", "translate"),
                          "explanation": exercise.get("explanation", ""), "hint": exercise.get("hint", ""),
                          "dueAt": row["due_at"], "stage": row["stage"],
                    "isDue": datetime.fromisoformat(row["due_at"]) <= current}
            item.update({key: exercise[key] for key in ("choices", "tokens", "passage", "audioText") if key in exercise})
            items.append(item)
        return {"items": items, "dueCount": sum(item["isDue"] for item in items)}

    def review_answer(self, db, user_id, data):
        lesson_id, exercise_id = data.get("lessonId"), data.get("exerciseId")
        answer, attempt_id = data.get("answer"), data.get("attemptId")
        if (not isinstance(lesson_id, str) or not 1 <= len(lesson_id) <= 128
                or not isinstance(exercise_id, str) or not 1 <= len(exercise_id) <= 128
                or not isinstance(answer, str) or len(answer) > 1000
                or not isinstance(attempt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", attempt_id)):
            raise APIError(400, "Μη έγκυρη απάντηση επανάληψης.")
        fingerprint = token_digest(json.dumps([lesson_id, exercise_id, normalize_answer(answer)], ensure_ascii=False))
        # Concurrent/retried requests cannot both advance one scheduling transition.
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT request_hash,result FROM review_attempts WHERE user_id=? AND attempt_id=?", (user_id, attempt_id)).fetchone()
        if previous:
            if not hmac.compare_digest(fingerprint, previous["request_hash"]):
                raise APIError(409, "Αυτή η προσπάθεια έχει ήδη χρησιμοποιηθεί με διαφορετική απάντηση.")
            return {**json.loads(previous["result"]), "duplicate": True}
        lesson = self.find_lesson(db, user_id, lesson_id, learned=True)
        exercise = self.server.exercise_map(lesson).get(exercise_id)
        if not exercise:
            raise APIError(404, "Η άσκηση δεν βρέθηκε.")
        card = db.execute("SELECT * FROM review_cards WHERE user_id=? AND lesson_id=? AND exercise_id=?", (user_id, lesson_id, exercise_id)).fetchone()
        if not card:
            raise APIError(409, "Δεν υπάρχει αποθηκευμένη επανάληψη για αυτή την άσκηση.")
        current = utc_now()
        due = datetime.fromisoformat(card["due_at"]) <= current
        correct = answer_matches(answer, exercise)
        stage, due_at = card["stage"], card["due_at"]
        if not correct:
            stage, due_at = 0, (current + timedelta(minutes=10)).isoformat()
        elif due:
            stage = min(stage + 1, len(REVIEW_INTERVAL_DAYS))
            due_at = (current + timedelta(days=REVIEW_INTERVAL_DAYS[stage-1])).isoformat()
        # Early correct practice provides feedback without inflating the memory ladder.
        advanced = not correct or due
        db.execute("UPDATE review_cards SET stage=?,due_at=?,last_reviewed=? WHERE user_id=? AND lesson_id=? AND exercise_id=?",
                   (stage, due_at, current.isoformat(), user_id, lesson_id, exercise_id))
        result = {"correct": correct, "explanation": exercise.get("explanation", ""),
                  "expectedAnswer": exercise.get("answer", ""), "nextDueAt": due_at,
                  "stage": stage, "duplicate": False, "scheduleAdvanced": advanced,
                  "earlyPractice": not due}
        db.execute("INSERT INTO review_attempts VALUES(?,?,?,?,?,?,?)",
                   (user_id, attempt_id, lesson_id, exercise_id, fingerprint, json.dumps(result, ensure_ascii=False), current.isoformat()))
        # Bounded retry history; the latest 1,000 attempts per account retain their results.
        db.execute("""DELETE FROM review_attempts WHERE user_id=? AND attempt_id NOT IN
                   (SELECT attempt_id FROM review_attempts WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?)""",
                   (user_id, user_id, REVIEW_ATTEMPT_LIMIT))
        return result

    def static(self, path):
        decoded = unquote(path)
        if "\x00" in decoded or "\\" in decoded or any(part.startswith(".") for part in decoded.split("/") if part):
            raise APIError(404, "Το αρχείο δεν βρέθηκε.")
        web = (self.server.root / "web").resolve()
        requested = (web / (decoded.lstrip("/") or "index.html")).resolve()
        if not requested.is_relative_to(web) or not requested.is_file():
            raise APIError(404, "Το αρχείο δεν βρέθηκε.")
        body = requested.read_bytes()
        content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        self.send_response(200)
        self.common_headers()
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type in ("application/javascript", "application/json") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def create_server(host="127.0.0.1", port=8000, db_path=None, root=ROOT, secure_cookies=False):
    default_db = str(ROOT.parent / "academy-data" / "academy.sqlite3")
    return AcademyServer((host, port), Path(db_path or os.environ.get("ACADEMY_DB", default_db)), root, secure_cookies)


def main():
    parser = argparse.ArgumentParser(description="AI Language Academy development server")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    server = create_server(args.host, args.port, secure_cookies=os.environ.get("ACADEMY_SECURE_COOKIES") == "1")
    print(f"Academy pilot running on {args.host}:{args.port}; scripted demonstration teacher; Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
