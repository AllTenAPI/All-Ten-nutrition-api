#!/usr/bin/env python3
"""Railway deploy entrypoint.

Started by ``python app-railway.py`` (see railway.json). Health check path is
``/health``.

All routing and analysis logic lives in ``http_app`` / ``nutrition_analyzer``
so this entrypoint and ``app-render.py`` share one implementation. This file
previously returned a hardcoded 250 kcal placeholder with ~60 invented
micronutrient fields; it now runs the real Claude vision + USDA pipeline.

Environment variables (set on Railway, never in this repo):
  ANTHROPIC_API_KEY        required -- resolved by the Anthropic SDK itself
  USDA_FDC_API_KEY         required -- USDA FoodData Central
  NUTRITION_MODEL          optional -- defaults to claude-sonnet-5
  MAX_MEAL_CALORIES        optional -- defaults to 2500
  MIN_CONFIDENCE           optional -- defaults to 0.5
  MAX_FOOD_PORTION_GRAMS   optional -- defaults to 1500
  PORT                     supplied by Railway
"""

import http_app

if __name__ == "__main__":
    http_app.run(platform="railway", default_port=5000)
