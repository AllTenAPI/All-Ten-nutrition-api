# All Ten Nutrition API — Response Contract

`analysis_version: 2.0.0-claude-usda`

This is the contract the Flutter client is built against. Both deploy
entrypoints (`app-render.py`, `app-railway.py`) serve it from the same
implementation.

## Which endpoint to call

| Endpoint | Input | LLM tokens | Use when |
|---|---|---|---|
| `POST /analyze_food` (`mode: "meal"`) | a meal photo | **yes** — one Claude vision call | Only a model can do this: look at a plate and estimate how much is on it. |
| `POST /analyze_food` (`mode: "nutrition_label"`) | a photo of a nutrition facts panel | **yes** — one Claude vision call | The numbers are printed on the package and the product is too specific for a database to match. |
| `POST /search_food` | a text query | **no** | The user knows what they ate and can type it. |
| `POST /barcode` | a UPC/EAN | **no** | The user is holding the package. |

Prefer `/barcode` over label mode when the package has a scannable barcode:
it costs no tokens and identifies the product exactly. Label mode is for the
case where the barcode is absent, unreadable, or unknown to both databases.

**`/search_food` and `/barcode` cost no LLM tokens. That is their purpose.**
They are database lookups, and they should be the default path in the client —
route to `/analyze_food` only when the user actually wants a photo analysed.
Both echo `llm_used: false` so this is verifiable from the payload rather than
assumed.

All three return the **same `foods[]` entry shape**, so the client has one
food parser and one editor.

---

## `POST /analyze_food`

### Request

```json
{
  "image": "<base64 image, or a data: URL>",
  "media_type": "image/jpeg",
  "mode": "meal"
}
```

| Field | Required | Notes |
|---|---|---|
| `image` | yes | Bare base64 or `data:image/jpeg;base64,…`. Whitespace and newlines are stripped server-side. Also accepted under the legacy key `image_base64`. |
| `media_type` | no | Advisory only. The server sniffs the real type from magic bytes and that wins. Also accepted as `mime_type`. |
| `mode` | no | `"meal"` (default) or `"nutrition_label"`. Case-insensitive. Absent, `null` or blank means `"meal"`. Any other value is a `400 bad_request` — a caller that asked for a label read is never silently given a meal estimate. |

Supported formats: `image/jpeg`, `image/png`, `image/gif`, `image/webp`.
Maximum decoded image size: 5 MB. Maximum request body: 8 MB. All of this is
identical in both modes — the mode is a flag on this endpoint, not a second
endpoint, so a client can ship the flag before the server honours it.

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
| `source` | `"usda"` \| `"estimated"` \| `"openfoodfacts"` \| `"nutrition_label"` | Where the macros came from. `/analyze_food` in meal mode emits the first two; `"openfoodfacts"` comes from `/barcode`; `"nutrition_label"` comes from `/analyze_food` in label mode and means the numbers were read off the package. |
| `usda_description` | string \| null | The matched USDA record, so the user can see what was looked up. `null` when `source` is not `"usda"`. |
| `fdc_id` | integer \| null | USDA FoodData Central id. `null` when estimated, and `null` for an Open Food Facts result — that database has no FDC id and one is not invented. |

`/search_food` and `/barcode` add four keys to the same entry. They are
additive, so a client written against `/analyze_food` ignores them safely:

| Field | Type | Notes |
|---|---|---|
| `brand` | string \| null | Manufacturer or brand. `null` on every generic (non-branded) record. |
| `data_type` | string \| null | `"Foundation"`, `"SR Legacy"`, `"Survey (FNDDS)"`, `"Branded"`, or `"Open Food Facts"`. |
| `gtin_upc` | string \| null | The product's barcode, when the source has one. |
| `serving` | object \| null | `{serving_size, serving_size_unit, household_serving, serving_size_grams}`. `null` when the source declares no serving — never a guessed 100 g. `serving_size_grams` is `null` unless the unit converts cleanly (g/ml), so a "1 cup" serving is displayed, not mis-scaled. |
| `basis` | `"per_100g"` | Present only on lookup endpoints. States that `portion_grams` is the reference basis, not an estimate. |

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
| `mode` | string | Present only when the request sent a `mode`. Always present on a label-mode response, including its failures. A request that sends no `mode` gets a response with no `mode` key — byte-identical to what this endpoint returned before label mode existed. |
| `label` | object | Label mode only. See below. |

---

### Label mode — `mode: "nutrition_label"`

Photographing the back of a box is a **reading** problem, not a recognition
one. Meal mode's whole design — estimate the as-served portion, name the food
so USDA can match it — produces a wrong answer there: there is no portion to
estimate, and USDA returns a *generic* product whose macros are not this
product's. That is how a tester's protein shake came back with macros that did
not match its own printed label.

