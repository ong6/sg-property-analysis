---
name: analyze-listing
description: Flow B — evaluate one specific PropertyGuru listing and give a clear Buy/Neutral/Avoid call on that exact unit with confidence. Use when the user pastes a single listing URL ("analyze this: <listing link>").
---

# Analyze a Single Listing (Flow B)

**Altitude**: should the user buy THIS unit, at THIS price? The rating is the
answer — grounded in this unit's price/PSF vs the development, floor, facing,
and the development's fundamentals.

**Purpose check first**: metrics assume investment (5–7yr hold). If the request
hints at own-stay or is ambiguous, ask the user before rating
(see `docs/evaluation-rubric.md` → Purpose check).

## Steps

1. **Recall** — references, not truth:
   ```bash
   python invest.py --recall "<condo name>"
   python invest.py --search-db "<condo name>"
   python invest.py --profile "<condo name>"   # researched stacks/facings/layouts
   ```

2. **Gather**:
   ```bash
   python invest.py --url "https://www.propertyguru.com.sg/listing/..."
   ```
   Creates `output/run_NNN/raw_analysis.json` with one entry.

**Forward-signal priors (v3.4 — backtested; full list in the rubric).** Lead with
VALUE, not trailing appreciation. Strongest robust signals: **cheap-vs-district-peers**
(`relative_value.premium_vs_age_adjusted_median_pct`, `psf_premium_vs_ura_median_pct`
+ `psf_cohort_txns` for trust) and **region** (realized fwd OCR≈+4.0% ≥ RCR≈+3.5% ≫
CCR≈+0.7% — the old "CCR leads" prior was inverted). Trailing
`appreciation.annual_rate_pct`/`momentum` ≈0 forward power (use for the story/catalyst,
not the number); freehold is **not** a forward edge; absolute low PSF is mostly the
region effect — don't double-count it. Model explains <10% of forward variance → small
`score_1000` gaps are noise; reserve **high** confidence for decisive physical/catalyst
evidence, near-ties → Neutral.

3. **Research & evaluate** (your view first, algo_reference second):
   - Web search the project: reviews, build quality, developer reputation
   - Recent transactions: is THIS unit's PSF fair vs the development?
     `factual_data.psf_premium_vs_ura_median_pct` is signed — premium or discount
   - This unit's floor/facing/stack vs the development's best. Read
     `factual_data.stack_profile` (the matched stack/layout, facings, known
     issues). **If it shows `status: not_researched`, run `/research-development
     "<condo>"` first** (one condo, cheap) — then the join has real stack data.
     Weight low-confidence matches lightly.
   - Rubric: `docs/evaluation-rubric.md`

4. **Fill `agent_evaluation`** — `rating`, `confidence`, `rating_rationale` are
   mandatory; one-line verdict in `report_level.agent_executive_summary`.

5. **Report** (auto-saves evaluation memory; commit `evaluations/`):
   ```bash
   python invest.py --from-review output/run_NNN/raw_analysis.json --output output/run_NNN/final
   ```
