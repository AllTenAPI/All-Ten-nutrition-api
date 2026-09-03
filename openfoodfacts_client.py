"""Open Food Facts barcode lookup with an in-process cache.

The fallback behind USDA Branded for :func:`food_lookup.lookup_barcode`. Open
Food Facts is free, needs no key, is indexed directly by barcode, and has far
better coverage outside the United States -- which is exactly where USDA
Branded stops being useful.

Everything returned here is per 100 g, matching ``usda_client``'s shape, so
the two sources are interchangeable to the caller.

Two things this module will not do:

* **Invent a value.** Open Food Facts is crowd-sourced and frequently has
  partial nutriment data. A nutrient the contributor did not enter is absent
  from the result -- never present as ``0``.
* **Send a credential.** There is none to send. No key is read, and the only
  identifying header is a descriptive ``User-Agent``, per Open Food Facts'
  published etiquette for API consumers.

Standard library only (``urllib``), like ``usda_client``.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"

# Open Food Facts asks every API consumer to identify itself with an app name,
# a version and a contact. Anonymous or generic agents get rate-limited.
USER_AGENT = (
    "AllTenNutritionAPI/2.0 "
    "(All Ten habit-tracking app; +https://github.com/AllTenAPI/All-Ten-nutrition-api)"
)

DEFAULT_TIMEOUT_SECONDS = 8.0

# Only ask for what we parse. Open Food Facts product documents are large and
# the API charges nobody but does ask consumers not to pull whole documents.
REQUESTED_FIELDS = (
    "code",
    "product_name",
    "product_name_en",
    "generic_name",
    "brands",
    "quantity",
    "serving_size",
    "serving_quantity",
    "nutriments",
)

# Open Food Facts nutriment key -> (our field, multiplier to our unit).
#
# Open Food Facts stores every per-100 g nutriment in grams, including the
# minerals and vitamins. Our contract wants mg for most minerals and mcg for
# the vitamins that are conventionally reported that way, hence the factors.
MACRO_FIELDS = {
    "proteins_100g": ("protein", 1.0),        # g
    "carbohydrates_100g": ("carbs", 1.0),     # g
    "fat_100g": ("fat", 1.0),                 # g
    "fiber_100g": ("fiber", 1.0),             # g
    "sugars_100g": ("sugar", 1.0),            # g
    "sodium_100g": ("sodium", 1000.0),        # g -> mg
}

MICRO_FIELDS = {
    "calcium_100g": ("calcium", 1000.0),          # g -> mg
    "iron_100g": ("iron", 1000.0),                # g -> mg
    "magnesium_100g": ("magnesium", 1000.0),      # g -> mg
    "phosphorus_100g": ("phosphorus", 1000.0),    # g -> mg
    "potassium_100g": ("potassium", 1000.0),      # g -> mg
    "zinc_100g": ("zinc", 1000.0),                # g -> mg
    "vitamin-c_100g": ("vitamin_c", 1000.0),      # g -> mg
    "vitamin-a_100g": ("vitamin_a", 1_000_000.0), # g -> mcg RAE
    "vitamin-d_100g": ("vitamin_d", 1_000_000.0), # g -> mcg
    "vitamin-b12_100g": ("vitamin_b12", 1_000_000.0),  # g -> mcg
    "vitamin-b9_100g": ("folate", 1_000_000.0),   # g -> mcg DFE
}

# 1 g of salt contains ~0.393 g of sodium; Open Food Facts uses 2.5 as the
# divisor and so does the EU labelling regulation this comes from.
SALT_TO_SODIUM_DIVISOR = 2.5

KJ_PER_KCAL = 4.184


class OpenFoodFactsUnavailable(Exception):
    """Raised when Open Food Facts cannot be reached.

    Distinct from "this barcode is unknown", which is a plain ``None``.
    """


# --- in-process cache -------------------------------------------------------

_cache: dict[str, dict | None] = {}
_cache_lock = threading.Lock()
_cache_stats = {"hits": 0, "misses": 0}


def cache_stats() -> dict:
    with _cache_lock:
        return {
            "entries": len(_cache),
            "hits": _cache_stats["hits"],
            "misses": _cache_stats["misses"],
        }


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _cache_stats["hits"] = 0
        _cache_stats["misses"] = 0


def normalize_barcode(barcode) -> str:
    """Reduce a scanned barcode to bare digits."""
    return "".join(ch for ch in str(barcode or "") if ch.isdigit())


# --- parsing ----------------------------------------------------------------

def _number(value):
    """Parse a nutriment value, or ``None`` if it is not a usable number.

    ``None`` is returned for anything missing, blank, non-numeric, NaN or
    negative. A nutrient we cannot read is unknown, not zero.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result < 0:  # NaN or negative
        return None
    return result


