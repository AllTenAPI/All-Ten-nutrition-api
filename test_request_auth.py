"""Tests for request_auth: Firebase ID-token checks and daily quotas.

Stdlib ``unittest`` on purpose: the deploy image has no pytest. Tokens are
signed with a throwaway RSA key generated per run, so nothing here touches
Google or the network.
"""

from __future__ import annotations

import time
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import crypt
from google.auth import jwt as google_jwt

import request_auth as ra

PROJECT = "all-ten"


def _pem(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


class _Keys:
    """One throwaway signing key, and a second one nobody trusts."""

    def __init__(self) -> None:
        trusted = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.signer = crypt.RSASigner.from_string(_pem(trusted), key_id="good")
        self.certs = {
            "good": trusted.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        }
        stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.stranger = crypt.RSASigner.from_string(_pem(stranger), key_id="good")

    def token(self, signer=None, **overrides) -> str:
        now = int(time.time())
        claims = {
            "iss": f"https://securetoken.google.com/{PROJECT}",
            "aud": PROJECT,
            "sub": "uid-123",
            "iat": now - 10,
            "exp": now + 3600,
            "auth_time": now - 10,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return google_jwt.encode(signer or self.signer, claims).decode("ascii")


KEYS = _Keys()


def verify(token: str) -> str:
    return ra.verify_id_token(token, project_id=PROJECT, certs=KEYS.certs)


class VerifyIdTokenTest(unittest.TestCase):
    def test_a_valid_token_yields_the_uid(self):
        self.assertEqual(verify(KEYS.token()), "uid-123")

    def test_a_token_signed_by_another_key_is_rejected(self):
        with self.assertRaises(ra.AuthError):
            verify(KEYS.token(signer=KEYS.stranger))

    def test_another_projects_token_is_rejected(self):
        with self.assertRaises(ra.AuthError):
            verify(KEYS.token(aud="someone-elses-app"))
        with self.assertRaises(ra.AuthError):
            verify(
                KEYS.token(iss="https://securetoken.google.com/someone-elses-app")
            )

    def test_an_expired_token_is_rejected(self):
        now = int(time.time())
        with self.assertRaises(ra.AuthError):
            verify(KEYS.token(iat=now - 7200, exp=now - 3600))

    def test_a_token_with_no_subject_is_rejected(self):
        with self.assertRaises(ra.AuthError):
            verify(KEYS.token(sub=""))

    def test_garbage_is_rejected_not_crashed_on(self):
        for junk in ["", "not-a-jwt", "a.b.c", "x" * 500]:
            with self.assertRaises(ra.AuthError, msg=junk[:20]):
                verify(junk)

    def test_an_unsigned_token_is_rejected(self):
        # alg "none": a header and claims with no signature at all.
        import base64
        import json

        def b64(obj):
            raw = json.dumps(obj).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        unsigned = ".".join(
            [
                b64({"alg": "none", "kid": "good"}),
                b64({"aud": PROJECT, "sub": "uid-123"}),
                "",
            ]
        )
        with self.assertRaises(ra.AuthError):
            verify(unsigned)


class BearerTokenTest(unittest.TestCase):
    def test_reads_the_bearer_token(self):
        self.assertEqual(ra.bearer_token("Bearer abc.def"), "abc.def")
        self.assertEqual(ra.bearer_token("bearer   abc.def "), "abc.def")

    def test_anything_else_is_no_token(self):
        for value in [None, "", "Basic abc", "Bearer", "Bearer a b"]:
            self.assertIsNone(ra.bearer_token(value), msg=repr(value))


class DailyQuotaTest(unittest.TestCase):
    def setUp(self):
        self.now = 1_000 * 86400.0 + 3600  # 01:00 UTC on some day
        self.quota = ra.DailyQuota(limit=3, clock=lambda: self.now)

    def test_allows_the_limit_then_refuses(self):
        self.assertEqual(
            [self.quota.try_consume("a") for _ in range(4)],
            [True, True, True, False],
        )

    def test_accounts_are_counted_separately(self):
        for _ in range(3):
            self.quota.try_consume("a")
        self.assertTrue(self.quota.try_consume("b"))

    def test_resets_the_next_utc_day(self):
        for _ in range(3):
            self.quota.try_consume("a")
        self.now += 86400
        self.assertTrue(self.quota.try_consume("a"))

    def test_a_refund_gives_one_back(self):
        for _ in range(3):
            self.quota.try_consume("a")
        self.quota.refund("a")
        self.assertTrue(self.quota.try_consume("a"))
        self.assertFalse(self.quota.try_consume("a"))

    def test_a_refund_never_goes_below_zero(self):
        self.quota.refund("a")
        self.assertEqual(
            [self.quota.try_consume("a") for _ in range(4)],
            [True, True, True, False],
        )


if __name__ == "__main__":
    unittest.main()
