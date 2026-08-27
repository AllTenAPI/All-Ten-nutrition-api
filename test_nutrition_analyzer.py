"""Tests for the pure logic of the nutrition pipeline.

Stdlib ``unittest`` on purpose: the deploy image has no pytest, so these must
run with ``python3 -m unittest``. They also run under pytest unchanged.

Nothing here makes a network call or needs a credential.
"""

from __future__ import annotations

import base64
import unittest

import claude_vision
import nutrition_analyzer as na
import usda_client

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64

CHICKEN_PER_100G = {
    "calories": 165.0,
    "protein": 31.0,
    "carbs": 0.0,
    "fat": 3.6,
    "fiber": 0.0,
    "sugar": 0.0,
    "sodium": 74.0,
}


class PortionScalingTests(unittest.TestCase):
    def test_scales_linearly_from_100g(self):
        scaled = na.scale_macros(CHICKEN_PER_100G, 250)
        self.assertEqual(scaled["calories"], 412)
        self.assertEqual(scaled["protein"], 77.5)
        self.assertEqual(scaled["fat"], 9.0)
        self.assertEqual(scaled["sodium"], 185)

    def test_100g_is_identity(self):
        scaled = na.scale_macros(CHICKEN_PER_100G, 100)
        self.assertEqual(scaled["calories"], 165)
        self.assertEqual(scaled["protein"], 31.0)

    def test_zero_grams_gives_zero_not_none(self):
        scaled = na.scale_macros(CHICKEN_PER_100G, 0)
        self.assertEqual(scaled["calories"], 0)
        self.assertEqual(scaled["protein"], 0.0)

    def test_missing_field_stays_none_not_zero(self):
        scaled = na.scale_macros({"calories": 200.0}, 150)
        self.assertEqual(scaled["calories"], 300)
        self.assertIsNone(scaled["protein"])
        self.assertIsNone(scaled["sodium"])

    def test_every_macro_field_is_present(self):
        scaled = na.scale_macros({}, 100)
        self.assertEqual(set(scaled), set(na.MACRO_FIELDS))

    def test_micronutrients_scale_too(self):
        scaled = na.scale_micronutrients({"iron": 1.0, "calcium": 20.0}, 250)
        self.assertEqual(scaled["iron"], 2.5)
        self.assertEqual(scaled["calcium"], 50.0)

    def test_micronutrient_none_values_are_dropped(self):
        scaled = na.scale_micronutrients({"iron": None, "calcium": 20.0}, 100)
        self.assertNotIn("iron", scaled)


class ClampTests(unittest.TestCase):
    def test_portion_within_limit_is_untouched(self):
        grams, clamped = na.clamp_portion(320, limit=1500)
        self.assertEqual(grams, 320.0)
        self.assertFalse(clamped)

    def test_absurd_portion_is_capped_and_flagged(self):
        grams, clamped = na.clamp_portion(9000, limit=1500)
        self.assertEqual(grams, 1500.0)
        self.assertTrue(clamped)

    def test_negative_and_garbage_portions_become_zero(self):
        for bad in (-50, "not a number", None, float("nan")):
            grams, clamped = na.clamp_portion(bad, limit=1500)
            self.assertEqual(grams, 0.0)
            self.assertTrue(clamped)

    def test_confidence_is_clamped_to_unit_interval(self):
        self.assertEqual(na.clamp_confidence(1.7), 1.0)
        self.assertEqual(na.clamp_confidence(-0.3), 0.0)
        self.assertEqual(na.clamp_confidence(0.42), 0.42)

    def test_unparseable_confidence_is_zero_not_one(self):
        # An unknown confidence must never read as a high confidence.
        for bad in (None, "high", float("nan")):
            self.assertEqual(na.clamp_confidence(bad), 0.0)


