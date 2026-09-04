"""Free-text food search and barcode lookup -- the two zero-LLM endpoints.

Neither ``/search_food`` nor ``/barcode`` makes an Anthropic call. That is the
whole point of them: the overwhelmingly common cases (the user knows what they
ate, or is holding the package) should cost a database query, not a vision
call. ``/analyze_food`` stays for the case only a model can do -- looking at a
plate and estimating how much is on it.

Both endpoints emit the **same per-food shape as ``/analyze_food``'s
``foods[]`` entries**, plus a few additive keys (``brand``, ``data_type``,
``serving``, ``basis``), so the client parses one food object everywhere.

Sources
-------
``/search_food``   USDA FoodData Central, all four data types, Branded ranked
                   last (see ``usda_client``).
``/barcode``       USDA Branded first, then Open Food Facts. The answering
                   source is reported in ``source``. When neither knows the
                   barcode the response is a clean not-found -- macros are
                   never invented for an unknown product.
"""

from __future__ import annotations

import time

import nutrition_analyzer as na
import openfoodfacts_client
import usda_client
from openfoodfacts_client import OpenFoodFactsUnavailable
from usda_client import UsdaUnavailable

# The nutrient basis every entry from these endpoints is reported on. There is
# no photo and therefore no portion estimate, so each candidate is returned per
# 100 g and the client scales it once the user picks a portion -- exactly the
# arithmetic it already does for /analyze_food.
REFERENCE_GRAMS = 100.0

# Free-text queries longer than this are a paste accident, not a food name.
MAX_QUERY_LENGTH = 200

# GTIN-8 through GTIN-14 covers EAN-8, UPC-E, UPC-A, EAN-13 and GTIN-14.
MIN_BARCODE_DIGITS = 8
MAX_BARCODE_DIGITS = 14

SOURCE_USDA = "usda"
SOURCE_OPENFOODFACTS = "openfoodfacts"


# --- shared shaping ---------------------------------------------------------

def build_food_entry(record: dict, source: str) -> dict:
    """One candidate, in ``/analyze_food``'s ``foods[]`` shape.

    ``portion_grams`` is the reference 100 g rather than an estimate, so
    ``macros`` are the per-100 g values and the client's existing
    ``macro x new_grams / portion_grams`` rescaling works unchanged.

    ``confidence`` is 1.0 because nothing here is estimated: the portion is
    exactly the reference basis and the macros come from a database. Whether
    this is the *right* food is the user's call, which is what the
    response-level ``needs_confirmation`` is for.
    """
    per_100g = record.get("per_100g") or {}
    macros = na.scale_macros(per_100g, REFERENCE_GRAMS)
    micros = na.scale_micronutrients(
        record.get("micronutrients_per_100g"), REFERENCE_GRAMS
    )

    description = record.get("description")

    return {
        # -- identical to /analyze_food's foods[] entries -------------------
        "name": description,
        "portion_grams": REFERENCE_GRAMS,
        "confidence": 1.0,
        "macros": macros,
        "micronutrients": micros,
        "source": source,
        "usda_description": description if source == SOURCE_USDA else None,
        "fdc_id": record.get("fdc_id"),
        # -- additive, for the search/barcode pickers -----------------------
        "brand": record.get("brand"),
        "data_type": record.get("data_type"),
        "gtin_upc": record.get("gtin_upc"),
        "serving": record.get("serving"),
        "basis": "per_100g",
    }


def _envelope(**fields) -> dict:
    base = {
        "foods": [],
        "needs_confirmation": True,
        "confirmation_reason": None,
        "analysis_version": na.ANALYSIS_VERSION,
        # These endpoints never call a model. Stated in the payload so the
        # client (and anyone reading a log) can see it without inference.
        "llm_used": False,
        "warnings": [],
        "analyzed_at": time.time(),
    }
    base.update(fields)
    return base


def _error(kind: str, message: str, retryable: bool, **fields) -> dict:
    return _envelope(
        confirmation_reason=message,
        error={"kind": kind, "message": message, "retryable": retryable},
        **fields,
    )


def _usda_failure(exc: Exception) -> tuple[str, int, bool]:
    """Classify a USDA failure into ``(kind, http_status, retryable)``.

    A missing key is an operator problem and not worth a client retry; every
    other failure is transient by assumption.
    """
    if not usda_client.is_configured():
        return "misconfigured", 500, False
    return "upstream_error", 200, True


# --- POST /search_food ------------------------------------------------------

def search_food(payload: dict, search=None) -> tuple[dict, int]:
    """Free-text food search. Returns ``(response_body, http_status)``.

    ``search`` is injected for testing; it defaults to
    :func:`usda_client.search_foods`. No LLM call is made on any path.
    """
    if search is None:
        search = usda_client.search_foods

    raw_query = payload.get("query")
    if raw_query is None:
        raw_query = payload.get("q") or payload.get("text")

    if not isinstance(raw_query, str):
        if raw_query is None:
            return _error(
                "bad_request",
                "A 'query' string is required.",
                False,
                query=None,
            ), 400
        return _error(
            "bad_request",
            "'query' must be a string.",
            False,
            query=None,
        ), 400

    query = raw_query.strip()
    if not query:
        return _error(
            "bad_request", "'query' must not be empty.", False, query=""
        ), 400
    if len(query) > MAX_QUERY_LENGTH:
        return _error(
            "bad_request",
            f"'query' is {len(query)} characters; the limit is {MAX_QUERY_LENGTH}.",
            False,
            query=None,
        ), 400

    page, limit = usda_client.clamp_search_paging(
        payload.get("page"), payload.get("limit")
    )

    try:
        result = search(query, page=page, limit=limit)
    except UsdaUnavailable as exc:
        kind, status, retryable = _usda_failure(exc)
        return _error(
            kind, str(exc), retryable,
            query=query, page=page, limit=limit, usda_available=False,
        ), status
    except Exception as exc:  # defensive: never leak a stack trace to a client
        print(f"ERROR: unexpected failure in search_food: {type(exc).__name__}: {exc}")
        return _error(
            "internal_error",
            "The food search failed unexpectedly. Please try again or add the "
            "meal manually.",
            True,
            query=query, page=page, limit=limit,
        ), 500

    records = result.get("records") or []
    foods = [build_food_entry(record, SOURCE_USDA) for record in records]

    if not foods:
        return _error(
            "not_found",
            f'No food matched "{query}". Try a simpler search, or add the meal '
            "manually.",
            False,
            query=query,
            page=page,
            limit=limit,
            total_hits=0,
            has_more=False,
            usda_available=True,
        ), 404

    return _envelope(
        query=query,
        foods=foods,
        page=page,
        limit=limit,
        total_hits=result.get("total_hits", len(foods)),
        has_more=bool(result.get("has_more")),
        usda_available=True,
        needs_confirmation=True,
        confirmation_reason=(
            "These are search candidates, not a measurement of your meal. Pick "
            "the one that matches and set the portion before logging."
        ),
    ), 200


