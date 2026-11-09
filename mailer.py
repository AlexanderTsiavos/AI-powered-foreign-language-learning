"""Optional SMTP delivery. Secrets and uncommitted messages stay in memory only."""

from dataclasses import dataclass, field
from email.message import EmailMessage
import ipaddress
import os
import queue
import re
import smtplib
import ssl
import threading
import time
from urllib.parse import urlsplit

from app_errors import ServiceError
from app_origin import canonical_origin
from email_addresses import is_mailbox


UNAVAILABLE = "Η υπηρεσία email δεν είναι διαθέσιμη. Δοκίμασε αργότερα."


def _mailbox(value):
    return is_mailbox(value)


def _origin(value, production):
    try:
        return canonical_origin(value, allow_loopback_http=not production, allow_trailing_slash=True)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, repr=False)
class MailSettings:
    mode: str = "disabled"
    host: str = ""
    port: int = 587
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    sender: str = ""
    origin: str = ""
    configured: bool = False

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        mode = env.get("ACADEMY_MAIL_MODE", "disabled").strip().lower()
        if mode == "disabled":
            return cls()
        if mode != "smtp":
            return cls(mode=mode)
        production = env.get("ACADEMY_ENV", "development").strip().lower() == "production"
        host = env.get("ACADEMY_SMTP_HOST", "").strip()
        username = env.get("ACADEMY_SMTP_USER", "")
        password = env.get("ACADEMY_SMTP_PASSWORD", "")
        sender = env.get("ACADEMY_SMTP_FROM", "").strip()
        origin = _origin(env.get("ACADEMY_APP_ORIGIN", ""), production)
        try:
            port = int(env.get("ACADEMY_SMTP_PORT", "587"))
        except (TypeError, ValueError):
            port = 0
        # These must be raw operator SMTP credentials, never proxy templates.
        def placeholder(value):
            text = value.strip().casefold()
            return (text.startswith(('${', '{{', 'codex-proxy'))
                    or bool(re.fullmatch(r'<[^<>]+>', text))
                    or text in {'your_username', 'your_password', 'replace_me', 'change_me', 'placeholder'})
        credentials = bool(username and password) and not any(placeholder(value) for value in (username, password))
        valid_host = bool(host and len(host) <= 253 and re.fullmatch(r"[A-Za-z0-9.:-]+", host))
        configured = bool(valid_host and port in (465, 587) and credentials and _mailbox(sender) and origin)
        return cls(mode, host, port, username, password, sender, origin or "", configured)


@dataclass(frozen=True, repr=False)
class MailMessage:
    recipient: str
    subject: str
    body: str


class _Reservation:
    """A worker cannot see the message until its database transaction commits."""

    def __init__(self, mailer):
        self._mailer = mailer
        self._ready = threading.Event()
        self._message = None
        self._resolved = False

    def publish(self, message=None):
        if message is not None and not isinstance(message, MailMessage):
            raise TypeError("Expected MailMessage")
        with self._mailer._condition:
            if self._resolved:
                raise ServiceError(503, UNAVAILABLE)
            self._resolved = True
            self._message = message
            self._ready.set()

    def cancel(self):
        with self._mailer._condition:
            if not self._resolved:
                self._resolved = True
                self._ready.set()


class Mailer:
    """Bounded, non-durable, one-attempt delivery queue; disabled by default.

    An accepted job means an attempt was queued, not successful delivery. The
    injectable transport is explicitly limited to tests and never reads a provider.
    """

    def __init__(self, settings=None, *, transport=None, _testing=False):
        if transport is not None and not _testing:
            raise ValueError("Injected mail transports are for tests only")
        self.settings = settings or MailSettings()
        self._transport = transport or self._send_smtp
        self._queue = queue.Queue(maxsize=100)
        self._slots = threading.BoundedSemaphore(100)
        self._condition = threading.Condition()
        self._reservations = set()
        self._closed = False
        self._worker = None
        self._counts = {"sent": 0, "failed": 0, "dummy": 0}

    @classmethod
    def from_env(cls, environ=None):
        return cls(MailSettings.from_env(environ))

    @classmethod
    def for_testing(cls, transport, origin="http://127.0.0.1:8000"):
        return cls(MailSettings(mode="smtp", origin=origin, configured=True),
                   transport=transport, _testing=True)

    def status(self):
        return {"enabled": self.settings.mode == "smtp",
                "configured": bool(self.settings.configured and not self._closed)}

    def require_ready(self):
        status = self.status()
        if not status["enabled"] or not status["configured"]:
            raise ServiceError(503, UNAVAILABLE)

    def link(self, route, token):
        self.require_ready()
        if route not in ("verify-email", "reset-password", "confirm-email-change"):
            raise ValueError("Unknown account link")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
            raise ValueError("Invalid account link")
        return f"{self.settings.origin}/#{route}/{token}"

    def reserve(self):
        self.require_ready()
        with self._condition:
            if self._closed or not self._slots.acquire(blocking=False):
                raise ServiceError(503, UNAVAILABLE)
            reservation = _Reservation(self)
            self._reservations.add(reservation)
            self._queue.put_nowait(reservation)
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name="academy-mail", daemon=True)
                self._worker.start()
            return reservation

    def _run(self):
        while True:
            reservation = self._queue.get()
            if reservation is None:
                self._queue.task_done()
                return
            reservation._ready.wait()
            try:
                if reservation._message is None:
                    with self._condition:
                        self._counts["dummy"] += 1
                else:
                    try:
                        self._transport(reservation._message)
                    except Exception:
                        # Provider exceptions may embed recipient, credentials or
                        # token-bearing bodies. Never log or expose their text.
                        with self._condition:
                            self._counts["failed"] += 1
                    else:
                        with self._condition:
                            self._counts["sent"] += 1
            finally:
                reservation._message = None
                with self._condition:
                    self._reservations.discard(reservation)
                    self._slots.release()
                    self._condition.notify_all()
                self._queue.task_done()

    def _send_smtp(self, message):
        if not _mailbox(message.recipient) or any(c in message.subject for c in "\r\n"):
            raise ValueError("Invalid mail headers")
        email = EmailMessage()
        email["From"] = self.settings.sender
        email["To"] = message.recipient
        email["Subject"] = message.subject
        email.set_content(message.body)
        context = ssl.create_default_context()
        if self.settings.port == 465:
            connection = smtplib.SMTP_SSL(self.settings.host, 465, timeout=10, context=context)
        else:
            connection = smtplib.SMTP(self.settings.host, 587, timeout=10)
        with connection as smtp:
            if self.settings.port == 587:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(self.settings.username, self.settings.password)
            smtp.send_message(email)

    def wait_idle(self, timeout=5):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._reservations:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
        return True

    def close(self, timeout=15):
        with self._condition:
            if not self._closed:
                self._closed = True
                for reservation in self._reservations:
                    reservation.cancel()
                worker = self._worker
            else:
                worker = self._worker
        if worker is not None:
            # Reserved but rolled-back jobs are cancelled. Published jobs drain
            # once, bounded by SMTP timeouts; there are no retry loops.
            self._queue.put(None)
            worker.join(timeout)
        return worker is None or not worker.is_alive()