class AggregationTests(unittest.TestCase):
    def test_sums_macros_across_foods(self):
        foods = [
            {"macros": {"calories": 400, "protein": 30.0, "carbs": 10.0,
                        "fat": 12.0, "fiber": 2.0, "sugar": 1.0, "sodium": 200}},
            {"macros": {"calories": 250, "protein": 5.0, "carbs": 50.0,
                        "fat": 2.0, "fiber": 4.0, "sugar": 6.0, "sodium": 100}},
        ]
        totals = na.aggregate_totals(foods)
        self.assertEqual(totals["calories"], 650)
        self.assertEqual(totals["protein"], 35.0)
        self.assertEqual(totals["sodium"], 300)

    def test_none_contributions_are_skipped_not_zeroed(self):
        foods = [
            {"macros": {"calories": 400, "sodium": 200}},
            {"macros": {"calories": 250, "sodium": None}},
        ]
        totals = na.aggregate_totals(foods)
        self.assertEqual(totals["calories"], 650)
        self.assertEqual(totals["sodium"], 200)

    def test_field_unknown_for_every_food_is_none_not_zero(self):
        totals = na.aggregate_totals([{"macros": {"calories": 400}}])
        self.assertEqual(totals["calories"], 400)
        self.assertIsNone(totals["fiber"])

    def test_empty_meal_totals_are_all_none(self):
        totals = na.aggregate_totals([])
        self.assertTrue(all(value is None for value in totals.values()))

    def test_micronutrients_aggregate_and_sort(self):
        foods = [
            {"micronutrients": {"iron": 1.5, "calcium": 20.0}},
            {"micronutrients": {"iron": 2.0}},
            {"micronutrients": {}},
        ]
        totals = na.aggregate_micronutrients(foods)
        self.assertEqual(totals, {"calcium": 20.0, "iron": 3.5})
        self.assertEqual(list(totals), ["calcium", "iron"])


class ConfirmationTests(unittest.TestCase):
    def _food(self, name="grilled chicken", confidence=0.9, source="usda"):
        return {"name": name, "confidence": confidence, "source": source}

    def test_clean_confident_usda_meal_needs_no_confirmation(self):
        needs, reason = na.evaluate_confirmation(
            {"calories": 620}, 0.88, [self._food()],
            calorie_limit=2500, confidence_floor=0.5,
        )
        self.assertFalse(needs)
        self.assertIsNone(reason)

    def test_calories_over_threshold_trip_confirmation(self):
        needs, reason = na.evaluate_confirmation(
            {"calories": 6000}, 0.9, [self._food()],
            calorie_limit=2500, confidence_floor=0.5,
        )
        self.assertTrue(needs)
        self.assertIn("6000", reason)
        self.assertIn("2500", reason)

    def test_calories_exactly_at_threshold_do_not_trip(self):
        needs, _ = na.evaluate_confirmation(
            {"calories": 2500}, 0.9, [self._food()],
            calorie_limit=2500, confidence_floor=0.5,
        )
        self.assertFalse(needs)

    def test_low_overall_confidence_trips_confirmation(self):
        needs, reason = na.evaluate_confirmation(
            {"calories": 500}, 0.3, [self._food()],
            calorie_limit=2500, confidence_floor=0.5,
        )
        self.assertTrue(needs)
        self.assertIn("confidence", reason.lower())

    def test_no_foods_always_needs_confirmation(self):
        needs, reason = na.evaluate_confirmation({"calories": None}, 0.0, [])
        self.assertTrue(needs)
        self.assertIn("No food", reason)

    def test_estimated_source_trips_confirmation_and_says_why(self):
        needs, reason = na.evaluate_confirmation(
            {"calories": 500}, 0.9,
            [self._food(name="ackee", source="estimated")],
            calorie_limit=2500, confidence_floor=0.5,
        )
        self.assertTrue(needs)
        self.assertIn("ackee", reason)
        self.assertIn("estimate", reason)

    def test_usda_outage_is_named_in_the_reason(self):
        needs, reason = na.evaluate_confirmation(
            {"calories": 500}, 0.9,
            [self._food(name="rice", source="estimated")],
            usda_available=False, calorie_limit=2500, confidence_floor=0.5,
        )
        self.assertTrue(needs)
        self.assertIn("USDA", reason)
        self.assertIn("unavailable", reason)

    def test_clamped_portion_is_reported(self):
        food = self._food()
        food["portion_clamped"] = True
        needs, reason = na.evaluate_confirmation(
            {"calories": 500}, 0.9, [food],
            calorie_limit=2500, confidence_floor=0.5,
        )
        self.assertTrue(needs)
        self.assertIn("capped", reason)

    def test_multiple_reasons_are_combined(self):
        needs, reason = na.evaluate_confirmation(
            {"calories": 6000}, 0.2,
            [self._food(confidence=0.2, source="estimated")],
            calorie_limit=2500, confidence_floor=0.5,
        )
        self.assertTrue(needs)
        self.assertGreaterEqual(reason.count(";"), 2)


