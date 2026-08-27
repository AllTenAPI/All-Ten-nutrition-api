"""USDA FoodData Central lookup with an in-process cache.

Authoritative macro/micronutrient data for a food name. Everything returned by
this module is per 100 g -- scaling to an actual portion happens in
``nutrition_analyzer``.

Credentials: ``USDA_FDC_API_KEY`` is read from the environment at call time.
It is never logged, never echoed, and never written to disk. If it is missing
the module reports "unavailable" and the caller degrades gracefully.

Uses only the standard library (``urllib``) so the deploy image does not need
an HTTP client dependency.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

# Data types worth searching, most-specific first. Foundation and SR Legacy are
# lab-measured single ingredients; FNDDS covers prepared/composite dishes,
# which is what most photographed meals actually are.
DATA_TYPES = ["Foundation", "SR Legacy", "Survey (FNDDS)"]

DEFAULT_TIMEOUT_SECONDS = 8.0

# USDA FoodData Central nutrient ids -> our field names. Everything here is
# reported per 100 g of the food.
MACRO_NUTRIENT_IDS = {
    1008: "calories",   # Energy, kcal
    1003: "protein",    # Protein, g
    1005: "carbs",      # Carbohydrate, by difference, g
    1004: "fat",        # Total lipid (fat), g
    1079: "fiber",      # Fiber, total dietary, g
    2000: "sugar",      # Sugars, total including NLEA, g
    1093: "sodium",     # Sodium, Na, mg
}

# A deliberately small, defensible micronutrient set. See API_CONTRACT.md for
# why the previous ~60-field model was removed.
MICRO_NUTRIENT_IDS = {
    1087: "calcium",     # mg
    1089: "iron",        # mg
    1090: "magnesium",   # mg
    1091: "phosphorus",  # mg
    1092: "potassium",   # mg
    1095: "zinc",        # mg
    1106: "vitamin_a",   # mcg RAE
    1162: "vitamin_c",   # mg
    1114: "vitamin_d",   # mcg
    1178: "vitamin_b12", # mcg
    1177: "folate",      # mcg DFE
}

# Alternate energy nutrient ids (Atwater specific / general factors) used when
# a record has no plain 1008 Energy row.
ENERGY_FALLBACK_IDS = (2047, 2048)

MICRONUTRIENT_UNITS = {
    "calcium": "mg",
    "iron": "mg",
    "magnesium": "mg",
    "phosphorus": "mg",
    "potassium": "mg",
    "zinc": "mg",
    "vitamin_a": "mcg_rae",
    "vitamin_c": "mg",
    "vitamin_d": "mcg",
    "vitamin_b12": "mcg",
    "folate": "mcg_dfe",
}


class UsdaUnavailable(Exception):
    """Raised when USDA cannot be reached or is not configured.

    Callers are expected to catch this and degrade -- never to substitute
    invented numbers.
    """


# --- in-process cache -------------------------------------------------------

_cache: dict[str, dict | None] = {}
_cache_lock = threading.Lock()
_cache_stats = {"hits": 0, "misses": 0}


def normalize_food_name(name: str) -> str:
    """Cache key for a food name: lowercased, whitespace-collapsed."""
    return " ".join(str(name or "").lower().split())


def cache_stats() -> dict:
    with _cache_lock:
        return {
            "entries": len(_cache),
            "hits": _cache_stats["hits"],
            "misses": _cache_stats["misses"],
        }


def clear_cache() -> None:
    """Drop every cached lookup. Used by tests and by /debug maintenance."""
    with _cache_lock:
        _cache.clear()
        _cache_stats["hits"] = 0
        _cache_stats["misses"] = 0


def is_configured() -> bool:
    """True when a USDA key is present in the environment. Never returns it."""
    return bool(os.environ.get("USDA_FDC_API_KEY", "").strip())


# --- parsing ----------------------------------------------------------------

def parse_food_record(record: dict) -> dict:
    """Turn one USDA search hit into our per-100g nutrient shape.

    Pure function -- no network. Split out so it is directly testable.
    """
    macros: dict[str, float] = {}
    micros: dict[str, float] = {}
    energy_fallback: float | None = None

    for nutrient in record.get("foodNutrients") or []:
        # The search endpoint uses `nutrientId`; the detail endpoint nests it
        # under `nutrient.id`. Accept both.
        nutrient_id = nutrient.get("nutrientId")
        if nutrient_id is None:
            nutrient_id = (nutrient.get("nutrient") or {}).get("id")
        value = nutrient.get("value")
        if value is None:
            value = nutrient.get("amount")
        if nutrient_id is None or value is None:
            continue
        try:
            nutrient_id = int(nutrient_id)
            value = float(value)
        except (TypeError, ValueError):
            continue

        if nutrient_id in MACRO_NUTRIENT_IDS:
            macros[MACRO_NUTRIENT_IDS[nutrient_id]] = value
        elif nutrient_id in MICRO_NUTRIENT_IDS:
            micros[MICRO_NUTRIENT_IDS[nutrient_id]] = value
        elif nutrient_id in ENERGY_FALLBACK_IDS and energy_fallback is None:
            energy_fallback = value

    if "calories" not in macros and energy_fallback is not None:
        macros["calories"] = energy_fallback

    # Last resort: derive energy from Atwater factors rather than report zero
    # calories for a food that clearly has some.
    if "calories" not in macros and {"protein", "carbs", "fat"} & set(macros):
        macros["calories"] = (
            4.0 * macros.get("protein", 0.0)
            + 4.0 * macros.get("carbs", 0.0)
            + 9.0 * macros.get("fat", 0.0)
        )

    return {
        "fdc_id": record.get("fdcId"),
        "description": record.get("description"),
        "data_type": record.get("dataType"),
        "per_100g": macros,
        "micronutrients_per_100g": micros,
    }


def _http_get_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "all-ten-nutrition-api"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _search_uncached(query: str, timeout: float) -> dict | None:
    api_key = os.environ.get("USDA_FDC_API_KEY", "").strip()
    if not api_key:
        raise UsdaUnavailable(
            "USDA_FDC_API_KEY is not set. Set it in the deploy environment; "
            "the server will not guess nutrition data without it."
        )

    params = urllib.parse.urlencode(
        {
            "query": query,
            "api_key": api_key,
            "pageSize": 5,
            "dataType": ",".join(DATA_TYPES),
            "requireAllWords": "false",
        }
    )
    try:
        payload = _http_get_json(f"{SEARCH_URL}?{params}", timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            # Do not include the URL: it carries the key in the query string.
            raise UsdaUnavailable(
                f"USDA rejected the credentials (HTTP {exc.code}). "
                "Check USDA_FDC_API_KEY in the deploy environment."
            ) from None
        if exc.code == 429:
            raise UsdaUnavailable("USDA rate limit reached (HTTP 429).") from None
        raise UsdaUnavailable(f"USDA returned HTTP {exc.code}.") from None
    except urllib.error.URLError as exc:
        raise UsdaUnavailable(f"Could not reach USDA FoodData Central: {exc.reason}") from None
    except (TimeoutError, OSError) as exc:
        raise UsdaUnavailable(f"Could not reach USDA FoodData Central: {exc}") from None
    except json.JSONDecodeError:
        raise UsdaUnavailable("USDA returned a response that was not valid JSON.") from None

    foods = payload.get("foods") or []
    if not foods:
        return None
    return parse_food_record(foods[0])


def lookup(food_name: str, timeout: float | None = None) -> dict | None:
    """Look up one food, per 100 g. Returns ``None`` when USDA has no match.

    Raises :class:`UsdaUnavailable` when USDA is unreachable or unconfigured --
    that is a different condition from "no match" and the caller reports it
    differently.

    Results (including "no match") are cached in-process by normalized name so
    a meal with repeated foods, or repeated meals across requests, costs one
    call each.
    """
    key = normalize_food_name(food_name)
    if not key:
        return None

    with _cache_lock:
        if key in _cache:
            _cache_stats["hits"] += 1
            return _cache[key]

    result = _search_uncached(key, timeout or DEFAULT_TIMEOUT_SECONDS)

    with _cache_lock:
        _cache[key] = result
        _cache_stats["misses"] += 1
    return result
