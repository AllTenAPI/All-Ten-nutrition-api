# All Ten Nutrition API — Deployment Guide

Two platforms are configured. Both run the same code via `http_app.py`.
There is no Flask and no gunicorn anywhere in this service — both entrypoints
are stdlib `http.server`.

---

## 1. Provision credentials (owner only)

Get these before deploying. **Set them as environment variables on the deploy
platform. Never put them in this repo, a `.env` committed to git, or a
`render.yaml`/`railway.json` file.**

| Variable | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic Console → API keys |
| `USDA_FDC_API_KEY` | https://fdc.nal.usda.gov/api-key-signup.html (free) |

Optional overrides: `NUTRITION_MODEL`, `MAX_MEAL_CALORIES`, `MIN_CONFIDENCE`,
`MAX_FOOD_PORTION_GRAMS`.

## 2. Render

`render.yaml` is already configured:

- Build command: `poetry install`
- Start command: `poetry run python app-render.py`
- `PYTHON_VERSION`: `3.11.7`

In the Render dashboard, add `ANTHROPIC_API_KEY` and `USDA_FDC_API_KEY` under
**Environment → Environment Variables** (mark them secret), then deploy.

Because `pyproject.toml` changed in 2.0.0 (`google-cloud-vision`, `Flask` and
`Flask-CORS` removed, `anthropic` added), the first deploy will resolve a new
dependency set. If a stale `poetry.lock` is ever added to this repo, it must be
regenerated with `poetry lock` or `poetry install` will refuse to run.

The old `GOOGLE_APPLICATION_CREDENTIALS_JSON` variable is no longer read by
anything and can be deleted from the Render environment.

## 3. Railway

`railway.json` is already configured:

- Builder: NIXPACKS (installs from `requirements.txt`)
- Start command: `python app-railway.py`
- Health check: `/health`

Add the same two variables under **Variables**, then deploy.

## 4. Verify

Replace `$API` with the deployed base URL.

```bash
# 1. Liveness. Expect "status": "healthy".
curl -s $API/health
```

Expect `"vision": "configured"` and `"usda": "configured"`. If either says
`not_configured`, the corresponding variable is missing.

```bash
# 2. Configuration. Reports "set"/"not set" only -- no secret values.
curl -s $API/debug
```

Check: `anthropic_sdk_installed` is `true`, `model` is the one you intend, and
both required variables read `"set"`.

```bash
# 3. Real analysis with a real meal photo.
curl -s -X POST $API/analyze_food \
  -H 'Content-Type: application/json' \
  -d "{\"image\": \"$(base64 -i meal.jpg | tr -d '\n')\"}" | python3 -m json.tool
```

What to check in the response:

- `foods[].portion_grams` is a plausible weight for what is in the photo.
  **This is the number the whole rebuild is about** — if portions look right,
  the calories will too.
- `foods[].source` is `"usda"` for common foods. Widespread `"estimated"`
  means the USDA key is wrong or USDA is unreachable; `warnings` will say so.
- `totals.calories` is in a sane range for the meal.
- `needs_confirmation` is `false` for a clear photo of everyday food. If it is
  `true`, `confirmation_reason` states exactly why.

```bash
# 4. Rejection path. Expect HTTP 400 with error.kind "bad_request".
curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/analyze_food \
  -H 'Content-Type: application/json' -d '{"image": "not-an-image"}'
```

## 5. A/B testing models

Change one environment variable and restart. No code change:

```
NUTRITION_MODEL=claude-haiku-4-5    # cheaper, faster
NUTRITION_MODEL=claude-sonnet-5     # default
NUTRITION_MODEL=claude-opus-5       # most capable
```

Every response echoes `model`, so results are attributable after the fact.
Compare on `portion_grams` accuracy against weighed meals — that is the axis
that drives calorie error.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `error.kind: "misconfigured"` | `ANTHROPIC_API_KEY` unset, or `anthropic` not installed | Check `/debug`; set the variable or fix the build |
| `error.kind: "auth"` | Key rejected by Anthropic | Rotate the key in the platform dashboard |
| Everything `source: "estimated"`, `usda_available: false` | USDA key missing/invalid or USDA unreachable | Check `/debug` and the `warnings` array |
| `needs_confirmation` always `true` | Working as designed — read `confirmation_reason` | Tune `MIN_CONFIDENCE` / `MAX_MEAL_CALORIES` only if the reasons are genuinely spurious |
| Build fails resolving dependencies | Stale lock file vs. the 2.0.0 `pyproject.toml` | `poetry lock` and redeploy |

## Rollback

The Google Cloud Vision implementation is preserved in git history at commit
`1683dbe`. Rolling back also means restoring `GOOGLE_APPLICATION_CREDENTIALS_JSON`
on the platform — and it restores the portion-estimation defect, so prefer
fixing forward.
