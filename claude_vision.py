"""Claude vision step: two different jobs, one API call shape.

**Meal mode** (:func:`detect_foods`) is the half of the pipeline that Google
Cloud Vision could not do. Label detection returns "pizza" / "food"; it cannot
tell you that there are roughly 310 g of pizza on the plate. Portion grams are
what make the calorie number correct, so they are the primary output of that
step.

**Label mode** (:func:`read_nutrition_label`) is the opposite job. Photograph
the back of a protein-shake box and there is no portion to estimate -- the
numbers are printed on the panel, and every instinct that makes meal mode
work (estimate the serving, name the food so USDA can match it) actively
produces a wrong answer, because USDA will return a *generic* product whose
macros are not this product's. So label mode reads rather than recognises:
transcribe what is printed, and refuse rather than invent when it cannot be
read.

The two share :func:`prepare_image` and :func:`_call_structured` -- identical
validation, identical error taxonomy, identical refusal handling -- and differ
only in prompt and output schema.

Credentials: ``anthropic.Anthropic()`` resolves ``ANTHROPIC_API_KEY`` from the
environment on its own. No key is ever passed explicitly, logged, or stored.
"""

from __future__ import annotations

import base64
import binascii
import json
import os

try:
    import anthropic

    ANTHROPIC_AVAILABLE = True
    ANTHROPIC_IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover - depends on deploy image
    anthropic = None  # type: ignore[assignment]
    ANTHROPIC_AVAILABLE = False
    ANTHROPIC_IMPORT_ERROR = str(exc)


# Single configurable model constant. The owner wants to A/B claude-haiku-4-5
# and claude-opus-5 later; that is an env var change, not a code change.
DEFAULT_MODEL = "claude-sonnet-5"


def model_id() -> str:
    """The model this process will call. Read at call time so a restart picks
    up a changed ``NUTRITION_MODEL`` without a redeploy of the code."""
    return os.environ.get("NUTRITION_MODEL", "").strip() or DEFAULT_MODEL


MAX_TOKENS = 2048

SUPPORTED_MEDIA_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")

# Roughly 5 MB of raw image; base64 inflates by ~4/3.
MAX_IMAGE_BYTES = 5 * 1024 * 1024

SYSTEM_PROMPT = (
    "You are a nutrition vision analyst. You look at a photo of a meal and "
    "report which foods are present and HOW MUCH of each is on the plate.\n\n"
    "Portion estimation is the most important part of your job. Use visible "
    "reference objects for scale: a dinner plate is about 26 cm across, a "
    "side plate about 20 cm, a fork about 19 cm long, a standard mug holds "
    "about 350 ml. Estimate the cooked, as-served weight in grams of each "
    "food, not a nominal 'serving'.\n\n"
    "Rules:\n"
    "- List each distinct food separately. Do not merge a whole plate into "
    "one entry unless it genuinely is one dish.\n"
    "- Use plain, searchable food names ('grilled chicken breast', not "
    "'protein').\n"
    "- usda_query should be the name most likely to match a USDA FoodData "
    "Central record, including the cooking method when it matters.\n"
    "- Report honest confidence. If the photo is blurry, the food is "
    "obscured, portions are hidden by the dish, or you are guessing at an "
    "ingredient, say so with a low number. An honest 0.4 is far more useful "
    "than an optimistic 0.9.\n"
    "- The per-100g macro estimates are a FALLBACK, used only when the food "
    "cannot be matched in the USDA database. Give your best general knowledge "
    "values for the food as prepared.\n"
    "- If the image contains no food at all, return an empty foods list and a "
    "confidence of 0."
)

USER_PROMPT = (
    "Identify every food in this meal photo and estimate the as-served "
    "portion weight in grams for each. Return the structured result."
)