So label mode reads. It makes **no USDA call**; the panel is the source.

```json
{
  "mode": "nutrition_label",
  "label": {
    "product_name": "Chocolate Protein Bar",
    "brand": "Acme",
    "serving": {
      "serving_size": 60.0,
      "serving_size_unit": "g",
      "household_serving": "1 bar",
      "serving_size_grams": 60.0
    },
    "servings_per_container": 12.0,
    "per_serving": {
      "calories": 220.0, "protein": 20.0, "carbs": 24.0, "fat": 7.0,
      "fiber": null, "sugar": 0.0, "sodium": 190.0
    },
    "micronutrients_per_serving": { "calcium": 500.0 }
  },
  "foods": [
    {
      "name": "Acme Chocolate Protein Bar",
      "portion_grams": 100.0,
      "confidence": 1.0,
      "macros": {
        "calories": 367, "protein": 33.3, "carbs": 40.0, "fat": 11.7,
        "fiber": null, "sugar": 0.0, "sodium": 317
      },
      "micronutrients": { "calcium": 833.33 },
      "source": "nutrition_label",
      "usda_description": null,
      "fdc_id": null,
      "brand": "Acme",
      "data_type": "Nutrition label",
      "gtin_upc": null,
      "serving": { "…": "as above" },
      "basis": "per_100g"
    }
  ],
  "totals": { "…": "the sum over foods[], i.e. the per-100 g basis" },
  "needs_confirmation": false,
  "confirmation_reason": null,
  "confidence": 0.93,
  "model": "claude-sonnet-5",
  "analysis_version": "2.0.0-claude-usda",
  "usda_available": true,
  "notes": "",
  "warnings": [],
  "analyzed_at": 1787873781.93
}
```

Every key meal mode returns is still here and still means the same thing, so
the client keeps one parser. `usda_available` reports whether the service is
configured, not whether it was called — label mode never calls it.

#### `label` — the panel as printed

| Field | Type | Notes |
|---|---|---|
| `product_name` | string \| null | As printed. |
| `brand` | string \| null | As printed. |
| `serving` | object \| null | Same four keys `/search_food` and `/barcode` use. `null` when the panel declares no serving at all — never a guessed 100 g. |
| `servings_per_container` | number \| null | `null` when the panel does not say. **Not defaulted to 1** — that would silently halve a two-serving container. |
| `per_serving` | object | The seven macros **per serving, exactly as printed**. Not converted, not multiplied by the servings per container. |
| `micronutrients_per_serving` | object | Sparse: only rows printed as an amount. A `%DV` with no amount is not converted into one. |

`serving.serving_size_grams` is `null` unless the printed unit converts
cleanly — the same `g`/`ml` rule `/search_food` already applies — so "1 scoop"
and "1 bar" are displayed, never turned into a weight.

#### Per serving → per 100 g

**This is the conversion the whole mode turns on.** A label states everything
per serving; every `foods[]` entry in this API is per 100 g, with
`portion_grams` as the basis the client rescales from. Passing a 60 g bar's
printed 20 g of protein straight through as a per-100 g figure understates it
by 40%:

```
per_100g = per_serving × 100 / serving_size_grams
20 g protein in a 60 g bar → 20 × 100 / 60 = 33.3 g per 100 g
```

`foods[0]` carries the converted figures; `label.per_serving` carries the
printed ones. Both are returned so the client can show the panel and log the
portion without doing arithmetic on the wrong basis.

**Without `serving.serving_size_grams` there is no conversion, and none is
invented.** The response then has `foods: []`, the `label` block still
populated, and `needs_confirmation: true` explaining that the panel gave no
serving weight — the user should re-shoot including the serving-size line.

#### `null` versus `0`

A row the panel does not print is `null` in both `label.per_serving` and
`foods[0].macros`. A row printed as `0 g` is `0.0`. These never collapse into
each other: reporting `0` for a nutrient the package never claimed is a
fabricated number, and it is the one that would be hardest for a user to
notice.

#### When label mode asks for confirmation

`needs_confirmation` is `true` when:

- No panel was found, or it was too blurry, cropped, angled or dark to read —
  `confirmation_reason` carries the model's own explanation.
- The panel was read but printed no serving weight in grams (see above).
- `confidence` in the transcription is below `MIN_CONFIDENCE` (default 0.5).
- No calorie figure was printed — the same rule `/barcode` applies to a
  product with no nutrition data on file.

