"""Who is calling, and how many photo analyses they have left today.

Every ``/analyze_food`` call costs a Claude vision request, and the service's
URL ships inside the app, so until this module existed anyone who pulled it
out of the binary could run up the bill without limit. The app now sends the
signed-in Stepper's Firebase ID token as ``Authorization: Bearer <token>``.

Two switches, both environment variables on Render:

``REQUIRE_FIREBASE_AUTH``
    Off by default. While off, a request with no token (or a bad one) is still
    served -- builds released before the app sent tokens keep working -- but
    it draws on one small shared daily pool. Turn it on once every tester is
    on a build that sends tokens; from then on such requests get a 401.

``MAX_PHOTO_ANALYSES_PER_DAY``
    Photo analyses per account per UTC day. Default 30: several times a heavy
    day of logging, including retries.

``MAX_ANONYMOUS_ANALYSES_PER_DAY``
    The shared pool for token-less requests while enforcement is off.
    Default 200.

Counts live in memory. A restart or redeploy resets them, which errs toward
letting a Stepper scan; the point is to make abuse expensive to sustain, not
to bill precisely.

Verifying a token needs no secret: Firebase signs ID tokens with Google keys
whose public certificates are published, and the checks below are the ones
Firebase documents for third-party verification.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

from google.auth import exceptions as google_exceptions
from google.auth import jwt as google_jwt

CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "securetoken@system.gserviceaccount.com"
)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "all-ten")
REQUIRED = _env_flag("REQUIRE_FIREBASE_AUTH")
PER_ACCOUNT_LIMIT = _env_int("MAX_PHOTO_ANALYSES_PER_DAY", 30)
ANONYMOUS_LIMIT = _env_int("MAX_ANONYMOUS_ANALYSES_PER_DAY", 200)

# Firebase's own rules for third-party verification allow some clock drift
# between Google's servers and this one.
CLOCK_SKEW_SECONDS = 60


class AuthError(Exception):
    """A token was present but is not a valid ID token for this project."""


def bearer_token(header_value: Optional[str]) -> Optional[str]:
    """The token from an ``Authorization: Bearer <token>`` header, or None."""
    if not header_value:
        return None
    match = re.fullmatch(r"\s*Bearer\s+(\S+)\s*", header_value, re.IGNORECASE)
    return match.group(1) if match else None


class _CertCache:
    """Google's signing certificates, refetched when their max-age runs out."""

    def __init__(self, url: str = CERTS_URL) -> None:
        self._url = url
        self._lock = threading.Lock()
        self._certs: dict[str, str] = {}
        self._expires_at = 0.0

    def get(self) -> dict[str, str]:
        with self._lock:
            if self._certs and time.time() < self._expires_at:
                return self._certs
            with urllib.request.urlopen(self._url, timeout=10) as response:
                certs = json.loads(response.read().decode("utf-8"))
                cache_control = response.headers.get("Cache-Control", "")
            match = re.search(r"max-age=(\d+)", cache_control)
            max_age = int(match.group(1)) if match else 3600
            self._certs = certs
            self._expires_at = time.time() + max_age
            return self._certs


_certs = _CertCache()


def verify_id_token(
    token: str,
    *,
    project_id: str = PROJECT_ID,
    certs: Optional[dict[str, str]] = None,
) -> str:
    """The Firebase uid in [token], or raises [AuthError].

    Checks, per Firebase's documentation for verifying ID tokens without the
    Admin SDK: RS256 signature by a current Google key, audience is this
    project, issuer is this project's securetoken URL, not expired, issued in
    the past, and a non-empty subject (the uid).
    """
    try:
        header = google_jwt.decode_header(token)
    except (ValueError, TypeError) as exc:
        raise AuthError(f"malformed token: {type(exc).__name__}") from None
    if header.get("alg") != "RS256":
        raise AuthError("wrong signing algorithm")

    try:
        claims = google_jwt.decode(
            token,
            certs=certs if certs is not None else _certs.get(),
            audience=project_id,
            clock_skew_in_seconds=CLOCK_SKEW_SECONDS,
        )
    except (ValueError, google_exceptions.GoogleAuthError) as exc:
        raise AuthError(f"rejected: {type(exc).__name__}") from None

    if claims.get("iss") != f"https://securetoken.google.com/{project_id}":
        raise AuthError("wrong issuer")
    uid = claims.get("sub")
    if not isinstance(uid, str) or not uid or len(uid) > 128:
        raise AuthError("missing subject")
    return uid


@dataclass
class DailyQuota:
    """At most [limit] uses per key per UTC day. Thread-safe."""

    limit: int
    clock: Callable[[], float] = time.time
    _counts: dict[str, tuple[int, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _today(self) -> int:
        return int(self.clock() // 86400)

    def try_consume(self, key: str) -> bool:
        """Takes one use. False, taking nothing, when the day's are spent."""
        with self._lock:
            today = self._today()
            day, used = self._counts.get(key, (today, 0))
            if day != today:
                used = 0
            if used >= self.limit:
                return False
            self._counts[key] = (today, used + 1)
            # Yesterday's keys are dead weight; drop them as we go.
            if len(self._counts) > 10000:
                self._counts = {
                    k: v for k, v in self._counts.items() if v[0] == today
                }
            return True

    def refund(self, key: str) -> None:
        """Gives back one use, for a request that never reached the model."""
        with self._lock:
            today = self._today()
            day, used = self._counts.get(key, (today, 0))
            if day == today and used > 0:
                self._counts[key] = (today, used - 1)


per_account = DailyQuota(PER_ACCOUNT_LIMIT)
anonymous_pool = DailyQuota(ANONYMOUS_LIMIT)
ANONYMOUS_KEY = "anonymous"
