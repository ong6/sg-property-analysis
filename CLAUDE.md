# Property Finder — Claude Code Playbook

This repo is driven by **Claude Code** (you). The Python scripts gather *facts* —
they scrape PropertyGuru, enrich with URA government transaction data, compute
costs, distances and a technical score. **You do the evaluation, research and the
final Buy / Neutral / Avoid call.** The algorithm's score is one input, never the
answer.

You have three tools that the scripts don't: **WebSearch/WebFetch** (reviews,
floor plans, transactions, news), the **evaluation memory** (past evaluations,
git-tracked), and the **listings sheet** (a growing, searchable inventory of
everything ever scraped). Use all three.

## Pick the flow from how the user asks

The user's request maps to one of three flows. **Match the output altitude to the
request** — a development question and a single-unit question get different answers.

| User says… | Flow | What you produce |
|---|---|---|
| "analyze this condo: **\<name\>**" or "is **\<development\>** good?" | **A — Development** | Whether the development as a whole is worth it, **plus best stacks / facings / floors** to target and which to avoid. Rate each unit type Buy/Neutral/Avoid. |
| "analyze this: **\<PropertyGuru listing link\>**" | **B — Single listing** | A clear **Buy / Neutral / Avoid** call on that exact unit, with confidence. |
| "analyze this set: **\<PG results/list page\>**" or "find me condos in \<area\>" | **C — Market scan** | A shortlist, each with a **Buy / Neutral / Avoid** rating + confidence, strongest buys first. |

Every flow follows the same 5 steps:

**① Recall → ② Gather → ③ Research & score → ④ Fill `agent_evaluation` → ⑤ Report (auto-saves to memory).**

### ① Recall — always check memory first
```bash
python invest.py --recall "<condo name>"     # past evaluations for this condo (fuzzy)
python invest.py --search-db "<condo name>"  # what's in the listings sheet already
```
Past evaluations are a **reference, not truth** — prices and conditions change, and
the prior call may simply have been wrong. Re-verify the current price and re-research;
your fresh evaluation is appended, the old one is kept for history.

### ② Gather data — one command per flow
```bash
# Flow A — Development by name (groups listings by unit type)
python invest.py --condo "The Continuum" --beds 1,2,3 --min-price 800000 --max-price 3500000

# Flow A/B — From a PropertyGuru URL (listing, project page, or results page)
python invest.py --url "https://www.propertyguru.com.sg/listing/24xxxxxx-..."   # single unit  -> Flow B
python invest.py --url "https://www.propertyguru.com.sg/project/the-continuum-12345"  # project -> Flow A
python invest.py --url "https://www.propertyguru.com.sg/property-for-sale?districtCode=D15"  # set -> Flow C

# Flow C — Market scan
python invest.py --discover-districts                 # see district rankings
python invest.py --auto --beds 2,3 --top 20           # auto-pick best districts, scrape, score
python invest.py --districts 3,5,14,15 --beds 2,3     # specific districts
```
Each run creates `output/run_NNN/` containing `raw_analysis.json` (this is what you
review), plus `report.md`, `scored.json`. Every scrape also **upserts into the
listings sheet** automatically.

### ③ Research & score
- Read `raw_analysis.json` → `ai_review_instructions` tells you the detected **mode**
  (development / single_listing / market_scan) and tailored steps.
- WebSearch each property: reviews, build quality, developer, recent transactions,
  floor/site plans (for stacks & facings), noise, schools, upcoming supply.
- **Sanity-check with the technical scorer** — feed it your researched numbers:
  ```bash
  python invest.py --score '{"price":2300000,"sqft":958,"psf":2401,"beds":2,"district":"D15","tenure":"Freehold","total_units":420,"project_name":"The Continuum","appreciation_rate_pct":5.5,"monthly_rent":6200}'
  ```
  It returns a 100-point technical score with the per-metric breakdown (rental 15 /
  appreciation 30 / future 20 / liquidity 25 / cost 10, red flags −10). Use it to
  pressure-test your view; the verdict is still yours.

