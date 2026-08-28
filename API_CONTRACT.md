# All Ten Nutrition API — Response Contract

`analysis_version: 2.0.0-claude-usda`

This is the contract the Flutter client is built against. Both deploy
entrypoints (`app-render.py`, `app-railway.py`) serve it from the same
implementation.

---

## `POST /analyze_food`

### Request

```json
{
  "image": "<base64 image, or a data: URL>",
  "media_type": "image/jpeg"
}
```

| Field | Required | Notes |
|---|---|---|
| `image` | yes | Bare base64 or `data:image/jpeg;base64,…`. Whitespace and newlines are stripped server-side. Also accepted under the legacy key `image_base64`. |
| `media_type` | no | Advisory only. The server sniffs the real type from magic bytes and that wins. Also accepted as `mime_type`. |

Supported formats: `image/jpeg`, `image/png`, `image/gif`, `image/webp`.
Maximum decoded image size: 5 MB. Maximum request body: 8 MB.

### Response

```json
{
  "foods": [
    {
      "name": "Grilled chicken breast",
      "portion_grams": 250,
      "confidence": 0.87,
      "macros": {
        "calories": 412,
        "protein": 77.5,
        "carbs": 0.0,
        "fat": 9.0,
        "fiber": 0.0,
        "sugar": 0.0,
        "sodium": 185
      },
      "micronutrients": { "iron": 2.6, "potassium": 640.0 },
      "source": "usda",
      "usda_description": "Chicken, broilers or fryers, breast, meat only, cooked",
      "fdc_id": 171077
    }
  ],
  "totals": {
    "calories": 1117,
    "protein": 46.2,
    "carbs": 138.6,
    "fat": 42.0,
    "fiber": 6.3,
    "sugar": 8.1,
    "sodium": 1840,
    "micronutrients": { "calcium": 310.0, "iron": 4.1 }
  },
  "needs_confirmation": false,
  "confirmation_reason": null,
  "confidence": 0.84,
  "model": "claude-sonnet-5",
  "analysis_version": "2.0.0-claude-usda",
  "usda_available": true,
  "notes": "",
  "warnings": [],
  "analyzed_at": 1787873781.93
}
```

### Field reference

#### `foods[]` — the editable unit

The client should render one row per entry and let the user adjust
`portion_grams`. Everything scales linearly from it, so the client can
recompute macros locally as `macro × new_grams / portion_grams` without a
round trip.

| Field | Type | Notes |
|---|---|---|
| `name` | string | Display name. |
| `portion_grams` | number | As-served weight estimated from the photo. **This is the number the user edits.** |
| `confidence` | number | 0–1, for this food and its portion. |
| `macros` | object | See below. Any field may be `null`. |
| `micronutrients` | object | May be `{}`. Only populated for `source: "usda"`. |
| `source` | `"usda"` \| `"estimated"` | Where the macros came from. |
| `usda_description` | string \| null | The matched USDA record, so the user can see what was looked up. `null` when `source` is `"estimated"`. |
| `fdc_id` | integer \| null | USDA FoodData Central id. `null` when estimated. |

#### `macros` and `totals`

Seven fields, always present as keys:

| Field | Unit |
|---|---|
| `calories` | kcal |
| `protein` | g |
| `carbs` | g |
| `fat` | g |
| `fiber` | g |
| `sugar` | g |
| `sodium` | mg |

**Any value may be `null`, and `null` is not zero.** It means "not known",
which happens when USDA has no value for that nutrient or when the food fell
back to a model estimate (estimates cover calories/protein/carbs/fat only).
A total is the sum of the foods that had a value; if no food had one, the
total is `null`. The client must render `null` as "—", never as `0`.

`totals.micronutrients` is a flat `{name: number}` map summed across the
USDA-sourced foods only. Possible keys and their units:

| Key | Unit |
|---|---|
| `calcium`, `iron`, `magnesium`, `phosphorus`, `potassium`, `zinc`, `vitamin_c` | mg |
| `vitamin_a` | mcg RAE |
| `vitamin_d`, `vitamin_b12`, `folate` | mcg (folate is mcg DFE) |

The map is sparse — treat a missing key as "not known", and do not assume any
particular key is present.

#### `needs_confirmation` and `confirmation_reason`

**`needs_confirmation: true` means: show the result but ask the user to
confirm or edit it before logging.** `confirmation_reason` is a
human-readable sentence explaining why, safe to show directly. It is `null`
exactly when `needs_confirmation` is `false`.

It is set to `true` when any of these hold:

- No food could be identified.
- `totals.calories` exceeds `MAX_MEAL_CALORIES` (default 2500).
- Overall or per-food `confidence` is below `MIN_CONFIDENCE` (default 0.5).
- Any food has `source: "estimated"` — the numbers are not from a database.
- A portion was clamped as implausible (above `MAX_FOOD_PORTION_GRAMS`,
  default 1500 g).

