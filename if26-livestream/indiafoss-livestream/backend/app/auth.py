"""Operator login for the admin, checked on behalf of the edge (Caddy forward_auth).

Why here and not in Caddy's basic_auth: Caddy has no lockout, and every wrong password
costs a bcrypt hash on the same process that serves video. This gate:
- locks an address out of the admin after too many wrong passwords (viewing is unaffected),
- caps bcrypt work server-wide so a flood of guesses can't eat the CPU,
- remembers a correct login briefly, so the admin's 3 s polling doesn't re-hash every time.
It fails closed: with no password hash configured, nobody gets in.
"""

from __future__ import annotations

import base64
import binascii
from collections import deque
import hashlib
import hmac
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Tuple

logger = logging.getLogger("auth")

MAX_FAILURES = 10            # wrong passwords per address...
FAILURE_WINDOW = 15 * 60     # ...within this many seconds
LOCKOUT_SECONDS = 15 * 60    # then the address is locked out of the admin for this long
MAX_HASH_CHECKS_PER_SECOND = 5
SUCCESS_CACHE_SECONDS = 10 * 60
MAX_TRACKED_ADDRESSES = 10_000


@dataclass
class Decision:
    status: int                  # 200 allow, 401 ask for credentials, 429 locked/busy, 503 misconfigured
    message: str = ""
    retry_after: Optional[int] = None
    newly_locked: bool = False


@dataclass
class _Address:
    failures: Deque[float] = field(default_factory=deque)
    locked_until: float = 0.0


def _default_verify(password: str, hashed: str) -> bool:
    import bcrypt

    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


class LoginGate:
    def __init__(
        self,
        user: Optional[str],
        password_hash: Optional[str],
        verify=_default_verify,
        clock=time.monotonic,
    ) -> None:
        self.user = user or ""
        self.password_hash = password_hash or ""
        self.verify = verify
        self.clock = clock
        self._addresses: Dict[str, _Address] = {}
        self._hash_checks: Deque[float] = deque()
        self._good: Dict[str, float] = {}   # sha256(Authorization header) -> expiry
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.user and self.password_hash)

    def check(self, address: str, authorization: Optional[str]) -> Decision:
        if not self.configured:
            return Decision(503, "Admin login is not configured on the server")

        now = self.clock()
        token = hashlib.sha256((authorization or "").encode()).hexdigest()
        with self._lock:
            # Already logged in (this exact login was verified recently): allowed even if the
            # address is locked, so a guesser on the same venue Wi-Fi can't lock the operator out.
            expiry = self._good.get(token)
            if authorization and expiry and expiry > now:
                return Decision(200)
            entry = self._addresses.get(address)
            if entry and entry.locked_until > now:
                return Decision(429, "Too many wrong passwords. Try again later.", int(entry.locked_until - now) + 1)

        credentials = self._parse(authorization)
        if credentials is None:
            return Decision(401, "Login required")

        with self._lock:
            # Server-wide cap on bcrypt work (not counted as a failure)
            while self._hash_checks and now - self._hash_checks[0] > 1.0:
                self._hash_checks.popleft()
            if len(self._hash_checks) >= MAX_HASH_CHECKS_PER_SECOND:
                return Decision(429, "Too many login attempts right now. Try again in a moment.", 2)
            self._hash_checks.append(now)

        user, password = credentials
        ok = hmac.compare_digest(user.encode(), self.user.encode()) & self.verify(password, self.password_hash)

        with self._lock:
            if ok:
                self._good[token] = now + SUCCESS_CACHE_SECONDS
                self._addresses.pop(address, None)
                if len(self._good) > 1000:
                    self._good = {t: e for t, e in self._good.items() if e > now}
                return Decision(200)

            entry = self._addresses.get(address)
            if entry is None:
                if len(self._addresses) >= MAX_TRACKED_ADDRESSES:
                    self._forget_stale(now)
                entry = self._addresses.setdefault(address, _Address())
            while entry.failures and now - entry.failures[0] > FAILURE_WINDOW:
                entry.failures.popleft()
            entry.failures.append(now)
            if len(entry.failures) >= MAX_FAILURES:
                entry.failures.clear()
                entry.locked_until = now + LOCKOUT_SECONDS
                return Decision(429, "Too many wrong passwords. Try again later.", LOCKOUT_SECONDS, newly_locked=True)
            return Decision(401, "Wrong username or password")

    def _forget_stale(self, now: float) -> None:
        for addr in [a for a, e in self._addresses.items() if e.locked_until <= now and not e.failures]:
            del self._addresses[addr]
        if len(self._addresses) >= MAX_TRACKED_ADDRESSES:
            # Keep the locked ones; drop the rest
            self._addresses = {a: e for a, e in self._addresses.items() if e.locked_until > now}

    @staticmethod
    def _parse(authorization: Optional[str]) -> Optional[Tuple[str, str]]:
        if not authorization or not authorization.lower().startswith("basic "):
            return None
        try:
            raw = base64.b64decode(authorization[6:].strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
        user, sep, password = raw.partition(":")
        return (user, password) if sep else None


gate = LoginGate(os.getenv("ADMIN_USER"), os.getenv("ADMIN_PASSWORD_HASH"))
