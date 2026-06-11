# Property Finder — Claude Code Playbook

This repo is driven by **Claude Code** (you). Python scripts gather *facts* —
scrape PropertyGuru, enrich with URA transaction data, compute costs, distances
and a technical MMR score. **You do the research, evaluation, and the final
Buy / Neutral / Avoid call.** The score is one input, never the answer.

## Before any flow: purpose check

All computed metrics (yield, ROI, liquidity, MMR) assume an **investment**
purpose (5–7yr hold). If the user's request suggests **own-stay** — or is
ambiguous between the two — **ask the user which it is before rating.**
Own-stay shifts the rubric: livability, layout, facing, noise, schools and
commute outweigh yield and exit liquidity; "oversized unit" flips from flag to
feature. Full comparison: `docs/evaluation-rubric.md`.

## Pick the flow (each has a skill with the full runbook)

| User says… | Flow / skill | Output |
|---|---|---|
| "analyze \<condo name\>" or project URL | `/analyze-development` | Development verdict + best stacks/facings/floors; rating per unit type |
| "analyze this: \<listing URL\>" | `/analyze-listing` | Buy/Neutral/Avoid on that exact unit, with confidence |
| "find condos in \<area\>" or results URL | `/market-scan` | Rated shortlist, strongest first ("no buys" is a valid outcome) |
| "research stacks/layout/facing for \<condo\>" (or a condo has no profile) | `/research-development` | Cited `profiles/<slug>.json` of physical facts (stacks/facings/layouts) |

