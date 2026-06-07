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
  directly — use `factual_data.relative_value` (age-adjusted at ~$50/psf/yr,
  region-dependent) to judge whether old vs new is the better value.

Full rubric, rating scale, and field guide: **`docs/evaluation-rubric.md`**.

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

Supporting data backbone (CSV, append-only — grep/analyze freely):
`data/listings_sheet.csv` (latest state + MMR), `data/mmr_history.csv`
(every scoring run), `data/arena_results.csv` (every tournament),
`data/ura_cache.csv` (per-project URA metrics).

## The two memory systems

**`evaluations/` (git-tracked)** — your past judgements, one JSON per condo,
append-only. May be stale or wrong: re-verify price and conditions before
relying on one. Commit this folder after evaluations.
```bash
python invest.py --recall "<condo>"     # prior ratings + staleness warning
python invest.py --list-evals
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
config.py              # MMR calibration, weights, thresholds
eval_memory.py         # Git-tracked evaluation memory
listings_db.py         # Master listings sheet
scoring/
  full_scorer.py       # Enrichment + component scoring
  mmr.py               # MMR (uncapped, continuous) + /1000 normalization
  raw_output.py        # raw_analysis.json (mode-aware AI instructions)
  models.py            # ScoredListing + verdict_from_rating
  roi.py / costs.py / rental_estimator.py / future_scorer.py / district_scorer.py / quick_scorer.py
scrapers/              # PropertyGuru + URA CSV + headless browser
docs/evaluation-rubric.md   # Full evaluation rubric (read in step ③)
.claude/skills/        # analyze-development / analyze-listing / market-scan
evaluations/           # Past evaluations (commit; may be stale)
output/                # Per-run reports (gitignored)
```
