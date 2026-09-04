"""Tests for nutrition-label mode on ``/analyze_food``.

Stdlib ``unittest`` on purpose, like the rest of the suite: the deploy image
has no pytest. Nothing here makes a network call, needs a credential, or opens
a socket -- the vision call is the only impure step and it is stubbed.

The two things most worth breaking a build over are covered first: the
per-serving to per-100 g conversion, and the guarantee that meal mode did not
move.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

import claude_vision
import http_app
import nutrition_analyzer as na
import usda_client

# A 60 g bar. Deliberately not 100 g, so any code path that forgets to
# convert produces a visibly wrong number rather than a coincidentally
# right one. fiber is absent from the panel; sugar is a printed zero.
BAR_READING = {
    "panel_found": True,
    "unreadable_reason": "",
    "product_name": "Chocolate Protein Bar",
    "brand": "Acme",
    "serving_size": 60,
    "serving_size_unit": "g",
    "household_serving": "1 bar",
    "servings_per_container": 12,
    "per_serving": {
        "calories": 220,
        "protein": 20,
        "carbs": 24,
        "fat": 7,
        "fiber": None,
        "sugar": 0,
        "sodium": 190,
    },
    "micronutrients_per_serving": {"calcium": 500.0, "iron": None},
    "confidence": 0.93,
    "notes": "",
    "model": "claude-sonnet-5",
}


def label_reading(**overrides) -> dict:
    reading = json.loads(json.dumps(BAR_READING))
    reading.update(overrides)
    return reading


def analyze_label_with(reading) -> dict:
    """Run label mode against a stubbed panel reading (or a raised error)."""
    kwargs = (
        {"side_effect": reading} if isinstance(reading, BaseException) else
        {"return_value": reading}
    )
    with mock.patch.object(claude_vision, "read_nutrition_label", **kwargs):
        payload, status = na.analyze_food("aW1hZ2U=", "image/jpeg", na.MODE_NUTRITION_LABEL)
    payload["_status"] = status
    return payload


class ModeSelectionTests(unittest.TestCase):
    def test_absent_mode_is_meal(self):
        self.assertEqual(na.normalize_mode(None), na.MODE_MEAL)

    def test_blank_mode_is_meal(self):
        self.assertEqual(na.normalize_mode("   "), na.MODE_MEAL)

    def test_label_mode_is_recognised(self):
        self.assertEqual(na.normalize_mode("nutrition_label"), na.MODE_NUTRITION_LABEL)

    def test_mode_is_case_and_space_insensitive(self):
        self.assertEqual(na.normalize_mode(" Nutrition_Label "), na.MODE_NUTRITION_LABEL)

    def test_unknown_mode_is_rejected_not_silently_downgraded(self):
        # Silently running a meal analysis for a caller that asked for a label
        # read is the exact failure this feature exists to prevent.
        with self.assertRaises(ValueError) as ctx:
            na.normalize_mode("label")
        self.assertIn("nutrition_label", str(ctx.exception))

    def test_non_string_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            na.normalize_mode(7)


class ModeEchoTests(unittest.TestCase):
    """The echo is the client's proof the flag was honoured; without it the
    client refuses to label anything 'read from the label'."""

    def test_label_response_echoes_mode(self):
        payload = analyze_label_with(label_reading())
        self.assertEqual(payload["mode"], "nutrition_label")

    def test_label_failure_also_echoes_mode(self):
        payload = analyze_label_with(
            claude_vision.VisionError(
                "Claude rate limit reached.", retryable=True, kind="rate_limit"
            )
        )
        self.assertEqual(payload["mode"], "nutrition_label")
        self.assertEqual(payload["error"]["kind"], "rate_limit")

    def test_unreadable_panel_still_echoes_mode(self):
        payload = analyze_label_with(
            label_reading(panel_found=False, unreadable_reason="The panel is cut off.")
        )
        self.assertEqual(payload["mode"], "nutrition_label")


class PerServingConversionTests(unittest.TestCase):
    """A label is per serving; every foods[] entry in this app is per 100 g."""

    def test_sixty_gram_bar_converts_to_per_100g(self):
        payload = analyze_label_with(label_reading())
        macros = payload["foods"][0]["macros"]
        # 20 g protein in 60 g  ->  33.3 g per 100 g. Using the printed figure
        # as-is would understate it by 40%.
        self.assertEqual(macros["protein"], 33.3)
        self.assertEqual(macros["calories"], 367)
        self.assertEqual(macros["carbs"], 40.0)
        self.assertEqual(macros["fat"], 11.7)
        self.assertEqual(macros["sodium"], 317)

    def test_printed_panel_is_returned_unconverted(self):
        payload = analyze_label_with(label_reading())
        self.assertEqual(payload["label"]["per_serving"]["protein"], 20.0)
        self.assertEqual(payload["label"]["per_serving"]["calories"], 220.0)

    def test_foods_entry_is_on_the_reference_basis(self):
        entry = analyze_label_with(label_reading())["foods"][0]
        self.assertEqual(entry["portion_grams"], 100.0)
        self.assertEqual(entry["basis"], "per_100g")
        self.assertEqual(entry["source"], "nutrition_label")

    def test_a_100g_serving_passes_through_unchanged(self):
        payload = analyze_label_with(
            label_reading(serving_size=100, household_serving=None)
        )
        self.assertEqual(payload["foods"][0]["macros"]["protein"], 20.0)

    def test_micronutrients_convert_too(self):
        payload = analyze_label_with(label_reading())
        # 500 mg calcium in 60 g -> 833.33 mg per 100 g.
        self.assertEqual(payload["foods"][0]["micronutrients"]["calcium"], 833.33)

    def test_pure_conversion_of_a_known_panel(self):
        per_100g = na.per_serving_to_per_100g({"protein": 20.0}, 60.0)
        self.assertAlmostEqual(per_100g["protein"], 33.3333, places=3)

    def test_conversion_refuses_without_a_serving_weight(self):
        self.assertIsNone(na.per_serving_to_per_100g({"protein": 20.0}, None))
        self.assertIsNone(na.per_serving_to_per_100g({"protein": 20.0}, 0))

    def test_ml_serving_converts_like_search_food(self):
        self.assertEqual(na.serving_grams(330, "ml"), 330.0)

    def test_household_units_do_not_convert(self):
        for unit in ("scoop", "bar", "cup", "oz", "tbsp", None, ""):
            with self.subTest(unit=unit):
                self.assertIsNone(na.serving_grams(1, unit))


class NullNotZeroTests(unittest.TestCase):
    """A row the panel omits is unknown, not zero. A printed 0 is zero."""

    def test_absent_row_stays_null_on_the_panel(self):
        payload = analyze_label_with(label_reading())
        self.assertIsNone(payload["label"]["per_serving"]["fiber"])

    def test_absent_row_stays_null_after_conversion(self):
        payload = analyze_label_with(label_reading())
        self.assertIsNone(payload["foods"][0]["macros"]["fiber"])
        self.assertIsNone(payload["totals"]["fiber"])

    def test_printed_zero_survives_as_zero(self):
        payload = analyze_label_with(label_reading())
        self.assertEqual(payload["label"]["per_serving"]["sugar"], 0.0)
        self.assertEqual(payload["foods"][0]["macros"]["sugar"], 0.0)

    def test_null_micronutrient_is_omitted_not_zeroed(self):
        payload = analyze_label_with(label_reading())
        self.assertNotIn("iron", payload["label"]["micronutrients_per_serving"])
        self.assertNotIn("iron", payload["foods"][0]["micronutrients"])

    def test_unusable_values_become_null_rather_than_zero(self):
        for value in (None, "", "n/a", float("nan"), -3):
            with self.subTest(value=value):
                self.assertIsNone(na.label_number(value))

    def test_zero_is_preserved(self):
        self.assertEqual(na.label_number(0), 0.0)
        self.assertEqual(na.label_number("0"), 0.0)

    def test_servings_per_container_is_null_when_not_printed(self):
        payload = analyze_label_with(label_reading(servings_per_container=None))
        # Assuming 1 would silently halve a two-serving container.
        self.assertIsNone(payload["label"]["servings_per_container"])


class UnreadablePanelTests(unittest.TestCase):
    """An unreadable panel yields a question, never a number."""

    def test_unreadable_panel_needs_confirmation(self):
        payload = analyze_label_with(
            label_reading(
                panel_found=False,
                unreadable_reason="The panel is cut off at the left edge.",
            )
        )
        self.assertTrue(payload["needs_confirmation"])
        self.assertIn("cut off", payload["confirmation_reason"])

    def test_unreadable_panel_returns_no_food_entry(self):
        payload = analyze_label_with(label_reading(panel_found=False))
        self.assertEqual(payload["foods"], [])
        self.assertIsNone(payload["totals"]["calories"])

    def test_unreadable_panel_gets_a_reason_even_if_the_model_gives_none(self):
        payload = analyze_label_with(
            label_reading(panel_found=False, unreadable_reason="")
        )
        self.assertTrue(payload["needs_confirmation"])
        self.assertTrue(payload["confirmation_reason"])

    def test_no_panel_at_all_is_not_reported_as_a_read(self):
        payload = analyze_label_with(
            label_reading(
                panel_found=False,
                per_serving={field: None for field in na.MACRO_FIELDS},
                micronutrients_per_serving={},
            )
        )
        self.assertEqual(payload["foods"], [])
        self.assertTrue(payload["needs_confirmation"])

    def test_panel_without_a_gram_serving_is_its_own_case(self):
        # Read fine, but "1 scoop" gives nothing to convert against. The user
        # should re-shoot including the serving line, not the whole panel.
        payload = analyze_label_with(
            label_reading(serving_size=None, serving_size_unit=None,
                          household_serving="1 scoop")
        )
        self.assertEqual(payload["foods"], [])
        self.assertTrue(payload["needs_confirmation"])
        self.assertIn("serving weight", payload["confirmation_reason"])
        # The printed panel still comes back, so the client can show it.
        self.assertEqual(payload["label"]["per_serving"]["protein"], 20.0)
        self.assertIsNone(payload["label"]["serving"]["serving_size_grams"])

    def test_missing_calories_asks_for_confirmation(self):
        reading = label_reading()
        reading["per_serving"]["calories"] = None
        payload = analyze_label_with(reading)
        self.assertTrue(payload["needs_confirmation"])
        self.assertIn("calorie", payload["confirmation_reason"])

    def test_low_confidence_asks_for_confirmation(self):
        payload = analyze_label_with(label_reading(confidence=0.2))
        self.assertTrue(payload["needs_confirmation"])
        self.assertIn("Confidence", payload["confirmation_reason"])

    def test_a_clean_read_does_not_ask(self):
        payload = analyze_label_with(label_reading())
        self.assertFalse(payload["needs_confirmation"])
        self.assertIsNone(payload["confirmation_reason"])


class LabelFailureTests(unittest.TestCase):
    def test_refusal_is_reported_as_a_refusal(self):
        payload = analyze_label_with(
            claude_vision.VisionRefusal("Claude declined to read this image.")
        )
        self.assertEqual(payload["error"]["kind"], "refusal")
        self.assertFalse(payload["error"]["retryable"])
        self.assertEqual(payload["_status"], 200)
        self.assertEqual(payload["foods"], [])
        self.assertTrue(payload["needs_confirmation"])

    def test_bad_image_is_a_400(self):
        payload = analyze_label_with(
            claude_vision.VisionError("No image data was provided.", kind="bad_request")
        )
        self.assertEqual(payload["_status"], 400)
        self.assertEqual(payload["error"]["kind"], "bad_request")

    def test_missing_credentials_are_an_operator_problem(self):
        payload = analyze_label_with(
            claude_vision.VisionError("ANTHROPIC_API_KEY is not set.", kind="misconfigured")
        )
        self.assertEqual(payload["_status"], 500)

    def test_no_failure_response_carries_a_number(self):
        payload = analyze_label_with(
            claude_vision.VisionRefusal("Claude declined to read this image.")
        )
        self.assertEqual(payload["foods"], [])
        for field in na.MACRO_FIELDS:
            self.assertIsNone(payload["totals"][field])


class LabelShapeTests(unittest.TestCase):
    def test_label_block_has_the_contracted_keys(self):
        label = analyze_label_with(label_reading())["label"]
        self.assertEqual(
            set(label),
            {
                "product_name",
                "brand",
                "serving",
                "servings_per_container",
                "per_serving",
                "micronutrients_per_serving",
            },
        )

    def test_serving_block_matches_search_food(self):
        serving = analyze_label_with(label_reading())["label"]["serving"]
        self.assertEqual(
            set(serving),
            {
                "serving_size",
                "serving_size_unit",
                "household_serving",
                "serving_size_grams",
            },
        )

    def test_serving_is_null_when_the_panel_declares_none(self):
        payload = analyze_label_with(
            label_reading(serving_size=None, serving_size_unit=None,
                          household_serving=None)
        )
        self.assertIsNone(payload["label"]["serving"])

    def test_foods_entry_keeps_the_shared_shape(self):
        entry = analyze_label_with(label_reading())["foods"][0]
        for field in (
            "name", "portion_grams", "confidence", "macros", "micronutrients",
            "source", "usda_description", "fdc_id",
        ):
            self.assertIn(field, entry)
        self.assertIsNone(entry["usda_description"])
        self.assertIsNone(entry["fdc_id"])

    def test_envelope_keeps_every_analyze_food_key(self):
        payload = analyze_label_with(label_reading())
        for field in (
            "foods", "totals", "needs_confirmation", "confirmation_reason",
            "confidence", "model", "analysis_version", "usda_available",
            "notes", "warnings", "analyzed_at",
        ):
            self.assertIn(field, payload)

    def test_display_name_puts_the_brand_first(self):
        self.assertEqual(na.label_display_name("Protein Bar", "Acme"), "Acme Protein Bar")

    def test_display_name_does_not_repeat_the_brand(self):
        self.assertEqual(
            na.label_display_name("Acme Protein Bar", "Acme"), "Acme Protein Bar"
        )

    def test_display_name_falls_back(self):
        self.assertEqual(na.label_display_name(None, None), "Packaged food")
        self.assertEqual(na.label_display_name(None, "Acme"), "Acme")
        self.assertEqual(na.label_display_name("Protein Bar", None), "Protein Bar")

    def test_label_mode_makes_no_usda_call(self):
        def explode(*args, **kwargs):
            raise AssertionError("USDA was queried in label mode")

        with mock.patch.object(usda_client, "lookup", side_effect=explode):
            payload = analyze_label_with(label_reading())
        self.assertEqual(payload["foods"][0]["source"], "nutrition_label")


# --- the regression this feature must not cause -----------------------------

MEAL_DETECTION = {
    "foods": [
        {
            "name": "Grilled chicken breast",
            "usda_query": "chicken breast, grilled",
            "estimated_portion_grams": 250,
            "confidence": 0.87,
            "estimated_calories_per_100g": 165,
            "estimated_protein_per_100g": 31,
            "estimated_carbs_per_100g": 0,
            "estimated_fat_per_100g": 3.6,
        }
    ],
    "confidence": 0.84,
    "notes": "",
    "model": "claude-sonnet-5",
}

# The exact serialization a meal scan produced before label mode existed,
# with the only non-deterministic field pinned. If a future change to this
# module alters meal output at all, this string stops matching.
EXPECTED_MEAL_JSON = (
    '{"foods": [{"name": "Grilled chicken breast", "portion_grams": 250, '
    '"confidence": 0.87, "macros": {"calories": 412, "protein": 77.5, '
    '"carbs": 0.0, "fat": 9.0, "fiber": null, "sugar": null, "sodium": null}, '
    '"micronutrients": {}, "source": "estimated", "usda_description": null, '
    '"fdc_id": null}], "totals": {"calories": 412, "protein": 77.5, '
    '"carbs": 0.0, "fat": 9.0, "fiber": null, "sugar": null, "sodium": null, '
    '"micronutrients": {}}, "needs_confirmation": true, '
    '"confirmation_reason": "No USDA match for Grilled chicken breast; '
    'macros are model estimates.", "confidence": 0.84, '
    '"model": "claude-sonnet-5", "analysis_version": "2.0.0-claude-usda", '
    '"usda_available": true, "notes": "", "warnings": [], "analyzed_at": 0}'
)


class MealModeUnchangedTests(unittest.TestCase):
    """Meal mode must be byte-identical to what it was before label mode.

    Every scan the app has ever made goes through this path; a change here is
    a silent change to numbers users already logged.
    """

    def _run_meal(self, mode=None, echo=False):
        with mock.patch.object(
            claude_vision, "detect_foods", return_value=json.loads(json.dumps(MEAL_DETECTION))
        ), mock.patch.object(usda_client, "lookup", return_value=None):
            if mode is None:
                payload, status = na.analyze_meal("aW1hZ2U=", "image/jpeg")
            else:
                payload, status = na.analyze_food(
                    "aW1hZ2U=", "image/jpeg", mode, echo_mode=echo
                )
        payload["analyzed_at"] = 0
        return payload, status

    def test_meal_output_is_byte_identical(self):
        payload, status = self._run_meal()
        self.assertEqual(status, 200)
        self.assertEqual(json.dumps(payload), EXPECTED_MEAL_JSON)

    def test_dispatching_through_analyze_food_changes_nothing(self):
        direct, _ = self._run_meal()
        dispatched, _ = self._run_meal(mode=na.MODE_MEAL)
        self.assertEqual(json.dumps(dispatched), json.dumps(direct))

    def test_a_request_without_a_mode_carries_no_mode_key(self):
        payload, _ = self._run_meal(mode=na.MODE_MEAL, echo=False)
        self.assertNotIn("mode", payload)
        self.assertNotIn("label", payload)

    def test_an_explicit_meal_request_gets_the_echo_and_nothing_else(self):
        payload, _ = self._run_meal(mode=na.MODE_MEAL, echo=True)
        self.assertEqual(payload.pop("mode"), "meal")
        self.assertEqual(json.dumps(payload), EXPECTED_MEAL_JSON)

    def test_meal_mode_never_reads_a_label(self):
        def explode(*args, **kwargs):
            raise AssertionError("label reader called in meal mode")

        with mock.patch.object(claude_vision, "read_nutrition_label", side_effect=explode):
            payload, _ = self._run_meal()
        self.assertEqual(payload["foods"][0]["name"], "Grilled chicken breast")

    def test_meal_failures_are_unchanged(self):
        with mock.patch.object(
            claude_vision,
            "detect_foods",
            side_effect=claude_vision.VisionRefusal("Claude declined to analyze this image."),
        ):
            payload, status = na.analyze_meal("aW1hZ2U=", "image/jpeg")
        self.assertEqual(status, 200)
        self.assertNotIn("mode", payload)
        self.assertEqual(payload["error"]["kind"], "refusal")


class RoutingTests(unittest.TestCase):
    """The mode flag reaches the analyzer, and a bad one is a 400 -- checked
    at the handler, without opening a socket."""

    def _post(self, body: dict):
        handler = object.__new__(http_app.NutritionRequestHandler)
        sent: dict = {}

        def capture(payload, status=200):
            sent["payload"] = payload
            sent["status"] = status

        handler._send_json = capture  # type: ignore[method-assign]
        handler._handle_analyze_food(body)
        return sent["payload"], sent["status"]

    def test_label_mode_reaches_the_label_reader(self):
        with mock.patch.object(
            claude_vision, "read_nutrition_label", return_value=label_reading()
        ):
            payload, status = self._post(
                {"image": "aW1hZ2U=", "media_type": "image/jpeg",
                 "mode": "nutrition_label"}
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "nutrition_label")
        self.assertEqual(payload["foods"][0]["macros"]["protein"], 33.3)

    def test_no_mode_runs_a_meal_analysis(self):
        with mock.patch.object(
            claude_vision, "detect_foods", return_value=json.loads(json.dumps(MEAL_DETECTION))
        ), mock.patch.object(usda_client, "lookup", return_value=None):
            payload, status = self._post({"image": "aW1hZ2U=", "media_type": "image/jpeg"})
        self.assertEqual(status, 200)
        self.assertNotIn("mode", payload)

    def test_unknown_mode_is_a_400_and_costs_no_tokens(self):
        def explode(*args, **kwargs):
            raise AssertionError("a model was called for an invalid request")

        with mock.patch.object(claude_vision, "detect_foods", side_effect=explode), \
                mock.patch.object(claude_vision, "read_nutrition_label", side_effect=explode):
            payload, status = self._post({"image": "aW1hZ2U=", "mode": "barcode"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["kind"], "bad_request")
        self.assertTrue(payload["needs_confirmation"])


if __name__ == "__main__":
    unittest.main()
