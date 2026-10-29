"""Tests for natural_voice. Stdlib unittest: the deploy image has no pytest.

ElevenLabs is never called: the HTTP opener is injected.
"""

from __future__ import annotations

import base64
import io
import os
import unittest
import urllib.error
from unittest import mock

import natural_voice as nv


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, "x", {}, None)

    return opener


class SpeakTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_returns_the_audio_and_sends_the_text(self):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["headers"] = dict(request.header_items())
            seen["body"] = request.data
            return _Response(b"ID3fake-mp3")

        payload, status = nv.speak({"text": "  I am   disciplined.  "}, opener=opener)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(payload["audio_base64"]), b"ID3fake-mp3")
        self.assertEqual(payload["content_type"], "audio/mpeg")
        self.assertIn(nv.DEFAULT_VOICE_ID, seen["url"])
        self.assertIn(b'"I am disciplined."', seen["body"])
        self.assertEqual(seen["headers"].get("Xi-api-key"), "test-key")

    def test_no_key_is_misconfigured_not_a_crash(self):
        with mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": ""}):
            payload, status = nv.speak({"text": "I am calm."})
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["kind"], "misconfigured")

    def test_empty_and_overlong_text_are_rejected_before_any_call(self):
        def opener(*_):
            raise AssertionError("must not call ElevenLabs")

        for data in [{}, {"text": "   "}, {"text": 5}]:
            _, status = nv.speak(data, opener=opener)
            self.assertEqual(status, 400)
        _, status = nv.speak({"text": "x" * (nv.MAX_TEXT_CHARS + 1)}, opener=opener)
        self.assertEqual(status, 400)

    def test_a_refused_key_falls_back_quietly(self):
        payload, status = nv.speak({"text": "I am calm."}, opener=_http_error(401))
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["kind"], "misconfigured")

    def test_out_of_characters_is_a_rate_limit(self):
        payload, _ = nv.speak({"text": "I am calm."}, opener=_http_error(429))
        self.assertEqual(payload["error"]["kind"], "rate_limit")

    def test_other_failures_are_retryable_upstream_errors(self):
        payload, status = nv.speak({"text": "I am calm."}, opener=_http_error(500))
        self.assertEqual(status, 502)
        self.assertTrue(payload["error"]["retryable"])

        def unreachable(*_args, **_kw):
            raise urllib.error.URLError("down")

        payload, _ = nv.speak({"text": "I am calm."}, opener=unreachable)
        self.assertEqual(payload["error"]["kind"], "connection")

    def test_voice_can_be_chosen_per_deploy(self):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            return _Response(b"mp3")

        with mock.patch.dict(os.environ, {"ELEVENLABS_VOICE_ID": "abc123"}):
            nv.speak({"text": "I am calm."}, opener=opener)
            self.assertEqual(nv.voice_id(), "abc123")
        self.assertIn("/abc123?", seen["url"])


if __name__ == "__main__":
    unittest.main()