class ResponseShapeTests(unittest.TestCase):
    def _analyzed_food(self):
        return {
            "name": "Grilled chicken breast",
            "portion_grams": 250.0,
            "portion_clamped": False,
            "confidence": 0.87,
            "macros": na.scale_macros(CHICKEN_PER_100G, 250),
            "micronutrients": {"iron": 2.5},
            "source": "usda",
            "usda_description": "Chicken, broilers or fryers, breast, cooked",
            "fdc_id": 171077,
        }

    def test_contract_top_level_keys(self):
        response = na.build_response([self._analyzed_food()], 0.87, "claude-sonnet-5")
        for key in (
            "foods", "totals", "needs_confirmation", "confirmation_reason",
            "model", "analysis_version",
        ):
            self.assertIn(key, response)

    def test_food_entries_are_editable_shape(self):
        response = na.build_response([self._analyzed_food()], 0.87, "claude-sonnet-5")
        food = response["foods"][0]
        self.assertEqual(
            set(food),
            {"name", "portion_grams", "confidence", "macros", "micronutrients",
             "source", "usda_description", "fdc_id"},
        )
        self.assertEqual(food["portion_grams"], 250)
        self.assertEqual(food["source"], "usda")

    def test_totals_carry_every_macro_plus_micronutrients(self):
        response = na.build_response([self._analyzed_food()], 0.87, "claude-sonnet-5")
        totals = response["totals"]
        for field in na.MACRO_FIELDS:
            self.assertIn(field, totals)
        self.assertEqual(totals["micronutrients"], {"iron": 2.5})

    def test_model_is_echoed_for_ab_testing(self):
        response = na.build_response([], 0.0, "claude-haiku-4-5")
        self.assertEqual(response["model"], "claude-haiku-4-5")

    def test_removed_nonsense_nutrients_are_absent(self):
        # The old model carried ~60 fields including these, which are not
        # meaningful nutrition-label values for a photographed meal.
        response = na.build_response([self._analyzed_food()], 0.87, "claude-sonnet-5")
        serialized = repr(response)
        for removed in ("dopamine", "serotonin", "epinephrine", "melatonin",
                        "norepinephrine", "gaba", "creatine", "coq10"):
            self.assertNotIn(removed, serialized)

    def test_failure_response_matches_the_same_contract(self):
        response = na._failure_response(
            "boom", model="claude-sonnet-5", kind="rate_limit", retryable=True
        )
        self.assertEqual(response["foods"], [])
        self.assertTrue(response["needs_confirmation"])
        self.assertEqual(response["confirmation_reason"], "boom")
        self.assertTrue(response["error"]["retryable"])
        # No fabricated numbers on the failure path.
        self.assertTrue(
            all(v is None for k, v in response["totals"].items() if k != "micronutrients")
        )