# Schema for output_config.format. Parsed with json.loads -- never string
# matched.
FOOD_DETECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "foods": {
            "type": "array",
            "description": "Every distinct food visible in the photo.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Human-readable food name for display.",
                    },
                    "usda_query": {
                        "type": "string",
                        "description": (
                            "Search phrase most likely to match a USDA "
                            "FoodData Central record."
                        ),
                    },
                    "estimated_portion_grams": {
                        "type": "number",
                        "description": "As-served weight in grams.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "0-1 confidence in this food and its portion.",
                    },
                    "estimated_calories_per_100g": {"type": "number"},
                    "estimated_protein_per_100g": {"type": "number"},
                    "estimated_carbs_per_100g": {"type": "number"},
                    "estimated_fat_per_100g": {"type": "number"},
                },
                "required": [
                    "name",
                    "usda_query",
                    "estimated_portion_grams",
                    "confidence",
                    "estimated_calories_per_100g",
                    "estimated_protein_per_100g",
                    "estimated_carbs_per_100g",
                    "estimated_fat_per_100g",
                ],
                "additionalProperties": False,
            },
        },
        "confidence": {
            "type": "number",
            "description": "0-1 overall confidence in the whole analysis.",
        },
        "notes": {
            "type": "string",
            "description": (
                "Short note on anything uncertain -- obscured food, unclear "
                "scale, ambiguous ingredients. Empty string if nothing to flag."
            ),
        },
    },
    "required": ["foods", "confidence", "notes"],
    "additionalProperties": False,
}


# --- nutrition label mode ---------------------------------------------------

LABEL_SYSTEM_PROMPT = (
    "You are transcribing a printed nutrition facts panel from a photograph "
    "of food packaging. You are a READER, not an estimator. Every number you "
    "return must be one you can actually see printed on the panel.\n\n"
    "Rules:\n"
    "- Report the macro column PER SERVING, exactly as printed. Do not "
    "convert it to per 100 g, do not multiply it by the servings per "
    "container, and do not recompute it.\n"
    "- If a row is not printed on the panel, return null for it. NEVER return "
    "0 for a row you cannot see. A printed '0 g' is 0; an absent row is null. "
    "This distinction matters more than completeness.\n"
    "- Do not calculate a missing row from the others (for example, do not "
    "derive calories from the macros). An unprinted row is null.\n"
    "- serving_size and serving_size_unit are the serving as printed. When "
    "the panel gives both a household measure and a metric weight -- "
    "'1 bar (60 g)', '2 tbsp (32 g)' -- put the metric figure in "
    "serving_size/serving_size_unit and the household measure in "
    "household_serving. When only a household measure is printed ('1 scoop', "
    "'1 bar' with no gram weight), leave serving_size and serving_size_unit "
    "null. Do not estimate what a scoop weighs.\n"
    "- Units: calories in kcal (use the kcal figure if both kJ and kcal are "
    "printed), protein/carbs/fat/fiber/sugar in grams, sodium in "
    "milligrams. Carbs means total carbohydrate; sugar means total sugars. "
    "Convert a printed unit to these units only when the conversion is "
    "arithmetic and certain (for example 1 g sodium = 1000 mg); otherwise "
    "return null.\n"
    "- Micronutrients: report the printed AMOUNT per serving in the stated "
    "unit (mg or mcg), never the %DV. If only a %DV is printed, return null "
    "for that nutrient.\n"
    "- If there is no nutrition panel in the photo, or it is too blurry, "
    "cropped, angled or dark to read, set panel_found to false and explain "
    "briefly in unreadable_reason. Return nulls rather than a guess. A "
    "refusal to read is a correct answer; an invented number is not.\n"
    "- Report honest confidence in the transcription. Glare, a curved "
    "surface, or a partially cropped panel should lower it."
)

LABEL_USER_PROMPT = (
    "Read the nutrition facts panel in this photo and transcribe it exactly "
    "as printed. Return the structured result."
)

# The canonical micronutrient set, matching ``usda_client.MICRO_NUTRIENT_IDS``
# so a label-read food and a USDA-read food carry the same keys and units.
LABEL_MICRONUTRIENTS = (
    ("calcium", "mg"),
    ("iron", "mg"),
    ("magnesium", "mg"),
    ("phosphorus", "mg"),
    ("potassium", "mg"),
    ("zinc", "mg"),
    ("vitamin_a", "mcg RAE"),
    ("vitamin_c", "mg"),
    ("vitamin_d", "mcg"),
    ("vitamin_b12", "mcg"),
    ("folate", "mcg DFE"),
)

