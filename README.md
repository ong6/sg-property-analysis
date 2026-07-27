# Property Finder

A Singapore condo investment analysis toolkit that combines algorithmic scoring with AI-powered qualitative research. It scrapes PropertyGuru listings, enriches them with URA government transaction data, and scores them with **MMR** — an Elo-style rating displayed as `score_1000` (0–1000) — while an AI coding agent (like [Claude Code](https://docs.anthropic.com/en/docs/claude-code)) does the research and makes the final Buy / Neutral / Avoid call.

Built for **5-7 year hold periods** — all cost models, ROI projections, and appreciation decay curves are calibrated for medium-term residential investment in Singapore.

## How It Works

The tool runs a **hybrid algo + AI agent pipeline**:

```
Stage 1 (Algo)              Stage 2 (AI Agent)              Stage 3 (Generator)
invest.py --raw         →   Agent reads raw JSON,       →   invest.py --from-review
scores + empty fields       does web research,               renders verdicts,
                            fills agent_evaluation           generates final report
```

**Stage 1** scrapes PropertyGuru, enriches with URA transaction data, and computes the MMR score plus the factual inputs (yield, relative value, liquidity, costs). **Stage 2** (optional) is where an AI agent or human researches each property — stack quality, condo reviews, construction issues, anything the algorithm can't quantify — and fills the `agent_evaluation` block. **Stage 3** renders the final markdown report and auto-saves the evaluation to memory.

## Prerequisites

- **Python 3.10+**
- **Google Chrome** (used by Patchright for headless scraping)
- **Node.js 18+** (only needed if using the Playwright MCP server for URA data fetching)

## Installation

```bash
git clone https://github.com/ong6/property-finder.git
cd property-finder
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m patchright install chromium            # Patchright needs a browser
```

## Quick Start

### Three ways to start an analysis

```bash
# A — A whole development, by name (groups listings by unit type)
python invest.py --condo "The Continuum" --beds 1,2,3
# B — A specific PropertyGuru listing, project page, or results page (by URL)
python invest.py --url "https://www.propertyguru.com.sg/listing/24xxxxxx-..."
# C — A market scan across districts
python invest.py --auto --beds 2,3 --top 20
python invest.py --districts 3,5,14,15 --beds 2,3 --top 20
```

Each run writes `output/run_NNN/` (raw analysis, report, scored JSON) and updates the
searchable listings sheet. See [CLAUDE.md](CLAUDE.md) for the full Claude Code playbook.

### Local web UIs

```bash
python ui.py          # http://127.0.0.1:8642 — fresh listings + condo arena
python dashboard.py   # http://127.0.0.1:8643 — system dashboard + headless ANALYZE
```

The listings UI auto-polls PropertyGuru in the background (every 6h by default, `--no-poll`
to disable) and scores only new or price-changed listings. Standalone poller: `python poller.py`.

### Standalone (no AI agent)

```bash
python invest.py --discover-districts    # district rankings, no scraping
python invest.py --score '{"price":2300000,"sqft":958,"psf":2401,"beds":2,"district":"D15","tenure":"Freehold","project_name":"The Continuum","appreciation_rate_pct":5.5,"monthly_rent":6200}'
python invest.py --fight                 # condo arena: pairwise tournament over the DB
python invest.py --search-db "continuum" # search the master listings sheet
python invest.py --update-db --districts 3,5,14,15 --beds 2,3   # refresh sheet only
```

### With AI Agent Review

```bash
python invest.py --recall "The Continuum"     # 0 (optional): recall past evaluations
python invest.py --auto --beds 2,3 --top 10   # 1: algo scoring → output/run_NNN/raw_analysis.json
# 2: AI agent (or human) reads raw_analysis.json, researches each property,
#    and fills the agent_evaluation fields (rating + confidence + rationale)
python invest.py --from-review output/run_NNN/raw_analysis.json --output output/run_NNN/final  # 3
```

The final report leads with a clear **🟢 BUY / 🟡 NEUTRAL / 🔴 AVOID** verdict and
confidence per listing. Evaluations are saved to the git-tracked `evaluations/` folder —
commit it so they can be recalled later (a *reference*; prices change, always re-verify).

### With Claude Code

If you use [Claude Code](https://docs.anthropic.com/en/docs/claude-code), just ask in plain
language — **"analyze The Continuum"**, **"analyze this: \<PropertyGuru link\>"**, or
**"find me 2-bedders in D15"**. It picks the right flow, recalls past evaluations, does the
research, and generates the report. [CLAUDE.md](CLAUDE.md) is the playbook it follows.

## CLI Reference

Most-used flags for `python invest.py`, grouped by purpose (`--help` for the full list):

| Flag | Description |
|------|-------------|
| **Flows** | |
| `--condo NAME` | Analyze a development by name (fuzzy match; groups by unit type) |
| `--url URL` | Analyze a PropertyGuru URL (listing, project page, or results page) |
| `--auto` | Auto-discover top districts, scrape, and score |
| `--districts, -d` | Comma-separated district numbers (e.g. `3,5,14`) |
| `--discover-districts` | Show district rankings without scraping |
| `--auto-districts N` / `--include-ccr` | Auto-mode tuning: district count (default 5) / include CCR |
| **Scraping filters** | |
| `--beds, -b` | Bedroom counts (default: 2,3 for scraping; ALL for `--fight`) |
| `--min-price` / `--max-price` | Price filters in SGD (optional, no default) |
| `--max-pages` / `--url-max-pages` | Pages to scrape per district / per results URL (default: 5) |
| `--enrich-top N` | Visit detail pages for the top N listings (default: 0 = off) |
| **Scoring** | |
| `--score JSON` | Score a condo from key inputs; prints the technical breakdown |
| `--score-db` | Batch-score every usable listing in the DB and write scores back |
| `--fight` | Condo arena: pairwise tournament over the DB (Elo ranking + champion) |
| `--raw PATH` / `--from-review PATH` | Write raw analysis JSON for agent review / generate final report from it |
| **Listings DB** | |
| `--from-db IDS` | Build a run dir + raw analysis from listings already in the DB — **no scraping** |
| `--search-db QUERY` / `--list-db` | Search the master sheet by condo name / show sheet stats |
| `--update-db` | Scrape and refresh the sheet only (skip scoring) |
| `--export-sheet` / `--no-db` | Re-export the CSV / don't upsert scraped listings |
| **Memory** | |
| `--recall NAME` | Recall past evaluations for a condo (fuzzy) |
| `--list-evals` / `--eval-stats` | List evaluations / rating-confidence distribution audit |
| `--no-save-eval` | Don't auto-save evaluations during `--from-review` |
| `--profile NAME` | Show a condo's researched stack/layout profile (fuzzy) |
| `--list-profiles` / `--save-profile FILE` | List profiles / save a profile JSON into `profiles/` |
| **URA data** | |
| `--build-ura-cache CSV...` / `--fetch-ura-districts` | Build the URA cache from CSVs / fetch by district |
| **Output / misc** | |
| `--top, -n` / `--output, -o` / `--csv` / `--json` | Result count and export paths |
| `--verbose, -v` / `--no-headless` / `--list-districts` | Score breakdowns / show browser / list districts |

## Weekly Scan

`weekly.py` is the once-a-week loop: **poll → algo-grade every new and
price-changed listing → gate → AI-analyze only what clears the gate → digest**.
The algo grade is free and applied to everything; the AI agent is expensive, so
it only runs on listings scoring `>= 650` (capped at 8 per scan, with a 30-day
re-evaluation cooldown per condo + bed count). Everything filtered out is still
logged, with the reason.

```bash
python weekly.py              # the real thing (headed browser)
python weekly.py --dry-run    # poll + gate + digest, no agents
```

Nothing auto-starts it — run it by hand when the reminder comes up. Digests land
in `output/weekly/<date>.md`; verdicts go to `evaluations/` like any other flow.
Thresholds are at the top of `weekly.py`. Full details: `CLAUDE.md`.

## Configuration

Investment parameters and scoring calibration live in `config.py`:

```python
TARGET_HOLD_YEARS = [5, 6, 7]
DEFAULT_BUYER_TYPE = "SC"       # SC, PR, or Foreigner
DEFAULT_PROPERTY_COUNT = 0      # 0 = first property (0% ABSD for SC)
```

The same file holds all MMR calibration: normalization constants (`MMR_BASE`,
`MMR_NORM_CENTER`, `MMR_NORM_SCALE`), tier thresholds (`SCORE1000_TIER1_MIN` /
`SCORE1000_TIER2_MIN`), backtest-calibrated component slopes, and regional baselines.

## Scoring System (MMR)

The score is **MMR** — Elo-style, base 1500, uncapped and continuous, built from
backtest-calibrated components (relative value, region, yield, liquidity, age,
red flags) — displayed as **`score_1000`** on a 0–1000 scale: **500 =
market-typical, 650+ = recommended tier, <450 = below threshold.** Missing data
is neutral, never penalized. The score is one *input* to the verdict — the AI
agent's researched rating is the answer. A separate 0–100 **livability score**
(`scoring/livability.py`) gives an own-stay lens; it is a heuristic and never
folds into MMR.

For the evaluation rubric, component details, and the release history of scoring
changes, see [CLAUDE.md](CLAUDE.md), [docs/evaluation-rubric.md](docs/evaluation-rubric.md),
and [docs/RELEASES.md](docs/RELEASES.md).

## URA Data Integration

[URA](https://www.ura.gov.sg/) provides 5 years of official government transaction data —
the most reliable source for appreciation calculations. Download CSVs from
[URA Property Market Information](https://eservice.ura.gov.sg/property-market-information/pmiResidentialTransactionSearch) into `data/`, then build the cache:

```bash
python invest.py --build-ura-cache data/ura_*.csv
```

The cache (`data/ura_cache.json`, with a CSV export) stores per-project appreciation
rates, transaction counts, momentum, and new-sale proportions. Matched properties
get real data instead of regional estimates.

## Agent Evaluation

When using the agent pipeline (`--raw` → review → `--from-review`), each listing in
the raw JSON carries a nested **`agent_evaluation`** dict to fill in: `rating`
(Buy/Neutral/Avoid), `confidence`, `rating_rationale`, `summary`, `red_flags`,
`catalysts`, rental/appreciation assessments, `stack_notes`, and an optional
appreciation-rate override with source. The rating scale and field guide are in
[docs/evaluation-rubric.md](docs/evaluation-rubric.md).

## Project Structure

```
property-finder/
├── invest.py              # Main CLI: flows, --score, --recall, --search-db, --from-review
├── backtest.py / backtest_ext.py   # Point-in-time URA backtests of scoring features
├── calibrate_forward.py   # Joins past scores to later URA PSF (forward calibration)
├── config.py              # MMR calibration, weights, thresholds, buyer profile
├── eval_memory.py         # Git-tracked evaluation memory (judgements)
├── profile_memory.py      # Git-tracked condo profiles (physical facts) + listing→stack join
├── listings_db.py         # Master listings sheet (continuously-updated, searchable)
├── poller.py              # PropertyGuru poll cycle: scrape newest → upsert → score new
├── ui.py                  # Fresh-listings + arena UI (:8642), auto-polls via poller.py
├── dashboard.py           # System dashboard (:8643) with headless ANALYZE buttons
├── scoring/
│   ├── full_scorer.py     # Enrichment + component scoring
│   ├── mmr.py             # MMR (uncapped, continuous) + /1000 normalization
│   ├── arena.py           # Condo arena (--fight) tournament engine
│   ├── livability.py      # 0-100 own-stay livability score (heuristic)
│   ├── relative_value.py  # Age/size-normalized peer comparison
│   ├── raw_output.py      # raw_analysis.json (mode-aware AI instructions)
│   ├── models.py          # ScoredListing + verdict mapping
│   └── roi.py / costs.py / rental_estimator.py / future_scorer.py / district_scorer.py / quick_scorer.py
├── scrapers/              # PropertyGuru + URA CSV + headless browser
├── utils/                 # Report generator, geo, logging
├── docs/                  # evaluation-rubric.md, RELEASES.md
├── .claude/skills/        # analyze-development / analyze-listing / market-scan / research-development
├── evaluations/           # Past evaluations — judgements (commit; may be stale)
├── profiles/              # Condo profiles — stacks/facings/layouts (commit)
├── data/                  # Reference data, listings DB/sheet, URA + rental caches
├── tests/                 # Test suite
└── output/                # Generated per-run reports (gitignored)
```

## Troubleshooting

**Cloudflare blocks** — PropertyGuru uses Cloudflare protection. If scraping is blocked:

```bash
rm -rf chrome-profile/                                       # clear browser state and retry
python invest.py --districts 3 --no-headless --max-pages 1   # solve the challenge manually
```

**No URA data for a property** — the scorer falls back to the **regional
appreciation baseline** (`REGIONAL_APPRECIATION_BASELINES` in `config.py`:
CCR 2.8% / RCR 3.7% / OCR 4.2%, default 3.5%/yr). For real per-project data,
download the relevant district CSVs from URA and build the cache.

**Missing dependencies** — `pip install -r requirements.txt && python -m patchright install chromium`

## Contributing

Contributions are welcome. Some areas that could use help:

- **More data sources** — integrate 99.co, EdgeProp, or SRX listings
- **Better rental estimation** — real-time rental data instead of district medians
- **International markets** — adapt the scoring framework for other countries
- **Test coverage** — expand the test suite for scoring edge cases

Please open an issue to discuss larger changes before submitting a PR.

## Disclaimer

This tool is for **educational and research purposes only**. It is not financial advice. Property investment carries risk, and past performance does not guarantee future results. Always do your own due diligence and consult a qualified financial advisor before making investment decisions. The authors are not responsible for any losses incurred from using this tool.

## License

[MIT](LICENSE)