An unreadable panel **never** produces a guessed number. It produces this, or
an `error.kind` of `refusal`.

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
| `bad_request` | 400 | false | Missing, malformed, oversized, or unsupported image; or an unrecognised `mode`. |
| `refusal` | 200 | false | The model declined this image. Ask for a different photo. |
| `rate_limit` | 200 | true | Rate limited upstream. Back off and retry. |
| `upstream_error` | 200 | true | Claude 5xx. |
| `connection` | 200 | true | Network failure reaching Claude. |
| `parse_error` | 200 | true | Malformed model output. Retry is reasonable. |
| `auth` | 500 | false | Bad credentials. Operator problem, not a user problem. |
| `misconfigured` | 500 | false | SDK or key missing on the server. Operator problem. |
| `internal_error` | 500 | true | Unexpected. |

A label-mode failure carries `mode: "nutrition_label"` alongside the `error`
object. Without it a refusal to read a panel would be indistinguishable from a
server that has never heard of label mode, and the user would be told the
feature is unavailable instead of being told what was wrong with the photo.
The one exception is an unrecognised `mode` itself, which has no valid mode to
echo.

Client rule: **retry only when `retryable` is `true`**, with backoff. On a
non-retryable failure, show `confirmation_reason` and offer manual entry.

---

## `POST /search_food`

Free-text food lookup against USDA FoodData Central. **No LLM call is made on
any path** — this endpoint costs no Anthropic tokens.

### Request

```json
{ "query": "quest protein bar", "page": 1, "limit": 20 }
```

| Field | Required | Notes |
|---|---|---|
| `query` | yes | The search text, 1–200 characters. Also accepted as `q` or `text`. |
| `page` | no | 1-based. Defaults to `1`. Out-of-range or unparseable values fall back to the default rather than erroring. |
| `limit` | no | Results per page. Defaults to `20`, capped at `50`. |

### Response

```json
{
  "query": "quest protein bar",
  "foods": [
    {
      "name": "Chicken, broilers or fryers, breast, meat only, cooked",
      "portion_grams": 100.0,
      "confidence": 1.0,
      "macros": { "calories": 165, "protein": 31.0, "carbs": 0.0, "fat": 3.6,
                  "fiber": null, "sugar": null, "sodium": 74 },
      "micronutrients": { "iron": 1.04, "potassium": 256.0 },
      "source": "usda",
      "usda_description": "Chicken, broilers or fryers, breast, meat only, cooked",
      "fdc_id": 171077,
      "brand": null,
      "data_type": "SR Legacy",
      "gtin_upc": null,
      "serving": null,
      "basis": "per_100g"
    }
  ],
  "page": 1,
  "limit": 20,
  "total_hits": 137,
  "has_more": true,
  "needs_confirmation": true,
  "confirmation_reason": "These are search candidates, not a measurement of your meal. Pick the one that matches and set the portion before logging.",
  "usda_available": true,
  "llm_used": false,
  "analysis_version": "2.0.0-claude-usda",
  "warnings": [],
  "analyzed_at": 1787873781.93
}
```

There is **no `totals` object.** Search results are unrelated candidates;
summing them would be meaningless. Totals exist only for `/analyze_food`,
which describes one actual meal.

#### Portion, macros and confidence

`portion_grams` is the reference **100 g**, not an estimate, so `macros` are
the per-100 g values. The client rescales exactly as it already does for
`/analyze_food`: `macro × new_grams / portion_grams`. Where the source
declares a serving, `serving.serving_size_grams` is the natural first choice
to offer the user ("1 bar, 60 g").

`confidence` is `1.0` on every entry, and that is a statement about the
*data*, not the *match*: the portion is exactly the reference basis and the
macros come from a database rather than a model. Whether this is the right
food is the user's call — which is what `needs_confirmation: true` is for. It
is always `true` on this endpoint.

**`null` still means unknown, never zero.** USDA has no fibre figure for many
SR Legacy records; those come back `null`.

#### Result ordering

Candidates are ranked by data type, and USDA's own relevance order is
preserved inside each tier:

1. `Foundation` — lab-measured single ingredients
2. `SR Legacy` — legacy lab-measured reference data
3. `Survey (FNDDS)` — prepared and composite dishes
4. `Branded` — manufacturer-declared label data

**`Branded` is always last.** It is what makes a named packaged product
findable at all, but it is self-reported and enormous (~2M records), so a
generic query like "grilled chicken" must not resolve to whichever packaged
chicken product happens to rank well. Each page reserves slots for both the
generic tiers and Branded, so neither can crowd the other out entirely; an
unused reservation spills over so a page is never left short.

### Failures

Same `error: {kind, message, retryable}` envelope as `/analyze_food`, with
`foods: []`.

