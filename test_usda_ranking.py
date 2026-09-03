"""Tests for USDA data-type ranking and the Branded fallback.

Stdlib ``unittest`` on purpose: the deploy image has no pytest.
Nothing here makes a network call or needs a credential -- the HTTP layer is
stubbed at ``usda_client._raw_search``.
"""

from __future__ import annotations

import unittest
from unittest import mock

import usda_client as uc


def _record(fdc_id, description, data_type, **extra):
    """A minimal USDA search hit."""
    record = {
        "fdcId": fdc_id,
        "description": description,
        "dataType": data_type,
        "foodNutrients": [
            {"nutrientId": 1008, "value": 165.0},
            {"nutrientId": 1003, "value": 31.0},
        ],
    }
    record.update(extra)
    return record


class DataTypeRankTests(unittest.TestCase):
    def test_ranking_is_foundation_sr_fndds_then_branded(self):
        order = sorted(uc.DATA_TYPE_RANK, key=uc.DATA_TYPE_RANK.get)
        self.assertEqual(
            order, ["Foundation", "SR Legacy", "Survey (FNDDS)", "Branded"]
        )

    def test_branded_ranks_below_every_generic_type(self):
        branded = uc.data_type_rank("Branded")
        for generic in uc.GENERIC_DATA_TYPES:
            self.assertLess(
                uc.data_type_rank(generic),
                branded,
                f"{generic} must outrank Branded",
            )

    def test_branded_is_searchable_but_listed_last(self):
        self.assertIn("Branded", uc.DATA_TYPES)
        self.assertEqual(uc.DATA_TYPES[-1], "Branded")

    def test_unknown_data_type_sorts_after_everything_known(self):
        self.assertGreater(uc.data_type_rank("Experimental"), uc.BRANDED_RANK)
        self.assertGreater(uc.data_type_rank(None), uc.BRANDED_RANK)

    def test_is_branded(self):
        self.assertTrue(uc.is_branded("Branded"))
        self.assertFalse(uc.is_branded("Foundation"))
        self.assertFalse(uc.is_branded(None))


class RankRecordsTests(unittest.TestCase):
    def test_generic_records_sort_ahead_of_branded(self):
        records = [
            {"fdc_id": 1, "data_type": "Branded"},
            {"fdc_id": 2, "data_type": "Survey (FNDDS)"},
            {"fdc_id": 3, "data_type": "Foundation"},
            {"fdc_id": 4, "data_type": "SR Legacy"},
        ]
        ranked = uc.rank_records(records)
        self.assertEqual([r["fdc_id"] for r in ranked], [3, 4, 2, 1])

    def test_sort_is_stable_within_a_tier(self):
        records = [
            {"fdc_id": 10, "data_type": "SR Legacy"},
            {"fdc_id": 11, "data_type": "SR Legacy"},
            {"fdc_id": 12, "data_type": "SR Legacy"},
        ]
        ranked = uc.rank_records(records)
        # USDA's own relevance order inside a tier is preserved untouched.
        self.assertEqual([r["fdc_id"] for r in ranked], [10, 11, 12])

    def test_a_page_of_branded_never_outranks_one_generic_record(self):
        records = [{"fdc_id": i, "data_type": "Branded"} for i in range(20)]
        records.append({"fdc_id": 99, "data_type": "Foundation"})
        self.assertEqual(uc.rank_records(records)[0]["fdc_id"], 99)

    def test_empty_input(self):
        self.assertEqual(uc.rank_records([]), [])