Every flow: **① Recall → ② Gather → ③ Research & evaluate → ④ Fill
`agent_evaluation` → ⑤ `--from-review` report (auto-saves to memory).**
Requests that fit none of these (e.g. "compare these two condos", "should I
sell?") still use the same tools — pick the closest altitude, state the rubric
used, and ask if intent is unclear.

## Evaluation principles (anti-bias)

- **Your view first.** Form it from `factual_data` + web research; consult
  `algo_reference`, MMR and past evaluations to pressure-test, not to start from.
- **Research broadly.** The algo's ranking is an input, not a shortlist — in
  scans, cover lower-ranked listings too.
- **Honest verdicts.** "No buys", Avoid, and low confidence are first-class
  outcomes. Never manufacture a winner. Always fill `rating`, `confidence` and
  `rating_rationale` (the rationale renders under the verdict).
- **Override with care.** The system de-biases new-launch appreciation; don't
  override the adjusted rate with a portal headline CAGR — resale-to-resale
  evidence only.
- **`--score` circularity.** If you feed it your own rate/rent, the result
  validates internal consistency, not your inputs.
- **Value is age-relative.** Don't compare a resale's PSF to a new launch's
  directly — use `factual_data.relative_value` (age-adjusted at ~1.6%/yr ≈
  $23–34/psf/yr by region, v3.4-recalibrated from the hedonic) to judge whether
  old vs new is the better value.
- **Value is size-relative (v3.2).** A small unit trades at a structurally
  higher PSF than a large one in the same project — never judge a 1BR's PSF
  against the project-pooled median (which mixes in penthouses). MMR now
  compares against the same-size band and damps appreciation/liquidity for a
  unit whose own size-cohort is thin (`psf_cohort_txns`). When you see a small
  high-PSF unit ranking well, check its cohort depth before trusting it.
- **Value & region lead; appreciation is de-emphasized (v3.4).** Point-in-time
  URA backtests (`backtest.py` / `backtest_ext.py`) show trailing appreciation has
  ~0 forward power and momentum is flat-to-contrarian; the strongest *robust*
  forward signals are **cheap-vs-district-peers** and **region**. v3.4 changes:
  momentum slope → 0; relative-value cap 20→28 (un-throttled); **regional baselines
  DE-INVERTED** (realized fwd OCR +4.0 ≥ RCR +3.5 ≫ CCR +0.7 — the old CCR>OCR table
  was backwards); **freehold is not a forward edge** (lease bonus 4.0→1.0); age slope
  halved to ~1.6%/yr; `price_band` reshaped to exit-liquidity-only (no quantum
  reward); future score made upside-only (stops penalizing data-poor listings).
  **Absolute cheapness is mostly the region effect** — don't double-count it. The
  full model explains **<10% of forward variance** — calibrate confidence; small
  `score_1000` gaps are noise. Re-run `backtest_ext.py` as the panel grows.
- **v3.5 (backtest PART 5, then re-validated on the FULL 28-district panel —
  106k txns, CCR n=270):** composite forward-ρ **+0.288** on the expanded panel.
  Realized forward **OCR +3.7% > RCR +3.1% ≫ CCR +1.9%** (de-inversion confirmed
  at scale; compressed baselines 3.0/3.7/4.0 well calibrated). **Freehold's
  apparent forward underperformance was a 13-district artifact** — on the full
  panel it is exactly neutral (marginal ≈0); it does carry a ~5% PSF *level*
  premium (in the price, not a return edge). The `future` infra score has **no
  measured forward power** (marginal ≈0/negative — "transformation upside" is
  narrative, not edge), while **`buyer_pool` strengthened** (std_β +0.16, real
  content beyond region). Liquidity's univariate signal is value/region in
  disguise (txn-volume weight 10→6, exit-risk only); appreciation slope 4→3.
  **Floor factors de-attenuated** (~1.4%→~3%/tier, within-project FE): don't
  hand-adjust for floor on top of `psf_value` — it's now properly normalized.
  `yield` and `dev_size` remain unvalidated (rental fetch in progress / no
  total_units data). Shipped-score calibration harness: `calibrate_forward.py`
  (joins `mmr_history.csv` to later URA PSF; meaningful from ~mid-2027).
- **v3.6 discount trust (agent-vs-score divergence audit, Jun 2026):** the top
  of the /1000 ranking was dominated by *fake* deep discounts — mis-scraped
  sqft/beds (700–1086 sqft "1BRs"), strata villas/terraces benchmarked against
  apartment medians, stale/bait prices ~30% below trailing prints. The fake
  cheapness earned up to +28 (`age_value`) plus an **uncapped** `psf_value`
  against only −3/−6 in red flags, so every audited top-15 artifact the agent
  had rated Avoid/Neutral ranked #1–15. Fix (`config.py` / `scoring/mmr.py`):
  discounts deeper than **25% vs verified comps** earn marginal credit at 25%
  (knee); `psf_value` now tanh-caps like `age_value`; and when
  `bedroom_sqft_mismatch` fires or a deep discount rests on a thin same-size
  cohort, the positive side of both value components retains only 25%
  (premiums stay fully penalized). Backtest unchanged (ρ +0.288 — URA panel
  data is clean; the rule only disarms scraped-listing artifacts). **A still-
  extreme discount that survives the damping is a verify-first signal, not
  value** — confirm sqft/format/price against URA prints before crediting it.

Full rubric, rating scale, and field guide: **`docs/evaluation-rubric.md`**.
Full audit + change rationale: **`docs/IMPROVEMENT_PLAN.md`**.

## Technical score (MMR)

Elo-style, base 1500, uncapped, continuous; displayed as `score_1000`
(0–1000; 500 = market-typical, 650+ recommended tier, <450 below threshold).
Missing data is neutral, never penalized. Sanity-check tool:

```bash
python invest.py --score '{"price":2300000,"sqft":958,"psf":2401,"beds":2,"district":"D15","tenure":"Freehold","project_name":"...","appreciation_rate_pct":5.5,"monthly_rent":6200}'
```

## Condo arena (`--fight`) — AI referee required

`python invest.py --fight` runs a pairwise tournament over the listings DB —
contenders are (condo × unit type), purely algorithmic (no AI in the fights).
**Every run requires you to referee before results are trusted**: read
`output/arena_referee_packet.json`, work through its `auto_flags` (thin
transactions behind high appreciation, fallback-rent yield edges, extreme
psf/age components, suspect sqft), verify the top contenders' stats are real
(web-check if extreme), then append a **Referee verdict** to
`output/arena_latest.md`: confirmed ranks, demoted contenders + reasons, and
run confidence. The arena ranks stats; you certify the stats deserve to rank.

Each run also writes a timestamped archive (`output/arena_<ts>.md`) since
`arena_latest.md` is overwritten per run. **Local rankings UI**:
`python ui.py` → http://127.0.0.1:8642 — sortable/filterable table with
one-click PropertyGuru listing + Google Maps links per contender.
**System dashboard**: `python dashboard.py` → http://127.0.0.1:8643 — the
whole engine state on one page (forward signals, regime analysis, score
distribution, coverage, district benchmarks, top projects) with per-condo
ANALYZE buttons that run `claude -p "analyze <condo>"` headless in the
background (transcript → `output/analyze_runs/`; the verdict auto-saves to
eval memory and surfaces as a VIEW link on the next page refresh).

Supporting data backbone (CSV, append-only — grep/analyze freely):
`data/listings_sheet.csv` (latest state + MMR), `data/mmr_history.csv`
(every scoring run), `data/arena_results.csv` (every tournament),
`data/ura_cache.csv` (per-project URA metrics).

## The memory systems

**`evaluations/` (git-tracked)** — your past judgements, one JSON per condo,
append-only. May be stale or wrong: re-verify price and conditions before
relying on one. Commit this folder after evaluations.
```bash
python invest.py --recall "<condo>"     # prior ratings + staleness warning
python invest.py --list-evals
```

**`profiles/` (git-tracked)** — a development's *physical facts* (stacks,
facings, views, layouts, site plan) — researched once, reused. Unlike
evaluations these are stable (not price-dependent). Listings auto-join to a
stack/layout at eval time (`factual_data.stack_profile`). Never invent a stack;
absent = not-yet-researched. Populate on-demand via `/research-development`.
```bash
python invest.py --profile "<condo>"    # show stacks/facings/layouts (fuzzy)
python invest.py --list-profiles
```

