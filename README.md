# All Ten Nutrition API

Meal-photo nutrition analysis: **Claude vision for portion estimation** plus
**USDA FoodData Central for authoritative macros**.

The client-facing response contract lives in [API_CONTRACT.md](API_CONTRACT.md).
Deploy steps are in [DEPLOYMENT.md](DEPLOYMENT.md).

## Why this was rebuilt

The previous version used Google Cloud Vision label detection. Labels tell you
*what* is on the plate ("pizza", "food") but carry no notion of *how much*, so
calories were looked up against an assumed serving size. That is what made a
~1,200 kcal meal report as ~6,000.

Portion weight in grams is now a first-class output of the vision step, and
every nutrient that can be sourced from a database is sourced rather than
generated. The ~60 fabricated micronutrient fields — which included dopamine,
serotonin and melatonin, all produced by `random.uniform()` — are gone.

## How it works

1. **Vision** (`claude_vision.py`) — one Claude call per photo. Structured
   output via `output_config.format` with a JSON schema; the model returns each
   food, its as-served weight in grams, and an honest per-food confidence.
   Runs at `effort: "low"` because this is bounded extraction, not reasoning.
2. **USDA** (`usda_client.py`) — each food is looked up in FoodData Central for
   per-100 g macros and micronutrients, then scaled by the estimated grams.
   Lookups are cached in-process by normalized food name.
3. **Clamps** (`nutrition_analyzer.py`) — implausible totals, low confidence,
   estimated-rather-than-sourced macros, and capped portions all set
   `needs_confirmation: true` with a reason, so the client asks the user
   instead of silently logging a wrong number.

That pipeline is `POST /analyze_food` with the default `mode: "meal"`, and
that endpoint is the only one that costs LLM tokens.

`POST /analyze_food` with `mode: "nutrition_label"` runs a different job on
the same endpoint. Photographing the back of a box is a **reading** problem,
not a recognition one: there is no portion to estimate, and matching the name
to USDA returns a *generic* product whose macros are not this product's. Label
mode transcribes the printed panel per serving, converts it to the per-100 g
basis using the panel's own serving weight, and refuses rather than guesses
when the panel is unreadable or gives no weight in grams. It makes no USDA
call.

Two lookup endpoints handle the cases that never needed a model:

- **`POST /search_food`** — free-text search against USDA. Branded data is
  searchable but ranked below Foundation / SR Legacy / FNDDS, so "grilled
  chicken" still resolves to lab-measured data while "quest protein bar"
  becomes findable.
- **`POST /barcode`** — UPC/EAN. USDA Branded first, then Open Food Facts
  (free, no key, far better coverage outside the US). An unknown barcode
  returns a clean not-found; macros are never invented for it.

Neither makes an Anthropic call. See `API_CONTRACT.md`.

## Layout

| File | Role |
|---|---|
| `nutrition_analyzer.py` | Orchestration, scaling, aggregation, clamps, response shaping |
| `claude_vision.py` | Claude vision step, image validation, error taxonomy |
| `usda_client.py` | USDA FoodData Central lookup, ranking, search, barcode + cache |
| `openfoodfacts_client.py` | Open Food Facts barcode fallback + cache |
| `food_lookup.py` | `/search_food` and `/barcode` — no LLM call on any path |
| `http_app.py` | Shared routes and server, used by both entrypoints |
| `app-render.py` | Render entrypoint (`poetry run python app-render.py`) |
| `app-railway.py` | Railway entrypoint (`python app-railway.py`) |
| `test_nutrition_analyzer.py` | Analyzer pipeline tests (stdlib `unittest`) |
| `test_nutrition_label.py` | Label mode, and the proof meal mode did not move |
| `test_usda_ranking.py` | Data-type ranking, search paging, USDA barcode matching |
| `test_food_lookup.py` | `/search_food` and `/barcode` behaviour |
| `test_openfoodfacts_client.py` | Open Food Facts parsing and unit conversions |

`app.py`, `app-simple.py`, `app-minimal.py` and `app-render.py.backup` are dead
code kept only for reference. Nothing deploys them; do not build on them.

## Configuration

All credentials are environment variables on the deploy platform. **No key is
ever read into a response, written to a file, or committed to this repo.**

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes | — | Resolved by the Anthropic SDK itself; never passed explicitly in code |
| `USDA_FDC_API_KEY` | yes | — | USDA FoodData Central (free key) |
| `NUTRITION_MODEL` | no | `claude-sonnet-5` | The model to run. Change this to A/B `claude-haiku-4-5` or `claude-opus-5` — it is one constant, not scattered strings |
| `MAX_MEAL_CALORIES` | no | `2500` | Above this, flag for confirmation |
| `MIN_CONFIDENCE` | no | `0.5` | Below this, flag for confirmation |
| `MAX_FOOD_PORTION_GRAMS` | no | `1500` | Cap on a single food's portion |
| `PORT` | no | platform | Supplied by Render/Railway |

If a required variable is missing the server starts, says so on stdout and at
`/debug`, and returns a clear `misconfigured` error — it never falls back to a
default key and never guesses nutrition data.

## Local development

```bash
pip install -r requirements.txt

export ANTHROPIC_API_KEY=<set-in-deploy-env>   # supply your own; never commit one
export USDA_FDC_API_KEY=<set-in-deploy-env>

python app-render.py          # http://localhost:10000
```

```bash
curl localhost:10000/health
curl localhost:10000/debug

# costs LLM tokens
curl -X POST localhost:10000/analyze_food \
  -H 'Content-Type: application/json' \
  -d "{\"image\": \"$(base64 -i meal.jpg | tr -d '\n')\"}"

# also costs LLM tokens — reads the printed panel instead of estimating
curl -X POST localhost:10000/analyze_food \
  -H 'Content-Type: application/json' \
  -d "{\"mode\": \"nutrition_label\", \"image\": \"$(base64 -i label.jpg | tr -d '\n')\"}"

# no LLM tokens
curl -X POST localhost:10000/search_food \
  -H 'Content-Type: application/json' \
  -d '{"query": "quest protein bar", "limit": 10}'

curl -X POST localhost:10000/barcode \
  -H 'Content-Type: application/json' \
  -d '{"barcode": "0888849000371"}'
```

## Tests

No pytest in the deploy image, so the suite is stdlib `unittest`:

```bash
python3 -m unittest discover -p 'test_*.py'
```

270 tests, no network calls and no credentials required. They cover portion
scaling, macro aggregation, clamp logic, response shaping, USDA parsing and
outage degradation, image validation, that `/debug` leaks nothing, USDA
data-type ranking with Branded last, `/search_food` and `/barcode` including
their not-found paths, barcode cache hits, Open Food Facts unit conversions,
label mode's per-serving to per-100 g conversion and its refusal to guess a
serving weight, and that an unmeasured nutrient is `null` rather than `0`
everywhere.

One of them, `test_meal_output_is_byte_identical`, pins the exact JSON a meal
scan produces. Meal mode is the path every scan the app has ever made went
through, so a change there is a silent change to numbers users already logged.

## Dependencies

`anthropic` is the only runtime dependency. USDA and Open Food Facts are both
called with stdlib `urllib` and the server is stdlib `http.server`, so there
is no HTTP client and no web framework. Open Food Facts needs no credential.
