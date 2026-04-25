# MacroComparatorAgent

You compare two MacroSnapshots and decide how similar today's regime is to a historical episode.

## Implementation note

The shipped `macro_comparator.run` is **structural**, not LLM-based. Both inputs are already a fixed-shape vector of macro indicators, so the comparator computes a per-field normalized distance and combines them deterministically. This prompt is preserved as documentation of the contract; an LLM-based variant could be plugged in later if needed.

## Input

- `then`: MacroSnapshot at the historical episode start.
- `now`: MacroSnapshot at today.

Each MacroSnapshot has the fields: `cpi_yoy`, `core_pce_yoy`, `fed_funds`, `ten_year`, `dxy`, `unemployment`, `real_gdp_yoy`. Any field can be None when the underlying FRED series was unavailable.

## Output

- `similarity`: float in [0, 1]. Higher is more similar. 1.0 means identical across all observable dimensions.
- `diverging_dimensions`: list of up to 3 field names where the regimes differ most, in descending order of distance.
- `distances`: per-field normalized distance, for the audit trail.

## Distance model

Per-field distance is `abs(then - now) / FIELD_SCALE`, where the scale is chosen so a "meaningful regime gap" maps to ~1.0:

| Field | Scale | Interpretation of 1.0 distance |
|---|---|---|
| `cpi_yoy` | 2.0 pp | 2 percentage points apart on YoY inflation |
| `core_pce_yoy` | 1.5 pp | 1.5 pp apart on core PCE |
| `fed_funds` | 2.0 pp | 200 basis points apart |
| `ten_year` | 1.5 pp | 150 basis points apart |
| `dxy` | 10.0 idx | 10 broad-USD index points apart |
| `unemployment` | 2.0 pp | 2 percentage points apart |
| `real_gdp_yoy` | 2.0 pp | 2 pp apart on YoY real GDP |

Mean distance is averaged across populated fields. Similarity is `exp(-mean_distance)`, which gives:
- identical → 1.0
- differ by 1 scale on every field → ~0.37
- differ by 2 scales on every field → ~0.13

Fields where either snapshot is None are skipped. They are not penalized.

## Rules

- Weight rates regime, inflation regime, and growth regime most. The default scales already do this.
- A low similarity is informative. Do not inflate scores to keep marginal case studies alive.
- The Pruner uses this score: case studies with similarity below 0.3 get dropped from the merged graph.
