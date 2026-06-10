---
name: analyze-development
description: Flow A — evaluate a whole condo development by name or project URL. Produces a development-level verdict plus best/worst stacks, facings and floors, with a Buy/Neutral/Avoid rating per unit type. Use when the user names a condo ("analyze The Continuum", "is Grand Dunman good?") or gives a PropertyGuru project page.
---

# Analyze a Development (Flow A)

**Altitude**: the question is whether the DEVELOPMENT is worth buying into, and
which stacks/facings/floors to target or avoid. Rate each unit type too.

**Purpose check first**: metrics assume investment (5–7yr hold). If the request
hints at own-stay or is ambiguous, ask the user before rating
(see `docs/evaluation-rubric.md` → Purpose check).

## Steps

1. **Recall** — past evaluations are references, not truth; re-verify prices:
   ```bash
   python invest.py --recall "<condo name>"
   python invest.py --search-db "<condo name>"
   python invest.py --profile "<condo name>"   # existing stack/facing/layout profile
   ```

2. **Gather**:
   ```bash
   python invest.py --condo "<name>" --beds 1,2,3            # by name
   python invest.py --url "<propertyguru project URL>"        # or by project page
   ```
   Creates `output/run_NNN/raw_analysis.json` (one entry per unit type).

**Forward-signal priors (v3.4 — backtested; full list in the rubric).** Lead with
VALUE, not trailing appreciation. Strongest robust signals: **cheap-vs-district-peers**
(`relative_value.premium_vs_age_adjusted_median_pct`, `psf_premium_vs_ura_median_pct`)
and **region** (realized fwd OCR≈+4.0% ≥ RCR≈+3.5% ≫ CCR≈+0.7% — the old "CCR leads"
prior was inverted). Trailing `appreciation.annual_rate_pct`/`momentum` ≈0 forward power
(story/catalyst only); freehold is **not** a forward edge; absolute low PSF is mostly the
region effect. Model explains <10% of forward variance → small `score_1000` gaps are
noise; reserve **high** confidence for decisive physical/catalyst evidence.

3. **Research & evaluate** (form your own view BEFORE looking at algo_reference):
   - Web search: `"<name> Singapore review"` (build quality, developer, defects),
     `"<name> price trend transactions"` (resale PSF direction),
     `"<name> floor plan site plan facing"` (stack/facing/floor quality)
   - Read `factual_data` per unit type; rubric: `docs/evaluation-rubric.md`
   - Optional sanity-check: `python invest.py --score '{...}'` — remember it
     takes your inputs at face value; it is not independent confirmation.

4. **Persist the physical findings to a profile** — this flow already researches
   the site plan/stacks/facings, so don't let it evaporate into freeform notes:
   run **`/research-development "<name>"`** (or build the profile JSON and
   `python invest.py --save-profile <file>`) so every future listing in this
   condo auto-joins to real stack data. Cite sources; set `confidence` honestly;
   never invent a stack. This is the structured home for what used to go only in
   `stack_notes`.

5. **Fill `agent_evaluation`** for each unit type in `raw_analysis.json`. Put
   stack/facing/floor guidance in `stack_notes`; overall development verdict in
   `report_level.agent_executive_summary`. Always set `rating`, `confidence`,
   `rating_rationale`.

6. **Report** (auto-saves evaluation memory; commit `evaluations/` and `profiles/`):
   ```bash
   python invest.py --from-review output/run_NNN/raw_analysis.json --output output/run_NNN/final
   ```