def _clean_text(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def _parse_calories(nutriments: dict):
    """Energy per 100 g in kcal, or ``None``.

    Preference order: an explicit kcal figure, then kJ converted. ``energy_100g``
    is only trusted when the document says its unit is kcal -- Open Food Facts
    defaults that field to kJ, and reading it as kcal would understate a
    product by a factor of four.
    """
    kcal = _number(nutriments.get("energy-kcal_100g"))
    if kcal is not None:
        return kcal

    kj = _number(nutriments.get("energy-kj_100g"))
    if kj is not None:
        return kj / KJ_PER_KCAL

    unit = str(nutriments.get("energy_unit") or "").strip().lower()
    energy = _number(nutriments.get("energy_100g"))
    if energy is None:
        return None
    if unit == "kcal":
        return energy
    if unit in ("kj", ""):
        # Open Food Facts stores energy_100g in kJ when no unit is given.
        return energy / KJ_PER_KCAL
    return None


def parse_product(product: dict) -> dict:
    """Turn one Open Food Facts product document into our per-100 g shape.

    Pure function -- no network. Mirrors ``usda_client.parse_food_record``.
    """
    nutriments = product.get("nutriments") or {}

    macros: dict[str, float] = {}
    calories = _parse_calories(nutriments)
    if calories is not None:
        macros["calories"] = calories

    for key, (field, factor) in MACRO_FIELDS.items():
        value = _number(nutriments.get(key))
        if value is not None:
            macros[field] = value * factor

    # Many European products declare salt rather than sodium.
    if "sodium" not in macros:
        salt = _number(nutriments.get("salt_100g"))
        if salt is not None:
            macros["sodium"] = (salt / SALT_TO_SODIUM_DIVISOR) * 1000.0

    micros: dict[str, float] = {}
    for key, (field, factor) in MICRO_FIELDS.items():
        value = _number(nutriments.get(key))
        if value is not None:
            micros[field] = value * factor

    name = (
        _clean_text(product.get("product_name"))
        or _clean_text(product.get("product_name_en"))
        or _clean_text(product.get("generic_name"))
    )

    # `brands` is a comma-separated list; the first entry is the primary brand.
    brands = _clean_text(product.get("brands"))
    brand = _clean_text(brands.split(",")[0]) if brands else None

    return {
        # Open Food Facts has no FoodData Central id. Reporting null rather
        # than inventing one keeps `fdc_id` meaning exactly one thing.
        "fdc_id": None,
        "description": name,
        "data_type": "Open Food Facts",
        "brand": brand,
        "gtin_upc": _clean_text(product.get("code")),
        "serving": parse_serving(product),
        "per_100g": macros,
        "micronutrients_per_100g": micros,
    }


def parse_serving(product: dict) -> dict | None:
    """Serving information as Open Food Facts reports it, or ``None``."""
    household = _clean_text(product.get("serving_size"))
    grams = _number(product.get("serving_quantity"))
    if grams is not None and grams <= 0:
        grams = None

    if grams is None and household is None:
        return None

    return {
        "serving_size": grams,
        "serving_size_unit": "g" if grams is not None else None,
        "household_serving": household,
        "serving_size_grams": grams,
    }


# --- network ----------------------------------------------------------------

def _http_get_json(url: str, timeout: float) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _lookup_uncached(barcode: str, timeout: float) -> dict | None:
    params = urllib.parse.urlencode({"fields": ",".join(REQUESTED_FIELDS)})
    url = PRODUCT_URL.format(barcode=urllib.parse.quote(barcode)) + "?" + params

    try:
        _, payload = _http_get_json(url, timeout)
    except urllib.error.HTTPError as exc:
        code = exc.code
        # An HTTPError is a file-like object holding the (unread) response
        # body. Nothing here reads it, so close it rather than leaving it for
        # the garbage collector.
        exc.close()
        if code == 404:
            # v2 answers an unknown barcode with a 404. That is a clean miss,
            # not an outage.
            return None
        if code == 429:
            raise OpenFoodFactsUnavailable(
                "Open Food Facts rate limit reached (HTTP 429)."
            ) from None
        raise OpenFoodFactsUnavailable(
            f"Open Food Facts returned HTTP {code}."
        ) from None
    except urllib.error.URLError as exc:
        raise OpenFoodFactsUnavailable(
            f"Could not reach Open Food Facts: {exc.reason}"
        ) from None
    except (TimeoutError, OSError) as exc:
        raise OpenFoodFactsUnavailable(f"Could not reach Open Food Facts: {exc}") from None
    except json.JSONDecodeError:
        raise OpenFoodFactsUnavailable(
            "Open Food Facts returned a response that was not valid JSON."
        ) from None

    if not isinstance(payload, dict):
        raise OpenFoodFactsUnavailable("Open Food Facts returned an unexpected payload.")

    # Some deployments answer 200 with status 0 rather than a 404.
    if payload.get("status") in (0, "0"):
        return None

    product = payload.get("product")
    if not isinstance(product, dict) or not product:
        return None

    parsed = parse_product(product)
    if not parsed.get("description"):
        # A document with no product name is not something worth showing a
        # user, even when it carries nutriments.
        return None
    return parsed


def lookup_barcode(barcode: str, timeout: float | None = None) -> dict | None:
    """Look up a packaged product by barcode. ``None`` when it is unknown.

    Raises :class:`OpenFoodFactsUnavailable` when the service cannot be
    reached -- a different condition from "unknown barcode", and reported
    differently by the caller.

    Results (including misses) are cached in-process, so a repeat scan of the
    same barcode costs no network call.
    """
    digits = normalize_barcode(barcode)
    if not digits:
        return None

    with _cache_lock:
        if digits in _cache:
            _cache_stats["hits"] += 1
            return _cache[digits]

    result = _lookup_uncached(digits, timeout or DEFAULT_TIMEOUT_SECONDS)

    with _cache_lock:
        _cache[digits] = result
        _cache_stats["misses"] += 1
    return result
