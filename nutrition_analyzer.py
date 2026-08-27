"""Shared nutrition analyzer -- the single implementation both deploy
entrypoints import.

Pipeline
--------
1. **Claude vision** (``claude_vision``) identifies the foods in a meal photo
   and, critically, estimates the as-served portion weight of each in grams.
2. **USDA FoodData Central** (``usda_client``) supplies authoritative
   per-100 g macros and micronutrients, which are scaled by those grams.
3. **Sanity clamps** flag implausible results for user confirmation instead of
   silently logging a wrong number.

Why this replaced Google Cloud Vision: label detection returned "pizza" and
"food" but had no notion of quantity, so calories were looked up against an
assumed serving size. That is how a 1,200 kcal meal was reported as 6,000.
Portion grams are now a first-class model output and every number that can be
sourced is sourced from USDA rather than generated.

No credential is read, logged, or stored by this module. ``ANTHROPIC_API_KEY``
is resolved by the Anthropic SDK itself; ``USDA_FDC_API_KEY`` is read inside
``usda_client`` at call time. ``/debug`` reports only whether a variable is
set, never its value.
"""

from __future__ import annotations

import os
import time

import claude_vision
import usda_client
from claude_vision import VisionError, VisionRefusal
from usda_client import UsdaUnavailable

ANALYSIS_VERSION = "2.0.0-claude-usda"

MACRO_FIELDS = ("calories", "protein", "carbs", "fat", "fiber", "sugar", "sodium")

# Environment variable names the owner must set on the deploy platform.
# Values are never read into the response -- only presence is reported.
REQUIRED_ENV_VARS = ("ANTHROPIC_API_KEY", "USDA_FDC_API_KEY")
OPTIONAL_ENV_VARS = (
    "NUTRITION_MODEL",
    "MAX_MEAL_CALORIES",
    "MIN_CONFIDENCE",
    "MAX_FOOD_PORTION_GRAMS",
    "PORT",
)

DEFAULT_MAX_MEAL_CALORIES = 2500.0
DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_MAX_FOOD_PORTION_GRAMS = 1500.0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        print(f"WARNING: {name} is not a number; using default {default}")
        return default
    if value <= 0:
        print(f"WARNING: {name} must be positive; using default {default}")
        return default
    return value


def max_meal_calories() -> float:
    return _env_float("MAX_MEAL_CALORIES", DEFAULT_MAX_MEAL_CALORIES)