Multiple reasons are joined with `; `.

#### Other fields

| Field | Type | Notes |
|---|---|---|
| `confidence` | number | 0–1 overall. |
| `model` | string | The model that produced this result. Echoed so A/B runs are attributable. |
| `analysis_version` | string | Bump this on any breaking contract change. |
| `usda_available` | boolean | `false` means USDA was unreachable or unconfigured for this request. |
| `notes` | string | Short free-text note from the model about anything ambiguous. May be `""`. |
| `warnings` | string[] | Operational warnings (e.g. a USDA outage message). Not user-facing copy. |
| `analyzed_at` | number | Unix timestamp. |

### Failure responses

Failures use the **same shape** with an added `error` object, so the client
has one parser and one rendering path. `foods` is `[]`, every total is `null`,
and `needs_confirmation` is `true` with the reason set — the client shows the
manual-entry editor rather than any invented number.

```json
{
  "foods": [],
  "totals": { "calories": null, "…": null, "micronutrients": {} },
  "needs_confirmation": true,
  "confirmation_reason": "Claude rate limit reached. Try again shortly.",
  "error": {
    "kind": "rate_limit",
    "message": "Claude rate limit reached. Try again shortly.",
    "retryable": true
  }
}
```

| `error.kind` | HTTP | `retryable` | Meaning |
|---|---|---|---|
| `bad_request` | 400 | false | Missing, malformed, oversized, or unsupported image. |
| `refusal` | 200 | false | The model declined this image. Ask for a different photo. |
| `rate_limit` | 200 | true | Rate limited upstream. Back off and retry. |
| `upstream_error` | 200 | true | Claude 5xx. |
| `connection` | 200 | true | Network failure reaching Claude. |
| `parse_error` | 200 | true | Malformed model output. Retry is reasonable. |
| `auth` | 500 | false | Bad credentials. Operator problem, not a user problem. |
| `misconfigured` | 500 | false | SDK or key missing on the server. Operator problem. |
| `internal_error` | 500 | true | Unexpected. |

Client rule: **retry only when `retryable` is `true`**, with backoff. On a
non-retryable failure, show `confirmation_reason` and offer manual entry.

---

## `GET /health`

```json
{
  "status": "healthy",
  "service": "all-ten-nutrition-api",
  "analysis_version": "2.0.0-claude-usda",
  "model": "claude-sonnet-5",
  "vision": "configured",
  "usda": "configured",
  "timestamp": 1787873781.93
}
```

`vision` and `usda` are `"configured"` / `"not_configured"`. Used as the
Railway health check path.

## `GET /debug`

Operator diagnostics. Reports the active model, whether the SDK is installed,
the effective thresholds, USDA cache statistics, and — for each environment
variable — the literal string `"set"` or `"not set"`.

**It never returns a credential value, length, prefix, or preview.** The
previous `/debug` returned the first 100 characters of the Google credentials
JSON; that is why this one reports presence only.

## `GET /`

Service banner: name, platform, analysis description, and the endpoint list.

---

## Removed from the nutrient model

The previous response carried roughly 60 micronutrient fields, **every one of
them generated by `random.uniform()` seeded from an MD5 of the image** — they
were not measurements of anything. Removed entirely:

- **Neurotransmitters and hormones**, which are not food-label nutrients:
  `dopamine`, `serotonin`, `norepinephrine`, `epinephrine`, `melatonin`,
  `histamine`, `gaba`.
- **All 20 free amino acids** (`glycine`, `proline`, `serine`, `threonine`,
  `tryptophan`, `tyrosine`, `valine`, `alanine`, `arginine`, `asparagine`,
  `aspartic_acid`, `cysteine`, `glutamine`, `glutamic_acid`, `isoleucine`,
  `leucine`, `lysine`, `methionine`, `phenylalanine`, `histidine`) —
  subsumed by total protein for this app's purposes.
- **Non-essential and supplement-style compounds**: `taurine`, `creatine`,
  `carnitine`, `inositol`, `betaine`, `choline`, `coq10`, `glutathione`,
  `paba`, `lipoic_acid`.
- **Trace minerals and vitamins USDA rarely populates** for prepared foods,
  which would have been mostly `null` noise: `chromium`, `molybdenum`,
  `iodine`, `chloride`, `biotin`, `selenium`, `copper`, `manganese`,
  `pantothenic_acid`, `vitamin_e`, `vitamin_k`, `niacin`, `riboflavin`,
  `thiamin`, `vitamin_b6`.

Kept: the 11 micronutrients above, all sourced from USDA FoodData Central,
present only when USDA actually has a value.