class LookupBrandedFallbackTests(unittest.TestCase):
    def setUp(self):
        uc.clear_cache()

    def tearDown(self):
        uc.clear_cache()

    def test_generic_hit_answers_without_consulting_branded(self):
        calls = []

        def fake_search(query, timeout, *, data_types, page_size=5, page_number=1):
            calls.append(list(data_types))
            return {"foods": [_record(1, "Chicken, breast, raw", "SR Legacy")]}

        with mock.patch.object(uc, "_raw_search", fake_search):
            result = uc.lookup("grilled chicken")

        self.assertEqual(result["data_type"], "SR Legacy")
        # Branded was never queried: a query that already worked is unchanged.
        self.assertEqual(calls, [uc.GENERIC_DATA_TYPES])

    def test_branded_is_used_only_when_no_generic_record_matches(self):
        calls = []

        def fake_search(query, timeout, *, data_types, page_size=5, page_number=1):
            calls.append(list(data_types))
            if list(data_types) == uc.GENERIC_DATA_TYPES:
                return {"foods": []}
            return {"foods": [_record(7, "Quest Protein Bar", "Branded",
                                      brandName="Quest Nutrition")]}

        with mock.patch.object(uc, "_raw_search", fake_search):
            result = uc.lookup("quest protein bar")

        self.assertEqual(result["data_type"], "Branded")
        self.assertEqual(result["brand"], "Quest Nutrition")
        self.assertEqual(calls, [uc.GENERIC_DATA_TYPES, uc.BRANDED_DATA_TYPES])

    def test_no_match_in_either_tier_returns_none(self):
        with mock.patch.object(uc, "_raw_search", return_value={"foods": []}):
            self.assertIsNone(uc.lookup("zzzz not a food"))

    def test_generic_tier_is_re_ranked_before_picking_the_best(self):
        # USDA returned FNDDS first; Foundation must still win.
        payload = {
            "foods": [
                _record(1, "Chicken dish", "Survey (FNDDS)"),
                _record(2, "Chicken, breast", "Foundation"),
            ]
        }
        with mock.patch.object(uc, "_raw_search", return_value=payload):
            result = uc.lookup("chicken")
        self.assertEqual(result["fdc_id"], 2)

    def test_lookup_is_cached_including_misses(self):
        calls = {"n": 0}

        def fake_search(query, timeout, *, data_types, page_size=5, page_number=1):
            calls["n"] += 1
            return {"foods": []}

        with mock.patch.object(uc, "_raw_search", fake_search):
            self.assertIsNone(uc.lookup("nothing here"))
            self.assertIsNone(uc.lookup("nothing here"))

        # Two tiers on the first call, nothing on the second.
        self.assertEqual(calls["n"], 2)
        self.assertEqual(uc.cache_stats()["hits"], 1)


class ParseServingTests(unittest.TestCase):
    def test_branded_serving_is_parsed(self):
        serving = uc.parse_serving(
            {
                "servingSize": 60.0,
                "servingSizeUnit": "g",
                "householdServingFullText": "1 bar",
            }
        )
        self.assertEqual(serving["serving_size"], 60.0)
        self.assertEqual(serving["serving_size_unit"], "g")
        self.assertEqual(serving["household_serving"], "1 bar")
        self.assertEqual(serving["serving_size_grams"], 60.0)

    def test_absent_serving_is_none_not_a_guessed_100g(self):
        self.assertIsNone(uc.parse_serving({}))
        self.assertIsNone(uc.parse_serving({"servingSize": None}))

    def test_non_gram_unit_leaves_serving_size_grams_unknown(self):
        serving = uc.parse_serving({"servingSize": 1.0, "servingSizeUnit": "cup"})
        self.assertEqual(serving["serving_size"], 1.0)
        self.assertIsNone(serving["serving_size_grams"])

    def test_nonsense_serving_size_is_dropped(self):
        self.assertIsNone(uc.parse_serving({"servingSize": "abc"}))
        self.assertIsNone(uc.parse_serving({"servingSize": -5, "servingSizeUnit": "g"}))

    def test_household_only_serving_still_reported(self):
        serving = uc.parse_serving({"householdServingFullText": "2 cookies"})
        self.assertIsNone(serving["serving_size"])
        self.assertEqual(serving["household_serving"], "2 cookies")


class ParseFoodRecordBrandTests(unittest.TestCase):
    def test_brand_name_preferred_over_brand_owner(self):
        parsed = uc.parse_food_record(
            _record(1, "Bar", "Branded", brandName="Quest", brandOwner="Quest Nutrition LLC")
        )
        self.assertEqual(parsed["brand"], "Quest")

    def test_brand_owner_used_when_brand_name_absent(self):
        parsed = uc.parse_food_record(
            _record(1, "Bar", "Branded", brandOwner="Quest Nutrition LLC")
        )
        self.assertEqual(parsed["brand"], "Quest Nutrition LLC")

    def test_generic_record_has_null_brand(self):
        parsed = uc.parse_food_record(_record(1, "Chicken", "Foundation"))
        self.assertIsNone(parsed["brand"])
        self.assertIsNone(parsed["gtin_upc"])
        self.assertIsNone(parsed["serving"])

    def test_blank_brand_is_null_not_empty_string(self):
        parsed = uc.parse_food_record(_record(1, "Bar", "Branded", brandName="   "))
        self.assertIsNone(parsed["brand"])

    def test_unmeasured_nutrients_are_absent_not_zero(self):
        parsed = uc.parse_food_record(_record(1, "Chicken", "Foundation"))
        self.assertNotIn("sodium", parsed["per_100g"])
        self.assertNotIn("fiber", parsed["per_100g"])
        self.assertEqual(parsed["micronutrients_per_100g"], {})


