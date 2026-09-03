"""USDA FoodData Central lookup with an in-process cache.

Authoritative macro/micronutrient data for a food name. Everything returned by
this module is per 100 g -- scaling to an actual portion happens in
``nutrition_analyzer``.

Credentials: ``USDA_FDC_API_KEY`` is read from the environment at call time.
It is never logged, never echoed, and never written to disk. If it is missing
the module reports "unavailable" and the caller degrades gracefully.

Uses only the standard library (``urllib``) so the deploy image does not need
an HTTP client dependency.

Data types and ranking
----------------------
"Branded" is now searchable, but it is ranked **below** every generic data
type. The ordering is spelled out in :data:`DATA_TYPE_RANK` and enforced by
:func:`rank_records`, so a query like "grilled chicken" resolves to
lab-measured Foundation/SR Legacy data rather than to whichever packaged
chicken product happens to score well on USDA's relevance sort. Branded is
what makes a *named packaged product* ("quest protein bar") findable at all.
"""

from __future__ import annotations

import json
import math
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

# Generic (non-branded) data types, most-specific first. Foundation and SR
# Legacy are lab-measured single ingredients; FNDDS covers prepared/composite
# dishes, which is what most photographed meals actually are.
GENERIC_DATA_TYPES = ["Foundation", "SR Legacy", "Survey (FNDDS)"]

# Manufacturer-declared label data for packaged products. Enormous (~2M
# records) and self-reported, so it is always a last resort.
BRANDED_DATA_TYPES = ["Branded"]

# Every searchable data type, in rank order.
DATA_TYPES = GENERIC_DATA_TYPES + BRANDED_DATA_TYPES

# Explicit, testable ranking. Lower sorts first. This is the single source of
# truth for "Branded is ranked last"; nothing else may hard-code an order.
DATA_TYPE_RANK = {
    "Foundation": 0,
    "SR Legacy": 1,
    "Survey (FNDDS)": 2,
    "Branded": 3,
}

# Anything USDA reports that we do not know about sorts after everything we do
# know about -- but still ahead of nothing, so it is never silently dropped.
UNKNOWN_DATA_TYPE_RANK = 99

# A branded record can never outrank a generic one.
BRANDED_RANK = DATA_TYPE_RANK["Branded"]

DEFAULT_TIMEOUT_SECONDS = 8.0

# Free-text search paging.
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 50
# Of each page of search results, at least this fraction of the slots is held
# for generic data types, so a flood of branded matches can never crowd out the
# lab-measured record the user probably wanted (and vice versa).
GENERIC_SLOT_FRACTION = 0.5

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
#
# One cache serves name lookups, free-text searches and barcode lookups. Keys
# are namespaced by prefix so the three never collide, and ``cache_stats()``
# keeps reporting a single set of counters to ``/debug``.

_cache: dict[str, object] = {}
_cache_lock = threading.Lock()
_cache_stats = {"hits": 0, "misses": 0}


def normalize_food_name(name: str) -> str:
    """Cache key for a food name: lowercased, whitespace-collapsed."""
    return " ".join(str(name or "").lower().split())


def normalize_barcode(barcode) -> str:
    """Reduce a scanned barcode to bare digits.

    Scanners and clients variously emit spaces, hyphens and a trailing
    newline. Anything that is not a digit is dropped; validation of the
    resulting length happens in the caller.
    """
    return "".join(ch for ch in str(barcode or "") if ch.isdigit())


def _gtin_key(barcode) -> str:
    """Comparison form for a GTIN/UPC/EAN.

    USDA stores the same product as a 12-digit UPC, a 13-digit EAN or a
    14-digit GTIN depending on the submission, so leading zeros carry no
    meaning for identity. Stripping them makes the three forms compare equal.
    """
    return normalize_barcode(barcode).lstrip("0")


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


def _cache_get(key: str):
    """Return ``(hit, value)``. ``hit`` distinguishes a cached ``None`` (a
    known "no match") from an absent entry."""
    with _cache_lock:
        if key in _cache:
            _cache_stats["hits"] += 1
            return True, _cache[key]
    return False, None


def _cache_put(key: str, value) -> None:
    with _cache_lock:
        _cache[key] = value
        _cache_stats["misses"] += 1


def is_configured() -> bool:
    """True when a USDA key is present in the environment. Never returns it."""
    return bool(os.environ.get("USDA_FDC_API_KEY", "").strip())


# --- ranking ----------------------------------------------------------------

def data_type_rank(data_type) -> int:
    """Rank one USDA data type. Lower sorts first; Branded is always last."""
    return DATA_TYPE_RANK.get(str(data_type or "").strip(), UNKNOWN_DATA_TYPE_RANK)


def is_branded(data_type) -> bool:
    return str(data_type or "").strip() == "Branded"


def rank_records(records: list) -> list:
    """Order parsed records by data type, preserving USDA's relevance order
    inside each tier.

    ``sorted`` is stable, so two Foundation hits keep the order USDA returned
    them in -- the only thing being imposed here is the tier ordering.
    """
    return sorted(records, key=lambda record: data_type_rank(record.get("data_type")))


