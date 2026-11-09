"""One-time account email links with durable quotas and no persisted raw tokens."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app_errors import ServiceError
from email_addresses import normalize_email
from mailer import MailMessage


INVALID_LINK = "Ο σύνδεσμος δεν ισχύει ή έχει λήξει. Ζήτησε νέο σύνδεσμο."
PENDING = "Αν υπάρχει κατάλληλος λογαριασμός, θα επιχειρηθεί αποστολή email με οδηγίες. Έλεγξε και τα ανεπιθύμητα."


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _expiry_text(timestamp):
    try:
        zone = ZoneInfo('Europe/Athens')
    except ZoneInfoNotFoundError:
        with (Path(__file__).parent / 'data/timezones/Europe/Athens').open('rb') as source:
            zone = ZoneInfo.from_file(source, key='Europe/Athens')
    return datetime.fromtimestamp(timestamp, zone).strftime('%d/%m/%Y %H:%M') + ' (ώρα Ελλάδας)'


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _email(value):
    try:
        return normalize_email(value)
    except ValueError:
        raise ServiceError(400, "Συμπλήρωσε έγκυρο email.") from None


class EmailAuthService:
    def __init__(self, db_factory, mailer, password_hash, password_matches, *, clock=time.time):
        self.db_factory = db_factory
        self.mailer = mailer
        self.password_hash = password_hash
        self.password_matches = password_matches
        self.clock = clock

    def initialize(self, db):
        columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        if "email_verified_at" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN email_verified_at TEXT")
        # execute(), rather than executescript(), preserves the caller's migration
        # transaction and does not silently commit another service's changes.
        db.execute("""CREATE TABLE IF NOT EXISTS email_auth_tokens (
            digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            purpose TEXT NOT NULL CHECK(purpose IN ('verify','reset','email-change')),
            source_email TEXT NOT NULL, target_email TEXT NOT NULL,
            password_version TEXT NOT NULL, created_at REAL NOT NULL,
            expires_at REAL NOT NULL, used_at REAL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS email_auth_tokens_user ON email_auth_tokens(user_id,purpose)")
        db.execute("""CREATE TABLE IF NOT EXISTS email_auth_rates (
            scope TEXT NOT NULL, key_hash TEXT NOT NULL, window_start REAL NOT NULL,
            count INTEGER NOT NULL, PRIMARY KEY(scope,key_hash)
        )""")
        db.execute("CREATE TABLE IF NOT EXISTS email_auth_config (name TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO email_auth_config VALUES('rate_salt',?)", (secrets.token_hex(32),))

    def status(self):
        return self.mailer.status()

    def _consume_rate(self, keys, actor):
        now = self.clock()
        with closing(self.db_factory()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            salt = db.execute("SELECT value FROM email_auth_config WHERE name='rate_salt'").fetchone()[0]
            values = [(scope, str(value), 5) for scope, value in keys]
            # One actor quota spans reset/verification/change and different emails.
            values.append(("actor", str(actor)[:256], 10))
            hashed = [(scope, hmac.new(salt.encode(), (scope + ":" + value).encode(), hashlib.sha256).hexdigest(), limit)
                      for scope, value, limit in values]
            for scope, key, limit in hashed:
                row = db.execute("SELECT window_start,count FROM email_auth_rates WHERE scope=? AND key_hash=?", (scope, key)).fetchone()
                if row and row["window_start"] > now - 3600 and row["count"] >= limit:
                    raise ServiceError(429, "Πολλά αιτήματα email. Δοκίμασε ξανά σε μία ώρα.")
            for scope, key, _ in hashed:
                db.execute("""INSERT INTO email_auth_rates VALUES(?,?,?,1)
                    ON CONFLICT(scope,key_hash) DO UPDATE SET
                    count=CASE WHEN window_start<=? THEN 1 ELSE count+1 END,
                    window_start=CASE WHEN window_start<=? THEN excluded.window_start ELSE window_start END""",
                           (scope, key, now, now - 3600, now - 3600))
            db.execute("DELETE FROM email_auth_rates WHERE window_start<?", (now - 86400,))

    def _record(self, db, user, purpose, target):
        token = secrets.token_urlsafe(32)
        now = self.clock()
        ttl = 86400 if purpose == "verify" else 1800
        db.execute("INSERT INTO email_auth_tokens VALUES(?,?,?,?,?,?,?,?,NULL)",
                   (_digest(token), user["id"], purpose, user["email"], target,
                    _digest(user["password_hash"]), now, now + ttl))
        # Retention bounds database growth without discarding usable links.
        db.execute("DELETE FROM email_auth_tokens WHERE expires_at<?", (now - 86400,))
        routes = {"verify": "verify-email", "reset": "reset-password", "email-change": "confirm-email-change"}
        subjects = {"verify": "Επιβεβαίωση email — AI Language Academy",
                    "reset": "Επαναφορά κωδικού — AI Language Academy",
                    "email-change": "Επιβεβαίωση νέου email — AI Language Academy"}
        link = self.mailer.link(routes[purpose], token)
        body = ("Χρησιμοποίησε τον παρακάτω σύνδεσμο στην εφαρμογή:\n\n" + link
                + f"\n\nΟ σύνδεσμος λήγει στις {_expiry_text(now + ttl)} και χρησιμοποιείται μία φορά."
                + "\nΑν δεν έκανες εσύ το αίτημα, αγνόησε αυτό το email. Μην κοινοποιήσεις τον σύνδεσμο.")
        return MailMessage(target, subjects[purpose], body)

    def request_verification(self, user_id, actor=""):
        self.mailer.require_ready()
        self._consume_rate([("user", user_id)], actor)
        reservation = self.mailer.reserve()
        try:
            with closing(self.db_factory()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                if not user:
                    raise ServiceError(401, "Συνδέσου ξανά.")
                message = None if user["email_verified_at"] else self._record(db, user, "verify", user["email"])
            reservation.publish(message)
        except BaseException:
            reservation.cancel()
            raise
        return {"ok": True, "pending": True, "message": PENDING}

    def request_password_reset(self, email, actor=""):
        self.mailer.require_ready()
        email = _email(email)
        self._consume_rate([("email", email)], actor)
        reservation = self.mailer.reserve()
        try:
            with closing(self.db_factory()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                message = self._record(db, user, "reset", user["email"]) if user else None
            # Nonexistent accounts consume the same bounded queue and quotas.
            reservation.publish(message)
        except BaseException:
            reservation.cancel()
            raise
        return {"ok": True, "pending": True, "message": PENDING}

    def request_email_change(self, user_id, current_password, new_email, actor=""):
        self.mailer.require_ready()
        target = _email(new_email)
        self._consume_rate([("user", user_id), ("email", target)], actor)
        if not isinstance(current_password, str) or not 1 <= len(current_password) <= 256:
            raise ServiceError(400, "Συμπλήρωσε τον τρέχοντα κωδικό.")
        reservation = self.mailer.reserve()
        try:
            with closing(self.db_factory()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                if not user or not self.password_matches(current_password, user["password_hash"]):
                    raise ServiceError(401, "Ο τρέχων κωδικός δεν είναι σωστός.")
                if target == user["email"] or db.execute("SELECT 1 FROM users WHERE email=?", (target,)).fetchone():
                    raise ServiceError(409, "Δεν ήταν δυνατή η αλλαγή με αυτά τα στοιχεία.")
                message = self._record(db, user, "email-change", target)
            reservation.publish(message)
        except BaseException:
            reservation.cancel()
            raise
        return {"ok": True, "pending": True, "message": PENDING}

    def _active_token(self, db, token, purpose):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
            raise ServiceError(410, INVALID_LINK)
        row = db.execute("""SELECT t.*,u.email current_email,u.password_hash current_password
            FROM email_auth_tokens t JOIN users u ON u.id=t.user_id
            WHERE t.digest=? AND t.purpose=?""", (_digest(token), purpose)).fetchone()
        if (not row or row["used_at"] is not None or row["expires_at"] <= self.clock()
                or row["source_email"] != row["current_email"]
                or row["password_version"] != _digest(row["current_password"])
                or (purpose != "email-change" and row["target_email"] != row["current_email"])):
            raise ServiceError(410, INVALID_LINK)
        return row

    def verify(self, token):
        with closing(self.db_factory()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = self._active_token(db, token, "verify")
            db.execute("UPDATE users SET email_verified_at=? WHERE id=?", (_iso(self.clock()), row["user_id"]))
            db.execute("UPDATE email_auth_tokens SET used_at=? WHERE user_id=? AND purpose='verify' AND used_at IS NULL",
                       (self.clock(), row["user_id"]))
        return {"ok": True, "message": "Το email επιβεβαιώθηκε. Μπορείς να συνδεθείς στον λογαριασμό σου."}

    def confirm_password_reset(self, token, new_password):
        if not isinstance(new_password, str) or not 10 <= len(new_password) <= 256:
            raise ServiceError(400, "Ο νέος κωδικός πρέπει να έχει 10–256 χαρακτήρες.")
        with closing(self.db_factory()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = self._active_token(db, token, "reset")
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (self.password_hash(new_password), row["user_id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
            db.execute("UPDATE email_auth_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL", (self.clock(), row["user_id"]))
        return {"ok": True, "sessionRevoked": True,
                "message": "Ο κωδικός άλλαξε και όλες οι συσκευές αποσυνδέθηκαν. Συνδέσου με τον νέο κωδικό."}

    def confirm_email_change(self, token):
        with closing(self.db_factory()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = self._active_token(db, token, "email-change")
            try:
                db.execute("UPDATE users SET email=?,email_verified_at=? WHERE id=?",
                           (row["target_email"], _iso(self.clock()), row["user_id"]))
            except sqlite3.IntegrityError as exc:
                raise ServiceError(409, "Δεν ήταν δυνατή η αλλαγή με αυτά τα στοιχεία.") from exc
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
            db.execute("UPDATE email_auth_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL", (self.clock(), row["user_id"]))
            old_email = row["source_email"]
        # A notice contains no link or token and cannot roll back a committed
        # security operation if delivery is disabled, full or unavailable.
        reservation = None
        try:
            reservation = self.mailer.reserve()
            reservation.publish(MailMessage(old_email, "Αλλαγή email — AI Language Academy",
                                           "Το email του λογαριασμού σου άλλαξε μετά από επιβεβαίωση του νέου email. "
                                           "Όλες οι συσκευές αποσυνδέθηκαν. Αν δεν το ζήτησες εσύ, επικοινώνησε με τον διαχειριστή."))
        except ServiceError:
            if reservation:
                reservation.cancel()
        return {"ok": True, "sessionRevoked": True,
                "message": "Το νέο email επιβεβαιώθηκε και όλες οι συσκευές αποσυνδέθηκαν. Συνδέσου με το νέο email."}

    def export_data(self, db, user_id):
        user = db.execute("SELECT email,email_verified_at,password_hash FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return {"emailVerified": False, "pending": []}
        pending = db.execute("""SELECT purpose,created_at,expires_at FROM email_auth_tokens
            WHERE user_id=? AND used_at IS NULL AND expires_at>? AND source_email=? AND password_version=?
            ORDER BY created_at DESC""", (user_id, self.clock(), user["email"], _digest(user["password_hash"]))).fetchall()
        return {"emailVerified": bool(user["email_verified_at"]),
                "pending": [{"purpose": row["purpose"], "createdAt": _iso(row["created_at"]),
                             "expiresAt": _iso(row["expires_at"])} for row in pending]}
