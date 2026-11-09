"""Bounded anonymous-session creation quotas; no stored raw network addresses."""

from collections import deque
import hashlib
import hmac
import secrets
import threading
import time

from app_errors import ServiceError


class AnonymousSessionQuota:
    def __init__(self, *, per_actor=30, total=300, window=900, clock=time.monotonic):
        self.per_actor, self.total, self.window, self.clock = per_actor, total, window, clock
        self._salt = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._events = deque()
        self._counts = {}

    def consume(self, actor):
        key = hmac.new(self._salt, str(actor).encode('utf-8'), hashlib.sha256).digest()
        now = self.clock()
        with self._lock:
            while self._events and self._events[0][0] <= now - self.window:
                _, expired = self._events.popleft()
                count = self._counts[expired] - 1
                if count:
                    self._counts[expired] = count
                else:
                    del self._counts[expired]
            if len(self._events) >= self.total or self._counts.get(key, 0) >= self.per_actor:
                raise ServiceError(429, 'Πολλές νέες συνδέσεις. Κράτησε τα cookies ή δοκίμασε ξανά σε 15 λεπτά.')
            self._events.append((now, key))
            self._counts[key] = self._counts.get(key, 0) + 1