_LABEL_MACROS = (
    ("calories", "kcal per serving as printed"),
    ("protein", "grams of protein per serving as printed"),
    ("carbs", "grams of total carbohydrate per serving as printed"),
    ("fat", "grams of total fat per serving as printed"),
    ("fiber", "grams of dietary fibre per serving as printed"),
    ("sugar", "grams of total sugars per serving as printed"),
    ("sodium", "milligrams of sodium per serving as printed"),
)


def _nullable(json_type: str, description: str) -> dict:
    """A schema node that is explicitly allowed to be null.

    Nullability is the whole point of this mode: a panel that does not print
    a row must come back as ``null``, never as ``0``.
    """
    return {"type": [json_type, "null"], "description": description}


NUTRITION_LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "panel_found": {
            "type": "boolean",
            "description": (
                "True only if a nutrition facts panel is visible AND legible "
                "enough to transcribe. False if absent, blurry, cropped or "
                "too dark."
            ),
        },
        "unreadable_reason": {
            "type": "string",
            "description": (
                "When panel_found is false, a short user-facing explanation "
                "(e.g. 'the panel is cut off at the left edge'). Empty string "
                "when the panel was read."
            ),
        },
        "product_name": _nullable("string", "Product name as printed, or null."),
        "brand": _nullable("string", "Brand or manufacturer as printed, or null."),
        "serving_size": _nullable(
            "number",
            "Numeric serving size as printed, e.g. 60 for '1 bar (60 g)'. "
            "Null when only a household measure is printed.",
        ),
        "serving_size_unit": _nullable(
            "string", "Unit of serving_size as printed: 'g', 'ml', 'oz'. Null if none."
        ),
        "household_serving": _nullable(
            "string", "Household measure as printed: '1 bar', '2 scoops', '1 bottle'."
        ),
        "servings_per_container": _nullable(
            "number",
            "Servings per container as printed. Null when the panel does not "
            "say -- do not assume 1.",
        ),
        "per_serving": {
            "type": "object",
            "description": "The macro column per serving, as printed. Null for absent rows.",
            "properties": {
                name: _nullable("number", description)
                for name, description in _LABEL_MACROS
            },
            "required": [name for name, _ in _LABEL_MACROS],
            "additionalProperties": False,
        },
        "micronutrients_per_serving": {
            "type": "object",
            "description": (
                "Printed micronutrient amounts per serving. Null for any not "
                "printed as an amount. Never derive an amount from a %DV."
            ),
            "properties": {
                name: _nullable("number", f"Amount per serving in {unit}, or null.")
                for name, unit in LABEL_MICRONUTRIENTS
            },
            "required": [name for name, _ in LABEL_MICRONUTRIENTS],
            "additionalProperties": False,
        },
        "confidence": {
            "type": "number",
            "description": "0-1 confidence in the transcription being correct.",
        },
        "notes": {
            "type": "string",
            "description": (
                "Short note on anything ambiguous -- glare, a partially "
                "obscured row, a second column for 'per container'. Empty "
                "string if nothing to flag."
            ),
        },
    },
    "required": [
        "panel_found",
        "unreadable_reason",
        "product_name",
        "brand",
        "serving_size",
        "serving_size_unit",
        "household_serving",
        "servings_per_container",
        "per_serving",
        "micronutrients_per_serving",
        "confidence",
        "notes",
    ],
    "additionalProperties": False,
}


class VisionError(Exception):
    """A vision step failure. ``retryable`` tells the caller whether a retry
    could plausibly succeed."""

    def __init__(self, message: str, *, retryable: bool = False, kind: str = "error"):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.kind = kind


class VisionRefusal(VisionError):
    """Claude declined the request. Never retryable."""

    def __init__(self, message: str, category: str | None = None):
        super().__init__(message, retryable=False, kind="refusal")
        self.category = category


# --- image handling (pure) --------------------------------------------------

