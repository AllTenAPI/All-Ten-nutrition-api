"""Serial vs parallel USDA enrichment, measured against a stubbed lookup.

Run it: ``python3 bench_usda_parallel.py``

**Makes no network call and needs no credential.** The point is to measure the
shape of the change, not USDA's weather: every lookup is a ``time.sleep`` of a
fixed duration, so the numbers are reproducible and attributable. Real USDA
round-trips from Render were the ~0.5 s that ``LOOKUP_SECONDS`` models.

The serial figure is produced by re-running the *pre-parallel* algorithm, not
by an estimate, so the comparison is like for like.
"""

from __future__ import annotations

import time

import nutrition_analyzer as na

# One stubbed USDA round-trip. Roughly what a warm Render dyno saw.
LOOKUP_SECONDS = 0.5

MEALS = (1, 2, 4, 6, 12)


def _stub_lookup(query):
    time.sleep(LOOKUP_SECONDS)
    return {
        "per_100g": {"calories": 165.0, "protein": 31.0, "carbs": 0.0, "fat": 3.6},
        "micronutrients_per_100g": {"iron": 1.0},
        "description": f"usda {query}",
        "fdc_id": abs(hash(query)) % 1_000_000,
    }


def _meal(count):
    # Distinct names on purpose: repeated foods would be answered by the cache
    # and flatter the parallel number for the wrong reason.
    return [
        {
            "name": f"food {i}",
            "estimated_portion_grams": 150,
            "confidence": 0.9,
            "estimated_calories_per_100g": 120,
        }
        for i in range(count)
    ]


def _serial_enrich(detected_foods, lookup):
    """The one-at-a-time loop this change replaced."""
    foods, warnings, available = [], [], True
    for raw in detected_foods:
        name = str(raw.get("name") or "").strip()
        grams, _ = na.clamp_portion(raw.get("estimated_portion_grams"))
        query = str(raw.get("usda_query") or name).strip() or name
        if available:
            try:
                record = lookup(query)
                if record is None and query.lower() != name.lower():
                    record = lookup(name)
            except Exception as exc:
                available = False
                warnings.append(str(exc))
                record = None
        else:
            record = None
        foods.append(record)
    return foods, available, warnings


def _time(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def main() -> None:
    print(
        f"Stubbed USDA lookup: {LOOKUP_SECONDS:.2f}s each. "
        f"Pool bound: {na.USDA_LOOKUP_WORKERS} workers.\n"
    )
    header = f"{'foods':>6}  {'serial':>9}  {'parallel':>9}  {'speedup':>8}"
    print(header)
    print("-" * len(header))

    for count in MEALS:
        meal = _meal(count)
        serial = _time(lambda: _serial_enrich(meal, _stub_lookup))
        parallel = _time(lambda: na.enrich_with_usda(meal, lookup=_stub_lookup))
        print(
            f"{count:>6}  {serial:>8.2f}s  {parallel:>8.2f}s  "
            f"{serial / parallel:>7.2f}x"
        )

    print(
        f"\nParallel time is ceil(foods / {na.USDA_LOOKUP_WORKERS}) round-trips: "
        "the bound is what stops a twelve-item buffet photo from opening twelve "
        "sockets, and it costs three round-trips instead of one to hold that line."
    )


if __name__ == "__main__":
    main()