def min_confidence() -> float:
    return _env_float("MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE)


def max_food_portion_grams() -> float:
    return _env_float("MAX_FOOD_PORTION_GRAMS", DEFAULT_MAX_FOOD_PORTION_GRAMS)


# --- pure helpers -----------------------------------------------------------

def _round(value, digits: int = 1):
    if value is None:
        return None
    return round(float(value), digits) if digits else round(float(value))


def clamp_confidence(value) -> float:
    """Coerce anything to a 0-1 float. Unparseable values become 0.0 --
    an unknown confidence is not a high confidence."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if result != result:  # NaN
        return 0.0
    return max(0.0, min(1.0, result))


def clamp_portion(grams, limit: float | None = None) -> tuple[float, bool]:
    """Clamp a portion weight into a sane range.

    Returns ``(grams, was_clamped)``. A single food above the limit (default
    1.5 kg) is far more likely to be a model mistake than a real plate.
    """
    if limit is None:
        limit = max_food_portion_grams()
    try:
        value = float(grams)
    except (TypeError, ValueError):
        return 0.0, True
    if value != value or value < 0:  # NaN or negative
        return 0.0, True
    if value > limit:
        return limit, True
    return value, False


def scale_macros(per_100g: dict, grams: float) -> dict:
    """Scale per-100 g nutrient values to an actual portion.

    Fields absent from ``per_100g`` stay ``None`` -- a missing value is
    reported as missing, never as zero.
    """
    factor = float(grams) / 100.0
    scaled = {}
    for field in MACRO_FIELDS:
        value = per_100g.get(field)
        if value is None:
            scaled[field] = None
        else:
            digits = 0 if field in ("calories", "sodium") else 1
            scaled[field] = _round(float(value) * factor, digits)
    return scaled


def scale_micronutrients(per_100g: dict, grams: float) -> dict:
    factor = float(grams) / 100.0
    return {
        name: _round(float(value) * factor, 2)
        for name, value in (per_100g or {}).items()
        if value is not None
    }


def aggregate_totals(foods: list) -> dict:
    """Sum the macros across foods. ``None`` contributions are skipped, so a
    total is the sum of what is actually known."""
    totals = {field: 0.0 for field in MACRO_FIELDS}
    seen = {field: False for field in MACRO_FIELDS}

    for food in foods:
        macros = food.get("macros") or {}
        for field in MACRO_FIELDS:
            value = macros.get(field)
            if value is None:
                continue
            totals[field] += float(value)
            seen[field] = True

    result = {}
    for field in MACRO_FIELDS:
        if not seen[field]:
            result[field] = None
            continue
        digits = 0 if field in ("calories", "sodium") else 1
        result[field] = _round(totals[field], digits)
    return result


def aggregate_micronutrients(foods: list) -> dict:
    """Sum micronutrients across foods. Only USDA-sourced foods contribute --
    micronutrients are never estimated."""
    totals: dict[str, float] = {}
    for food in foods:
        for name, value in (food.get("micronutrients") or {}).items():
            if value is None:
                continue
            totals[name] = totals.get(name, 0.0) + float(value)
    return {name: _round(value, 2) for name, value in sorted(totals.items())}


def evaluate_confirmation(
    totals: dict,
    overall_confidence: float,
    foods: list,
    *,
    usda_available: bool = True,
    calorie_limit: float | None = None,
    confidence_floor: float | None = None,
) -> tuple[bool, str | None]:
    """Decide whether the client must ask the user to confirm or edit.

    The point of this gate: it is better to ask than to silently log a wrong
    number. Returns ``(needs_confirmation, reason_or_None)``.
    """
    if calorie_limit is None:
        calorie_limit = max_meal_calories()
    if confidence_floor is None:
        confidence_floor = min_confidence()

    reasons: list[str] = []

    if not foods:
        return True, "No food could be identified in this photo. Please add the meal manually."

    calories = totals.get("calories")
    if calories is not None and float(calories) > calorie_limit:
        reasons.append(
            f"Estimated {int(float(calories))} kcal exceeds the "
            f"{int(calorie_limit)} kcal sanity threshold for a single meal"
        )

    if overall_confidence < confidence_floor:
        reasons.append(
            f"Overall confidence {overall_confidence:.2f} is below the "
            f"{confidence_floor:.2f} threshold"
        )

    low_confidence_foods = [
        food["name"]
        for food in foods
        if clamp_confidence(food.get("confidence")) < confidence_floor
    ]
    if low_confidence_foods and not any("confidence" in r for r in reasons):
        reasons.append(
            "Low confidence on: " + ", ".join(low_confidence_foods[:5])
        )

    estimated_foods = [f["name"] for f in foods if f.get("source") != "usda"]
    if estimated_foods:
        if not usda_available:
            reasons.append(
                "USDA FoodData Central was unavailable, so macros for "
                + ", ".join(estimated_foods[:5])
                + " are model estimates rather than database values"
            )
        else:
            reasons.append(
                "No USDA match for "
                + ", ".join(estimated_foods[:5])
                + "; macros are model estimates"
            )

    clamped = [f["name"] for f in foods if f.get("portion_clamped")]
    if clamped:
        reasons.append(
            "Portion size was capped as implausible for: " + ", ".join(clamped[:5])
        )

    if not reasons:
        return False, None
    return True, "; ".join(reasons) + "."


def build_response(
    foods: list,
    overall_confidence: float,
    model: str,
    *,
    usda_available: bool = True,
    notes: str = "",
    warnings: list | None = None,
) -> dict:
    """Shape the client-facing contract. See API_CONTRACT.md."""
    totals = aggregate_totals(foods)
    totals["micronutrients"] = aggregate_micronutrients(foods)

    needs_confirmation, reason = evaluate_confirmation(
        totals, overall_confidence, foods, usda_available=usda_available
    )

    public_foods = []
    for food in foods:
        public_foods.append(
            {
                "name": food["name"],
                "portion_grams": _round(food["portion_grams"], 0),
                "confidence": _round(clamp_confidence(food.get("confidence")), 2),
                "macros": food.get("macros") or {field: None for field in MACRO_FIELDS},
                "micronutrients": food.get("micronutrients") or {},
                "source": food.get("source", "estimated"),
                "usda_description": food.get("usda_description"),
                "fdc_id": food.get("fdc_id"),
            }
        )

    return {
        "foods": public_foods,
        "totals": totals,
        "needs_confirmation": needs_confirmation,
        "confirmation_reason": reason,
        "confidence": _round(clamp_confidence(overall_confidence), 2),
        "model": model,
        "analysis_version": ANALYSIS_VERSION,
        "usda_available": usda_available,
        "notes": notes or "",
        "warnings": list(warnings or []),
        "analyzed_at": time.time(),
    }


# --- USDA enrichment --------------------------------------------------------

def enrich_with_usda(detected_foods: list, lookup=None) -> tuple[list, bool, list]:
    """Attach macros to each detected food.

    ``lookup`` is injected for testing; it defaults to :func:`usda_client.lookup`.
    Returns ``(foods, usda_available, warnings)``.

    A food that USDA cannot supply falls back to the model's own per-100 g
    estimate and is labelled ``source: "estimated"``. Micronutrients are never
    estimated -- an estimated food carries an empty micronutrient map.
    """
    if lookup is None:
        lookup = usda_client.lookup

    foods: list = []
    warnings: list[str] = []
    usda_available = True

    for raw in detected_foods:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue

        grams, was_clamped = clamp_portion(raw.get("estimated_portion_grams"))
        query = str(raw.get("usda_query") or name).strip() or name

        record = None
        if usda_available:
            try:
                record = lookup(query)
                if record is None and query.lower() != name.lower():
                    record = lookup(name)
            except UsdaUnavailable as exc:
                # First failure disables USDA for the rest of this meal --
                # no point retrying a down service once per food.
                usda_available = False
                warnings.append(f"USDA lookup unavailable: {exc}")
                record = None
            except Exception as exc:  # defensive: never fail the whole meal
                usda_available = False
                warnings.append(f"USDA lookup error: {exc}")
                record = None

        if record and record.get("per_100g"):
            macros = scale_macros(record["per_100g"], grams)
            micros = scale_micronutrients(record.get("micronutrients_per_100g"), grams)
            source = "usda"
            usda_description = record.get("description")
            fdc_id = record.get("fdc_id")
        else:
            estimate = {
                "calories": raw.get("estimated_calories_per_100g"),
                "protein": raw.get("estimated_protein_per_100g"),
                "carbs": raw.get("estimated_carbs_per_100g"),
                "fat": raw.get("estimated_fat_per_100g"),
                # Not estimated: the model is not asked to guess these, and a
                # guessed micronutrient is worse than an absent one.
                "fiber": None,
                "sugar": None,
                "sodium": None,
            }
            macros = scale_macros(estimate, grams)
            micros = {}
            source = "estimated"
            usda_description = None
            fdc_id = None

        foods.append(
            {
                "name": name,
                "portion_grams": grams,
                "portion_clamped": was_clamped,
                "confidence": clamp_confidence(raw.get("confidence")),
                "macros": macros,
                "micronutrients": micros,
                "source": source,
                "usda_description": usda_description,
                "fdc_id": fdc_id,
            }
        )

    return foods, usda_available, warnings


# --- public entrypoint ------------------------------------------------------

def analyze_meal(image_data: str, media_type: str | None = None) -> tuple[dict, int]:
    """Analyze one meal photo. Returns ``(response_body, http_status)``.

    Never raises for an expected failure -- every failure mode comes back as a
    JSON body the client can render, with ``needs_confirmation: true`` so the
    user is asked rather than told a wrong number.
    """
    try:
        detection = claude_vision.detect_foods(image_data, media_type)
    except VisionRefusal as exc:
        return _failure_response(
            str(exc), model=claude_vision.model_id(), kind="refusal", retryable=False
        ), 200
    except VisionError as exc:
        status = 400 if exc.kind == "bad_request" else 200
        if exc.kind in ("misconfigured", "auth"):
            status = 500
        return _failure_response(
            exc.message,
            model=claude_vision.model_id(),
            kind=exc.kind,
            retryable=exc.retryable,
        ), status
    except Exception as exc:  # pragma: no cover - unexpected
        print(f"ERROR: unexpected failure in vision step: {type(exc).__name__}: {exc}")
        return _failure_response(
            "The meal analysis failed unexpectedly. Please try again or add "
            "the meal manually.",
            model=claude_vision.model_id(),
            kind="internal_error",
            retryable=True,
        ), 500

    foods, usda_available, warnings = enrich_with_usda(detection.get("foods") or [])

    return (
        build_response(
            foods,
            clamp_confidence(detection.get("confidence")),
            detection.get("model") or claude_vision.model_id(),
            usda_available=usda_available,
            notes=str(detection.get("notes") or ""),
            warnings=warnings,
        ),
        200,
    )


def _failure_response(message: str, *, model: str, kind: str, retryable: bool) -> dict:
    """An empty but well-shaped response. The client renders the same editor
    and asks the user to enter the meal, rather than showing invented numbers.
    """
    return {
        "foods": [],
        "totals": {field: None for field in MACRO_FIELDS} | {"micronutrients": {}},
        "needs_confirmation": True,
        "confirmation_reason": message,
        "confidence": 0.0,
        "model": model,
        "analysis_version": ANALYSIS_VERSION,
        "usda_available": usda_client.is_configured(),
        "notes": "",
        "warnings": [],
        "error": {"kind": kind, "message": message, "retryable": retryable},
        "analyzed_at": time.time(),
    }


# --- status endpoints -------------------------------------------------------

def health_payload() -> dict:
    return {
        "status": "healthy",
        "service": "all-ten-nutrition-api",
        "analysis_version": ANALYSIS_VERSION,
        "model": claude_vision.model_id(),
        "vision": "configured" if _env_is_set("ANTHROPIC_API_KEY") else "not_configured",
        "usda": "configured" if usda_client.is_configured() else "not_configured",
        "timestamp": time.time(),
    }


def _env_is_set(name: str) -> bool:
    """Presence only. The value is never read into a response or a log."""
    return bool(os.environ.get(name, "").strip())


def debug_payload() -> dict:
    """Diagnostics with no secret material.

    Reports only whether each variable is SET or NOT SET. Values, prefixes,
    lengths, and suffixes are all withheld deliberately -- a length or a
    preview is still information about a secret.
    """
    return {
        "analysis_version": ANALYSIS_VERSION,
        "model": claude_vision.model_id(),
        "model_source": "NUTRITION_MODEL" if _env_is_set("NUTRITION_MODEL") else "default",
        "anthropic_sdk_installed": claude_vision.ANTHROPIC_AVAILABLE,
        "env": {
            name: ("set" if _env_is_set(name) else "not set")
            for name in REQUIRED_ENV_VARS + OPTIONAL_ENV_VARS
        },
        "thresholds": {
            "max_meal_calories": max_meal_calories(),
            "min_confidence": min_confidence(),
            "max_food_portion_grams": max_food_portion_grams(),
        },
        "usda_cache": usda_client.cache_stats(),
        "supported_media_types": list(claude_vision.SUPPORTED_MEDIA_TYPES),
        "timestamp": time.time(),
    }