_MAGIC_BYTES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_media_type(image_bytes: bytes) -> str | None:
    """Detect the media type from magic bytes. Returns None if unrecognised."""
    for magic, media_type in _MAGIC_BYTES:
        if image_bytes.startswith(magic):
            return media_type
    if (
        len(image_bytes) >= 12
        and image_bytes[:4] == b"RIFF"
        and image_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def prepare_image(image_data: str, declared_media_type: str | None = None) -> tuple[str, str]:
    """Validate an uploaded image and return ``(clean_base64, media_type)``.

    Accepts a bare base64 string or a ``data:`` URL. Strips all whitespace --
    the API rejects base64 containing newlines. Raises :class:`VisionError`
    with a clear message on anything malformed or unsupported.
    """
    if not image_data or not str(image_data).strip():
        raise VisionError("No image data was provided.", kind="bad_request")

    payload = str(image_data).strip()
    if payload.startswith("data:"):
        header, _, encoded = payload.partition(",")
        if not encoded:
            raise VisionError("Malformed data URL: no base64 payload.", kind="bad_request")
        if declared_media_type is None and ";" in header:
            candidate = header[5:].split(";")[0].strip().lower()
            if candidate:
                declared_media_type = candidate
        payload = encoded

    # Base64 must contain no newlines when sent to the API.
    payload = "".join(payload.split())

    try:
        image_bytes = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise VisionError("Image data is not valid base64.", kind="bad_request") from None

    if not image_bytes:
        raise VisionError("Image data decoded to zero bytes.", kind="bad_request")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise VisionError(
            f"Image is {len(image_bytes) // 1024} KB; the limit is "
            f"{MAX_IMAGE_BYTES // 1024} KB. Resize before uploading.",
            kind="bad_request",
        )

    sniffed = sniff_media_type(image_bytes)
    media_type = sniffed or (declared_media_type or "").strip().lower() or None

    if media_type not in SUPPORTED_MEDIA_TYPES:
        raise VisionError(
            "Unsupported image format. Supported: "
            + ", ".join(SUPPORTED_MEDIA_TYPES)
            + (f" (detected: {media_type})" if media_type else " (format not recognised)"),
            kind="bad_request",
        )

    # Re-encode from the decoded bytes so the payload is canonical and
    # newline-free regardless of what the client sent.
    return base64.b64encode(image_bytes).decode("ascii"), media_type


# --- API call ---------------------------------------------------------------

_client = None


def _get_client():
    """Lazily build one module-level client.

    ``anthropic.Anthropic()`` resolves ANTHROPIC_API_KEY from the environment
    itself -- we never read, pass, or log the value.
    """
    global _client
    if not ANTHROPIC_AVAILABLE:
        raise VisionError(
            "The 'anthropic' package is not installed on this server "
            f"({ANTHROPIC_IMPORT_ERROR}). Add it to requirements.txt / "
            "pyproject.toml and redeploy.",
            kind="misconfigured",
        )
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise VisionError(
            "ANTHROPIC_API_KEY is not set in this environment. Set it on the "
            "deploy platform; the server will not analyze meals without it.",
            kind="misconfigured",
        )
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def reset_client() -> None:
    """Drop the cached client (tests, and after an env change)."""
    global _client
    _client = None


def _extract_json_text(response) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise VisionError(
        "Claude returned no text block to parse.", retryable=True, kind="empty_response"
    )


def _call_structured(
    clean_b64: str,
    media_type: str,
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict,
    refusal_message: str,
) -> tuple[dict, str]:
    """One structured vision call. Returns ``(parsed_json, model_id)``.

    Shared by meal and label mode so both get exactly the same error taxonomy,
    the same refusal handling, and the same parse guards. Only the prompt and
    the schema differ between the two.
    """
    client = _get_client()
    model = model_id()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            # Bounded extraction, not deep reasoning -- effort drives cost.
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": clean_b64,
                            },
                        },
                        {"type": "text", "text": user_prompt},
                    ],
                }
            ],
        )
    # Most specific first. Retryable and non-retryable are distinguished so the
    # caller can tell the client whether trying again is worth it.
    except anthropic.AuthenticationError:
        raise VisionError(
            "Claude rejected the API credentials. Check ANTHROPIC_API_KEY in "
            "the deploy environment.",
            retryable=False,
            kind="auth",
        ) from None
    except anthropic.RateLimitError:
        raise VisionError(
            "Claude rate limit reached. Try again shortly.",
            retryable=True,
            kind="rate_limit",
        ) from None
    except anthropic.APIStatusError as exc:
        status = getattr(exc, "status_code", 0) or 0
        if status >= 500:
            raise VisionError(
                f"Claude returned a server error (HTTP {status}). Try again shortly.",
                retryable=True,
                kind="upstream_error",
            ) from None
        raise VisionError(
            f"Claude rejected the request (HTTP {status}): {exc.message}",
            retryable=False,
            kind="bad_request",
        ) from None
    except anthropic.APIConnectionError:
        raise VisionError(
            "Could not reach the Claude API. Try again shortly.",
            retryable=True,
            kind="connection",
        ) from None

    # Check the stop reason BEFORE reading content.
    if getattr(response, "stop_reason", None) == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise VisionRefusal(refusal_message, category=category)

    text = _extract_json_text(response)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise VisionError(
            "Claude's response was not valid JSON.", retryable=True, kind="parse_error"
        ) from None

    if not isinstance(parsed, dict):
        raise VisionError(
            "Claude's response was not a JSON object.",
            retryable=True,
            kind="parse_error",
        )

    return parsed, model


