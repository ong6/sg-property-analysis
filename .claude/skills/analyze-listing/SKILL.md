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
(see `docs/evaluation-rubric.md` → Purpose check). For own-stay weight the
0–100 livability score (`scoring/livability.py` — heuristic, never folded
into MMR) alongside the rubric's own-stay lens.

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

   **If the listing is already in the DB** — always true when the daily scan
   sent you here, and worth checking (`--search-db`) otherwise — gather from the
   DB instead and skip the scrape entirely:
   ```bash
   python invest.py --from-db <listing_id>
   ```
   Same run dir and `raw_analysis.json`, no network, and the MMR matches the
   score the listing already carries. Prefer it in unattended runs: PropertyGuru
   is behind Cloudflare and a background scrape will usually just fail.

**Forward-signal priors**: see `docs/evaluation-rubric.md` ("What the data
actually predicts") — value + region lead; trailing CAGR/momentum ≈ 0 forward
power; model explains <10% of forward variance, so calibrate confidence.

3. **Research & evaluate** (your view first, algo_reference second):
   - Web search the project: reviews, build quality, developer reputation
   - Recent transactions: is THIS unit's PSF fair vs the development?
     `factual_data.psf_premium_vs_ura_median_pct` is signed — premium or discount
   - This unit's floor/facing/stack vs the development's best. Read
     `factual_data.stack_profile` (the matched stack/layout, facings, known
     issues). **If it shows `status: not_researched`, run `/research-development
     "<condo>"` first** (one condo, cheap) — then the join has real stack data.
     Weight low-confidence matches lightly.
   - Trust rules: if factual_data shows `ask_above_own_stack_prints`,
     `ask_below_stack_prints`, or `stack_low_floor_share` ≥ 0.7, the unit's
     apparent discount is a floor/PES/loft artifact — treat the stack's own
     recent (24mo) prints as the true comp and verify before crediting any
     deep (>25%) discount or >5.5% gross yield.
   - Rubric: `docs/evaluation-rubric.md`

4. **Fill `agent_evaluation`** — `rating`, `confidence`, `rating_rationale` are
   mandatory; one-line verdict in `report_level.agent_executive_summary`.

5. **Report** (auto-saves evaluation memory; commit `evaluations/`):
   ```bash
   python invest.py --from-review output/run_NNN/raw_analysis.json --output output/run_NNN/final
   ```
