"""Shared HTTP layer for both deploy entrypoints.

``app-render.py`` (Render) is a thin wrapper
around this module, so there is exactly one implementation of both the routing
and the analysis pipeline. Neither platform runs Flask -- this is
``http.server``, matching what was already deployed.

Routes
------
``GET  /``              service banner
``GET  /health``        liveness + configuration status
``GET  /debug``         diagnostics (presence of env vars only, never values)
``POST /analyze_food``  photo analysis -- Claude vision + USDA. Costs LLM tokens.
``POST /search_food``   free-text food search -- USDA only. No LLM call.
``POST /barcode``       UPC/EAN lookup -- USDA Branded, then Open Food Facts.
                        No LLM call.
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import food_lookup
import natural_voice
import nutrition_analyzer
import request_auth

# A base64-encoded 5 MB image is ~6.7 MB; leave headroom for the JSON envelope.
MAX_REQUEST_BYTES = 8 * 1024 * 1024

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}


class NutritionRequestHandler(BaseHTTPRequestHandler):
    # Set by the entrypoint so the banner names the right platform.
    platform = "unknown"

    server_version = "AllTenNutritionAPI/2.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------

    def log_message(self, format, *args):
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {format % args}")

    def _send_json(self, payload: dict, status: int = 200) -> None:
        # Read back by do_POST to refund a photo scan that never reached the
        # model.
        self._last_status = status
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in CORS_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        """Read and parse the request body. Raises ValueError with a message
        safe to return to the client."""
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            raise ValueError("Invalid Content-Length header.") from None

        if length <= 0:
            raise ValueError("Request body is empty.")
        if length > MAX_REQUEST_BYTES:
            raise ValueError(
                f"Request body is {length // 1024} KB; the limit is "
                f"{MAX_REQUEST_BYTES // 1024} KB."
            )

        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Request body is not valid JSON.") from None
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object.")
        return parsed

    # -- routes ------------------------------------------------------------

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            # Says whether sign-in is enforced, so flipping the Render switch
            # can be confirmed from a browser.
            self._send_json(
                nutrition_analyzer.health_payload()
                | {
                    "auth_required": request_auth.REQUIRED,
                    # Whether the ELEVENLABS_API_KEY is set; never the key.
                    "natural_voice": natural_voice.is_configured(),
                }
            )
        elif path == "/debug":
            self._send_json(nutrition_analyzer.debug_payload())
        elif path == "/":
            self._send_json(
                {
                    "service": "All Ten Nutrition API",
                    "status": "live",
                    "platform": self.platform,
                    "analysis": "Claude vision portion estimation + USDA FoodData Central",
                    "analysis_version": nutrition_analyzer.ANALYSIS_VERSION,
                    "endpoints": [
                        "/",
                        "/health",
                        "/debug",
                        "/analyze_food",
                        "/search_food",
                        "/barcode",
                        "/speak",
                    ],
                }
            )
        else:
            self._send_json({"error": "Not found", "path": path}, status=404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/analyze_food":
            handler = self._handle_analyze_food
        elif path == "/search_food":
            handler = self._handle_search_food
        elif path == "/barcode":
            handler = self._handle_barcode
        elif path == "/speak":
            handler = self._handle_speak
        else:
            self._send_json({"error": "Not found", "path": path}, status=404)
            return

        uid = self._caller_uid(path)
        if uid is None and request_auth.REQUIRED:
            # The body is never read, so the connection cannot be reused:
            # its bytes would be parsed as the next request.
            self.close_connection = True
            self._send_error(
                401,
                "auth",
                "Your sign-in could not be confirmed. Sign out and back in, "
                "then try again.",
            )
            return

        try:
            data = self._read_json_body()
        except ValueError as exc:
            self._send_json(
                {
                    "error": {"kind": "bad_request", "message": str(exc), "retryable": False},
                    "needs_confirmation": True,
                    "confirmation_reason": str(exc),
                },
                status=400,
            )
            return

        if path == "/speak":
            # New route, so no older app build calls it: sign-in is required
            # now rather than behind REQUIRE_FIREBASE_AUTH. Every render spends
            # ElevenLabs characters, so each account has its own daily cap.
            if uid is None:
                self._send_error(
                    401,
                    "auth",
                    "Your sign-in could not be confirmed. Sign out and back "
                    "in, then try again.",
                )
                return
            if not request_auth.voice_quota.try_consume(uid):
                self._send_error(
                    429,
                    "rate_limit",
                    "That is today's limit for new voice readings. Saved "
                    "ones still play; new ones use the phone's voice until "
                    "tomorrow.",
                )
                return
            handler(data)
            if getattr(self, "_last_status", 200) != 200:
                # Nothing was rendered, so nothing was spent.
                request_auth.voice_quota.refund(uid)
            return

        if path != "/analyze_food":
            handler(data)
            return

        # Photo analysis is the only route that calls the model, so it is the
        # only one metered.
        quota = request_auth.per_account if uid else request_auth.anonymous_pool
        key = uid or request_auth.ANONYMOUS_KEY
        if not quota.try_consume(key):
            self._send_error(
                429,
                "rate_limit",
                f"You've used today's {quota.limit} photo scans. They reset "
                "tomorrow. You can still search for a food or scan a barcode.",
            )
            return
        handler(data)
        if 400 <= getattr(self, "_last_status", 200) < 500:
            # Rejected before the model ran (bad image, bad mode): no cost.
            quota.refund(key)

    def _caller_uid(self, path: str):
        """The verified Firebase uid of the caller, or None.

        Logs why a request has none, never the token itself, so the logs
        show when every client is sending tokens and enforcement can go on.
        """
        token = request_auth.bearer_token(self.headers.get("Authorization"))
        if token is None:
            print(f"auth: no token on {path}")
            return None
        try:
            return request_auth.verify_id_token(token)
        except request_auth.AuthError as exc:
            print(f"auth: token rejected on {path}: {exc}")
            return None
        except Exception as exc:  # certificate fetch failed, and so on
            print(f"auth: could not verify on {path}: {type(exc).__name__}")
            return None

    def _send_error(self, status: int, kind: str, message: str) -> None:
        self._send_json(
            {
                "error": {"kind": kind, "message": message, "retryable": False},
                "needs_confirmation": True,
                "confirmation_reason": message,
            },
            status=status,
        )

    # -- POST handlers -----------------------------------------------------

    def _handle_analyze_food(self, data: dict) -> None:
        image_data = data.get("image") or data.get("image_base64") or ""
        media_type = data.get("media_type") or data.get("mime_type")

        # One endpoint, two modes -- not two endpoints. The request shape, the
        # 5 MB image validation, the error envelope and the foods[] entry
        # shape are identical either way, so a mode flag lets the client ship
        # ahead of the server and light up when the server honours it.
        raw_mode = data.get("mode")
        try:
            mode = nutrition_analyzer.normalize_mode(raw_mode)
        except ValueError as exc:
            self._send_json(
                {
                    "error": {
                        "kind": "bad_request",
                        "message": str(exc),
                        "retryable": False,
                    },
                    "needs_confirmation": True,
                    "confirmation_reason": str(exc),
                },
                status=400,
            )
            return

        try:
            payload, status = nutrition_analyzer.analyze_food(
                image_data, media_type, mode, echo_mode=raw_mode is not None
            )
        except Exception as exc:  # pragma: no cover - last-resort guard
            # Log the type, never the request body (it carries the image).
            print(f"ERROR: unhandled analyze_food failure: {type(exc).__name__}: {exc}")
            self._send_json(
                ({"mode": mode} if raw_mode is not None else {})
                | {
                    "error": {
                        "kind": "internal_error",
                        "message": "The analysis failed unexpectedly.",
                        "retryable": True,
                    },
                    "needs_confirmation": True,
                    "confirmation_reason": "The analysis failed unexpectedly. "
                    "Please try again or add the meal manually.",
                },
                status=500,
            )
            return

        self._send_json(payload, status=status)

    def _handle_search_food(self, data: dict) -> None:
        """Free-text food search. Makes no Anthropic call on any path."""
        payload, status = food_lookup.search_food(data)
        self._send_json(payload, status=status)

    def _handle_speak(self, data: dict) -> None:
        """Affirmation text to an MP3 in a natural voice. See natural_voice."""
        payload, status = natural_voice.speak(data)
        self._send_json(payload, status=status)

    def _handle_barcode(self, data: dict) -> None:
        """UPC/EAN lookup. Makes no Anthropic call on any path."""
        payload, status = food_lookup.barcode_lookup(data)
        self._send_json(payload, status=status)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        for name, value in CORS_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()


def _startup_report() -> None:
    """Say what is and is not configured, without printing any value."""
    debug = nutrition_analyzer.debug_payload()
    print(f"   analysis version : {debug['analysis_version']}")
    print(f"   model            : {debug['model']} (from {debug['model_source']})")
    print(f"   anthropic sdk    : {'installed' if debug['anthropic_sdk_installed'] else 'MISSING'}")
    for name in nutrition_analyzer.REQUIRED_ENV_VARS:
        state = debug["env"][name]
        marker = "ok" if state == "set" else "MISSING - set it on the deploy platform"
        print(f"   {name:<20}: {marker}")


def run(platform: str, default_port: int) -> None:
    port = int(os.environ.get("PORT", default_port))
    NutritionRequestHandler.platform = platform

    print(f"Starting All Ten Nutrition API ({platform}) on 0.0.0.0:{port}")
    _startup_report()

    # Threading matters here: a single analysis makes a Claude call plus one
    # USDA call per food, so a single-threaded server would serialize users
    # behind each other for seconds at a time.
    #
    # Each in-flight analysis additionally runs up to
    # ``nutrition_analyzer.USDA_LOOKUP_WORKERS`` lookup threads of its own, so
    # peak USDA concurrency is (concurrent analyses x that bound). The per-meal
    # bound is what keeps that product finite as meals get longer; if the
    # deploy platform ever needs a ceiling on the first factor too, cap it here
    # rather than by shrinking the per-meal pool.
    server = ThreadingHTTPServer(("0.0.0.0", port), NutritionRequestHandler)
    server.serve_forever()
