"""Tests for /search_food and /barcode.

Stdlib ``unittest`` on purpose: the deploy image has no pytest.

Nothing here makes a network call, needs a credential, or touches the
Anthropic SDK -- which is itself part of what is being asserted: both
endpoints must resolve without an LLM.
"""

from __future__ import annotations

import unittest
from unittest import mock

import food_lookup as fl
import openfoodfacts_client as off
import usda_client as uc
from openfoodfacts_client import OpenFoodFactsUnavailable
from usda_client import UsdaUnavailable

CHICKEN = {
    "fdc_id": 171077,
    "description": "Chicken, broilers or fryers, breast, meat only, cooked",
    "data_type": "SR Legacy",
    "brand": None,
    "gtin_upc": None,
    "serving": None,
    "per_100g": {
        "calories": 165.0,
        "protein": 31.0,
        "carbs": 0.0,
        "fat": 3.6,
    },
    "micronutrients_per_100g": {"iron": 1.04, "potassium": 256.0},
}

QUEST_BAR = {
    "fdc_id": 2000001,
    "description": "Quest Protein Bar, Cookies & Cream",
    "data_type": "Branded",
    "brand": "Quest Nutrition",
    "gtin_upc": "0888849000371",
    "serving": {
        "serving_size": 60.0,
        "serving_size_unit": "g",
        "household_serving": "1 bar",
        "serving_size_grams": 60.0,
    },
    "per_100g": {
        "calories": 317.0,
        "protein": 33.3,
        "carbs": 40.0,
        "fat": 11.7,
        "fiber": 23.3,
        "sugar": 1.7,
        "sodium": 483.0,
    },
    "micronutrients_per_100g": {"calcium": 417.0},
}

OFF_BAR = {
    "fdc_id": None,
    "description": "Barre proteinee chocolat",
    "data_type": "Open Food Facts",
    "brand": "Some EU Brand",
    "gtin_upc": "3017620422003",
    "serving": {
        "serving_size": 40.0,
        "serving_size_unit": "g",
        "household_serving": "40 g",
        "serving_size_grams": 40.0,
    },
    "per_100g": {"calories": 400.0, "protein": 20.0, "carbs": 45.0, "fat": 15.0},
    "micronutrients_per_100g": {},
}


def _search_returning(records, total_hits=None, has_more=False):
    def fake(query, page=1, limit=20):
        return {
            "records": list(records),
            "total_hits": len(records) if total_hits is None else total_hits,
            "has_more": has_more,
        }

    return fake


# --- food entry shape -------------------------------------------------------

class FoodEntryShapeTests(unittest.TestCase):
    ANALYZE_FOOD_KEYS = {
        "name",
        "portion_grams",
        "confidence",
        "macros",
        "micronutrients",
        "source",
        "usda_description",
        "fdc_id",
    }

    def test_entry_carries_every_analyze_food_key(self):
        entry = fl.build_food_entry(CHICKEN, fl.SOURCE_USDA)
        self.assertTrue(self.ANALYZE_FOOD_KEYS.issubset(set(entry)))

    def test_macros_are_the_seven_contract_fields(self):
        entry = fl.build_food_entry(CHICKEN, fl.SOURCE_USDA)
        import nutrition_analyzer as na

        self.assertEqual(set(entry["macros"]), set(na.MACRO_FIELDS))

    def test_portion_is_the_reference_100g_so_macros_are_per_100g(self):
        entry = fl.build_food_entry(CHICKEN, fl.SOURCE_USDA)
        self.assertEqual(entry["portion_grams"], 100.0)
        self.assertEqual(entry["basis"], "per_100g")
        self.assertEqual(entry["macros"]["calories"], 165)
        self.assertEqual(entry["macros"]["protein"], 31.0)

    def test_unmeasured_macros_are_null_not_zero(self):
        entry = fl.build_food_entry(CHICKEN, fl.SOURCE_USDA)
        # USDA had no fiber/sugar/sodium row for this record.
        self.assertIsNone(entry["macros"]["fiber"])
        self.assertIsNone(entry["macros"]["sugar"])
        self.assertIsNone(entry["macros"]["sodium"])
        # ...but a genuine zero survives as a zero.
        self.assertEqual(entry["macros"]["carbs"], 0.0)

    def test_food_with_no_nutrients_at_all_is_all_null_never_zero(self):
        bare = dict(CHICKEN, per_100g={}, micronutrients_per_100g={})
        entry = fl.build_food_entry(bare, fl.SOURCE_USDA)
        self.assertTrue(all(v is None for v in entry["macros"].values()))
        self.assertEqual(entry["micronutrients"], {})

    def test_brand_and_data_type_are_exposed(self):
        entry = fl.build_food_entry(QUEST_BAR, fl.SOURCE_USDA)
        self.assertEqual(entry["brand"], "Quest Nutrition")
        self.assertEqual(entry["data_type"], "Branded")
        self.assertEqual(entry["serving"]["serving_size_grams"], 60.0)

    def test_generic_food_has_null_brand_and_null_serving(self):
        entry = fl.build_food_entry(CHICKEN, fl.SOURCE_USDA)
        self.assertIsNone(entry["brand"])
        self.assertIsNone(entry["serving"])

    def test_openfoodfacts_entry_has_null_fdc_id_and_null_usda_description(self):
        entry = fl.build_food_entry(OFF_BAR, fl.SOURCE_OPENFOODFACTS)
        self.assertIsNone(entry["fdc_id"])
        self.assertIsNone(entry["usda_description"])
        self.assertEqual(entry["source"], "openfoodfacts")
        self.assertEqual(entry["name"], "Barre proteinee chocolat")

    def test_micronutrients_are_scaled_to_the_reference_basis(self):
        entry = fl.build_food_entry(CHICKEN, fl.SOURCE_USDA)
        self.assertEqual(entry["micronutrients"]["iron"], 1.04)
        self.assertEqual(entry["micronutrients"]["potassium"], 256.0)