# --- POST /barcode ----------------------------------------------------------

def _validate_barcode(payload: dict) -> tuple[str | None, dict | None, int]:
    """Returns ``(digits, error_body, status)``. ``digits`` is set on success."""
    raw = payload.get("barcode")
    if raw is None:
        raw = payload.get("code") or payload.get("upc") or payload.get("ean")

    if raw is None:
        return None, _error(
            "bad_request", "A 'barcode' is required.", False, barcode=None, found=False
        ), 400

    if not isinstance(raw, (str, int)):
        return None, _error(
            "bad_request",
            "'barcode' must be a string of digits.",
            False,
            barcode=None,
            found=False,
        ), 400

    digits = usda_client.normalize_barcode(raw)
    if not digits:
        return None, _error(
            "bad_request",
            "'barcode' contains no digits.",
            False,
            barcode=None,
            found=False,
        ), 400
    if not MIN_BARCODE_DIGITS <= len(digits) <= MAX_BARCODE_DIGITS:
        return None, _error(
            "bad_request",
            f"'barcode' has {len(digits)} digits; a UPC/EAN/GTIN has "
            f"{MIN_BARCODE_DIGITS}-{MAX_BARCODE_DIGITS}.",
            False,
            barcode=digits,
            found=False,
        ), 400

    return digits, None, 200


def barcode_lookup(payload: dict, usda=None, openfoodfacts=None) -> tuple[dict, int]:
    """UPC/EAN lookup. Returns ``(response_body, http_status)``.

    USDA Branded is tried first (the key is already provisioned and the data is
    label-declared), then Open Food Facts. ``source`` names whichever answered.
    Both lookups are injected for testing.

    When neither source knows the barcode the result is a 404 with
    ``found: false`` and no foods. **No macros are ever synthesised for an
    unknown product.** When a source *errored* rather than missed, the response
    is a retryable upstream error instead -- reporting "not found" on the back
    of a failed request would be a lie the user cannot detect.
    """
    if usda is None:
        usda = usda_client.lookup_barcode
    if openfoodfacts is None:
        openfoodfacts = openfoodfacts_client.lookup_barcode

    digits, error_body, status = _validate_barcode(payload)
    if error_body is not None:
        return error_body, status

    warnings: list[str] = []
    record = None
    source = None
    usda_errored = False
    off_errored = False

    try:
        record = usda(digits)
        if record is not None:
            source = SOURCE_USDA
    except UsdaUnavailable as exc:
        usda_errored = True
        warnings.append(f"USDA barcode lookup unavailable: {exc}")
    except Exception as exc:  # defensive
        usda_errored = True
        warnings.append(f"USDA barcode lookup error: {type(exc).__name__}")
        print(f"ERROR: USDA barcode lookup: {type(exc).__name__}: {exc}")

    if record is None:
        try:
            record = openfoodfacts(digits)
            if record is not None:
                source = SOURCE_OPENFOODFACTS
        except OpenFoodFactsUnavailable as exc:
            off_errored = True
            warnings.append(f"Open Food Facts lookup unavailable: {exc}")
        except Exception as exc:  # defensive
            off_errored = True
            warnings.append(f"Open Food Facts lookup error: {type(exc).__name__}")
            print(f"ERROR: Open Food Facts lookup: {type(exc).__name__}: {exc}")

    if record is None:
        if usda_errored or off_errored:
            # At least one source never actually answered, so "not found" is
            # not a claim we are entitled to make.
            message = (
                "Could not check this barcode against every source. Please try "
                "again shortly."
            )
            return _error(
                "upstream_error", message, True,
                barcode=digits, found=False, source=None, warnings=warnings,
            ), 200

        return _error(
            "not_found",
            "No product matched this barcode in USDA or Open Food Facts. Add "
            "the item manually, or use the label.",
            False,
            barcode=digits, found=False, source=None, warnings=warnings,
        ), 404

    food = build_food_entry(record, source)

    # An exact barcode match identifies the product, so nothing needs
    # confirming -- unless the source has no calorie figure on file, in which
    # case the user has to supply the numbers rather than see a null total.
    if food["macros"].get("calories") is None:
        needs_confirmation = True
        reason = (
            f"{food['name']} was found, but the source has no nutrition data on "
            "file for it. Enter the values from the label."
        )
    else:
        needs_confirmation = False
        reason = None

    return _envelope(
        barcode=digits,
        found=True,
        source=source,
        foods=[food],
        needs_confirmation=needs_confirmation,
        confirmation_reason=reason,
        warnings=warnings,
    ), 200
