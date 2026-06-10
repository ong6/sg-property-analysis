---
name: research-development
description: Research a development's stable physical facts — stacks, facings, views, layouts, site plan — and save a cited, confidence-scored profile to profiles/ for reuse. Use when a condo has no profile yet (or it's stale), or the user says "research the stacks/layout/facing for <condo>", "build a profile for <condo>". This is the "perfect info" layer that analyze-listing / analyze-development join against.
---

# Research a Development profile (the "perfect info" layer)

**Goal**: produce ONE structured, cited fact-sheet of a development's *physical*
attributes (which don't change with price) so future evaluations join against it
instead of re-deriving. Output is `profiles/<slug>.json`.

**This is facts, not judgement.** No Buy/Avoid here — that's `evaluations/`.
The cardinal rule: **never invent a stack or a facing.** An absent stack means
"not yet researched". Every claim needs a `source`; set `confidence` honestly.

## Steps

1. **Check what exists** — don't redo good work:
   ```bash
   python invest.py --profile "<condo>"        # existing profile (if any)
   ```
   If a fresh, high-confidence profile exists, stop (or only fill gaps).

2. **Anchor on hard data first** — the URA sqft clusters reveal the real layouts
   for free, and they're authoritative:
   ```bash
   python invest.py --search-db "<condo>"      # sqft/beds actually on market
   grep -i "<CONDO>" data/ura_district_D*.csv   # every transacted sqft (layout clusters)
   ```
   Group the distinct sqft values into layouts (1BR/2BR/3BR/4BR/PH).

3. **Web-research the rest** — fan out, prefer primary/visual sources:
   - `"<condo> site plan"` and `"<condo> floor plan"` → stack count, blocks,
     storeys, units-per-floor, which stack faces which way / which view.
   - `"<condo> review"` (StackedHomes, EdgeProp, forums) → orientation, noise,
     afternoon-sun (west) stacks, pool vs road facing, known defects.
   - Developer brochure / e-brochure → official stack & facing diagram.
   Screen sources: a marketing page overstates; a tour/forum is franker. If two
   sources conflict, record the conflict and lower `confidence`.

4. **Fill the schema** (`profile_memory.PROFILE_TEMPLATE` — see its docstring):
   - `development`: total_units, blocks, max_floor, tenure, top_year, developer,
     `orientation_notes`, `known_issues[]`.
   - `stacks[]`: ONLY stacks you can actually source — `{stack, beds, sqft,
     facing, view, desirability: high|mid|low, flags: [west_facing|road_noise|
     pool_view|unblocked|...], notes}`. Leave `stacks: []` if the site plan
     didn't yield per-stack facing — that's honest and the join falls back to
     layouts.
   - `layouts[]`: from step 2 — `{type, sqft: [...], efficiency, notes}`.
   - `sources[]`: `{url, type: site_plan|brochure|stackedhomes|forum|listing, note}`.
   - `confidence`: high = site plan + per-stack facing verified; medium = solid
     development + layout data, stacks partial; low = sparse/conflicting.

5. **Save**:
   ```bash
   python invest.py --save-profile <your-profile>.json
   python invest.py --profile "<condo>"        # verify it reads back
   ```
   Commit `profiles/` after researching (git-tracked, like `evaluations/`).

## How it gets used
analyze-listing / market-scan auto-match each listing to a stack/layout by sqft
(+ beds, + facing/floor when enriched) and surface it as
`factual_data.stack_profile` — so "this is the west-facing road-noise stack"
becomes a fact the evaluator sees. Low-confidence matches are informative, not
authoritative; weight them by the `confidence` you set here.