**`data/listings_db.json` + `listings_sheet.csv`** — every listing ever
scraped, deduplicated, with price history (price drops surface). Auto-upserts
on every run.
```bash
python invest.py --search-db "<name>"   # find listings
python invest.py --list-db              # stats, price drops, by district
python invest.py --update-db --districts 3,5,14,15 --beds 2,3   # refresh only
```

## URA appreciation cache

```bash
python invest.py --build-ura-cache data/ura_*.csv
python invest.py --fetch-ura-districts 3,5,14,15
```

## Project structure

```
invest.py              # CLI: flows, --score, --recall, --search-db, --from-review
backtest.py            # Point-in-time URA backtest of scoring features (re-validate MMR weights)
config.py              # MMR calibration, weights, thresholds
eval_memory.py         # Git-tracked evaluation memory (judgements)
profile_memory.py      # Git-tracked condo profiles (physical facts) + listing→stack join
listings_db.py         # Master listings sheet
scoring/
  full_scorer.py       # Enrichment + component scoring
  mmr.py               # MMR (uncapped, continuous) + /1000 normalization
  raw_output.py        # raw_analysis.json (mode-aware AI instructions)
  models.py            # ScoredListing + verdict_from_rating
  roi.py / costs.py / rental_estimator.py / future_scorer.py / district_scorer.py / quick_scorer.py
scrapers/              # PropertyGuru + URA CSV + headless browser
docs/evaluation-rubric.md   # Full evaluation rubric (read in step ③)
.claude/skills/        # analyze-development / analyze-listing / market-scan / research-development
evaluations/           # Past evaluations — judgements (commit; may be stale)
profiles/              # Condo profiles — physical facts: stacks/facings/layouts (commit)
output/                # Per-run reports (gitignored)
```
