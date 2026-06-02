# Property Finder

A Singapore condo investment analysis toolkit that combines algorithmic scoring with AI-powered qualitative research. Scrapes PropertyGuru listings, scores them on a 100-point system using URA transaction data, and optionally uses an AI coding agent (like [Claude Code](https://docs.anthropic.com/en/docs/claude-code)) to research stacks, reviews, and red flags.

Built for **5-7 year hold periods** — all cost models, ROI projections, and appreciation decay curves are calibrated for medium-term residential investment in Singapore.

## How It Works

The tool runs a **hybrid algo + AI agent pipeline**:

```
Stage 1 (Algo)              Stage 2 (AI Agent)              Stage 3 (Generator)
invest.py --raw         →   Agent reads raw JSON,       →   invest.py --from-review
scores + empty fields       does web research,               applies adjustments,
                            fills agent_* fields             generates final report
```

**Stage 1** scrapes PropertyGuru, enriches with URA government transaction data, and scores each listing across rental yield, capital appreciation, future infrastructure, liquidity, and cost efficiency.

**Stage 2** is where an AI coding agent (or a human) reviews the top-scored properties — web searching for stack quality, condo reviews, construction issues, school catchments, and anything else the algorithm can't quantify. These findings are written into structured `agent_*` fields in the JSON.

**Stage 3** merges the algo scores with agent adjustments, re-ranks, and generates a final markdown report.

> Stage 2 is optional. You can skip the agent review and go directly from scraping to report generation.

## Prerequisites

- **Python 3.10+**
- **Google Chrome** (used by Patchright for headless scraping)
- **Node.js 18+** (only needed if using the Playwright MCP server for URA data fetching)

## Installation

```bash
git clone https://github.com/ong6/property-finder.git
cd property-finder

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

After installing, Patchright needs a browser:

```bash
python -m patchright install chromium
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

### Standalone (no AI agent)

```bash
# Discover which districts score highest for investment
python invest.py --discover-districts

# Score a condo from key inputs and get the technical breakdown (no scraping)
python invest.py --score '{"price":2300000,"sqft":958,"psf":2401,"beds":2,"district":"D15","tenure":"Freehold","project_name":"The Continuum","appreciation_rate_pct":5.5,"monthly_rent":6200}'

# Search / inspect the continuously-updated listings sheet
python invest.py --search-db "continuum"
python invest.py --list-db
python invest.py --update-db --districts 3,5,14,15 --beds 2,3   # refresh sheet only
```

### With AI Agent Review

```bash
# Step 0 (optional): recall any past evaluation for this condo first
python invest.py --recall "The Continuum"

# Step 1: Run algo scoring (any flow above) — each run writes output/run_NNN/raw_analysis.json
python invest.py --auto --beds 2,3 --top 10

# Step 2: AI agent (or human) reads raw_analysis.json, researches each top
#          property, and fills in the agent_evaluation fields (rating + confidence).

# Step 3: Generate the final report — it auto-saves evaluations into evaluations/
python invest.py --from-review output/run_NNN/raw_analysis.json --output output/run_NNN/final
```

The final report leads with a clear **🟢 BUY / 🟡 NEUTRAL / 🔴 AVOID** verdict and
confidence per listing. Evaluations are written to the git-tracked `evaluations/`
folder — commit it so past evaluations can be recalled later (they are a *reference*;
prices and conditions change, so always re-verify).

### With Claude Code

If you use [Claude Code](https://docs.anthropic.com/en/docs/claude-code), just ask it
in plain language — **"analyze The Continuum"**, **"analyze this: \<PropertyGuru link\>"**,
or **"find me 2-bedders in D15"**. It picks the right flow, recalls past evaluations,
does the web research, fills the evaluation, and generates the report. See
[CLAUDE.md](CLAUDE.md) for the playbook it follows.

## CLI Reference

```bash
python invest.py [options]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--condo` | str | — | Analyze a development by name (groups by unit type) |
| `--url` | str | — | Analyze a PropertyGuru URL (listing, project page, or results page) |
| `--score` | JSON | — | Score a condo from key inputs; prints the technical breakdown (AI-callable) |
| `--auto` | flag | off | Auto-discover top districts, scrape, and score |
| `--discover-districts` | flag | off | Show district rankings without scraping |
| `--districts`, `-d` | str | — | Comma-separated district numbers (e.g. `3,5,14`) |
| `--min-price` | int | 2000000 | Minimum price filter (SGD) |
| `--max-price` | int | 3000000 | Maximum price filter (SGD) |
| `--beds`, `-b` | str | 2,3 | Bedroom counts to search |
| `--max-pages` | int | 5 | PropertyGuru pages to scrape per district |
| `--input`, `-i` | str | — | Score existing listings JSON instead of scraping |
| `--output`, `-o` | str | — | Output path for report (without extension) |
| `--csv` | str | — | CSV export path |
| `--json` | str | — | JSON export path |
| `--raw` | path | — | Output raw analysis JSON for agent review |
| `--from-review` | path | — | Generate final report from reviewed JSON |
| `--top`, `-n` | int | 10 | Number of top properties to display |
| `--verbose`, `-v` | flag | off | Show detailed score breakdowns |
| `--build-ura-cache` | files | — | Build URA cache from downloaded CSVs |
| `--fetch-ura-districts` | str | — | Fetch URA data by postal district |
| `--list-districts` | flag | off | Show all Singapore district numbers |
| `--no-headless` | flag | off | Show browser window (runs headless by default) |
| `--recall` | str | — | Recall past evaluations for a condo (git-tracked memory) |
| `--list-evals` | flag | off | List all stored evaluations |
| `--save-eval` | path | — | Save evaluations from a reviewed JSON into memory |
| `--no-save-eval` | flag | off | Don't auto-save evaluations during `--from-review` |
| `--search-db` | str | — | Search the master listings sheet by condo name |
| `--list-db` | flag | off | Listings sheet stats (counts, price drops, by district) |
| `--update-db` | flag | off | Scrape and refresh the listings sheet only (skip scoring) |
| `--export-sheet` | flag | off | Re-export the listings sheet CSV from the database |
| `--no-db` | flag | off | Don't upsert scraped listings into the master sheet |

## Configuration

Investment parameters and scoring weights live in `config.py`:

```python
# Target investment range
TARGET_PRICE_MIN = 2_200_000
TARGET_PRICE_MAX = 2_700_000
TARGET_HOLD_YEARS = [5, 6, 7]

# Buyer profile
DEFAULT_BUYER_TYPE = "SC"       # SC, PR, or Foreigner
DEFAULT_PROPERTY_COUNT = 0      # 0 = first property (0% ABSD for SC)
```

All scoring weights, tier thresholds, and bias correction parameters are also in `config.py`. See [docs/ANALYSIS_METHODOLOGY.md](docs/ANALYSIS_METHODOLOGY.md) for the full scoring breakdown.

## Scoring System (100 Points)

| Category | Weight | What It Measures |
|----------|--------|------------------|
| **Rental Yield** | 15 pts | Gross yield, MRT proximity, unit config, tenant pool |
| **Capital Appreciation** | 30 pts | URA historical growth, momentum, PSF vs median, tenure, age |
| **Future Potential** | 20 pts | Upcoming MRT lines (not yet operational), government development zones |
| **Liquidity & Exit Risk** | 25 pts | Transaction volume, buyer pool depth, development size |
| **Cost Efficiency** | 10 pts | MCST, property tax, space efficiency |
| **Red Flags** | -10 pts | West-facing, small dev, low lease, very old |

**Tier classification**: Tier 1 (60+) = recommended, Tier 2 (45-59) = consider, below 45 = not recommended.

For the full methodology including bias corrections, ROI calculations, and all sub-component scoring tables, see [docs/ANALYSIS_METHODOLOGY.md](docs/ANALYSIS_METHODOLOGY.md).

## URA Data Integration

[URA](https://www.ura.gov.sg/) (Urban Redevelopment Authority) provides 5 years of official government transaction data — the most reliable source for appreciation calculations.

### Building the URA Cache

1. Download transaction CSVs from [URA Property Market Information](https://eservice.ura.gov.sg/property-market-information/pmiResidentialTransactionSearch)
2. Save them to the `data/` folder
3. Build the cache:

```bash
python invest.py --build-ura-cache data/ura_*.csv
```

The cache (`data/ura_cache.json`) stores per-project appreciation rates, transaction counts, momentum, and new-sale proportions. Properties matched to the cache get real data instead of default estimates.

## Agent Review Fields

When using the AI agent pipeline (`--raw` → review → `--from-review`), the raw JSON contains empty `agent_*` fields for each listing. The agent (or a human reviewer) fills these in:

### Per-Listing Fields

| Field | Type | Description |
|-------|------|-------------|
| `agent_summary` | string | 2-3 sentence investment thesis |
| `agent_red_flags` | list[str] | Risks the algo can't detect (construction, noise, reputation) |
| `agent_catalysts` | list[str] | Upside factors the algo misses (schools, lifestyle, en-bloc) |
| `agent_score_adjustment` | float | Score adjustment from -8 to +8 |
| `agent_adjustment_reason` | string | Why the adjustment was made |
| `agent_rental_assessment` | string | Qualitative view on rental demand |
| `agent_appreciation_assessment` | string | Qualitative view on price trajectory |
| `agent_appreciation_rate_pct` | float | AI-researched appreciation rate (percent) when algo used fallback |
| `agent_appreciation_source` | string | Source for the AI-provided appreciation rate |
| `agent_stack_notes` | string | Best/worst stacks, floors to avoid, facing analysis |
| `agent_confidence` | string | `"high"`, `"medium"`, or `"low"` |

### Report-Level Fields

| Field | Type | Description |
|-------|------|-------------|
| `agent_executive_summary` | string | Top picks and overall reasoning |
| `agent_market_commentary` | string | Current market conditions and timing |
| `agent_methodology_notes` | string | Caveats about this analysis run |

## Project Structure

```
property-finder/
├── invest.py                # Main CLI entry point (all flows + scorer/memory/sheet commands)
├── config.py                # Scoring weights, thresholds, investment params
├── models.py                # Data models (SearchParams, Listing, etc.)
├── fetch_ura_districts.py   # URA district-level data fetcher
├── eval_memory.py           # Git-tracked evaluation memory (recall/save past evaluations)
├── listings_db.py           # Master listings sheet (continuously-updated, searchable)
│
├── scrapers/
│   ├── propertyguru.py      # PropertyGuru scraper (search, condo-name, URL, detail pages)
│   ├── ura_scraper.py       # URA transaction CSV parser
│   └── browser.py           # Browser manager (Patchright/Chromium)
│
├── scoring/
│   ├── full_scorer.py       # 100-point scoring engine
│   ├── quick_scorer.py      # Fast pre-filter scorer
│   ├── raw_output.py        # Raw analysis JSON (mode-aware AI instructions)
│   ├── models.py            # Score data models (ScoredListing) + verdict mapping
│   ├── roi.py               # ROI projections with appreciation decay
│   ├── costs.py             # BSD, ABSD, SSD, property tax calculations
│   ├── rental_estimator.py  # Rental yield estimation
│   ├── district_scorer.py   # District-level ranking
│   └── future_scorer.py     # Infrastructure & govt zone scoring
│
├── utils/
│   ├── markdown.py          # Report generator (Buy/Neutral/Avoid verdict + confidence)
│   ├── geo.py               # Distance calculations
│   └── logging.py           # Logging setup
│
├── data/                    # Reference data + listings sheet (db.json / sheet.csv)
├── evaluations/             # Git-tracked past evaluations (commit these — may be stale)
├── tests/                   # Test suite
├── docs/                    # Detailed methodology documentation
└── output/                  # Generated per-run reports (gitignored)
```

## Troubleshooting

### Cloudflare Blocks

PropertyGuru uses Cloudflare protection. If scraping is blocked:

```bash
# Clear browser state and retry
rm -rf chrome-profile/

# Run in headed mode to solve the challenge manually
python invest.py --districts 3 --no-headless --max-pages 1
```

### No URA Data for a Property

The scorer falls back to a conservative 2% default rate (capped at 4/14 pts). To get real data, download CSVs from the URA website for the relevant districts and build the cache.

### Missing Dependencies

```bash
pip install -r requirements.txt
python -m patchright install chromium
```

## Contributing

Contributions are welcome. Some areas that could use help:

- **More data sources** — integrate 99.co, EdgeProp, or SRX listings
- **Better rental estimation** — real-time rental data instead of district medians
- **International markets** — adapt the scoring framework for other countries
- **Test coverage** — expand the test suite for scoring edge cases
- **UI** — a web frontend for browsing results instead of markdown reports

Please open an issue to discuss larger changes before submitting a PR.

## Disclaimer

This tool is for **educational and research purposes only**. It is not financial advice. Property investment carries risk, and past performance does not guarantee future results. Always do your own due diligence and consult a qualified financial advisor before making investment decisions. The authors are not responsible for any losses incurred from using this tool.

## License

[MIT](LICENSE)