| `error.kind` | HTTP | `retryable` | Meaning |
|---|---|---|---|
| `bad_request` | 400 | false | `query` missing, empty, not a string, or over 200 characters. |
| `not_found` | 404 | false | USDA matched nothing. Offer manual entry or a different search. |
| `upstream_error` | 200 | true | USDA unreachable, rate limited, or 5xx. |
| `misconfigured` | 500 | false | `USDA_FDC_API_KEY` is not set. Operator problem, not a user problem. |
| `internal_error` | 500 | true | Unexpected. |

---

## `POST /barcode`

UPC/EAN/GTIN lookup. **No LLM call is made on any path** — this endpoint costs
no Anthropic tokens.

### Request

```json
{ "barcode": "0888849000371" }
```

| Field | Required | Notes |
|---|---|---|
| `barcode` | yes | 8–14 digits (EAN-8, UPC-E, UPC-A, EAN-13, GTIN-14). Spaces, hyphens and newlines are stripped. An integer is accepted. Also accepted as `code`, `upc` or `ean`. |

### Sources

Tried in order, and the one that answered is named in `source`:

1. **`"usda"`** — USDA Branded, matched on `gtinUpc`. The key is already
   provisioned, and the data is label-declared. A hit is accepted only on an
   **exact** GTIN match (leading zeros ignored, so a 12-digit UPC and its
   13-digit EAN spelling compare equal). USDA's search will return
   loosely-related products for a numeric query; returning the wrong
   product's macros would be worse than returning nothing.
2. **`"openfoodfacts"`** — Open Food Facts, `api/v2/product/<barcode>.json`.
   Free, no key, barcode-indexed, and far better coverage outside the US. A
   descriptive `User-Agent` identifying the app is sent on every request, per
   their published API etiquette.

Open Food Facts stores nutriments in grams; the server converts to the
contract's units (minerals to mg, vitamins A/D/B12/folate to mcg, kJ to kcal,
and salt to sodium at the 2.5 divisor where sodium is not declared directly).

### Response

```json
{
  "barcode": "0888849000371",
  "found": true,
  "source": "usda",
  "foods": [ { "…": "one entry, same shape as above" } ],
  "needs_confirmation": false,
  "confirmation_reason": null,
  "llm_used": false,
  "analysis_version": "2.0.0-claude-usda",
  "warnings": [],
  "analyzed_at": 1787873781.93
}
```

`foods` holds exactly one entry, on the same per-100 g basis as
`/search_food`. `needs_confirmation` is `false` — a barcode identifies the
product exactly — **except** when the source has no calorie figure on file,
in which case it is `true` and `confirmation_reason` asks the user to enter
the label values. Crowd-sourced Open Food Facts entries are frequently
partial; those gaps arrive as `null`, never as `0`.

### Not found

```json
{
  "barcode": "0000000000000",
  "found": false,
  "source": null,
  "foods": [],
  "needs_confirmation": true,
  "confirmation_reason": "No product matched this barcode in USDA or Open Food Facts. Add the item manually, or use the label.",
  "error": { "kind": "not_found", "message": "…", "retryable": false }
}
```

HTTP 404. **Macros are never synthesised for an unknown product.**

If a source *errored* rather than missed, the response is
`upstream_error` (HTTP 200, `retryable: true`) with `found: false` instead —
"not found" is not a claim the server is entitled to make on the back of a
request that never completed. The individual source failures appear in
`warnings`.

### Failures

| `error.kind` | HTTP | `retryable` | Meaning |
|---|---|---|---|
| `bad_request` | 400 | false | `barcode` missing, non-numeric, or not 8–14 digits. |
| `not_found` | 404 | false | Both sources answered, neither knows this barcode. |
| `upstream_error` | 200 | true | A source could not be reached, so the lookup is inconclusive. Retry. |

### Caching

Both clients cache in-process by barcode, **including misses**, so repeat
scans of the same product cost no network call. USDA caches on the
leading-zero-stripped GTIN, so the UPC and EAN spellings of one product share
an entry. Counters are reported by `/debug` as `usda_cache` and
`openfoodfacts_cache`.

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
the effective thresholds, USDA and Open Food Facts cache statistics, and — for
each environment variable — the literal string `"set"` or `"not set"`.

**It never returns a credential value, length, prefix, or preview.** The
previous `/debug` returned the first 100 characters of the Google credentials
JSON; that is why this one reports presence only.

## `GET /`

Service banner: name, platform, analysis description, and the endpoint list
(`/`, `/health`, `/debug`, `/analyze_food`, `/search_food`, `/barcode`).

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
