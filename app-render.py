#!/usr/bin/env python3
"""Render deploy entrypoint.

Started by ``poetry run python app-render.py`` (see render.yaml).

All routing and analysis logic lives in ``http_app`` / ``nutrition_analyzer``
so this entrypoint and ``app-railway.py`` share one implementation. The
Google Cloud Vision analyzer that used to live in this file is preserved in
git history at commit 1683dbe; it was replaced because label detection cannot
estimate portion size, which is what made calorie totals wrong.

Environment variables (set on Render, never in this repo):
  ANTHROPIC_API_KEY        required -- resolved by the Anthropic SDK itself
  USDA_FDC_API_KEY         required -- USDA FoodData Central
  NUTRITION_MODEL          optional -- defaults to claude-sonnet-5
  MAX_MEAL_CALORIES        optional -- defaults to 2500
  MIN_CONFIDENCE           optional -- defaults to 0.5
  MAX_FOOD_PORTION_GRAMS   optional -- defaults to 1500
  PORT                     supplied by Render
"""

import http_app

if __name__ == "__main__":
    http_app.run(platform="render", default_port=10000)