class UsdaEnrichmentTests(unittest.TestCase):
    def test_usda_hit_is_scaled_and_labelled(self):
        def fake_lookup(query):
            return {
                "fdc_id": 171077,
                "description": "Chicken, breast, cooked",
                "per_100g": CHICKEN_PER_100G,
                "micronutrients_per_100g": {"iron": 1.0},
            }

        foods, available, warnings = na.enrich_with_usda(
            [{"name": "Grilled chicken", "usda_query": "chicken breast cooked",
              "estimated_portion_grams": 250, "confidence": 0.9}],
            lookup=fake_lookup,
        )
        self.assertTrue(available)
        self.assertEqual(warnings, [])
        self.assertEqual(foods[0]["source"], "usda")
        self.assertEqual(foods[0]["macros"]["calories"], 412)
        self.assertEqual(foods[0]["micronutrients"]["iron"], 2.5)
        self.assertEqual(foods[0]["fdc_id"], 171077)

    def test_no_usda_match_falls_back_to_model_estimate(self):
        foods, available, _ = na.enrich_with_usda(
            [{"name": "ackee and saltfish", "usda_query": "ackee",
              "estimated_portion_grams": 200, "confidence": 0.7,
              "estimated_calories_per_100g": 150,
              "estimated_protein_per_100g": 8,
              "estimated_carbs_per_100g": 5,
              "estimated_fat_per_100g": 11}],
            lookup=lambda query: None,
        )
        self.assertTrue(available)  # service is up, it just had no match
        self.assertEqual(foods[0]["source"], "estimated")
        self.assertEqual(foods[0]["macros"]["calories"], 300)
        # Micronutrients are never estimated.
        self.assertEqual(foods[0]["micronutrients"], {})
        # Nor are fiber/sugar/sodium.
        self.assertIsNone(foods[0]["macros"]["sodium"])

    def test_usda_outage_degrades_and_reports_rather_than_inventing(self):
        def down(query):
            raise usda_client.UsdaUnavailable("USDA returned HTTP 503.")

        foods, available, warnings = na.enrich_with_usda(
            [{"name": "rice", "estimated_portion_grams": 180, "confidence": 0.8,
              "estimated_calories_per_100g": 130}],
            lookup=down,
        )
        self.assertFalse(available)
        self.assertEqual(len(warnings), 1)
        self.assertIn("503", warnings[0])
        self.assertEqual(foods[0]["source"], "estimated")

    def test_outage_stops_further_lookups_for_the_same_meal(self):
        calls = []

        def down(query):
            calls.append(query)
            raise usda_client.UsdaUnavailable("down")

        na.enrich_with_usda(
            [{"name": "rice", "estimated_portion_grams": 100, "confidence": 0.8},
             {"name": "beans", "estimated_portion_grams": 100, "confidence": 0.8},
             {"name": "plantain", "estimated_portion_grams": 100, "confidence": 0.8}],
            lookup=down,
        )
        self.assertEqual(len(calls), 1)

    def test_nameless_and_malformed_entries_are_dropped(self):
        foods, _, _ = na.enrich_with_usda(
            [{"name": "  "}, "not a dict", {"estimated_portion_grams": 100}],
            lookup=lambda query: None,
        )
        self.assertEqual(foods, [])

    def test_falls_back_to_display_name_when_query_misses(self):
        seen = []

        def lookup(query):
            seen.append(query)
            return {"per_100g": CHICKEN_PER_100G} if query == "chicken" else None

        foods, _, _ = na.enrich_with_usda(
            [{"name": "chicken", "usda_query": "poulet roti",
              "estimated_portion_grams": 100, "confidence": 0.8}],
            lookup=lookup,
        )
        self.assertEqual(seen, ["poulet roti", "chicken"])
        self.assertEqual(foods[0]["source"], "usda")


class PortionDefectRegressionTests(unittest.TestCase):
    """The bug this rebuild exists to fix.

    Google Vision returned the label "pizza" with no quantity, so calories were
    looked up against an assumed serving and multiplied out to ~6,000 kcal for
    a meal that was really ~1,200.
    """

    PIZZA_PER_100G = {"calories": 266.0, "protein": 11.0, "carbs": 33.0, "fat": 10.0}

    def test_real_portion_produces_a_realistic_calorie_total(self):
        foods, _, _ = na.enrich_with_usda(
            [{"name": "pepperoni pizza", "usda_query": "pizza, pepperoni",
              "estimated_portion_grams": 420, "confidence": 0.82}],
            lookup=lambda query: {"per_100g": self.PIZZA_PER_100G,
                                  "micronutrients_per_100g": {}},
        )
        response = na.build_response(foods, 0.82, "claude-sonnet-5")
        self.assertEqual(response["totals"]["calories"], 1117)
        self.assertLess(response["totals"]["calories"], 2500)
        self.assertFalse(response["needs_confirmation"])

    def test_a_6000_kcal_result_would_now_be_flagged_not_logged(self):
        foods, _, _ = na.enrich_with_usda(
            [{"name": "pepperoni pizza", "estimated_portion_grams": 1400,
              "confidence": 0.82}],
            lookup=lambda query: {"per_100g": {"calories": 266.0},
                                  "micronutrients_per_100g": {}},
        )
        response = na.build_response(foods, 0.82, "claude-sonnet-5")
        self.assertGreater(response["totals"]["calories"], 2500)
        self.assertTrue(response["needs_confirmation"])
        self.assertIn("sanity threshold", response["confirmation_reason"])