class BarcodeLookupTests(unittest.TestCase):
    def setUp(self):
        uc.clear_cache()

    def tearDown(self):
        uc.clear_cache()

    def test_normalize_barcode_strips_separators(self):
        self.assertEqual(uc.normalize_barcode(" 888-849 000371\n"), "888849000371")
        self.assertEqual(uc.normalize_barcode("abc"), "")
        self.assertEqual(uc.normalize_barcode(None), "")

    def test_gtin_key_ignores_leading_zeros(self):
        self.assertEqual(uc._gtin_key("0888849000371"), uc._gtin_key("888849000371"))
        self.assertEqual(uc._gtin_key("00888849000371"), "888849000371")

    def test_exact_gtin_match_is_returned(self):
        payload = {
            "foods": [
                _record(5, "Some other bar", "Branded", gtinUpc="111111111111"),
                _record(6, "Quest Bar", "Branded", gtinUpc="0888849000371",
                        brandName="Quest"),
            ]
        }
        with mock.patch.object(uc, "_raw_search", return_value=payload):
            result = uc.lookup_barcode("888849000371")
        self.assertEqual(result["fdc_id"], 6)
        self.assertEqual(result["brand"], "Quest")

    def test_near_miss_is_a_miss_not_a_fabricated_match(self):
        payload = {
            "foods": [
                _record(5, "A different protein bar", "Branded", gtinUpc="111111111111")
            ]
        }
        with mock.patch.object(uc, "_raw_search", return_value=payload):
            self.assertIsNone(uc.lookup_barcode("888849000371"))

    def test_barcode_miss_is_cached(self):
        calls = {"n": 0}

        def fake_search(query, timeout, *, data_types, page_size=5, page_number=1):
            calls["n"] += 1
            return {"foods": []}

        with mock.patch.object(uc, "_raw_search", fake_search):
            self.assertIsNone(uc.lookup_barcode("888849000371"))
            self.assertIsNone(uc.lookup_barcode("888-849-000371"))

        self.assertEqual(calls["n"], 1)
        self.assertEqual(uc.cache_stats()["hits"], 1)

    def test_empty_barcode_never_calls_usda(self):
        with mock.patch.object(uc, "_raw_search", side_effect=AssertionError("called")):
            self.assertIsNone(uc.lookup_barcode("no digits"))