# --- parsing ----------------------------------------------------------------

def _clean_text(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def parse_serving(record: dict) -> dict | None:
    """Serving information as USDA reports it, or ``None`` when it reports none.

    Branded records carry ``servingSize`` + ``servingSizeUnit`` and often a
    ``householdServingFullText`` ("1 bar"). Generic records usually carry
    neither, and an absent serving is reported as absent -- never as 100 g by
    assumption.
    """
    size = record.get("servingSize")
    unit = _clean_text(record.get("servingSizeUnit"))
    household = _clean_text(record.get("householdServingFullText"))

    try:
        size = float(size) if size is not None else None
    except (TypeError, ValueError):
        size = None
    if size is not None and (size != size or size <= 0):  # NaN or non-positive
        size = None

    if size is None and household is None:
        return None

    # Only g/ml convert cleanly to the per-100g basis; anything else is left
    # for the client to display rather than silently mis-scaled.
    grams = None
    if size is not None and unit and unit.lower() in ("g", "gram", "grams", "ml"):
        grams = size

    return {
        "serving_size": size,
        "serving_size_unit": unit,
        "household_serving": household,
        "serving_size_grams": grams,
    }


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

    data_type = record.get("dataType")
    # brandOwner is the manufacturer, brandName the marketing name; either may
    # be absent, and both are absent on every generic record.
    brand = _clean_text(record.get("brandName")) or _clean_text(record.get("brandOwner"))

    return {
        "fdc_id": record.get("fdcId"),
        "description": record.get("description"),
        "data_type": data_type,
        "brand": brand,
        "gtin_upc": _clean_text(record.get("gtinUpc")),
        "serving": parse_serving(record),
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


def _raw_search(
    query: str,
    timeout: float,
    *,
    data_types: list,
    page_size: int = 5,
    page_number: int = 1,
) -> dict:
    """One USDA search call. Returns the decoded payload.

    Raises :class:`UsdaUnavailable` for every transport, credential and
    protocol failure, so callers have exactly one exception type to catch.
    """
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
            "pageSize": page_size,
            "pageNumber": page_number,
            "dataType": ",".join(data_types),
            "requireAllWords": "false",
        }
    )
    try:
        return _http_get_json(f"{SEARCH_URL}?{params}", timeout)
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


def _search_uncached(query: str, timeout: float) -> dict | None:
    """Best single match for a food name.

    Generic data types are searched first and answer on their own. Branded is
    only consulted when no generic record matched at all -- that is what
    "ranked below Foundation / SR Legacy / FNDDS" means here, and it is why
    adding Branded cannot change the answer for a query that already worked.
    """
    for data_types in (GENERIC_DATA_TYPES, BRANDED_DATA_TYPES):
        payload = _raw_search(query, timeout, data_types=data_types, page_size=5)
        foods = payload.get("foods") or []
        if not foods:
            continue
        ranked = rank_records([parse_food_record(food) for food in foods])
        return ranked[0]
    return None


def lookup(food_name: str, timeout: float | None = None) -> dict | None:
    """Look up one food, per 100 g. Returns ``None`` when USDA has no match.

    Raises :class:`UsdaUnavailable` when USDA is unreachable or unconfigured --
    that is a different condition from "no match" and the caller reports it
    differently.

    Results (including "no match") are cached in-process by normalized name so
    a meal with repeated foods, or repeated meals across requests, costs one
    call each.
    """
    name = normalize_food_name(food_name)
    if not name:
        return None

    key = f"name:{name}"
    hit, value = _cache_get(key)
    if hit:
        return value

    result = _search_uncached(name, timeout or DEFAULT_TIMEOUT_SECONDS)
    _cache_put(key, result)
    return result


# --- free-text search -------------------------------------------------------

def _slot_split(limit: int) -> tuple[int, int]:
    """Split a page of ``limit`` slots into (generic, branded) quotas.

    Neither tier can take the whole page. Generic gets the larger half so the
    lab-measured records lead, but branded always keeps a reserved share --
    otherwise "quest protein bar" would be squeezed out by loosely-matching
    FNDDS entries.
    """
    generic = max(1, math.ceil(limit * GENERIC_SLOT_FRACTION))
    branded = max(1, limit - generic)
    return generic, branded


def clamp_search_paging(page, limit) -> tuple[int, int]:
    """Coerce client-supplied paging into supported bounds.

    Junk becomes the default rather than an error -- paging is not worth
    failing a request over.
    """
    try:
        page_number = int(page)
    except (TypeError, ValueError):
        page_number = 1
    if page_number < 1:
        page_number = 1

    try:
        page_limit = int(limit)
    except (TypeError, ValueError):
        page_limit = DEFAULT_SEARCH_LIMIT
    if page_limit < 1:
        page_limit = DEFAULT_SEARCH_LIMIT
    if page_limit > MAX_SEARCH_LIMIT:
        page_limit = MAX_SEARCH_LIMIT

    return page_number, page_limit