# --- /search_food -----------------------------------------------------------

class SearchFoodTests(unittest.TestCase):
    def test_happy_path(self):
        body, status = fl.search_food(
            {"query": "chicken"}, search=_search_returning([CHICKEN, QUEST_BAR])
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body["foods"]), 2)
        self.assertEqual(body["query"], "chicken")
        self.assertNotIn("error", body)
        self.assertEqual(body["foods"][0]["fdc_id"], 171077)

    def test_no_llm_call_is_made(self):
        import claude_vision

        with mock.patch.object(
            claude_vision, "detect_foods", side_effect=AssertionError("LLM called")
        ):
            body, status = fl.search_food(
                {"query": "chicken"}, search=_search_returning([CHICKEN])
            )
        self.assertEqual(status, 200)
        self.assertFalse(body["llm_used"])

    def test_ranking_is_preserved_from_the_client(self):
        # usda_client already ranks; search_food must not reorder.
        body, _ = fl.search_food(
            {"query": "bar"}, search=_search_returning([CHICKEN, QUEST_BAR])
        )
        self.assertEqual(
            [f["data_type"] for f in body["foods"]], ["SR Legacy", "Branded"]
        )

    def test_results_always_ask_the_user_to_pick(self):
        body, _ = fl.search_food(
            {"query": "chicken"}, search=_search_returning([CHICKEN])
        )
        self.assertTrue(body["needs_confirmation"])
        self.assertIsNotNone(body["confirmation_reason"])

    def test_paging_is_echoed_and_clamped(self):
        body, _ = fl.search_food(
            {"query": "chicken", "page": "3", "limit": 500},
            search=_search_returning([CHICKEN], has_more=True),
        )
        self.assertEqual(body["page"], 3)
        self.assertEqual(body["limit"], uc.MAX_SEARCH_LIMIT)
        self.assertTrue(body["has_more"])

    def test_alternate_query_keys_are_accepted(self):
        for key in ("query", "q", "text"):
            body, status = fl.search_food(
                {key: "chicken"}, search=_search_returning([CHICKEN])
            )
            self.assertEqual(status, 200, key)

    # -- malformed input ---------------------------------------------------

    def test_missing_query_is_a_400_bad_request(self):
        body, status = fl.search_food({}, search=_search_returning([CHICKEN]))
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["kind"], "bad_request")
        self.assertFalse(body["error"]["retryable"])
        self.assertEqual(body["foods"], [])

    def test_empty_query_is_a_400(self):
        body, status = fl.search_food(
            {"query": "   "}, search=_search_returning([CHICKEN])
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["kind"], "bad_request")

    def test_non_string_query_is_a_400(self):
        for value in (42, [], {}, True):
            body, status = fl.search_food(
                {"query": value}, search=_search_returning([CHICKEN])
            )
            self.assertEqual(status, 400, repr(value))
            self.assertEqual(body["error"]["kind"], "bad_request")

    def test_absurdly_long_query_is_a_400(self):
        body, status = fl.search_food(
            {"query": "x" * (fl.MAX_QUERY_LENGTH + 1)},
            search=_search_returning([CHICKEN]),
        )
        self.assertEqual(status, 400)
        self.assertIn("limit", body["error"]["message"])

    def test_malformed_input_never_reaches_usda(self):
        def explode(*args, **kwargs):
            raise AssertionError("USDA was called for a malformed request")

        for payload in ({}, {"query": ""}, {"query": 7}):
            fl.search_food(payload, search=explode)

    # -- no results and outages -------------------------------------------

    def test_no_results_is_a_404_with_the_error_envelope(self):
        body, status = fl.search_food(
            {"query": "asdfghjkl"}, search=_search_returning([])
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["kind"], "not_found")
        self.assertFalse(body["error"]["retryable"])
        self.assertEqual(body["foods"], [])
        self.assertIn("asdfghjkl", body["error"]["message"])

    def test_usda_outage_is_a_retryable_upstream_error(self):
        def down(*args, **kwargs):
            raise UsdaUnavailable("USDA returned HTTP 503.")

        with mock.patch.object(uc, "is_configured", return_value=True):
            body, status = fl.search_food({"query": "chicken"}, search=down)

        self.assertEqual(status, 200)
        self.assertEqual(body["error"]["kind"], "upstream_error")
        self.assertTrue(body["error"]["retryable"])
        self.assertFalse(body["usda_available"])

    def test_missing_usda_key_is_a_non_retryable_misconfiguration(self):
        def down(*args, **kwargs):
            raise UsdaUnavailable("USDA_FDC_API_KEY is not set.")

        with mock.patch.object(uc, "is_configured", return_value=False):
            body, status = fl.search_food({"query": "chicken"}, search=down)

        self.assertEqual(status, 500)
        self.assertEqual(body["error"]["kind"], "misconfigured")
        self.assertFalse(body["error"]["retryable"])

    def test_unexpected_failure_is_contained(self):
        def boom(*args, **kwargs):
            raise RuntimeError("kaboom")

        body, status = fl.search_food({"query": "chicken"}, search=boom)
        self.assertEqual(status, 500)
        self.assertEqual(body["error"]["kind"], "internal_error")
        self.assertTrue(body["error"]["retryable"])
        self.assertNotIn("kaboom", body["error"]["message"])

    def test_error_envelope_shape_is_always_complete(self):
        for payload, search in (
            ({}, _search_returning([])),
            ({"query": "nope"}, _search_returning([])),
        ):
            body, _ = fl.search_food(payload, search=search)
            self.assertEqual(
                set(body["error"]), {"kind", "message", "retryable"}
            )
            self.assertTrue(body["needs_confirmation"])
            self.assertIsInstance(body["error"]["retryable"], bool)


