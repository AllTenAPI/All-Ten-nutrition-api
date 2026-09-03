"""Tests for the Open Food Facts barcode client.

Stdlib ``unittest`` on purpose: the deploy image has no pytest.
No network call and no credential -- Open Food Facts needs neither, and the
HTTP layer is stubbed at ``openfoodfacts_client._http_get_json``.

The unit conversions get the most attention here. Open Food Facts stores every
per-100 g nutriment in grams, including minerals and vitamins, while our
contract reports minerals in mg and several vitamins in mcg. Getting a factor
wrong would be reported to the user as a confident, precise, wrong number.
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

import openfoodfacts_client as off


def _product(**nutriments):
    return {
        "code": "3017620422003",
        "product_name": "Test Product",
        "brands": "Test Brand, Other Brand",
        "nutriments": nutriments,
    }


def _ok(product):
    return lambda url, timeout: (200, {"status": 1, "product": product})


def _http_error(url, code, reason):
    """An HTTPError with a real (empty) body, so it does not leave an
    unclosed temp file behind and trip a ResourceWarning."""
    return urllib.error.HTTPError(url, code, reason, {}, io.BytesIO(b""))


class UserAgentTests(unittest.TestCase):
    def test_user_agent_identifies_the_app_a_version_and_a_contact(self):
        # Open Food Facts' API etiquette asks for exactly this.
        self.assertIn("AllTenNutritionAPI", off.USER_AGENT)
        self.assertIn("/2.0", off.USER_AGENT)
        self.assertIn("All Ten", off.USER_AGENT)
        self.assertIn("http", off.USER_AGENT)

    def test_user_agent_is_actually_sent(self):
        captured = {}

        real_urlopen = off.urllib.request.urlopen

        class FakeResponse:
            status = 200

            def read(self):
                return json.dumps({"status": 0}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=None):
            captured["ua"] = request.get_header("User-agent")
            return FakeResponse()

        with mock.patch.object(off.urllib.request, "urlopen", fake_urlopen):
            off.clear_cache()
            off.lookup_barcode("3017620422003")

        self.assertEqual(captured["ua"], off.USER_AGENT)
        self.assertIs(off.urllib.request.urlopen, real_urlopen)
        off.clear_cache()


class MacroParsingTests(unittest.TestCase):
    def test_macros_are_read_in_grams(self):
        parsed = off.parse_product(
            _product(
                **{
                    "energy-kcal_100g": 539,
                    "proteins_100g": 6.3,
                    "carbohydrates_100g": 57.5,
                    "fat_100g": 30.9,
                    "fiber_100g": 3.4,
                    "sugars_100g": 56.3,
                }
            )
        )
        macros = parsed["per_100g"]
        self.assertEqual(macros["calories"], 539)
        self.assertEqual(macros["protein"], 6.3)
        self.assertEqual(macros["carbs"], 57.5)
        self.assertEqual(macros["fat"], 30.9)
        self.assertEqual(macros["fiber"], 3.4)
        self.assertEqual(macros["sugar"], 56.3)

    def test_sodium_is_converted_from_grams_to_milligrams(self):
        parsed = off.parse_product(_product(sodium_100g=0.107))
        self.assertAlmostEqual(parsed["per_100g"]["sodium"], 107.0)

    def test_salt_is_used_when_sodium_is_absent(self):
        parsed = off.parse_product(_product(salt_100g=1.0))
        # 1 g salt / 2.5 = 0.4 g sodium = 400 mg
        self.assertAlmostEqual(parsed["per_100g"]["sodium"], 400.0)

    def test_declared_sodium_wins_over_salt(self):
        parsed = off.parse_product(_product(sodium_100g=0.1, salt_100g=99.0))
        self.assertAlmostEqual(parsed["per_100g"]["sodium"], 100.0)

    def test_kilojoules_are_converted_when_kcal_is_absent(self):
        parsed = off.parse_product(_product(**{"energy-kj_100g": 2252}))
        self.assertAlmostEqual(parsed["per_100g"]["calories"], 2252 / 4.184, places=3)

    def test_kcal_is_preferred_over_kilojoules(self):
        parsed = off.parse_product(
            _product(**{"energy-kcal_100g": 539, "energy-kj_100g": 2252})
        )
        self.assertEqual(parsed["per_100g"]["calories"], 539)

    def test_bare_energy_field_is_read_as_kilojoules_by_default(self):
        # Open Food Facts defaults energy_100g to kJ. Reading it as kcal would
        # understate the product roughly fourfold.
        parsed = off.parse_product(_product(energy_100g=2252))
        self.assertAlmostEqual(parsed["per_100g"]["calories"], 2252 / 4.184, places=3)

    def test_bare_energy_field_honours_a_declared_kcal_unit(self):
        parsed = off.parse_product(_product(energy_100g=539, energy_unit="kcal"))
        self.assertEqual(parsed["per_100g"]["calories"], 539)


class MicronutrientParsingTests(unittest.TestCase):
    def test_minerals_are_converted_from_grams_to_milligrams(self):
        parsed = off.parse_product(
            _product(calcium_100g=0.12, iron_100g=0.0036, potassium_100g=0.4)
        )
        micros = parsed["micronutrients_per_100g"]
        self.assertAlmostEqual(micros["calcium"], 120.0)
        self.assertAlmostEqual(micros["iron"], 3.6)
        self.assertAlmostEqual(micros["potassium"], 400.0)

    def test_vitamins_reported_in_mcg_are_converted_from_grams(self):
        parsed = off.parse_product(
            _product(
                **{
                    "vitamin-a_100g": 0.0008,      # 800 mcg RAE
                    "vitamin-d_100g": 0.000005,    # 5 mcg
                    "vitamin-b12_100g": 0.0000024, # 2.4 mcg
                    "vitamin-b9_100g": 0.0004,     # 400 mcg DFE
                }
            )
        )
        micros = parsed["micronutrients_per_100g"]
        self.assertAlmostEqual(micros["vitamin_a"], 800.0)
        self.assertAlmostEqual(micros["vitamin_d"], 5.0)
        self.assertAlmostEqual(micros["vitamin_b12"], 2.4)
        self.assertAlmostEqual(micros["folate"], 400.0)

    def test_vitamin_c_is_converted_to_milligrams(self):
        parsed = off.parse_product(_product(**{"vitamin-c_100g": 0.06}))
        self.assertAlmostEqual(parsed["micronutrients_per_100g"]["vitamin_c"], 60.0)

    def test_micronutrient_names_match_the_usda_contract(self):
        import usda_client as uc

        ours = {field for field, _ in off.MICRO_FIELDS.values()}
        theirs = set(uc.MICRO_NUTRIENT_IDS.values())
        self.assertTrue(
            ours.issubset(theirs),
            f"Open Food Facts emits keys the contract does not define: {ours - theirs}",
        )


class NullNotZeroTests(unittest.TestCase):
    """Open Food Facts is crowd-sourced and often partial. An unentered
    nutrient is unknown, and must never arrive as 0."""

    def test_absent_nutriments_are_absent_not_zero(self):
        parsed = off.parse_product(_product(**{"energy-kcal_100g": 100}))
        macros = parsed["per_100g"]
        for field in ("protein", "carbs", "fat", "fiber", "sugar", "sodium"):
            self.assertNotIn(field, macros, field)
        self.assertEqual(parsed["micronutrients_per_100g"], {})

    def test_a_product_with_no_nutriments_at_all_yields_an_empty_macro_map(self):
        parsed = off.parse_product(_product())
        self.assertEqual(parsed["per_100g"], {})
        self.assertEqual(parsed["micronutrients_per_100g"], {})

    def test_blank_and_non_numeric_values_are_dropped_not_zeroed(self):
        parsed = off.parse_product(
            _product(proteins_100g="", carbohydrates_100g="n/a", fat_100g=None)
        )
        self.assertEqual(parsed["per_100g"], {})

    def test_negative_values_are_dropped(self):
        parsed = off.parse_product(_product(proteins_100g=-3))
        self.assertEqual(parsed["per_100g"], {})

    def test_a_genuine_zero_survives_as_zero(self):
        parsed = off.parse_product(_product(fat_100g=0))
        self.assertEqual(parsed["per_100g"]["fat"], 0.0)

    def test_numeric_strings_are_accepted(self):
        parsed = off.parse_product(_product(proteins_100g="6.3"))
        self.assertEqual(parsed["per_100g"]["protein"], 6.3)

    def test_booleans_are_not_numbers(self):
        self.assertIsNone(off._number(True))
        self.assertIsNone(off._number(False))


class ProductMetadataTests(unittest.TestCase):
    def test_first_brand_is_used(self):
        parsed = off.parse_product(_product())
        self.assertEqual(parsed["brand"], "Test Brand")

    def test_missing_brand_is_null(self):
        product = _product()
        product["brands"] = ""
        self.assertIsNone(off.parse_product(product)["brand"])

    def test_fdc_id_is_null_because_there_is_none(self):
        self.assertIsNone(off.parse_product(_product())["fdc_id"])

    def test_data_type_names_the_source(self):
        self.assertEqual(off.parse_product(_product())["data_type"], "Open Food Facts")

    def test_english_name_is_a_fallback(self):
        product = _product()
        del product["product_name"]
        product["product_name_en"] = "Chocolate spread"
        self.assertEqual(off.parse_product(product)["description"], "Chocolate spread")

    def test_serving_is_parsed(self):
        product = _product()
        product["serving_size"] = "15 g"
        product["serving_quantity"] = 15
        serving = off.parse_product(product)["serving"]
        self.assertEqual(serving["serving_size_grams"], 15.0)
        self.assertEqual(serving["household_serving"], "15 g")

    def test_absent_serving_is_none_not_a_guessed_100g(self):
        self.assertIsNone(off.parse_product(_product())["serving"])


class LookupTests(unittest.TestCase):
    def setUp(self):
        off.clear_cache()

    def tearDown(self):
        off.clear_cache()

    def test_happy_path(self):
        with mock.patch.object(
            off, "_http_get_json", _ok(_product(**{"energy-kcal_100g": 539}))
        ):
            result = off.lookup_barcode("3017620422003")
        self.assertEqual(result["description"], "Test Product")
        self.assertEqual(result["per_100g"]["calories"], 539)

    def test_status_zero_is_a_clean_miss(self):
        with mock.patch.object(
            off, "_http_get_json",
            lambda url, timeout: (200, {"status": 0, "status_verbose": "not found"}),
        ):
            self.assertIsNone(off.lookup_barcode("9999999999999"))

    def test_http_404_is_a_clean_miss_not_an_outage(self):
        def raise_404(url, timeout):
            raise _http_error(url, 404, "Not Found")

        with mock.patch.object(off, "_http_get_json", raise_404):
            self.assertIsNone(off.lookup_barcode("9999999999999"))

    def test_a_product_with_no_name_is_a_miss(self):
        product = _product(**{"energy-kcal_100g": 100})
        product["product_name"] = ""
        with mock.patch.object(off, "_http_get_json", _ok(product)):
            self.assertIsNone(off.lookup_barcode("3017620422003"))

    def test_empty_product_document_is_a_miss(self):
        with mock.patch.object(
            off, "_http_get_json", lambda url, timeout: (200, {"status": 1, "product": {}})
        ):
            self.assertIsNone(off.lookup_barcode("3017620422003"))

    def test_server_error_is_an_outage(self):
        def raise_503(url, timeout):
            raise _http_error(url, 503, "Service Unavailable")

        with mock.patch.object(off, "_http_get_json", raise_503):
            with self.assertRaises(off.OpenFoodFactsUnavailable):
                off.lookup_barcode("3017620422003")

    def test_rate_limit_is_an_outage(self):
        def raise_429(url, timeout):
            raise _http_error(url, 429, "Too Many Requests")

        with mock.patch.object(off, "_http_get_json", raise_429):
            with self.assertRaises(off.OpenFoodFactsUnavailable) as ctx:
                off.lookup_barcode("3017620422003")
        self.assertIn("429", str(ctx.exception))

    def test_network_failure_is_an_outage(self):
        def raise_urlerror(url, timeout):
            raise urllib.error.URLError("name resolution failed")

        with mock.patch.object(off, "_http_get_json", raise_urlerror):
            with self.assertRaises(off.OpenFoodFactsUnavailable):
                off.lookup_barcode("3017620422003")

    def test_malformed_json_is_an_outage_not_a_miss(self):
        def raise_json(url, timeout):
            raise json.JSONDecodeError("boom", "", 0)

        with mock.patch.object(off, "_http_get_json", raise_json):
            with self.assertRaises(off.OpenFoodFactsUnavailable):
                off.lookup_barcode("3017620422003")

    def test_outages_are_not_cached(self):
        calls = {"n": 0}

        def raise_503(url, timeout):
            calls["n"] += 1
            raise _http_error(url, 503, "Service Unavailable")

        with mock.patch.object(off, "_http_get_json", raise_503):
            for _ in range(2):
                with self.assertRaises(off.OpenFoodFactsUnavailable):
                    off.lookup_barcode("3017620422003")

        self.assertEqual(calls["n"], 2, "an outage must not poison the cache")

    def test_misses_are_cached(self):
        calls = {"n": 0}

        def miss(url, timeout):
            calls["n"] += 1
            return 200, {"status": 0}

        with mock.patch.object(off, "_http_get_json", miss):
            off.lookup_barcode("9999999999999")
            off.lookup_barcode("9999999999999")

        self.assertEqual(calls["n"], 1)
        self.assertEqual(off.cache_stats()["hits"], 1)

    def test_empty_barcode_never_calls_the_network(self):
        with mock.patch.object(
            off, "_http_get_json", side_effect=AssertionError("called")
        ):
            self.assertIsNone(off.lookup_barcode("no digits"))

    def test_barcode_is_url_encoded_into_the_path(self):
        seen = {}

        def capture(url, timeout):
            seen["url"] = url
            return 200, {"status": 0}

        with mock.patch.object(off, "_http_get_json", capture):
            off.lookup_barcode("3017620422003")

        self.assertIn("/product/3017620422003.json", seen["url"])
        self.assertIn("fields=", seen["url"])


if __name__ == "__main__":
    unittest.main()