class SearchFoodsTests(unittest.TestCase):
    def setUp(self):
        uc.clear_cache()

    def tearDown(self):
        uc.clear_cache()

    def _tiered(self, generic, branded, total_pages=1):
        def fake_search(query, timeout, *, data_types, page_size, page_number=1):
            foods = generic if list(data_types) == uc.GENERIC_DATA_TYPES else branded
            return {
                "foods": foods[:page_size],
                "totalHits": len(foods),
                "totalPages": total_pages,
            }

        return fake_search

    def test_results_are_ranked_generic_first(self):
        generic = [_record(1, "Chicken, breast", "Foundation")]
        branded = [_record(2, "Chicken Bites", "Branded", brandName="Acme")]
        with mock.patch.object(uc, "_raw_search", self._tiered(generic, branded)):
            result = uc.search_foods("chicken", limit=10)
        self.assertEqual(
            [r["fdc_id"] for r in result["records"]], [1, 2]
        )

    def test_branded_keeps_reserved_slots_so_a_named_product_is_findable(self):
        generic = [_record(i, f"generic {i}", "Survey (FNDDS)") for i in range(100, 140)]
        branded = [_record(7, "Quest Protein Bar", "Branded", brandName="Quest")]
        with mock.patch.object(uc, "_raw_search", self._tiered(generic, branded)):
            result = uc.search_foods("protein bar", limit=10)

        ids = [r["fdc_id"] for r in result["records"]]
        self.assertIn(7, ids, "branded result was crowded out by generic hits")
        self.assertEqual(len(ids), 10)
        # ...but it still sorts last.
        self.assertEqual(ids[-1], 7)

    def test_slot_split_never_gives_a_tier_the_whole_page(self):
        for limit in (2, 5, 10, 20, 50):
            generic, branded = uc._slot_split(limit)
            self.assertGreaterEqual(generic, 1)
            self.assertGreaterEqual(branded, 1)
            self.assertEqual(generic + branded, limit)

    def test_merge_fills_the_page_from_generic_when_branded_underfills(self):
        generic = [{"fdc_id": i, "data_type": "Foundation"} for i in range(20)]
        merged = uc.merge_tiers(generic, [], limit=10)
        self.assertEqual(len(merged), 10)

    def test_merge_fills_the_page_from_branded_when_generic_underfills(self):
        branded = [{"fdc_id": i, "data_type": "Branded"} for i in range(20)]
        merged = uc.merge_tiers([], branded, limit=10)
        self.assertEqual(len(merged), 10)

    def test_merge_puts_every_generic_record_ahead_of_every_branded_one(self):
        generic = [{"fdc_id": i, "data_type": "SR Legacy"} for i in range(3)]
        branded = [{"fdc_id": 100 + i, "data_type": "Branded"} for i in range(3)]
        merged = uc.merge_tiers(generic, branded, limit=10)
        types = [r["data_type"] for r in merged]
        self.assertEqual(types, ["SR Legacy"] * 3 + ["Branded"] * 3)

    def test_duplicate_fdc_ids_are_dropped(self):
        shared = _record(3, "Same record", "Branded")
        with mock.patch.object(uc, "_raw_search", self._tiered([shared], [shared])):
            result = uc.search_foods("thing", limit=10)
        self.assertEqual(len(result["records"]), 1)

    def test_no_results_is_not_an_error(self):
        with mock.patch.object(uc, "_raw_search", self._tiered([], [])):
            result = uc.search_foods("asdfghjkl", limit=10)
        self.assertEqual(result["records"], [])
        self.assertEqual(result["total_hits"], 0)
        self.assertFalse(result["has_more"])

    def test_has_more_reports_further_pages(self):
        generic = [_record(1, "a", "Foundation")]
        with mock.patch.object(
            uc, "_raw_search", self._tiered(generic, [], total_pages=5)
        ):
            result = uc.search_foods("a", limit=10)
        self.assertTrue(result["has_more"])

    def test_blank_query_never_calls_usda(self):
        with mock.patch.object(uc, "_raw_search", side_effect=AssertionError("called")):
            result = uc.search_foods("   ")
        self.assertEqual(result["records"], [])

    def test_search_is_cached_per_query_page_and_limit(self):
        calls = {"n": 0}
        generic = [_record(1, "a", "Foundation")]

        def fake_search(query, timeout, *, data_types, page_size, page_number=1):
            calls["n"] += 1
            base = self._tiered(generic, [])
            return base(query, timeout, data_types=data_types,
                        page_size=page_size, page_number=page_number)

        with mock.patch.object(uc, "_raw_search", fake_search):
            uc.search_foods("chicken", page=1, limit=10)
            uc.search_foods("Chicken", page=1, limit=10)   # normalized: cache hit
            uc.search_foods("chicken", page=2, limit=10)   # different page: miss

        self.assertEqual(calls["n"], 4)  # two tiers x two distinct pages
        self.assertEqual(uc.cache_stats()["hits"], 1)

    def test_paging_is_clamped(self):
        self.assertEqual(uc.clamp_search_paging(None, None),
                         (1, uc.DEFAULT_SEARCH_LIMIT))
        self.assertEqual(uc.clamp_search_paging(0, 0), (1, uc.DEFAULT_SEARCH_LIMIT))
        self.assertEqual(uc.clamp_search_paging(-3, 999), (1, uc.MAX_SEARCH_LIMIT))
        self.assertEqual(uc.clamp_search_paging("2", "5"), (2, 5))
        self.assertEqual(uc.clamp_search_paging("x", "y"),
                         (1, uc.DEFAULT_SEARCH_LIMIT))


class UnavailableTests(unittest.TestCase):
    def setUp(self):
        uc.clear_cache()

    def tearDown(self):
        uc.clear_cache()

    def test_missing_key_raises_unavailable_and_never_names_the_value(self):
        with mock.patch.dict("os.environ", {"USDA_FDC_API_KEY": ""}, clear=False):
            with self.assertRaises(uc.UsdaUnavailable) as ctx:
                uc.search_foods("chicken")
            self.assertIn("USDA_FDC_API_KEY", str(ctx.exception))
            self.assertIn("not set", str(ctx.exception))

    def test_outage_propagates_from_search(self):
        with mock.patch.object(
            uc, "_raw_search", side_effect=uc.UsdaUnavailable("USDA returned HTTP 503.")
        ):
            with self.assertRaises(uc.UsdaUnavailable):
                uc.search_foods("chicken")

    def test_outage_propagates_from_barcode(self):
        with mock.patch.object(
            uc, "_raw_search", side_effect=uc.UsdaUnavailable("USDA returned HTTP 503.")
        ):
            with self.assertRaises(uc.UsdaUnavailable):
                uc.lookup_barcode("888849000371")


if __name__ == "__main__":
    unittest.main()
