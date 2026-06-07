# Condo Evaluation Rubric

Read this before filling any `agent_evaluation`. The technical score is one input —
**you** make the call. Form your view from `factual_data` and your own web research
FIRST; use `algo_reference` and past evaluations to pressure-test it, not as a
starting point (anti-anchoring).

## Purpose check (before anything else)

Everything the scripts compute (yield, ROI, liquidity, MMR) assumes an
**investment** purpose with a 5–7 year hold. If the user's request suggests
**own-stay** — or is ambiguous — **ask the user which it is before rating.**

| | Investment | Own-stay |
|---|---|---|
| Top factors | Appreciation, exit liquidity, yield | Livability, layout, facing, noise, schools, commute |
| Yield/ROI | Central | Mostly irrelevant |
| Liquidity | Critical (can you exit?) | Matters only for eventual resale |
| Unit size | Efficient = better | Generous = better; "oversized" flags don't apply |
| Verdict framing | Buy/Neutral/Avoid as investment | Suitability for the household's life, plus resale safety net |

For own-stay, several algorithmic red flags invert or vanish: `oversized_unit` is a
*feature*, low yield is irrelevant, and a quiet low-liquidity boutique development
can be a fine home. Say explicitly which rubric you applied.

## Investment rubric (5–7 year hold)

### 1. Capital appreciation (most important)

From `factual_data.appreciation`:
- `annual_rate_pct` + `data_source` — `ura_*` sources are reliable; `regional_baseline`
  means no project data: research it yourself before trusting any number
- `transaction_count` / `data_confidence` — more transactions = more reliable
- `momentum` — positive = accelerating
- `raw_rate_before_bias_adjustment_pct` — if present, the system already discounted
  new-launch developer-pricing inflation. **Do not override the adjusted rate with a
  headline CAGR from a portal — that re-introduces the inflation the adjustment removed.**
  Only override with resale-to-resale evidence.

Research: recent URA/PropertyGuru transactions and direction, catalysts (MRT, govt
zones, en-bloc), developer reputation, whether launch pricing is inflated.

Context (2024 URA index baselines — verify currency before leaning on them):
CCR ~4.5%/yr, RCR ~5.8%/yr, OCR ~3.7%/yr. These are *priors, not destiny* — a
specific OCR project with a catalyst can outperform a generic RCR one. Judge the
project, not the region label.

### 2. Liquidity & exit risk

From `factual_data.liquidity`: `ura_transaction_count` (50+ strong, <10 thin),
`buyer_pool_depth`, `total_units` (400+ ideal). Your judgment: price band
($1.8M–2.5M deepest pool; >$3M thins), HDB-upgrader appeal, broad vs niche unit.

### 3. Future potential

`factual_data.future_infrastructure` — future MRT within 500m is the strongest
uplift. Govt zones by impact: JLD (D22), GSW (D4/5), Paya Lebar Central (D14),
PDD (D19), Woodlands RC (D25). CRL ~2030, JRL ~2028.

### 4. Rental yield

`rental.gross_yield_pct` — 4%+ excellent at $2M+; 3–3.5% typical. Check tenant
demand drivers (CBD, hospitals, universities, MRT) and rental oversupply from
nearby new launches. 2BR has the widest tenant pool.

### 5. Costs & taxes

`roi_projections` / `roi_sensitivity` already include BSD, ABSD, SSD, property
tax. SSD is why holds are modeled at 5–7yr.

### Relative value: new launch vs resale, age-adjusted

`factual_data.relative_value` (when district peer coverage allows) answers
"is this condo cheap or dear *for its age*?" using real URA transacted PSF:

- `new_launch_median_psf` — what new launches (≤3yr) actually transact at in
  this district: the current new-launch market price
- `implied_fair_psf_from_new_launch` — new-launch median minus the age slope
  (~$50/psf per year of age; CCR $60 / RCR $50 / OCR $40, freehold ×0.6 —
  heuristics in config, verify against the market)
