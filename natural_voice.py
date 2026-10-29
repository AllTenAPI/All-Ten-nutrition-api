"""Natural-sounding speech for affirmations, via ElevenLabs.

The app read affirmations with the phone's built-in voice, which the owner
described as unbelievably robotic. This renders one affirmation into an MP3
with an ElevenLabs voice. The app caches every render on the device, so an
affirmation is voiced once and then replays for free, offline, and on a loop
with the phone locked.

Environment variables (set on Render, never in this repo):
  ELEVENLABS_API_KEY        required for this route; without it the route
                            answers ``misconfigured`` and the app falls back
                            to the phone's own voice
  ELEVENLABS_VOICE_ID       optional -- defaults to a calm premade voice
  ELEVENLABS_MODEL_ID       optional -- defaults to eleven_multilingual_v2
  MAX_VOICE_RENDERS_PER_DAY optional -- per account, default 60

Stdlib ``urllib`` on purpose, like the USDA client: no new HTTP dependency.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Callable, Optional

API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# "Rachel", one of ElevenLabs' premade voices: calm and clear. Replaceable
# per deploy with ELEVENLABS_VOICE_ID.
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_MODEL_ID = "eleven_multilingual_v2"

# Affirmations are a sentence or two. The cap keeps one request from spending
# a large share of the monthly character allowance.
MAX_TEXT_CHARS = 400

# Steady and soothing rather than expressive: these are listened to on repeat,
# often while falling asleep.
VOICE_SETTINGS = {
    "stability": 0.6,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
}


def _error(kind: str, message: str, retryable: bool, status: int):
    return (
        {
            "error": {"kind": kind, "message": message, "retryable": retryable},
            "needs_confirmation": False,
            "confirmation_reason": message,
        },
        status,
    )


def is_configured() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())


def speak(
    data: dict,
    *,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> tuple[dict, int]:
    """Renders ``data["text"]`` and returns ``(payload, status)``.

    On success the payload is ``{"audio_base64", "content_type", "voice_id",
    "characters"}``. Every failure uses the API's error envelope, so the app
    parses this route exactly like the others. [opener] is injectable for
    tests; it is ``urllib.request.urlopen`` in production.
    """
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return _error("bad_request", "There is no text to read.", False, 400)
    text = " ".join(text.split())
    if len(text) > MAX_TEXT_CHARS:
        return _error(
            "bad_request",
            f"That is {len(text)} characters; an affirmation can be read up "
            f"to {MAX_TEXT_CHARS}.",
            False,
            400,
        )

    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        return _error(
            "misconfigured",
            "The natural voice is not set up yet, so the phone's own voice "
            "is used.",
            False,
            503,
        )

    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or DEFAULT_VOICE_ID
    model_id = os.environ.get("ELEVENLABS_MODEL_ID", "").strip() or DEFAULT_MODEL_ID

    request = urllib.request.Request(
        API_URL.format(voice_id=voice_id) + "?output_format=mp3_44100_128",
        data=json.dumps(
            {"text": text, "model_id": model_id, "voice_settings": VOICE_SETTINGS}
        ).encode("utf-8"),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )

    try:
        with opener(request, timeout=30) as response:
            audio = response.read()
    except urllib.error.HTTPError as exc:
        # Never echo the response body: it can repeat request details.
        print(f"ElevenLabs HTTP {exc.code}")
        if exc.code in (401, 403):
            return _error(
                "misconfigured",
                "The natural voice key was refused, so the phone's own voice "
                "is used.",
                False,
                503,
            )
        if exc.code == 429:
            return _error(
                "rate_limit",
                "The natural voice is busy or out of characters this month. "
                "The phone's own voice is used for now.",
                True,
                503,
            )
        return _error(
            "upstream_error",
            "The natural voice could not be made just now.",
            True,
            502,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"ElevenLabs unreachable: {type(exc).__name__}")
        return _error(
            "connection",
            "Could not reach the natural voice service.",
            True,
            502,
        )

    if not audio:
        return _error(
            "upstream_error", "The natural voice came back empty.", True, 502
        )

    return (
        {
            "audio_base64": base64.b64encode(audio).decode("ascii"),
            "content_type": "audio/mpeg",
            "voice_id": voice_id,
            "characters": len(text),
        },
        200,
    )


def voice_id() -> Optional[str]:
    """The voice in use, for /health. Never the key."""
    if not is_configured():
        return None
    return os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or DEFAULT_VOICE_ID
