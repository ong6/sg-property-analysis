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
feature, and the 0–100 livability score becomes the primary lens. Full
comparison: `docs/evaluation-rubric.md`.

## Pick the flow (each has a skill with the full runbook)

| User says… | Flow / skill | Output |
|---|---|---|
| "analyze \<condo name\>" or project URL | `/analyze-development` | Development verdict + best stacks/facings/floors; rating per unit type |
| "analyze this: \<listing URL\>" | `/analyze-listing` | Buy/Neutral/Avoid on that exact unit, with confidence |
| "find condos in \<area\>" or results URL | `/market-scan` | Rated shortlist, strongest first ("no buys" is a valid outcome) |
| "research stacks/layout/facing for \<condo\>" (or a condo has no profile) | `/research-development` | Cited `profiles/<slug>.json` of physical facts (stacks/facings/layouts) |

Every flow: **① Recall → ② Gather → ③ Research & evaluate → ④ Fill
`agent_evaluation` → ⑤ `--from-review` report (auto-saves to memory).**
In ② Gather, pull per-project market context from realsmart.sg via the
`/realsmart-data` skill (owner-preferred source: **REALSCORE**, transaction/
profitability stats, holding-period distribution, rental yield) alongside URA
data. The public `/p/<slug>` page carries all of it on a plain fetch — no login.
Record REALSCORE, `realsmart_pct_profitable` and `realsmart_annual_return_pct`
in `agent_evaluation`; read a high score as *downside* evidence (nobody has lost
money here), never as an appreciation forecast, and always report the
transaction count behind it — a 5.0 on six resales is noise.
Requests that fit none of these (e.g. "compare these two condos", "should I
sell?") still use the same tools — pick the closest altitude, state the rubric
used, and ask if intent is unclear.

## Evaluation principles (current rules — history & evidence in docs/RELEASES.md)

- **Your view first.** Form it from `factual_data` + web research; consult
  `algo_reference`, MMR and past evaluations to pressure-test, not to start from.
- **Research broadly.** The algo's ranking is an input, not a shortlist — in
  scans, cover lower-ranked listings too.
- **Honest verdicts.** "No buys", Avoid, and low confidence are first-class
  outcomes. Never manufacture a winner. Always fill `rating`, `confidence` and
  `rating_rationale` (the rationale renders under the verdict).
- **Value & region lead; trailing appreciation is de-weighted.** Backtested on
  the full 28-district URA panel (106k txns; **in-sample, single 2021–26 bull
  regime, 563 effective projects** — see docs/AUDIT_JUN2026.md): the robust
  forward signals are **cheap-vs-district-peers** and **region** (realized fwd
  OCR +3.7 > RCR +3.1 ≫ CCR +1.9%/yr; config baselines 2.8/3.7/4.2). The
  OCR>CCR tilt is long-run but **not a law** — CCR won 20/81 rolling windows
  since 2004, incl. the 2004–08 era outright. Trailing CAGR/momentum: marginal
  undetermined once clustered (univariate +0.13) — story, not signal. The
  future-infra score has no measured power; **freehold is forward-neutral**
  (a ~5% PSF *level* premium, not a return edge); absolute cheapness is mostly
  the region effect — don't double-count. Buyer-pool depth survives clustering
  (std_β +0.16); MRT proximity is real but weaker than first measured
  (as-of-T std_β −0.11 — a third of the old −0.16 was unopened-station
  look-ahead). High gross yield predicts *lower* forward price growth
  (std_β −0.16) — count yield as carry, never as appreciation. **The full
  model explains <10% of forward variance** (composite ρ +0.29, CI +0.21..
  +0.36) — small `score_1000` gaps are noise; calibrate confidence accordingly.
- **Value is age-relative — and the age curve is KINKED.** Don't compare a
  resale's PSF to a new launch's directly — use `factual_data.relative_value`,
  which normalizes peers along the measured piecewise decay (`AGE_PSF_DECAY_
  SEGMENTS`): ~3%/yr (≈$50/psf/yr) ages 0–10, a 10–15yr plateau, ~2.6%/yr at
  15–20, then slow drift. A flat $/yr rule under-adjusts new-vs-old ~2× inside
  0–10yr.
- **Age for *returns* plateaus 7–30yr.** Forward returns: the 0–5yr cohort is the
  *worst* (still amortizing launch premium); 5–30yr all fine; only 30+ slows.
  Don't discount a 15–25yr-old condo's prospects just for age, and don't stack
  an age penalty on top of the lease component (leasehold decay is its job).
- **Value is size-relative.** A small unit trades at a structurally higher PSF
  than a large one in the same project — MMR benchmarks against the same-size
  band plus **tight same-stack comps (±7% sqft, 24mo)** and damps signals on a
  thin cohort (`psf_cohort_txns`). When a small high-PSF unit ranks well, check
  its cohort depth before trusting it.
- **A deep discount is a verify-first signal, not value.** The ranking *selects*
  for artifact cheapness (mis-scraped sqft/beds, strata villas vs apartment
  medians, PES patios, double-volume lofts, stale/bait asks). MMR already
  kinks credit at −25%, caps psf_value/age_value/yield, and damps suspects —
  but a still-extreme discount that survives is a flag to confirm sqft, format
  and price against the project's own **recent (24mo)** URA prints before
  crediting. Trust flags: `ask_above_own_stack_prints` / `stack_low_floor_share`
  ≥ 0.7 (floor/PES artifact — the stack's own prints are the true comp),
  `ask_below_stack_prints` (below p10 of a deep print set — loft void or bait),
  `bedroom_sqft_mismatch`. Gross yield >~5.5% is the same artifact mirrored
  through rent ÷ price — verify ask, `rent_source`, and format first.
- **Override with care.** The system de-biases new-launch appreciation; don't
  override the adjusted rate with a portal headline CAGR — resale-to-resale
  evidence only.
- **`--score` circularity.** If you feed it your own rate/rent, the result
  validates internal consistency, not your inputs.

Full rubric, rating scale, and field guide: **`docs/evaluation-rubric.md`**
(read it in step ③). Release-by-release rationale: **`docs/RELEASES.md`**.

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
`output/arena_referee_packet.json`, work through its `auto_flags`, verify the
top contenders' stats are real (web-check if extreme), then append a
**Referee verdict** to `output/arena_latest.md`: confirmed ranks, demoted
contenders + reasons, and run confidence. The arena ranks stats; you certify
the stats deserve to rank. Each run also writes a timestamped archive
(`output/arena_<ts>.md`).

## Weekly scan (`weekly.py`) — poll, algo-grade, AI-triage

The once-a-week loop. **Nothing auto-starts it** — the reminder lives in the
personal data store (`projects/property-finder/weekly-scan.md`, surfaced at
session start when due) and the owner drives it from there; a successful run
stamps that note's `last_run` so the reminder clears itself. It chains what
already existed:

**poll → algo-grade EVERY new/changed listing → gate → AI-analyze only what
clears the gate → digest → push the good ones to the store.**

The gate is the point: `score_1000` is free, an agent run is not. Knobs live at
the top of `weekly.py` (deliberately **not** `config.py` — that file is hashed
into `score_version`, and a scan-policy threshold must not restamp the scored
book's vintage):

| Knob | Default | Why |
|---|---|---|
| `MIN_SCORE_FOR_AI` | 650 | the recommended tier; ~p88 of the book |
| `MAX_AI_RUNS_PER_SCAN` | 8 | the gate alone is unbounded — a bulk relist could put 40 listings over 650 |
| `POLL_MAX_PAGES` | 5 | search is date-desc, so pages = reach; the poller's 2 only covers a 6-hourly cadence |
| `RE_EVAL_COOLDOWN_DAYS` | 30 | a fresh listing of an already-judged (condo, beds) rarely changes the call |
| store push | Buy/Strong Buy **and** ≥ medium confidence | the store note is a feed the owner reads, not a log |

In-batch dupes collapse to one run per (condo, bed count), and stale /
incomplete / unscored records never reach an agent. Every gated-out listing
carries a `gate_reason` that the digest prints — the gate is never a black box.

Each shortlisted project also gets a **realsmart.sg REALSCORE** lookup in the
agent step (public `/p/<slug>`, plain fetch), surfaced in the digest and the
store row. This sits *after* the gate on purpose: realsmart's ToS is
personal-use, so ≤8 project lookups a week is a research source — never a
per-listing feed.

```bash
python weekly.py                # the real thing (HEADED browser — Cloudflare)
python weekly.py --dry-run      # poll + gate + digest, spawn no agents
python weekly.py --no-poll      # grade what already arrived since the last run
python weekly.py --force        # re-run a week that already succeeded
```

Outputs: digest `output/weekly/<date>.md`, agent transcripts
`output/weekly_runs/`, state `data/weekly_state.json`. A run whose **poll
failed** does not close the week — just run it again. Verdicts land in
`evaluations/` like any other flow — that, not the digest, is the durable record.

The agent step gathers with **`invest.py --from-db <id>`**, which builds a run
dir + `raw_analysis.json` straight from the DB record with **no scraping**
(cohort stats from the full DB, so the MMR matches what the gate saw). Use it
by hand whenever PropertyGuru is unreachable but the listing is already in the DB.

## UI, dashboard, poller

**Listings UI**: `python ui.py` → http://127.0.0.1:8642 — a **viewer over the
scored DB** (full book, sorted by score_1000; overview stats strip, NEW /
price-drop badges, min-score / recency / district / beds filters, same-unit
dedupe ×N badge, Score/Val/Liv/Ovr axis chips); arena table is the second tab
(`#arena`). **Live scraping is OFF by default** — PropertyGuru is behind
Cloudflare, which blocks headless background polls, so the UI never scrapes on
its own. Refresh data with the standalone headed poller: `python poller.py`
(one cycle) or `--loop` (solve Cloudflare once in the Chrome window; the
clearance cookie is reused), state in `data/poll_state.json`. To opt the
in-UI scraping back in (auto-poll + SCAN NOW): `python ui.py --poll
--poll-no-headless` (`--poll-districts/--poll-beds/--poll-interval-mins` to
tune). JSON APIs: `/api/fresh`, `/api/rankings`, `/api/poll-status`,
`POST /poll`.

The fresh view shows the **three-score system** (v3.11) — `score_1000` (MMR —
forward-return money axis, backtest-anchored), plus three 0–100 axes:
**Valuation** (`scoring/valuation.py` — "priced right today?" vs age-adjusted
peers; backtested, 50 = typical ask, >50 cheaper, shares MMR's artifact
defences), **Livability** (`scoring/livability.py`: baths/bed, space/bed, MRT
walk, floor, facing, age, facilities; 50 = neutral, missing data neutral, hover
the chip), and **Overall** (`scoring/overall.py` — purpose-weighted; the fresh
view shows the investment-weighted one). Livability is an explicit
**heuristic** — URA carries none of those fields, so it can never be
backtested and must NEVER be folded into MMR; in the investment Overall it
enters only as a capped demand-floor nudge, never as appreciation. Valuation
and Overall populate on the next poll / `--score-db` run (like `score_1000`).

**System dashboard**: `python dashboard.py` → http://127.0.0.1:8643 — engine
state on one page (forward signals, regime analysis, score distribution,
coverage, district benchmarks, top projects) with per-condo ANALYZE buttons
that run `claude -p "analyze <condo>"` headless (transcript →
`output/analyze_runs/`; verdict auto-saves to eval memory).

Supporting data backbone (append-only — grep/analyze freely):
`data/listings_sheet.csv` (latest state + MMR), `data/mmr_history.csv`
(every scoring run), `data/arena_results.csv` (every tournament),
`data/ura_cache.json` (per-project URA metrics), `data/ura_district_D*.csv`
(raw URA prints — the comp source).

## The memory systems

**`evaluations/` (git-tracked)** — your past judgements, one JSON per condo,
append-only. May be stale or wrong: re-verify price and conditions before
relying on one. Pre-2026-06-10 evals predate the current priors — re-judge
rather than inherit. Commit this folder after evaluations.
```bash
python invest.py --recall "<condo>"     # prior ratings + staleness warning
python invest.py --list-evals
python invest.py --eval-stats           # rating/confidence calibration audit
```

**`profiles/` (git-tracked)** — a development's *physical facts* (stacks,
facings, views, layouts, site plan) — researched once, reused. Stable (not
price-dependent). Listings auto-join to a stack/layout at eval time
(`factual_data.stack_profile`). Never invent a stack; absent =
not-yet-researched. Populate on-demand via `/research-development`.
```bash
python invest.py --profile "<condo>"    # show stacks/facings/layouts (fuzzy)
python invest.py --list-profiles
```

**`data/listings_db.json` + `listings_sheet.csv`** — every listing ever
scraped, deduplicated, with price history (price drops surface).
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
invest.py              # CLI: flows, --score, --recall, --search-db, --from-db, --from-review
weekly.py              # Weekly scan: poll -> algo grade -> AI gate -> digest -> store push
backtest.py            # Point-in-time URA backtest (validates MMR weights)
backtest_ext.py        # Extended panel: lever measurement, age curve, regimes
calibrate_forward.py   # Shipped-score vs realized-return calibration (~mid-2027+)
config.py              # MMR calibration, weights, thresholds
eval_memory.py         # Git-tracked evaluation memory (judgements)
profile_memory.py      # Git-tracked condo profiles + listing→stack join
listings_db.py         # Master listings sheet
poller.py              # PropertyGuru poll cycle (data/poll_state.json)
ui.py                  # Fresh-listings + arena UI (:8642), auto-polls
dashboard.py           # System dashboard (:8643) + headless ANALYZE
scoring/
  full_scorer.py       # Enrichment + component scoring + tight stack comps
  mmr.py               # MMR (uncapped, continuous) + /1000 norm + trust rules
  livability.py        # 0–100 own-stay heuristic (NEVER folded into MMR)
  raw_output.py        # raw_analysis.json (mode-aware AI instructions)
  models.py            # ScoredListing + verdict_from_rating
  roi.py / costs.py / rental_estimator.py / future_scorer.py / district_scorer.py / quick_scorer.py
scrapers/              # PropertyGuru + URA CSV + headless browser
docs/evaluation-rubric.md   # Full evaluation rubric (read in step ③)
docs/RELEASES.md            # Condensed release history + open items
docs/AUDIT_JUN2026.md       # Jun-2026 full audit — prioritized fix queue
.claude/skills/        # analyze-development / analyze-listing / market-scan / research-development / arena-cycle
evaluations/           # Past evaluations — judgements (commit; may be stale)
profiles/              # Condo profiles — physical facts (commit)
output/                # Per-run reports (gitignored)
```