class UsdaParsingTests(unittest.TestCase):
    def test_parses_search_endpoint_shape(self):
        record = usda_client.parse_food_record({
            "fdcId": 171077,
            "description": "Chicken, breast, cooked",
            "dataType": "SR Legacy",
            "foodNutrients": [
                {"nutrientId": 1008, "value": 165},
                {"nutrientId": 1003, "value": 31},
                {"nutrientId": 1089, "value": 1.04},
                {"nutrientId": 9999, "value": 42},
            ],
        })
        self.assertEqual(record["per_100g"]["calories"], 165.0)
        self.assertEqual(record["per_100g"]["protein"], 31.0)
        self.assertEqual(record["micronutrients_per_100g"]["iron"], 1.04)
        self.assertEqual(record["fdc_id"], 171077)

    def test_parses_detail_endpoint_nested_shape(self):
        record = usda_client.parse_food_record({
            "foodNutrients": [{"nutrient": {"id": 1008}, "amount": 200}],
        })
        self.assertEqual(record["per_100g"]["calories"], 200.0)

    def test_energy_falls_back_to_atwater_nutrient_ids(self):
        record = usda_client.parse_food_record({
            "foodNutrients": [{"nutrientId": 2047, "value": 180}],
        })
        self.assertEqual(record["per_100g"]["calories"], 180.0)

    def test_energy_derived_from_macros_when_absent(self):
        record = usda_client.parse_food_record({
            "foodNutrients": [
                {"nutrientId": 1003, "value": 10},
                {"nutrientId": 1005, "value": 20},
                {"nutrientId": 1004, "value": 5},
            ],
        })
        self.assertEqual(record["per_100g"]["calories"], 165.0)

    def test_unparseable_rows_are_ignored(self):
        record = usda_client.parse_food_record({
            "foodNutrients": [
                {"nutrientId": "abc", "value": 1},
                {"nutrientId": 1008, "value": None},
                {"nutrientId": 1003, "value": "x"},
            ],
        })
        self.assertEqual(record["per_100g"], {})

    def test_name_normalization_is_the_cache_key(self):
        self.assertEqual(
            usda_client.normalize_food_name("  Grilled   CHICKEN Breast "),
            "grilled chicken breast",
        )


class ImagePreparationTests(unittest.TestCase):
    def _b64(self, raw: bytes) -> str:
        return base64.b64encode(raw).decode("ascii")

    def test_detects_media_type_from_magic_bytes(self):
        self.assertEqual(claude_vision.sniff_media_type(PNG_BYTES), "image/png")
        self.assertEqual(claude_vision.sniff_media_type(JPEG_BYTES), "image/jpeg")
        self.assertEqual(claude_vision.sniff_media_type(WEBP_BYTES), "image/webp")
        self.assertIsNone(claude_vision.sniff_media_type(b"not an image"))

    def test_accepts_bare_base64(self):
        payload, media_type = claude_vision.prepare_image(self._b64(JPEG_BYTES))
        self.assertEqual(media_type, "image/jpeg")
        self.assertNotIn("\n", payload)

    def test_accepts_data_url(self):
        data_url = "data:image/png;base64," + self._b64(PNG_BYTES)
        payload, media_type = claude_vision.prepare_image(data_url)
        self.assertEqual(media_type, "image/png")
        self.assertEqual(base64.b64decode(payload), PNG_BYTES)

    def test_strips_newlines_from_base64(self):
        wrapped = "\n".join(
            self._b64(PNG_BYTES)[i:i + 16]
            for i in range(0, len(self._b64(PNG_BYTES)), 16)
        )
        payload, _ = claude_vision.prepare_image(wrapped)
        self.assertNotIn("\n", payload)
        self.assertEqual(base64.b64decode(payload), PNG_BYTES)

    def test_magic_bytes_win_over_a_wrong_declared_type(self):
        _, media_type = claude_vision.prepare_image(self._b64(PNG_BYTES), "image/jpeg")
        self.assertEqual(media_type, "image/png")

    def test_rejects_empty_input(self):
        with self.assertRaises(claude_vision.VisionError):
            claude_vision.prepare_image("")

    def test_rejects_invalid_base64(self):
        with self.assertRaises(claude_vision.VisionError):
            claude_vision.prepare_image("!!!! not base64 !!!!")

    def test_rejects_unsupported_format(self):
        with self.assertRaises(claude_vision.VisionError) as ctx:
            claude_vision.prepare_image(self._b64(b"%PDF-1.4 fake pdf content"))
        self.assertIn("Unsupported", str(ctx.exception))

    def test_rejects_oversized_image(self):
        oversized = JPEG_BYTES + b"\x00" * (claude_vision.MAX_IMAGE_BYTES + 1)
        with self.assertRaises(claude_vision.VisionError) as ctx:
            claude_vision.prepare_image(self._b64(oversized))
        self.assertIn("limit", str(ctx.exception))


