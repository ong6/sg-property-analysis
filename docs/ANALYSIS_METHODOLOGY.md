# Property Finder: Analysis & Scoring Methodology

> A comprehensive guide to how properties are evaluated, scored, and ranked for investment potential in the Singapore residential market.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Investment Parameters](#2-investment-parameters)
3. [Workflow (Hybrid Algo + AI Agent Pipeline)](#3-workflow)
4. [Phase 1: Quick Filter](#4-phase-1-quick-filter)
5. [Phase 2: Full Scoring (100 pts)](#5-phase-2-full-scoring-100-pts)
   - [A. Rental Yield (15 pts)](#a-rental-yield-15-pts)
   - [B. Capital Appreciation (30 pts)](#b-capital-appreciation-30-pts)
   - [C. Future Potential (20 pts)](#c-future-potential-20-pts)
   - [D. Liquidity & Exit Risk (25 pts)](#d-liquidity--exit-risk-25-pts)
   - [E. Cost Efficiency (10 pts)](#e-cost-efficiency-10-pts)
   - [F. Red Flag Deductions (-10 pts)](#f-red-flag-deductions--10-pts)
6. [ROI Calculation](#6-roi-calculation)
7. [District Discovery](#7-district-discovery)
8. [Data Sources & Integration](#8-data-sources--integration)
9. [Bias Corrections](#9-bias-corrections)
10. [Score Interpretation](#10-score-interpretation)

---

## 1. Overview

The scoring system evaluates Singapore residential properties on a **100-point scale** across five positive categories and one penalty category. The philosophy is to identify properties that balance **income generation** (rental yield), **growth** (capital appreciation), **future catalysts** (infrastructure and government development), **exit safety** (liquidity), and **holding cost efficiency** — while penalizing properties with structural red flags.

### Score Weight Summary

| Category | Weight | What It Measures |
|----------|--------|------------------|
| Rental Yield | 15 pts | Current income potential |
| Capital Appreciation | 30 pts | Historical price growth, momentum, and upside room |
| Future Potential | 20 pts | Upcoming MRT lines, government zones, transformation |
| Liquidity & Exit Risk | 25 pts | Ability to sell quickly at fair price |
| Cost Efficiency | 10 pts | Holding costs relative to value |
| Red Flags | -10 pts | Structural problems that destroy value |

**Practical score range**: 24–85 points.

---

## 2. Investment Parameters

The system is configured for a specific buyer profile:

| Parameter | Value | Notes |
|-----------|-------|-------|
| Target Price | $2,200,000 – $2,700,000 | Hard filter — outside = auto-reject |
| Hold Period | 5, 6, or 7 years | ROI computed for each |
| Buyer Type | Singapore Citizen (SC) | Affects ABSD rates |
| Property Count | 0 (first property) | 0% ABSD |
| Min Remaining Lease | 60 years | Hard filter — below = auto-reject |

These can be adjusted in `config.py`.

---

## 3. Workflow

The analysis runs in a multi-stage **hybrid algo + AI agent pipeline**:

```
District Discovery → Scraping → Quick Filter → Full Scoring → ROI → Raw Output → AI Agent Review → Final Report
```

### Stage 1: District Discovery
An AI-driven ranking of all 28 Singapore districts using macro factors (historical appreciation, liquidity, future MRT lines, government priority zones, supply constraints). This narrows the search to the 4–6 most promising districts.

### Stage 2: Scraping
PropertyGuru listings are scraped for the target districts, extracting price, PSF, bedrooms, tenure, built year, MRT proximity, coordinates, development size, facing, and more.

### Stage 3: Quick Filter (Phase 1)
A fast pre-screening pass using only scraped data. Listings scoring below **40/85** (or violating hard filters) are eliminated. This avoids wasting expensive API calls on poor candidates. Tier thresholds are **60 (Tier 1)** and **45 (Tier 2)**, but the pre-filter cutoff is intentionally lenient at 40.

### Stage 4: Full Scoring (Phase 2)
Surviving listings are scored on the full 100-point system using URA transaction data, rental estimates, geographic analysis, and infrastructure data.

### Stage 5: ROI Projection
For top-ranked properties, detailed return-on-investment projections are computed for 5/6/7-year hold periods, incorporating stamp duties, holding costs, and appreciation decay. A simple **sensitivity range** is also produced (downside/base/upside) by shifting rent and appreciation assumptions.

### Stage 6: Raw Output (for AI agent)
When `--raw` is used, the algo outputs a structured JSON file (`raw_analysis.json`) containing all computed scores, breakdowns, ROI projections, and ROI sensitivity ranges — plus empty `agent_*` fields for the AI to fill in. This is the handoff point from algo to AI.

### Stage 7: AI Agent Review
The AI agent (Claude Code) reads the raw analysis file and performs qualitative research:

1. **For each top listing**, web searches for:
   - Best/worst stacks and floor plans
   - Condo reviews, resident feedback, known issues
   - Upcoming developments or disruptions nearby
   - Rental demand patterns and tenant profiles

2. **Fills in agent fields** on each listing:
   - `agent_summary`: 2-3 sentence investment thesis
   - `agent_red_flags`: risks the algo can't detect (construction noise, reputation, micro-location)
   - `agent_catalysts`: upside factors the algo misses (popular schools, lifestyle appeal)
   - `agent_stack_notes`: which stacks/floors/facings to target or avoid
   - `agent_rental_assessment`: qualitative rental demand outlook
   - `agent_appreciation_assessment`: qualitative price trajectory view
   - `agent_score_adjustment`: bounded [-8, +8] score tweak with reason
   - `agent_appreciation_rate_pct`: (optional) if algo used fallback appreciation, AI can supply a researched rate
   - `agent_appreciation_source`: (optional) source for the AI-provided appreciation rate
   - `agent_confidence`: "high"/"medium"/"low" trust in the algo score

3. **Fills in report-level fields**:
   - `agent_executive_summary`: top picks and overall reasoning
   - `agent_market_commentary`: current market conditions and timing context
   - `agent_methodology_notes`: caveats about this analysis run

4. **Saves** the completed JSON for the final stage.

### Stage 8: Final Report
When `--from-review` is used, the generator loads the AI-reviewed JSON, applies score adjustments, re-ranks listings by adjusted total score, and produces the final markdown report with both algo data and agent insights integrated.

---

## 4. Phase 1: Quick Filter

**Purpose**: Eliminate clearly unsuitable listings cheaply (no external data lookups).

### Hard Rejects (Score = 0)
- Price outside $2.2M – $2.7M
- Remaining lease < 60 years

### Quick Scoring Components (max ~85 pts)

| Component | Max | Scoring |
|-----------|-----|---------|
| PSF Value | 20 | <$1,800: 20 · <$2,000: 15 · <$2,200: 10 · <$2,400: 5 |
| MRT Proximity | 15 | ≤300m: 15 · ≤500m: 12 · ≤800m: 8 · ≤1000m: 4 |
| Tenure Quality | 10 | 99yr ≥90yr left: 10 · ≥80yr: 8 · ≥70yr: 5 · FH/999yr: 7 |
| Property Age | 10 | ≤5yr: 10 · ≤10yr: 8 · ≤15yr: 5 · ≤20yr: 3 |
| District | 10 | Prime RCR (D3/5/14/15/19/22): 10 · Other good: 7 · Other: 5 |
| Bedrooms | 8 | 2BR: 8 · 3BR: 6 · 1BR: 4 · 4BR: 3 |
| Size Efficiency | 7 | Optimal sqft/bed ratio: 7 · Good: 5 |
| Development Size | 5 | ≥600 units: 5 · ≥400: 4 · ≥200: 3 · ≥100: 2 |

### Quick Red Flags (max -15)
- West-facing: -2
- Very old (>20yr): -5 / Old (>15yr): -3
- Small dev (<100 units): -3
- Low remaining lease (<70yr): -4

**Cutoff**: Listings scoring below **40** are dropped.

---

## 5. Phase 2: Full Scoring (100 pts)

### A. Rental Yield (15 pts)

Measures current income-generating potential. Weight is kept moderate because gross yields are mechanically compressed at the $2M+ price point (typically 3.0–3.5%).

| Sub-component | Max | How It Works |
|---------------|-----|--------------|
| Gross Yield | 6 | Estimated annual rent / purchase price |
| MRT Proximity | 4 | Distance to nearest MRT station |
| Unit Configuration | 3 | Bedroom count (2BR most rentable) |
| Tenant Pool | 2 | Proximity to CBD, hospitals, universities |

**Gross Yield Scoring**:

| Yield | Score |
|-------|-------|
| ≥4.0% | 6 (outstanding for this price range) |
| ≥3.5% | 4–5 |
| ≥3.2% | 3 |
| ≥3.0% | 1–2 |
| <3.0% | 0 |

**Rental Estimation Data Priority**:
1. Same-condo actual rental listings (exact match)
2. District median rental PSF from verified data
3. Market fallback: $3.50/sqft/month

**Tenant Pool Scoring** (proximity-based):
- Within 3km of CBD: 5 pts (raw, scaled to max 2)
- Within 2km of major hospital: 4 pts
- Within 2km of university: 4 pts
- Within 8km of city center: 3 pts
- Suburban: 2 pts

**Note:** MRT proximity is percentile-scored within the current cohort when enough data is available; otherwise it falls back to distance thresholds.

---

### B. Capital Appreciation (30 pts)

The **heaviest-weighted category**. Measures historical price growth, current momentum, and structural upside. This is where the most sophisticated analysis occurs.

| Sub-component | Points | What It Captures |
|---------------|--------|------------------|
| Appreciation Rate | 0–14 | Historical annualized price growth (confidence-adjusted) |
| Momentum | -2 to +3 | Is growth accelerating or decelerating? |
| PSF vs District Median | 0–5 | Room to appreciate (below-median = upside) |
| Tenure Quality | 0–4 | Lease value preservation |
| Property Age | 0–4 | Physical condition |

#### Appreciation Rate (0–14 pts)

Based on annualized price growth from URA transaction data:

| Annual Rate | Score |
|-------------|-------|
| ≥6.0% | 14 (outstanding) |
| ≥5.0% | 12 (excellent) |
| ≥4.0% | 10 (very good) |
| ≥3.0% | 7 (good) |
| ≥2.0% | 5 (average) |
| ≥1.0% | 2 (below average) |
| ≥0.0% | 0 (flat) |
| <0.0% | -3 (depreciating — penalty) |

**Data is fallback-chained** (first available wins):
1. URA resale-only CAGR (preferred when new-launch bias detected)
2. URA all-transaction 5-year CAGR
3. PropertyGuru transaction scraping
4. Regional baseline fallback (flagged for AI review)

**Confidence Scaling**: The rate score is multiplied by a confidence factor based on transaction count:

| Transactions | Confidence |
|-------------|------------|
| ≥50 | 100% (full) |
| ≥20 | 90% |
| ≥10 | 80% |
| <10 | 60% |

This prevents low-data projects from getting artificially high scores.

#### Momentum (-2 to +3 pts)

Compares short-term vs long-term appreciation to detect trend direction:

| Momentum | Score | Meaning |
|----------|-------|---------|
| ≥ 0.4 | +3 | Strong acceleration — prices picking up |
| ≥ 0.15 | +2 | Moderate acceleration |
| ≥ 0.0 | +1 | Stable growth |
| ≥ -0.2 | 0 | Mild deceleration |
| ≥ -0.5 | -1 | Significant slowdown |
| < -0.5 | -2 | Severe deceleration — prices cooling fast |

#### PSF vs District Median (0–5 pts)

Properties trading below their district median PSF have structural upside:

| Price/Median Ratio | Score | Interpretation |
|---------------------|-------|----------------|
| ≤ 0.85 | 5 | 15%+ below median — strong value |
| ≤ 0.95 | 4 | 5–15% below — good value |
| ≤ 1.00 | 3 | At or below median |
| ≤ 1.10 | 1 | Slightly above median |
| > 1.10 | 0 | Premium priced — limited upside |

When enough listings exist in the current cohort for that district, PSF is scored by percentile (cheaper percentile = more points), then falls back to the ratio table above.

#### Tenure (0–4 pts)

| Tenure | Score |
|--------|-------|
| 99yr leasehold, ≥90yr remaining | 4 |
| 99yr leasehold, ≥80yr remaining | 3 |
| Freehold / 999yr | 2 |
| 99yr leasehold, ≥70yr remaining | 1 |

Note: Freehold scores lower than young 99yr leasehold because 99yr properties in the target price range offer better value per dollar.

#### Property Age (0–4 pts)

Physical condition score only — data reliability is handled separately by confidence scaling.

| Age | Score |
|-----|-------|
| ≤5 years | 4 |
| ≤10 years | 3 |
| ≤15 years | 2 |
| ≤20 years | 1 |

---

### C. Future Potential (20 pts)

Measures upcoming catalysts that could drive future price growth. Forward-looking, complementing the backward-looking appreciation data.

| Sub-component | Max | Scoring |
|---------------|-----|---------|
| Future MRT Proximity | 6 | ≤500m: 6 · ≤800m: 4 · ≤1000m: 2 · ≤1500m: 1 |
| Government Zone | 6 | High priority: 6 · Medium: 4–5 · Low: 2–3 |
| Transformation | 4 | Number of future MRT lines + govt zones nearby |
| Supply Constraint | 4 | How scarce is supply in the area |

#### Future MRT Lines Tracked

Only lines that are **not yet operational** and have completion year **>= current year** are treated as “future” for scoring.

| Line | Stations | Completion |
|------|----------|------------|
| TEL (Thomson-East Coast) | 29 | 2025 (operational, excluded) |
| CRL (Cross Island) | 12 (Phase 1) | 2030 |
| JRL (Jurong Region) | 18 | 2028 |
| DTL Extension | 2 | 2026 |

#### Government Development Zones

| Zone | Districts | Priority | Score |
|------|-----------|----------|-------|
| Jurong Lake District (JLD) | D22 | High | 6 |
| Greater Southern Waterfront (GSW) | D4, D5 | High | 6 |
| Paya Lebar Central (PLC) | D14 | Medium | 5 |
| Woodlands Regional Centre (WRC) | D25 | Medium | 5 |
| Punggol Digital District (PDD) | D19 | Medium | 5 |
| Tampines Regional Centre (TRC) | D18 | Medium | 4 |
| Kallang Riverside (KR) | D12–D14 | Medium | 4 |
| one-north (ON) | D5 | Medium | 4 |
| Marina South (MS) | D1 | Medium | 4 |
| Bidadari (BD) | D13 | Low | 3 |
| Tengah (TG) | D22, D23 | Low | 2 |

#### Transformation Score (0–4 pts)
Rewards areas with multiple upcoming catalysts:
- ≥2 future MRT lines nearby: +2 pts
- 1 future MRT line: +1 pt
- ≥2 government zones: +2 pts
- 1 government zone: +1 pt

---

### D. Liquidity & Exit Risk (25 pts)

The **second-heaviest category**. A property is only a good investment if you can sell it when you need to. This is often underweighted by individual investors.

| Sub-component | Max | Scoring |
|---------------|-----|---------|
| Transaction Volume | 8 | Total txns (all available years): ≥50: 8 · ≥20: 6 · ≥10: 4 · ≥5: 2 |
| Buyer Pool Depth | 7 | very_deep: 7 · deep: 5 · moderate: 3 · shallow: 1 |
| Development Size | 5 | ≥600 units: 5 · ≥400: 4 · ≥200: 3 · ≥100: 1 |
| Price Appeal | 5 | $1.8M–$2.5M sweet spot: 5 · Below: 4 · ≤$3M: 2 · >$3M: 1 |

**Buyer Pool Depth** (replaces static "District Popularity"):
- Data-driven proxy for exit liquidity based on condo density + HDB upgrader pool + employment centers
- `very_deep`: Dense condo belt with large HDB upgrader catchment (D14, D15, D19)
- `deep`: Substantial condo stock with good upgrader pool (D03, D05, D11, D12, D16, D18, D20, D22, D23, D25, D27)
- `moderate`: Decent but smaller buyer pool (D01, D02, D04, D07, D08, D09, D10, D13, D21, D26)
- `shallow`: Few condos, niche market (D06, D17, D28)

**Why liquidity matters so much (25%)**:
- A 5–7 year hold means selling into uncertain market conditions
- Small developments and shallow buyer pools can mean months on the market
- Forced to discount = erosion of all appreciation gains
- Transaction volume is the strongest empirical signal of demand

---

### E. Cost Efficiency (10 pts)

Measures ongoing holding costs that eat into net returns.

| Sub-component | Max | Scoring |
|---------------|-----|---------|
| MCST Estimate | 4 | <$350/mo: 4 · <$450: 3 · <$550: 2 · <$650: 1 |
| Property Tax Band | 3 | AV <$50k: 3 · <$70k: 2 · else: 1 |
| Efficiency Ratio | 3 | Optimal sqft/bed: 3 · Good: 2 · else: 1 |

**MCST Rate**: Estimated at $0.35/sqft/month.

**Efficiency Ratio** — optimal sqft per bedroom:

| Unit Type | Optimal Range | Notes |
|-----------|--------------|-------|
| 1BR | 500–650 sqft total | Compact but livable |
| 2BR | 400–500 sqft/bed | 800–1000 sqft total |
| 3BR | 350–420 sqft/bed | 1050–1260 sqft total |
| 4BR+ | ≤400 sqft/bed | Efficient large units |

Oversized units pay more MCST, property tax, and renovation costs for the same rental income.

---

### F. Red Flag Deductions (-10 pts)

Structural issues that cannot be fixed and destroy long-term value.

| Red Flag | Penalty | Trigger |
|----------|---------|---------|
| West-facing | -1 | "west" in unit facing |
| Small development | -2 | < 100 units |
| Low remaining lease | -3 | < 70 years remaining |
| Very old property | -4 | Built > 20 years ago |
| Older property | -2 | Built > 15 years ago (but ≤ 20) |
| Very low PSF | -2 | PSF < $1,300 (signals problems) |
| Oversized 2BR | -2 | > 700 sqft per bedroom |
| Oversized 3BR | -2 | > 550 sqft per bedroom |

Total deduction is capped at **10 points**.

**Rationale**:
- **West-facing**: Afternoon sun makes units hot and increases cooling costs. Lower tenant preference.
- **Small developments**: Fewer amenities, higher per-unit MCST, harder to sell.
- **Low lease**: Banks restrict loans below 60yr remaining; investor pool shrinks.
- **Old properties**: Higher maintenance, less attractive to tenants, potential en-bloc uncertainty.
- **Very low PSF**: Usually signals fundamental problems (location, condition, stigma).
- **Oversized units**: Pay more in costs for no additional rental income.

---

## 6. ROI Calculation

### Appreciation Decay Model

To model mean reversion over longer hold periods, the system applies a **3% annual decay** to the appreciation rate:

```
Year 1: rate × 0.97⁰ = 100% of rate
Year 2: rate × 0.97¹ = 97% of rate
Year 3: rate × 0.97² = 94.1% of rate
Year 4: rate × 0.97³ = 91.3% of rate
Year 5: rate × 0.97⁴ = 88.5% of rate
```

Example: A 6.0% initial rate becomes 5.82% → 5.65% → 5.48% → 5.32% over 5 years.

### Sensitivity Ranges

To avoid false precision, the system computes a simple **downside/base/upside** range by shifting assumptions:
- Rent: ±10%
- Appreciation rate: ±1.5% (absolute)

### Cost Components

**Upfront Costs**:
- Purchase price
- BSD (Buyer's Stamp Duty) — progressive rates from 1% to 6%
- ABSD (Additional Buyer's Stamp Duty) — 0% for SC first property
- Legal fees: $3,500

**Annual Holding Costs**:
- Property tax (non-owner-occupied rates, 12–24% of Annual Value)
- MCST ($0.35/sqft/month × 12)
- Agent fee for rental (0.5 months/year)
- Vacancy cost (1.5 months/year)
- Repairs: $1,500/year
- Insurance: $300/year

**Exit Costs**:
- SSD (Seller's Stamp Duty) — 12%/8%/4%/0% at 1/2/3/3+ years
- Agent commission: 2% of sale price
- Legal fees: $3,000

### ROI Formula

```
Total Return = Capital Gain + Net Rental Income - Exit Costs
Total Upfront = Purchase Price + BSD + ABSD + Legal Fees

ROI % = Total Return / Total Upfront × 100
Annualized ROI = (1 + ROI/100)^(1/years) - 1
```

### Stamp Duty Schedule

**BSD** (all buyers):

| Bracket | Rate |
|---------|------|
| First $180k | 1% |
| $180k – $360k | 2% |
| $360k – $1M | 3% |
| $1M – $1.5M | 4% |
| $1.5M – $3M | 5% |
| Above $3M | 6% |

**ABSD** (Singapore Citizens):

| Property # | Rate |
|------------|------|
| 1st property | 0% |
| 2nd property | 20% |
| 3rd+ property | 30% |

**SSD** (all sellers):

| Holding Period | Rate |
|----------------|------|
| < 1 year | 12% |
| 1–2 years | 8% |
| 2–3 years | 4% |
| > 3 years | 0% |

---

## 7. District Discovery

Before individual property scoring, the system can rank all 28 Singapore districts to identify the best areas for investment. Each district is scored 0–100 using these weighted factors:

| Factor | Weight | What It Measures |
|--------|--------|------------------|
| Historical Appreciation | 30% | Past price growth (URA index data) |
| Future Infrastructure | 25% | Upcoming MRT lines and transport links |
| Government Priority | 20% | Designated development zones |
| Liquidity | 20% | Transaction volume and market depth |
| Supply Constraint | 5% | Scarcity of available units |

### Regional Classification

Singapore properties are classified into three market segments:

| Region | Typical Districts | 2024 Appreciation |
|--------|-------------------|-------------------|
| **CCR** (Core Central) | D1, D2, D6, D9, D10, D11 | 4.5%/yr |
| **RCR** (Rest of Central) | D3, D4, D5, D7, D8, D12–D15 | 5.8%/yr |
| **OCR** (Outside Central) | D16–D28 | 3.7%/yr |

### Top-Ranked Districts (from AI Discovery)

| District | Area | Score | Key Catalysts |
|----------|------|-------|---------------|
| D03 | Queenstown, Alexandra | 88.5 | GSW, TEL |
| D05 | Clementi, West Coast | 88.5 | GSW, TEL, one-north |
| D22 | Jurong, Boon Lay | 83.0 | JLD "2nd CBD", JRL |
| D19 | Punggol, Sengkang | 81.0 | Digital District, CRL |

---

## 8. Data Sources & Integration

### Primary Data Sources

| Source | Data | Method |
|--------|------|--------|
| **PropertyGuru** | Listings (price, PSF, beds, sqft, tenure, age, MRT, coordinates) | Web scraping via Patchright |
| **URA** | 5-year transaction history (price, PSF, date, sale type, tenure) | Playwright automation + CSV download |
| **PropertyGuru Rentals** | Rental listing PSF by condo/district | Web scraping |
| **LTA data.gov.sg** | Future MRT station coordinates | Static data file |
| **URA Master Plan** | Government development zones | Static data file |

### URA Data Pipeline

1. **Fetch**: Automate URA website → search by postal district → download CSV
2. **Parse**: Extract project, price, PSF, date, sale type (New Sale / Resale / Sub Sale)
3. **Calculate**: Per-project metrics:
   - Average PSF by year
   - 1-year, 3-year, 5-year appreciation rates
   - Annualized CAGR
   - Resale-only CAGR (excludes new sale transactions)
   - New sale proportion
   - Appreciation momentum
4. **Cache**: Store in `data/ura_cache.json` for fast lookups
5. **Match**: Fuzzy-match listing names to cached projects (exact → stripped → substring)

### Static Data Files

| File | Contents |
|------|----------|
| `data/future_infrastructure.json` | 61 future MRT stations with lat/lng and completion dates |
| `data/government_zones.json` | 11 government development zones with districts and priority |
| `data/district_profiles.json` | 24 districts with median PSF, rental PSF, appreciation, volume |
| `data/district_medians.json` | Rental PSF medians by district |
| `data/mrt_stations.csv` | All existing MRT stations with coordinates |
| `data/cost_parameters.json` | MCST rate, agent fees, vacancy, legal costs, etc. |

---

## 9. Bias Corrections

The scoring system applies several bias corrections to improve accuracy:

### New Launch Bias Correction

**Problem**: New launch properties show inflated appreciation because initial prices include developer discounts, progressive payment schemes, and launch premiums that compress as the project matures. A condo sold at $1,500 PSF at launch and now trading at $2,000 PSF shows "33% appreciation" but much of that is just the discount unwinding, not genuine market appreciation.

**Solution**: When ≥20% of a project's transactions are "New Sale" type:
1. Prefer resale-only CAGR (excludes all new sale transactions)
2. If resale-only data is unavailable, apply a discount to the excess appreciation above regional baseline:

| Property Age | Discount on Excess |
|-------------|-------------------|
| 0–1 years | 60% |
| 2–3 years | 45% |
| 4–5 years | 30% |
| 6–7 years | 15% |
| 8+ years | 0% (no adjustment) |

The adjusted rate is never reduced below the regional baseline (CCR: 4.5%, RCR: 5.8%, OCR: 3.7%).

### Transaction Count Confidence Scaling

**Problem**: A project with 3 transactions can show wild appreciation rates that are statistically meaningless.

**Solution**: Appreciation rate score is multiplied by a confidence factor:
- ≥50 transactions: 100%
- ≥20 transactions: 90%
- ≥10 transactions: 80%
- <10 transactions: 60%

### Appreciation Decay

**Problem**: Extrapolating recent high growth rates over a 5–7 year hold period tends to be over-optimistic. Markets mean-revert.

**Solution**: Apply 3% annual decay to the appreciation rate in ROI projections. A 6% rate becomes ~5.3% by year 5.

### Fallback Data Penalty

**Problem**: Properties with no real appreciation data must fall back to a regional baseline, which could still score moderately.

**Solution**: When the appreciation source is a fallback (`regional_baseline`), the appreciation rate score is capped at 4 points (out of 14 max) and flagged for AI review.

---

## 10. Score Interpretation

### Tier Classification

| Score Range | Tier | Recommendation |
|-------------|------|----------------|
| ≥ 60 | Tier 1 | **Recommended** — strong across multiple dimensions |
| 45–59 | Tier 2 | **Consider** — good in some areas, weaker in others |
| < 45 | Below threshold | Not recommended for investment |

### What High Scores Look Like

A **Tier 1 property** (60+) typically has:
- URA-verified appreciation ≥4%/yr with good transaction count
- Within 500m of existing or future MRT
- In a government development zone or near one
- 2–3BR in a large development (200+ units)
- 99-year lease with 85+ years remaining
- Under 10 years old
- In a prime RCR district (D3, D5, D14, D15)
- No red flags

### What the Algo Score Does NOT Capture

The algorithmic scoring intentionally omits factors that require qualitative judgment. These are handled by the **AI agent review** (Stage 7 of the pipeline):

| Factor | How the Agent Addresses It |
|--------|---------------------------|
| **Unit-level stack quality** | `agent_stack_notes` — best/worst stacks, floors, facings |
| **Renovation condition** | `agent_red_flags` — flagged if known from reviews |
| **Micro-location nuances** | `agent_red_flags` / `agent_catalysts` — noise, views, nearby facilities |
| **En-bloc potential** | `agent_catalysts` — noted if development age/size suggests en-bloc |
| **Market timing** | `agent_market_commentary` — report-level market context |
| **Developer reputation** | `agent_summary` — incorporated into investment thesis |
| **School proximity** | `agent_catalysts` — popular school catchments noted |

The agent can also apply a **score adjustment of -8 to +8 points** to reflect these qualitative factors in the final ranking, with a mandatory reason for each adjustment.

---

## Appendix: Configuration Reference

All scoring parameters are defined in `config.py` and can be adjusted for different investment profiles. Key files:

| File | Purpose |
|------|---------|
| `config.py` | All weights, thresholds, and investment parameters |
| `scoring/full_scorer.py` | Main scoring engine |
| `scoring/quick_scorer.py` | Phase 1 filter |
| `scoring/future_scorer.py` | Infrastructure and government zone scoring |
| `scoring/roi.py` | ROI calculation with appreciation decay |
| `scoring/district_scorer.py` | District-level ranking |
| `scoring/models.py` | Score data models (ScoredListing with agent fields) and total score formula |
| `scoring/raw_output.py` | Raw analysis JSON generator and reviewed file loader |
| `utils/markdown.py` | Report generator (integrates algo + agent sections) |
| `utils/costs.py` | Stamp duty and tax calculations |
| `utils/geo.py` | Distance calculations and MRT lookup |