### ④ Fill `agent_evaluation`
Edit `output/run_NNN/raw_analysis.json` and fill the `agent_evaluation` block for each
listing (see [Filling agent_evaluation Fields](#filling-agent_evaluation-fields)). Always
set `rating` and `confidence` — the final report shows them as the headline verdict.
For **development** mode, put stack/facing/floor guidance in `stack_notes` and give the
overall development verdict in `agent_executive_summary`.

### ⑤ Generate the final report (auto-saves to memory)
```bash
python invest.py --from-review output/run_NNN/raw_analysis.json --output output/run_NNN/final
```
The report leads with a clear **🟢 BUY / 🟡 NEUTRAL / 🔴 AVOID** badge and confidence
per listing. Evaluations are auto-saved into the git-tracked `evaluations/` folder
(disable with `--no-save-eval`). **Commit `evaluations/` so the knowledge persists.**

---

## The two memory systems

**`evaluations/` — your past judgements (git-tracked).** One JSON per condo, append-only
history. This is curated knowledge meant to be committed and shared.
```bash
python invest.py --recall "Grand Dunman"   # prior ratings + staleness warning
python invest.py --list-evals              # everything you've evaluated
```
⚠ Past evaluations may be stale or wrong — always re-verify before relying on one.

**`data/listings_sheet.csv` + `data/listings_db.json` — the market inventory.** Every
listing ever scraped, deduplicated, with price history (so **price drops** surface).
Grows on every run. You can `grep`/read it directly, or:
```bash
python invest.py --search-db "continuum" # find listings by name
python invest.py --list-db               # stats: counts, price drops, by district
python invest.py --update-db --districts 3,5,14,15 --beds 2,3   # refresh the sheet only (no report)
python invest.py --export-sheet          # re-export the CSV from the DB
```
To keep the sheet rich, run `--update-db` periodically (it's safe to cron). Use `--no-db`
on a run to skip upserting.

---

## URA appreciation cache
```bash
python invest.py --build-ura-cache data/ura_*.csv    # build from downloaded CSVs
python invest.py --fetch-ura-districts 3,5,14,15     # fetch by district
```

---

## How to Evaluate a Singapore Condo

Use this framework when reviewing properties. These are the factors that matter, roughly in order of importance for a 5-7 year investment hold:

### 1. Capital Appreciation Potential (most important)

**What to look at in factual_data:**
- `appreciation.annual_rate_pct` — URA 5-year CAGR from government data
- `appreciation.data_source` — "ura_cache" is reliable; "regional_baseline" means no project-specific data (research this yourself)
- `appreciation.transaction_count` — more transactions = more reliable rate
- `appreciation.data_confidence` — statistical confidence in the rate
- `appreciation.momentum` — positive = accelerating appreciation
- `appreciation.psf_vs_district_median` — below 1.0 means cheaper than district average (room for upside)

**What you should research:**
- Recent transaction prices on URA/PropertyGuru — is the trend up or down?
- Any upcoming catalysts (new MRT, government development zone, en-bloc potential)?
- Developer reputation and project quality
- Is this a new launch with inflated pricing?

**Singapore-specific knowledge:**
- Regional appreciation baselines: RCR ~5.8%/yr (best value), CCR ~4.5%/yr, OCR ~3.7%/yr
- New launches show inflated appreciation from developer pricing — resale-only data is more honest
- Young 99yr leasehold often appreciates better than freehold at $2M+ (freehold premium already priced in)
- Properties near upcoming MRT lines (CRL 2030, JRL 2028) can see 10-20% uplift

### 2. Liquidity and Exit Risk (second most important)

A property is worthless if you can't sell it when you want to. Consider:

**What to look at in factual_data:**
- `liquidity.ura_transaction_count` — 50+ is high confidence; <10 is concerning
- `liquidity.buyer_pool_depth` — very_deep/deep/moderate/shallow
- `liquidity.total_units` — larger developments are more liquid (400+ units ideal)

**Your judgment:**
- Is the price in the liquid sweet spot ($1.8M-$2.5M)? Above $3M, buyer pool shrinks significantly
- Is this district popular with HDB upgraders? (D14, D15, D19 = yes)
- Would this unit appeal to a broad buyer base, or is it niche?

### 3. Future Potential

**What to look at in factual_data:**
- `future_infrastructure.nearest_future_mrt` — station name, distance, MRT line
- `future_infrastructure.government_zones` — e.g., Jurong Lake District, Greater Southern Waterfront

**Key government development zones (by transformation impact):**
- Jurong Lake District (D22) — designated "2nd CBD", massive office/commercial development
- Greater Southern Waterfront (D4/D5) — waterfront transformation of former port areas
- Paya Lebar Central (D14) — commercial hub with airbase relocation creating new land
- Punggol Digital District (D19) — tech hub with SIT university
- Woodlands Regional Centre (D25) — cross-border link to Malaysia

**Future MRT lines (not yet operational):**
- Cross Island Line (CRL): 12 stations, ~2030
- Jurong Region Line (JRL): 18 stations, ~2028
- Properties within 500m of a future station get the strongest uplift

### 4. Rental Yield

**What to look at in factual_data:**
- `rental.gross_yield_pct` — 4%+ is excellent for properties at $2M+; 3-3.5% is typical
- `rental.estimated_monthly_rent` — is this realistic for the area?
- `rental.rent_source` — "district_bedroom" uses area averages; condo-specific data is better

**Your judgment:**
- Is this area popular with tenants? (near CBD, hospitals, universities, MRT)
- 2BR units have the largest tenant pool; 1BR is niche; 4BR is family-only
- Is there rental oversupply in this area from other new launches?

### 5. Costs and Taxes

**What to look at:**
- `roi_projections` — total return over 5/6/7 years including all costs
- `roi_sensitivity` — downside/upside scenarios

**Singapore tax rules (already factored into ROI):**
- BSD: Progressive 1-6% on purchase price
- ABSD: 0% for SC first property, 20% for SC second property, 30% for foreigners
- SSD: 12%/8%/4%/0% if selling within 1yr/2yr/3yr/3yr+ (why we model 5-7yr holds)
- Property tax: Progressive non-owner-occupied rates on Annual Value

### 6. Red Flags to Watch For

**Algorithmic red flags** (in `factual_data.red_flags_detected`):
- `west_facing` — equatorial afternoon sun, high cooling costs, tenant dispreference
- `small_development` — <100 units, illiquid, limited amenities
- `low_remaining_lease` — <70 years remaining, hard to finance
- `very_old_property` — >20 years, higher maintenance, harder resale
- `psf_overpriced` / `psf_above_market` — listing price significantly above URA median
- `bedroom_sqft_mismatch` — unusual size for bedroom count (possible misclassification)

**What you should research:**
- Construction quality issues or defects
- Noise sources (highways, MRT tracks, construction)
- Stigma (crime, previous incidents)
- Developer reputation and track record
- Competing new launches in the area that could depress resale value

---

## Rating Scale

Rate each property using this scale:

| Rating | Report verdict | Meaning |
|--------|----------------|---------|
| **Strong Buy** | 🟢 BUY | Excellent fundamentals across appreciation, liquidity, and yield. No major red flags. Clear catalysts. |
| **Buy** | 🟢 BUY | Good investment with solid fundamentals. Minor concerns but overall positive. |
| **Neutral** | 🟡 NEUTRAL | Mixed signals. Some strong points but significant concerns balance them out. |
| **Avoid** | 🔴 AVOID (no-buy) | Poor investment fundamentals, major red flags, or significantly overpriced. |

The final report collapses these four ratings into a clear **BUY / NEUTRAL / AVOID**
headline and shows your **confidence** (high/medium/low) next to it — so always set both
`rating` and `confidence`. Always explain your rating — what specifically makes this
property good or bad.

**Altitude matters.** For a **single listing** (Flow B) the rating is the answer. For a
**development** (Flow A) also rate each unit type, but lead with whether the *development
as a whole* is worth buying into and which **stacks / facings / floors** are the ones to
target — that guidance goes in `stack_notes` and the executive summary.

---

## Filling agent_evaluation Fields

For each listing in `raw_analysis.json`, fill these fields:

```json
{
  "agent_evaluation": {
    "summary": "2-3 sentence investment thesis",
    "rating": "Strong Buy / Buy / Neutral / Avoid",
    "rating_rationale": "Key reasons — be specific about numbers and facts",
    "red_flags": ["specific risk 1", "specific risk 2"],
    "catalysts": ["specific growth driver 1", "specific growth driver 2"],
    "rental_assessment": "Rental demand outlook with reasoning",
    "appreciation_assessment": "Price trajectory view with supporting data",
    "stack_notes": "Best/worst stacks, floors, facing based on research",
    "confidence": "high/medium/low",
    "appreciation_rate_override_pct": 4.5,
    "appreciation_rate_source": "Source for your rate"
  }
}
```

Also fill the report-level fields:
- `agent_executive_summary` — overall verdict with top picks and reasoning
- `agent_market_commentary` — current Singapore market conditions and timing
- `agent_methodology_notes` — data gaps, caveats, what you couldn't verify

---

## Singapore District Quick Reference

**RCR (Rest of Central Region) — Best value, ~5.8%/yr appreciation:**
- D03 Queenstown/Alexandra — Greater Southern Waterfront, TEL
- D05 Clementi/West Coast — One-North tech hub, TEL
- D14 Paya Lebar/Geylang — Paya Lebar Central, airbase relocation
- D15 East Coast — deep buyer pool, lifestyle district

**OCR (Outside Central Region) — Growth potential, ~3.7%/yr baseline:**
- D22 Jurong/Boon Lay — Jurong Lake District "2nd CBD", JRL
- D19 Punggol/Sengkang — Punggol Digital District, CRL
- D18 Tampines/Pasir Ris — Tampines Regional Centre, CRL + DTL extension

**CCR (Core Central Region) — Premium, ~4.5%/yr appreciation:**
- D09/D10/D11 Orchard/Newton/Holland — established prime, lower yield at $2M+

---

## Project Structure
```
invest.py              # CLI entry point (all flows + --score, --recall, --search-db, ...)
config.py              # Scoring weights, price range, hold years
models.py              # Scraper data models
eval_memory.py         # Git-tracked evaluation memory (recall/save past evaluations)
listings_db.py         # Master listings sheet (continuously-updated, searchable inventory)
scoring/
  full_scorer.py       # Data enrichment + scoring engine
  raw_output.py        # Produces raw_analysis.json (mode-aware AI instructions)
  models.py            # ScoredListing dataclass + verdict_from_rating()
  roi.py               # ROI calculator
  costs.py             # Government tax rates (BSD, ABSD, SSD)
  rental_estimator.py  # Rental yield estimation
  future_scorer.py     # Future MRT + govt zone data
  district_scorer.py   # District ranking
  quick_scorer.py      # Pre-filter
scrapers/
  propertyguru.py      # PropertyGuru scraper (search, condo-name, --url, detail pages)
  ura_scraper.py       # URA CSV parser
  browser.py           # Headless browser
utils/
  markdown.py          # Report generator (Buy/Neutral/Avoid verdict + confidence)
  geo.py               # Distance calculations
data/                  # Reference data + listings_db.json / listings_sheet.csv
evaluations/           # Git-tracked past evaluations (commit these — may be stale)
output/                # Generated per-run reports (gitignored)
```