class ConfigurationTests(unittest.TestCase):
    def test_model_defaults_to_sonnet_5(self):
        import os
        previous = os.environ.pop("NUTRITION_MODEL", None)
        try:
            self.assertEqual(claude_vision.model_id(), "claude-sonnet-5")
        finally:
            if previous is not None:
                os.environ["NUTRITION_MODEL"] = previous

    def test_model_is_overridable_for_ab_testing(self):
        import os
        previous = os.environ.get("NUTRITION_MODEL")
        os.environ["NUTRITION_MODEL"] = "claude-haiku-4-5"
        try:
            self.assertEqual(claude_vision.model_id(), "claude-haiku-4-5")
        finally:
            if previous is None:
                os.environ.pop("NUTRITION_MODEL", None)
            else:
                os.environ["NUTRITION_MODEL"] = previous

    def test_thresholds_fall_back_when_env_is_garbage(self):
        import os
        os.environ["MAX_MEAL_CALORIES"] = "not a number"
        try:
            self.assertEqual(na.max_meal_calories(), na.DEFAULT_MAX_MEAL_CALORIES)
        finally:
            os.environ.pop("MAX_MEAL_CALORIES", None)

    def test_debug_payload_never_exposes_secret_material(self):
        import os
        os.environ["ANTHROPIC_API_KEY"] = "test-value-not-a-real-key"
        os.environ["USDA_FDC_API_KEY"] = "another-test-value"
        try:
            payload = na.debug_payload()
            serialized = repr(payload)
            self.assertNotIn("test-value-not-a-real-key", serialized)
            self.assertNotIn("another-test-value", serialized)
            self.assertEqual(payload["env"]["ANTHROPIC_API_KEY"], "set")
            self.assertEqual(payload["env"]["USDA_FDC_API_KEY"], "set")
            # No length, prefix, or preview leaks either.
            self.assertNotIn("length", serialized)
            self.assertNotIn("preview", serialized)
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("USDA_FDC_API_KEY", None)

    def test_health_payload_reports_configuration_not_values(self):
        payload = na.health_payload()
        self.assertEqual(payload["status"], "healthy")
        self.assertIn(payload["usda"], ("configured", "not_configured"))
        self.assertEqual(payload["analysis_version"], na.ANALYSIS_VERSION)


class CacheTests(unittest.TestCase):
    def setUp(self):
        usda_client.clear_cache()

    def tearDown(self):
        usda_client.clear_cache()

    def test_repeat_lookups_hit_the_cache(self):
        calls = []
        original = usda_client._search_uncached
        usda_client._search_uncached = lambda query, timeout: (
            calls.append(query) or {"per_100g": CHICKEN_PER_100G}
        )
        try:
            usda_client.lookup("Chicken Breast")
            usda_client.lookup("  chicken   breast  ")
            usda_client.lookup("chicken breast")
        finally:
            usda_client._search_uncached = original

        self.assertEqual(len(calls), 1)
        self.assertEqual(usda_client.cache_stats()["hits"], 2)

    def test_a_miss_is_cached_too(self):
        calls = []
        original = usda_client._search_uncached
        usda_client._search_uncached = lambda query, timeout: (
            calls.append(query) or None
        )
        try:
            self.assertIsNone(usda_client.lookup("nonexistent food"))
            self.assertIsNone(usda_client.lookup("nonexistent food"))
        finally:
            usda_client._search_uncached = original
        self.assertEqual(len(calls), 1)

    def test_missing_key_raises_unavailable_without_leaking_anything(self):
        import os
        previous = os.environ.pop("USDA_FDC_API_KEY", None)
        try:
            with self.assertRaises(usda_client.UsdaUnavailable) as ctx:
                usda_client.lookup("chicken")
            self.assertIn("USDA_FDC_API_KEY", str(ctx.exception))
        finally:
            if previous is not None:
                os.environ["USDA_FDC_API_KEY"] = previous


if __name__ == "__main__":
    unittest.main(verbosity=2)
