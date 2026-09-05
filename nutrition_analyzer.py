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
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import claude_vision
import openfoodfacts_client
import usda_client
from claude_vision import VisionError, VisionRefusal
from usda_client import UsdaUnavailable

ANALYSIS_VERSION = "2.0.0-claude-usda"

MACRO_FIELDS = ("calories", "protein", "carbs", "fat", "fiber", "sugar", "sodium")

# The two things a photo sent to /analyze_food can be.
#
# ``meal``  -- a plate. Estimate the portion, match it to USDA. The default,
#              and the only behaviour before label mode existed: a request
#              that sends no ``mode`` gets byte-identical output to what it
#              got before.
# ``nutrition_label`` -- packaging. Read the printed panel; estimate nothing.
MODE_MEAL = "meal"
MODE_NUTRITION_LABEL = "nutrition_label"
SUPPORTED_MODES = (MODE_MEAL, MODE_NUTRITION_LABEL)

# The nutrient basis every foods[] entry in this app is reported on. A label
# is printed per serving, so it has to be converted before it can go in one.
REFERENCE_GRAMS = 100.0

# Serving units that convert to the per-100 g basis without a guess. This is
# deliberately the same set ``usda_client.parse_serving`` accepts, so a label
# read and a USDA record agree on when ``serving_size_grams`` is knowable.
# ``ml`` assumes 1 g/ml, which is the same assumption /search_food already
# ships; "1 scoop", "1 bar" and "1 cup" convert to nothing and stay null.
CONVERTIBLE_SERVING_UNITS = ("g", "gram", "grams", "ml")

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

# How many USDA lookups one meal may have in flight at once.
#
# The lookups are blocking HTTP waits, not computation, so threads are the
# right tool and the GIL is not a factor. The number is a deliberate ceiling
# rather than "one thread per food": a photo of a buffet can detect a dozen
# items, and neither our socket budget nor USDA's rate limit should scale with
# how busy the plate was. Four covers the overwhelmingly common 1-4 item meal
# in a single round-trip's worth of wall time.
USDA_LOOKUP_WORKERS = 4


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


# --- nutrition label mode ---------------------------------------------------
#
# Everything from here to ``analyze_label`` is pure except the one vision
# call, and none of it touches the meal path.

def normalize_mode(value) -> str:
    """Validate the request's ``mode``. Returns the mode to run.

    Absent, null or blank means :data:`MODE_MEAL` -- every client shipped
    before this feature existed sends no mode and must keep getting exactly
    what it got before. Anything else unrecognised is a client bug worth a
    400 rather than a silent fallback to meal mode: a caller that asked for a
    label read and quietly got a portion estimate of a cardboard box is the
    precise failure this feature exists to prevent.
    """
    if value is None:
        return MODE_MEAL
    if not isinstance(value, str):
        raise ValueError(
            f"'mode' must be a string, one of: {', '.join(SUPPORTED_MODES)}."
        )
    mode = value.strip().lower()
    if not mode:
        return MODE_MEAL
    if mode not in SUPPORTED_MODES:
        raise ValueError(
            f"Unsupported mode {value.strip()!r}. Supported modes: "
            f"{', '.join(SUPPORTED_MODES)}."
        )
    return mode