# --- /barcode ---------------------------------------------------------------

class BarcodeLookupTests(unittest.TestCase):
    def _lookup(self, payload, usda_result=None, off_result=None,
                usda_exc=None, off_exc=None):
        def usda(barcode):
            if usda_exc:
                raise usda_exc
            return usda_result

        def openfoodfacts(barcode):
            if off_exc:
                raise off_exc
            return off_result

        return fl.barcode_lookup(payload, usda=usda, openfoodfacts=openfoodfacts)

    def test_usda_answers_first(self):
        body, status = self._lookup(
            {"barcode": "0888849000371"},
            usda_result=QUEST_BAR,
            off_result=OFF_BAR,
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["found"])
        self.assertEqual(body["source"], "usda")
        self.assertEqual(body["foods"][0]["brand"], "Quest Nutrition")

    def test_openfoodfacts_answers_when_usda_misses(self):
        body, status = self._lookup(
            {"barcode": "3017620422003"}, usda_result=None, off_result=OFF_BAR
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["found"])
        self.assertEqual(body["source"], "openfoodfacts")
        self.assertIsNone(body["foods"][0]["fdc_id"])

    def test_openfoodfacts_answers_when_usda_is_down(self):
        body, status = self._lookup(
            {"barcode": "3017620422003"},
            usda_exc=UsdaUnavailable("USDA returned HTTP 503."),
            off_result=OFF_BAR,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["source"], "openfoodfacts")
        self.assertTrue(body["warnings"])

    def test_no_llm_call_is_made(self):
        import claude_vision

        with mock.patch.object(
            claude_vision, "detect_foods", side_effect=AssertionError("LLM called")
        ):
            body, status = self._lookup(
                {"barcode": "0888849000371"}, usda_result=QUEST_BAR
            )
        self.assertEqual(status, 200)
        self.assertFalse(body["llm_used"])

    def test_exact_match_needs_no_confirmation(self):
        body, _ = self._lookup({"barcode": "0888849000371"}, usda_result=QUEST_BAR)
        self.assertFalse(body["needs_confirmation"])
        self.assertIsNone(body["confirmation_reason"])

    def test_product_with_no_nutrition_data_asks_the_user_and_never_shows_zero(self):
        bare = dict(OFF_BAR, per_100g={}, micronutrients_per_100g={})
        body, status = self._lookup({"barcode": "3017620422003"}, off_result=bare)
        self.assertEqual(status, 200)
        self.assertTrue(body["found"])
        self.assertTrue(body["needs_confirmation"])
        self.assertTrue(
            all(v is None for v in body["foods"][0]["macros"].values())
        )

    # -- not found ---------------------------------------------------------

    def test_neither_source_knows_it_is_a_clean_404(self):
        body, status = self._lookup({"barcode": "0000000000000"})
        self.assertEqual(status, 404)
        self.assertFalse(body["found"])
        self.assertIsNone(body["source"])
        self.assertEqual(body["foods"], [])
        self.assertEqual(body["error"]["kind"], "not_found")
        self.assertFalse(body["error"]["retryable"])

    def test_not_found_never_fabricates_macros(self):
        body, _ = self._lookup({"barcode": "0000000000000"})
        self.assertEqual(body["foods"], [])
        self.assertNotIn("totals", body)

    def test_an_outage_is_not_reported_as_not_found(self):
        # USDA missed but Open Food Facts never answered -- "not found" would
        # be a claim the server is not entitled to make.
        body, status = self._lookup(
            {"barcode": "3017620422003"},
            usda_result=None,
            off_exc=OpenFoodFactsUnavailable("Could not reach Open Food Facts"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["error"]["kind"], "upstream_error")
        self.assertTrue(body["error"]["retryable"])
        self.assertFalse(body["found"])

    def test_both_sources_down_is_a_retryable_upstream_error(self):
        body, status = self._lookup(
            {"barcode": "3017620422003"},
            usda_exc=UsdaUnavailable("down"),
            off_exc=OpenFoodFactsUnavailable("down"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["error"]["kind"], "upstream_error")
        self.assertEqual(len(body["warnings"]), 2)

    def test_unexpected_source_failure_is_contained(self):
        body, status = self._lookup(
            {"barcode": "3017620422003"},
            usda_exc=RuntimeError("kaboom"),
            off_exc=RuntimeError("kaboom"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["error"]["kind"], "upstream_error")
        self.assertNotIn("kaboom", body["error"]["message"])

    # -- malformed input ---------------------------------------------------

    def test_missing_barcode_is_a_400(self):
        body, status = self._lookup({})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["kind"], "bad_request")

    def test_barcode_with_no_digits_is_a_400(self):
        body, status = self._lookup({"barcode": "not-a-barcode"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["kind"], "bad_request")

    def test_wrong_length_barcode_is_a_400(self):
        for value in ("123", "1" * 15):
            body, status = self._lookup({"barcode": value})
            self.assertEqual(status, 400, value)
            self.assertIn("digits", body["error"]["message"])

    def test_non_string_barcode_is_a_400(self):
        for value in ([], {}, 3.5):
            body, status = self._lookup({"barcode": value})
            self.assertEqual(status, 400, repr(value))

    def test_integer_barcode_is_accepted(self):
        body, status = self._lookup({"barcode": 888849000371}, usda_result=QUEST_BAR)
        self.assertEqual(status, 200)
        self.assertEqual(body["barcode"], "888849000371")

    def test_separators_are_stripped_and_the_normalized_form_is_echoed(self):
        body, status = self._lookup(
            {"barcode": " 0888-849 000371\n"}, usda_result=QUEST_BAR
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["barcode"], "0888849000371")

    def test_alternate_barcode_keys_are_accepted(self):
        for key in ("barcode", "code", "upc", "ean"):
            body, status = self._lookup({key: "0888849000371"}, usda_result=QUEST_BAR)
            self.assertEqual(status, 200, key)

    def test_malformed_input_never_reaches_a_source(self):
        def explode(barcode):
            raise AssertionError("a source was called for a malformed barcode")

        for payload in ({}, {"barcode": "abc"}, {"barcode": "12"}):
            fl.barcode_lookup(payload, usda=explode, openfoodfacts=explode)


# --- caching ----------------------------------------------------------------

class BarcodeCachingTests(unittest.TestCase):
    """A repeat scan of the same barcode must not re-hit the network."""

    def setUp(self):
        uc.clear_cache()
        off.clear_cache()

    def tearDown(self):
        uc.clear_cache()
        off.clear_cache()

    def test_usda_barcode_hit_is_cached_across_requests(self):
        calls = {"n": 0}

        def fake_search(query, timeout, *, data_types, page_size=5, page_number=1):
            calls["n"] += 1
            return {
                "foods": [
                    {
                        "fdcId": 2000001,
                        "description": "Quest Protein Bar",
                        "dataType": "Branded",
                        "gtinUpc": "0888849000371",
                        "foodNutrients": [{"nutrientId": 1008, "value": 317.0}],
                    }
                ]
            }

        with mock.patch.object(uc, "_raw_search", fake_search):
            first, _ = fl.barcode_lookup({"barcode": "0888849000371"})
            second, _ = fl.barcode_lookup({"barcode": "888849000371"})

        self.assertTrue(first["found"])
        self.assertTrue(second["found"])
        self.assertEqual(calls["n"], 1, "second scan re-hit the network")
        self.assertEqual(uc.cache_stats()["hits"], 1)

    def test_openfoodfacts_hit_is_cached_across_requests(self):
        calls = {"n": 0}

        def fake_get(url, timeout):
            calls["n"] += 1
            return 200, {
                "status": 1,
                "product": {
                    "code": "3017620422003",
                    "product_name": "Nutella",
                    "brands": "Ferrero",
                    "nutriments": {"energy-kcal_100g": 539},
                },
            }

        # USDA misses, Open Food Facts answers -- twice.
        with mock.patch.object(uc, "_raw_search", return_value={"foods": []}):
            with mock.patch.object(off, "_http_get_json", fake_get):
                first, _ = fl.barcode_lookup({"barcode": "3017620422003"})
                second, _ = fl.barcode_lookup({"barcode": "3017620422003"})

        self.assertEqual(first["source"], "openfoodfacts")
        self.assertEqual(second["source"], "openfoodfacts")
        self.assertEqual(calls["n"], 1, "second scan re-hit Open Food Facts")
        self.assertEqual(off.cache_stats()["hits"], 1)

    def test_a_miss_is_cached_too(self):
        usda_calls = {"n": 0}
        off_calls = {"n": 0}

        def fake_usda(query, timeout, *, data_types, page_size=5, page_number=1):
            usda_calls["n"] += 1
            return {"foods": []}

        def fake_get(url, timeout):
            off_calls["n"] += 1
            return 200, {"status": 0, "status_verbose": "product not found"}

        with mock.patch.object(uc, "_raw_search", fake_usda):
            with mock.patch.object(off, "_http_get_json", fake_get):
                fl.barcode_lookup({"barcode": "9999999999999"})
                body, status = fl.barcode_lookup({"barcode": "9999999999999"})

        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["kind"], "not_found")
        self.assertEqual(usda_calls["n"], 1)
        self.assertEqual(off_calls["n"], 1)

    def test_search_results_are_cached(self):
        calls = {"n": 0}

        def fake_search(query, timeout, *, data_types, page_size, page_number=1):
            calls["n"] += 1
            return {
                "foods": [
                    {
                        "fdcId": 1,
                        "description": "Chicken",
                        "dataType": "Foundation",
                        "foodNutrients": [{"nutrientId": 1008, "value": 165.0}],
                    }
                ],
                "totalHits": 1,
                "totalPages": 1,
            }

        with mock.patch.object(uc, "_raw_search", fake_search):
            fl.search_food({"query": "chicken"})
            fl.search_food({"query": "  Chicken  "})

        self.assertEqual(calls["n"], 2, "one call per data-type tier, once only")
        self.assertEqual(uc.cache_stats()["hits"], 1)


if __name__ == "__main__":
    unittest.main()