def _dedupe(records: list) -> list:
    """Drop repeated fdcIds, keeping the first (higher-ranked) occurrence."""
    seen: set = set()
    unique: list = []
    for record in records:
        fdc_id = record.get("fdc_id")
        if fdc_id is not None:
            if fdc_id in seen:
                continue
            seen.add(fdc_id)
        unique.append(record)
    return unique


def merge_tiers(generic: list, branded: list, limit: int) -> list:
    """Fill one page of ``limit`` slots from the two tiers.

    Each tier gets a reserved quota (see :func:`_slot_split`); whatever a tier
    does not use is handed to the other, so a page is never left short. The
    result is re-ranked, which means branded entries always sit below generic
    ones regardless of which quota they came from.
    """
    generic = _dedupe(rank_records(generic))
    branded = _dedupe(rank_records(branded))

    generic_quota, branded_quota = _slot_split(limit)
    # Hand each tier its own quota, then let the other tier absorb whatever
    # the first one left unused. Spare is computed from the original quotas so
    # the two adjustments cannot compound.
    generic_slots = generic_quota + max(0, branded_quota - len(branded))
    branded_slots = branded_quota + max(0, generic_quota - len(generic))

    chosen = generic[:generic_slots] + branded[:branded_slots]
    return rank_records(_dedupe(chosen))[:limit]


def _search_foods_uncached(query: str, page: int, limit: int, timeout: float) -> dict:
    tiers: dict[str, list] = {}
    total_hits = 0
    tier_more = False

    for data_types in (GENERIC_DATA_TYPES, BRANDED_DATA_TYPES):
        # Each tier is paged independently at the full page size; the quota
        # split is applied when the two are merged, so an under-filled tier
        # cannot leave the page short.
        payload = _raw_search(
            query, timeout, data_types=data_types, page_size=limit, page_number=page
        )
        foods = payload.get("foods") or []
        tiers[data_types[0]] = [parse_food_record(food) for food in foods]

        try:
            total_hits += int(payload.get("totalHits") or 0)
        except (TypeError, ValueError):
            pass
        try:
            total_pages = int(payload.get("totalPages") or 0)
        except (TypeError, ValueError):
            total_pages = 0
        if total_pages > page:
            tier_more = True

    generic = tiers.get(GENERIC_DATA_TYPES[0], [])
    branded = tiers.get(BRANDED_DATA_TYPES[0], [])
    records = merge_tiers(generic, branded, limit)

    return {
        "records": records,
        "total_hits": total_hits,
        "has_more": tier_more or len(generic) + len(branded) > len(records),
    }


def search_foods(
    query: str,
    page: int = 1,
    limit: int = DEFAULT_SEARCH_LIMIT,
    timeout: float | None = None,
) -> dict:
    """Free-text search. Returns ``{"records": [...], "total_hits", "has_more"}``.

    ``records`` are parsed per-100 g shapes ordered by :func:`rank_records`:
    Foundation, then SR Legacy, then FNDDS, then Branded. An empty ``records``
    list means USDA had no match -- it is not an error.

    Raises :class:`UsdaUnavailable` when USDA is unreachable or unconfigured.
    Cached per (query, page, limit).
    """
    normalized = normalize_food_name(query)
    if not normalized:
        return {"records": [], "total_hits": 0, "has_more": False}

    page, limit = clamp_search_paging(page, limit)
    key = f"search:{page}:{limit}:{normalized}"
    hit, value = _cache_get(key)
    if hit:
        return value

    result = _search_foods_uncached(
        normalized, page, limit, timeout or DEFAULT_TIMEOUT_SECONDS
    )
    _cache_put(key, result)
    return result


# --- barcode ----------------------------------------------------------------

def _barcode_uncached(barcode: str, timeout: float) -> dict | None:
    """Search USDA Branded for a GTIN/UPC and accept only an exact match.

    USDA's search index will happily return loosely-related products for a
    numeric query, so every hit is verified against its own ``gtinUpc`` before
    being returned. A near-miss is a miss: returning the wrong product's macros
    would be worse than returning nothing.
    """
    payload = _raw_search(
        barcode, timeout, data_types=BRANDED_DATA_TYPES, page_size=10
    )
    wanted = _gtin_key(barcode)
    for food in payload.get("foods") or []:
        if _gtin_key(food.get("gtinUpc")) == wanted:
            return parse_food_record(food)
    return None


def lookup_barcode(barcode: str, timeout: float | None = None) -> dict | None:
    """Look up a packaged product by barcode in USDA Branded.

    Returns the per-100 g record, or ``None`` when USDA does not know this
    barcode. Raises :class:`UsdaUnavailable` when USDA is unreachable or
    unconfigured. Cached (including misses) so a repeat scan of the same
    barcode costs no network call.
    """
    digits = normalize_barcode(barcode)
    if not digits:
        return None

    key = f"barcode:{digits}"
    hit, value = _cache_get(key)
    if hit:
        return value

    result = _barcode_uncached(digits, timeout or DEFAULT_TIMEOUT_SECONDS)
    _cache_put(key, result)
    return result