def label_number(value):
    """Coerce one printed figure to a float, or ``None``.

    ``None`` means the panel did not print it. **That is not zero**, and the
    two must never collapse into each other: a panel with no fibre row is
    unknown fibre, while a printed "Dietary Fiber 0 g" is a real zero. So a
    genuine 0 survives, and everything unusable -- absent, non-numeric, NaN,
    negative -- becomes ``None``.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    if result < 0:
        return None
    return result


def _clean_text(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def serving_grams(size, unit) -> float | None:
    """The serving weight in grams, or ``None`` when it does not convert.

    ``None`` is the honest answer for "1 scoop" or "1 bar". Guessing what a
    scoop weighs would put a fabricated number underneath every macro on the
    panel, because the whole per-100 g conversion divides by this figure.
    """
    grams = label_number(size)
    if grams is None or grams <= 0:
        return None
    unit_text = (_clean_text(unit) or "").lower()
    if unit_text not in CONVERTIBLE_SERVING_UNITS:
        return None
    return grams


def parse_label_serving(reading: dict) -> dict | None:
    """The serving block, in the same shape ``/search_food`` already emits.

    ``None`` when the panel declared no serving at all -- never a guessed
    100 g.
    """
    size = label_number(reading.get("serving_size"))
    if size is not None and size <= 0:
        size = None
    unit = _clean_text(reading.get("serving_size_unit"))
    household = _clean_text(reading.get("household_serving"))

    if size is None and household is None:
        return None

    return {
        "serving_size": size,
        "serving_size_unit": unit,
        "household_serving": household,
        "serving_size_grams": serving_grams(size, unit),
    }


def label_per_serving(reading: dict) -> dict:
    """The seven macros as printed, per serving. Absent rows stay ``None``."""
    printed = reading.get("per_serving") or {}
    return {field: label_number(printed.get(field)) for field in MACRO_FIELDS}


def label_micronutrients_per_serving(reading: dict) -> dict:
    """The printed micronutrient rows, per serving. Sparse: rows the panel
    does not carry are absent rather than zero."""
    printed = reading.get("micronutrients_per_serving") or {}
    result = {}
    for name, raw in sorted(printed.items()):
        value = label_number(raw)
        if value is not None:
            result[str(name)] = value
    return result


def per_serving_to_per_100g(per_serving: dict, grams) -> dict | None:
    """Convert a printed per-serving column to the app's per-100 g basis.

    **This is the conversion the whole feature turns on.** Every other
    ``foods[]`` entry in this system is per 100 g with ``portion_grams`` as
    the basis, and the client rescales by ``macro x new_grams /
    portion_grams``. A label is per *serving*. Passing a 60 g bar's printed
    20 g of protein straight through as a per-100 g figure understates it by
    40% -- the correct value is 20 x 100 / 60 = 33.3 g per 100 g.

    Returns ``None`` when there is no serving weight to divide by. There is
    no honest conversion without one, and inventing a basis here would
    corrupt every number above it.
    """
    basis = label_number(grams)
    if basis is None or basis <= 0:
        return None

    factor = REFERENCE_GRAMS / basis
    return {
        field: (None if value is None else float(value) * factor)
        for field, value in ((f, per_serving.get(f)) for f in MACRO_FIELDS)
    }


def _micronutrients_per_100g(micros: dict, grams: float) -> dict:
    factor = REFERENCE_GRAMS / float(grams)
    return {name: float(value) * factor for name, value in micros.items()}


def label_display_name(product_name, brand) -> str:
    """Brand first, without repeating a brand the product name already
    carries ("Acme" + "Acme Protein Bar" is "Acme Protein Bar")."""
    name = _clean_text(product_name) or ""
    make = _clean_text(brand) or ""
    if not name and not make:
        return "Packaged food"
    if not make:
        return name
    if not name:
        return make
    if name.lower().startswith(make.lower()):
        return name
    return f"{make} {name}"


def build_label_facts(reading: dict) -> dict:
    """The ``label`` block: the panel as printed, before any conversion."""
    return {
        "product_name": _clean_text(reading.get("product_name")),
        "brand": _clean_text(reading.get("brand")),
        "serving": parse_label_serving(reading),
        "servings_per_container": label_number(reading.get("servings_per_container")),
        "per_serving": label_per_serving(reading),
        "micronutrients_per_serving": label_micronutrients_per_serving(reading),
    }


def build_label_food_entry(label: dict) -> dict | None:
    """The panel as one ``foods[]`` entry on the per-100 g basis.

    ``None`` when the panel gave no serving weight, or carried no macro at
    all. The client is told why via ``needs_confirmation`` rather than handed
    an entry built on a guess.
    """
    serving = label.get("serving") or {}
    grams = serving.get("serving_size_grams")
    per_serving = label.get("per_serving") or {}

    if grams is None:
        return None
    if all(per_serving.get(field) is None for field in MACRO_FIELDS):
        return None

    per_100g = per_serving_to_per_100g(per_serving, grams)
    micros_100g = _micronutrients_per_100g(
        label.get("micronutrients_per_serving") or {}, grams
    )

    return {
        # -- identical to /analyze_food's foods[] entries -------------------
        "name": label_display_name(label.get("product_name"), label.get("brand")),
        # The reference basis, exactly as /search_food and /barcode report it,
        # so the client's existing portion arithmetic works unchanged.
        "portion_grams": REFERENCE_GRAMS,
        # A printed panel is not a probabilistic match. Whatever uncertainty
        # exists is in the reading of it, and that is reported separately as
        # the response-level confidence.
        "confidence": 1.0,
        "macros": scale_macros(per_100g, REFERENCE_GRAMS),
        "micronutrients": scale_micronutrients(micros_100g, REFERENCE_GRAMS),
        "source": "nutrition_label",
        "usda_description": None,
        "fdc_id": None,
        # -- additive, matching /search_food and /barcode -------------------
        "brand": label.get("brand"),
        "data_type": "Nutrition label",
        "gtin_upc": None,
        "serving": label.get("serving"),
        "basis": "per_100g",
    }


def evaluate_label_confirmation(
    label: dict,
    foods: list,
    overall_confidence: float,
    *,
    unreadable_reason: str | None = None,
    confidence_floor: float | None = None,
) -> tuple[bool, str | None]:
    """Decide whether a label read must be confirmed before it is logged.

    Deliberately separate from :func:`evaluate_confirmation`: that one's rules
    are about estimation ("no USDA match, so these are model estimates"),
    which say nothing about a panel that was read off packaging.
    """
    if confidence_floor is None:
        confidence_floor = min_confidence()

    if unreadable_reason:
        return True, unreadable_reason

    per_serving = (label or {}).get("per_serving") or {}
    has_any_macro = any(per_serving.get(field) is not None for field in MACRO_FIELDS)

    if not has_any_macro:
        return True, (
            "No nutrition panel could be read in this photo. Point the camera "
            "at the nutrition facts label, or enter the values manually."
        )

    if not foods:
        # The panel was read, but it printed no serving weight in grams, so
        # there is nothing to convert against. Worth its own message: the fix
        # is to re-shoot including the serving-size line, not to re-shoot the
        # panel.
        return True, (
            "The panel was read, but it does not state a serving weight in "
            "grams, so the printed values cannot be converted to a portion. "
            "Check the numbers and set the weight yourself."
        )

    reasons: list[str] = []

    if overall_confidence < confidence_floor:
        reasons.append(
            f"Confidence in the label reading {overall_confidence:.2f} is below "
            f"the {confidence_floor:.2f} threshold"
        )

    if per_serving.get("calories") is None:
        # Same rule /barcode applies to a product with no calorie figure on
        # file: show it, but make the user supply the missing number.
        reasons.append(
            "No calorie figure was printed on the panel; enter it from the "
            "package"
        )

    if not reasons:
        return False, None
    return True, "; ".join(reasons) + "."


def build_label_response(
    label: dict,
    foods: list,
    overall_confidence: float,
    model: str,
    *,
    unreadable_reason: str | None = None,
    notes: str = "",
    warnings: list | None = None,
) -> dict:
    """The label-mode response: the meal envelope, plus ``mode`` and ``label``.

    Every key ``/analyze_food`` already returns is still here and still means
    the same thing, so the client's single parser handles both modes. ``totals``
    is the sum over ``foods[]``, which in this mode is the per-100 g basis
    rather than a meal total -- there is no meal, only a product.
    """
    totals = aggregate_totals(foods)
    totals["micronutrients"] = aggregate_micronutrients(foods)

    needs_confirmation, reason = evaluate_label_confirmation(
        label,
        foods,
        overall_confidence,
        unreadable_reason=unreadable_reason,
    )

    return {
        "mode": MODE_NUTRITION_LABEL,
        "label": label,
        "foods": foods,
        "totals": totals,
        "needs_confirmation": needs_confirmation,
        "confirmation_reason": reason,
        "confidence": _round(clamp_confidence(overall_confidence), 2),
        "model": model,
        "analysis_version": ANALYSIS_VERSION,
        # No USDA call is made in this mode -- the panel is the source. The
        # flag still reports whether the service is configured, so it never
        # implies an outage that did not happen.
        "usda_available": usda_client.is_configured(),
        "notes": notes or "",
        "warnings": list(warnings or []),
        "analyzed_at": time.time(),
    }


def analyze_label(image_data: str, media_type: str | None = None) -> tuple[dict, int]:
    """Read one nutrition-label photo. Returns ``(response_body, http_status)``.

    Same failure contract as :func:`analyze_meal`: nothing raises for an
    expected failure, and every failure comes back with
    ``needs_confirmation: true`` rather than a guessed number.
    """
    try:
        reading = claude_vision.read_nutrition_label(image_data, media_type)
    except VisionRefusal as exc:
        return _failure_response(
            str(exc),
            model=claude_vision.model_id(),
            kind="refusal",
            retryable=False,
            mode=MODE_NUTRITION_LABEL,
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
            mode=MODE_NUTRITION_LABEL,
        ), status
    except Exception as exc:  # pragma: no cover - unexpected
        print(f"ERROR: unexpected failure in label step: {type(exc).__name__}: {exc}")
        return _failure_response(
            "Reading the nutrition label failed unexpectedly. Please try again "
            "or add the item manually.",
            model=claude_vision.model_id(),
            kind="internal_error",
            retryable=True,
            mode=MODE_NUTRITION_LABEL,
        ), 500

    label = build_label_facts(reading)

    unreadable_reason = None
    if not reading.get("panel_found"):
        unreadable_reason = _clean_text(reading.get("unreadable_reason")) or (
            "No readable nutrition panel was found in this photo. Try a "
            "straight-on, well-lit shot of the whole panel."
        )

    entry = None if unreadable_reason else build_label_food_entry(label)

    return (
        build_label_response(
            label,
            [entry] if entry else [],
            clamp_confidence(reading.get("confidence")),
            reading.get("model") or claude_vision.model_id(),
            unreadable_reason=unreadable_reason,
            notes=str(reading.get("notes") or ""),
        ),
        200,
    )


# --- USDA enrichment --------------------------------------------------------

def _prepare_foods(detected_foods: list) -> list:
    """Filter and normalize the model's food list before any lookup runs.

    Malformed and nameless entries are dropped here, so the surviving list is
    positionally stable: index ``i`` of the result is index ``i`` of the
    lookups and of the returned ``foods``.
    """
    prepared: list = []
    for raw in detected_foods:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        grams, was_clamped = clamp_portion(raw.get("estimated_portion_grams"))
        prepared.append(
            {
                "raw": raw,
                "name": name,
                "grams": grams,
                "was_clamped": was_clamped,
                "query": str(raw.get("usda_query") or name).strip() or name,
            }
        )
    return prepared


def _lookup_one(item: dict, lookup):
    """The lookup sequence for a single food: the model's USDA query first,
    then its display name if that missed. Identical to what the serial loop
    did per food -- only *where* it runs has changed."""
    record = lookup(item["query"])
    if record is None and item["query"].lower() != item["name"].lower():
        record = lookup(item["name"])
    return record


def _lookup_warning(error: BaseException) -> str:
    if isinstance(error, UsdaUnavailable):
        return f"USDA lookup unavailable: {error}"
    return f"USDA lookup error: {error}"


def _lookup_in_parallel(prepared: list, lookup, max_workers: int) -> list:
    """Look every prepared food up concurrently.

    Returns one ``(record, error)`` pair per food, **positionally** -- the list
    is indexed by the food's position in the meal, never by which lookup
    finished first. Exceptions are captured rather than raised so that one
    food's failure cannot cancel the rest; deciding what a failure *means* is
    left to the caller, which walks the results in meal order.

    ``first_failure`` is an optimisation only. Once a food has failed, every
    food after it is reported as estimated regardless of what its own lookup
    returns (that is the pre-existing rule), so a later food that has not
    started yet may as well skip the call instead of hammering a service
    already known to be down. It is compared by index, so it can never cause
    an *earlier* food to skip a call whose result would have been used.
    """
    results: list = [(None, None)] * len(prepared)
    if not prepared:
        return results

    if len(prepared) == 1:
        # A single food is not worth the cost of standing up a pool.
        try:
            results[0] = (_lookup_one(prepared[0], lookup), None)
        except Exception as exc:  # defensive: never fail the whole meal
            results[0] = (None, exc)
        return results

    state_lock = threading.Lock()
    first_failure: list = [None]

    def run(index: int):
        with state_lock:
            failed_at = first_failure[0]
        if failed_at is not None and index > failed_at:
            return None, None
        try:
            return _lookup_one(prepared[index], lookup), None
        except Exception as exc:  # defensive: never fail the whole meal
            with state_lock:
                if first_failure[0] is None or index < first_failure[0]:
                    first_failure[0] = index
            return None, exc

    workers = max(1, min(max_workers, len(prepared)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="usda") as pool:
        # ``Executor.map`` yields in submission order, not completion order,
        # which is exactly the ordering guarantee the response contract needs.
        results = list(pool.map(run, range(len(prepared))))
    return results


def enrich_with_usda(
    detected_foods: list, lookup=None, *, max_workers: int | None = None
) -> tuple[list, bool, list]:
    """Attach macros to each detected food.

    ``lookup`` is injected for testing; it defaults to :func:`usda_client.lookup`.
    Returns ``(foods, usda_available, warnings)``.

    A food that USDA cannot supply falls back to the model's own per-100 g
    estimate and is labelled ``source: "estimated"``. Micronutrients are never
    estimated -- an estimated food carries an empty micronutrient map.

    The lookups run concurrently (see :func:`_lookup_in_parallel`), because
    each one is a blocking HTTP wait and a four-food meal used to pay for four
    of them end to end. The results are then folded back together **in meal
    order** by the loop below, which is what keeps every observable outcome --
    ordering, warnings, and the "first failure disables USDA for the rest of
    the meal" rule -- identical to the serial version.
    """
    if lookup is None:
        lookup = usda_client.lookup
    if max_workers is None:
        max_workers = USDA_LOOKUP_WORKERS

    prepared = _prepare_foods(detected_foods)
    results = _lookup_in_parallel(prepared, lookup, max_workers)

    foods: list = []
    warnings: list[str] = []
    usda_available = True

    for item, (record, error) in zip(prepared, results):
        name = item["name"]
        grams = item["grams"]
        was_clamped = item["was_clamped"]

        if not usda_available:
            # A food earlier in the meal already failed. Serially this food
            # would never have been looked up at all, so whatever its own
            # concurrent lookup returned is discarded and it falls back to the
            # estimate -- same outcome, same warning count.
            record = None
        elif error is not None:
            # First failure disables USDA for the rest of this meal -- no
            # point retrying a down service once per food.
            usda_available = False
            warnings.append(_lookup_warning(error))
            record = None

        if record and record.get("per_100g"):
            macros = scale_macros(record["per_100g"], grams)
            micros = scale_micronutrients(record.get("micronutrients_per_100g"), grams)
            source = "usda"
            usda_description = record.get("description")
            fdc_id = record.get("fdc_id")
        else:
            raw = item["raw"]
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
                "confidence": clamp_confidence(item["raw"].get("confidence")),
                "macros": macros,
                "micronutrients": micros,
                "source": source,
                "usda_description": usda_description,
                "fdc_id": fdc_id,
            }
        )

    return foods, usda_available, warnings


# --- public entrypoint ------------------------------------------------------

def analyze_food(
    image_data: str,
    media_type: str | None = None,
    mode: str = MODE_MEAL,
    *,
    echo_mode: bool = False,
) -> tuple[dict, int]:
    """Dispatch one ``/analyze_food`` request to the mode it asked for.

    ``echo_mode`` controls whether a **meal** response carries a ``mode`` key.
    It is off by default so that a request sending no ``mode`` -- every client
    built before this feature -- gets output byte-identical to what it got
    before. A caller that explicitly asked for ``"meal"`` gets the echo, since
    it is asking a question the old server could not answer. Label mode always
    echoes: the client treats the echo as its proof the flag was honoured, and
    refuses to call anything "read from the label" without it.
    """
    if mode == MODE_NUTRITION_LABEL:
        return analyze_label(image_data, media_type)

    payload, status = analyze_meal(image_data, media_type)
    if echo_mode:
        payload["mode"] = MODE_MEAL
    return payload, status


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


def _failure_response(
    message: str,
    *,
    model: str,
    kind: str,
    retryable: bool,
    mode: str | None = None,
) -> dict:
    """An empty but well-shaped response. The client renders the same editor
    and asks the user to enter the meal, rather than showing invented numbers.

    ``mode`` is echoed on label-mode failures too. The client's gate is the
    echo, so without it a refusal to read a panel would be indistinguishable
    from a server that has never heard of label mode -- and the user would be
    told the feature is unavailable instead of being told what was wrong with
    the photo.
    """
    body = {} if mode is None else {"mode": mode}
    return body | {
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
        # Open Food Facts needs no credential, so there is nothing to report
        # about its configuration -- only whether repeat barcode scans are
        # being served from cache rather than re-hitting the network.
        "openfoodfacts_cache": openfoodfacts_client.cache_stats(),
        "supported_media_types": list(claude_vision.SUPPORTED_MEDIA_TYPES),
        "timestamp": time.time(),
    }