- `premium_vs_new_launch_implied_pct` — subject asking PSF vs that implied
  fair value; negative = priced below what new-launch pricing implies
- `premium_vs_age_adjusted_median_pct` — vs the age-normalized median of ALL
  district peers (headline; negative = cheap for its age)

Use both: a resale can look "cheap" vs a new launch simply because it's old —
the age adjustment is what makes the comparison fair. When the block is
missing (thin peer data), research new-launch and resale PSF for the area
yourself before judging value.

### 6. Red flags — verify, don't just inherit

Algorithmic flags in `factual_data.red_flags_detected` (west facing, small dev,
low lease, old property, PSF mismatch flags) are *detections*, not verdicts —
check each. Also research what the algorithm can't see: construction defects,
noise sources, stigma, developer track record, competing launches.
`psf_premium_vs_ura_median_pct` is signed: negative = listed below recent
transactions (worth investigating why — could be motivated seller or a problem).

## Technical score (MMR)

The scorer outputs an Elo-style **MMR** (base 1500, uncapped, continuous) and its
0–1000 normalization (`score_1000`; 500 = market-typical, 650+ recommended tier).
Missing data scores neutral, never penalized. Sanity-check your evaluation with:

```bash
python invest.py --score '{"price":2300000,"sqft":958,"psf":2401,"beds":2,"district":"D15","tenure":"Freehold","total_units":420,"project_name":"The Continuum","appreciation_rate_pct":5.5,"monthly_rent":6200}'
```

⚠ If you pass `appreciation_rate_pct`/`monthly_rent`, the score takes your numbers
at face value — it validates internal consistency, **not** your research. Don't
cite it as independent confirmation of inputs you supplied.

## Rating scale

| Rating | Verdict | Meaning |
|--------|---------|---------|
| Strong Buy | 🟢 BUY | Excellent fundamentals, no major flags, clear catalysts |
| Buy | 🟢 BUY | Solid fundamentals, minor concerns |
| Neutral | 🟡 NEUTRAL | Mixed signals; strengths and concerns balance |
| Avoid | 🔴 AVOID | Poor fundamentals, major flags, or overpriced |

Always set `rating` AND `confidence`, and explain the rating in
`rating_rationale` (it renders directly under the verdict). **"No buys in this
batch" is a valid scan outcome** — don't manufacture a winner.

## agent_evaluation fields

```json
{
  "summary": "2-3 sentence investment thesis",
  "rating": "Strong Buy / Buy / Neutral / Avoid",
  "rating_rationale": "Specific reasons, numbers and facts",
  "red_flags": ["specific risk"],
  "catalysts": ["specific growth driver"],
  "rental_assessment": "...", "appreciation_assessment": "...",
  "stack_notes": "Best/worst stacks, floors, facing (development mode)",
  "confidence": "high/medium/low",
  "appreciation_rate_override_pct": 4.5,
  "appreciation_rate_source": "resale-to-resale basis only"
}
```

Report-level: `agent_executive_summary` (verdict + picks), `agent_market_commentary`,
`agent_methodology_notes` (data gaps, what you couldn't verify).

## District reference (verify before relying — written Jun 2026)

- **RCR**: D03 Queenstown (GSW, TEL), D05 Clementi (One-North), D14 Paya Lebar
  (airbase relocation), D15 East Coast (deep buyer pool)
- **OCR**: D22 Jurong (JLD "2nd CBD", JRL), D19 Punggol (PDD, CRL), D18 Tampines
  (CRL+DTL), D16 Bedok/Upper East Coast (TEL, Bayshore precinct)
- **CCR**: D09/10/11 prime — high entry cost, typically lower yield

These labels are coarse priors. The quick-scorer's district points and the regional
baselines both encode them too — don't triple-count the same prior in your own
reasoning; judge the specific project.