def detect_foods(image_data: str, declared_media_type: str | None = None) -> dict:
    """Meal mode. Returns the parsed structured result.

    Shape::

        {"foods": [{"name", "usda_query", "estimated_portion_grams",
                    "confidence", "estimated_*_per_100g"}, ...],
         "confidence": float,
         "notes": str,
         "model": str}
    """
    clean_b64, media_type = prepare_image(image_data, declared_media_type)
    parsed, model = _call_structured(
        clean_b64,
        media_type,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=USER_PROMPT,
        schema=FOOD_DETECTION_SCHEMA,
        refusal_message=(
            "Claude declined to analyze this image. Please try a different "
            "photo of the meal."
        ),
    )

    parsed.setdefault("foods", [])
    parsed.setdefault("confidence", 0.0)
    parsed.setdefault("notes", "")
    parsed["model"] = model
    return parsed


def read_nutrition_label(
    image_data: str, declared_media_type: str | None = None
) -> dict:
    """Label mode. Returns the panel as printed, per serving.

    Shape::

        {"panel_found": bool,
         "unreadable_reason": str,
         "product_name": str | None,
         "brand": str | None,
         "serving_size": float | None,
         "serving_size_unit": str | None,
         "household_serving": str | None,
         "servings_per_container": float | None,
         "per_serving": {7 macro fields, each float | None},
         "micronutrients_per_serving": {name: float | None},
         "confidence": float,
         "notes": str,
         "model": str}

    **Nothing here is converted.** The values are per serving because that is
    how the panel prints them; turning that into the app's per-100 g basis is
    :mod:`nutrition_analyzer`'s job, and it needs a serving weight in grams to
    do it honestly.
    """
    clean_b64, media_type = prepare_image(image_data, declared_media_type)
    parsed, model = _call_structured(
        clean_b64,
        media_type,
        system_prompt=LABEL_SYSTEM_PROMPT,
        user_prompt=LABEL_USER_PROMPT,
        schema=NUTRITION_LABEL_SCHEMA,
        refusal_message=(
            "Claude declined to read this image. Please try a different photo "
            "of the nutrition label."
        ),
    )

    parsed.setdefault("panel_found", False)
    parsed.setdefault("unreadable_reason", "")
    parsed.setdefault("confidence", 0.0)
    parsed.setdefault("notes", "")
    if not isinstance(parsed.get("per_serving"), dict):
        parsed["per_serving"] = {}
    if not isinstance(parsed.get("micronutrients_per_serving"), dict):
        parsed["micronutrients_per_serving"] = {}
    parsed["model"] = model
    return parsed
