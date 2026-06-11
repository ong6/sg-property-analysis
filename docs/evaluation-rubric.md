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

## What the data actually predicts (backtest-calibrated priors, v3.5 — full 28-district panel)

Read this BEFORE the per-factor sections below — it overrides older intuitions
baked into them. From point-in-time URA backtests (`backtest.py` / `backtest_ext.py`,
**106k txns, all 28 districts**, 2021–26, mean-reversion corrected). Forward =
realized 2yr resale-PSF appreciation:

- **Cheap vs district peers is the strongest robust forward signal** (ρ≈−0.25;
  the one signal that survives controlling for region). Lead your appreciation
  view with `factual_data.relative_value.premium_vs_age_adjusted_median_pct`
  (negative = cheap for its age) and `psf_premium_vs_ura_median_pct`.
- **Region matters and the old priors were INVERTED.** Realized forward returns
  on the full panel: **OCR ≈ +3.7%/yr > RCR ≈ +3.1% ≫ CCR ≈ +1.9%** (CCR n=270 —
  confirmed at scale, not a small-sample artifact). Do **not** treat CCR/prestige
  as a forward edge. **Regime-tested (v3.5b):** across 81 rolling 2yr windows
  2004–2026, OCR out-returned CCR in *every* regime — down markets (+1.75pp/yr,
  OCR also falls less), flat (+4.9pp), bull (+1.6pp). The tilt is structural,
  not a bull-market artifact.
- **MRT proximity is a real forward signal** (std_β ≈ −0.16 with region+value
  controls — closer wins; it looks weak univariately only because central
  near-MRT stock underperformed). Weigh `mrt_distance_m` seriously.
- **District buyer-pool depth carries real content beyond region** (std_β ≈ +0.16).
  Bigger developments also tilt mildly positive (dev_size marginal ≈ +0.05).
- **Absolute price "cheapness" is mostly the region effect in disguise** — once
  region is controlled it vanishes (ridge weight ≈ 0). A low PSF is not an
  independent buy signal; it's mostly "this is OCR." Don't double-count it.
- **Trailing CAGR has little forward power (marginal ≈ +0.03) and momentum is
  unstable across samples.** Use `annual_rate_pct` / `momentum` for the
  *story/catalyst*, never as the forward number.
- **Freehold is NOT a forward edge** — exactly neutral forward on the full panel
  (marginal ≈ 0; the earlier "freehold underperforms" was a 13-district artifact).
  It carries a ~5% PSF *level* premium — already in the price you pay. Weigh
  tenure only as own-stay/optionality and short-remaining-lease *downside*.
- **Real-rent yield predicts CARRY, not price.** Joined actual URA rental
  contracts show high gross yield has ~0/mildly negative forward *price* signal
  (yield compression). A 1pp yield edge is still ~1pp/yr of total return — count
  it as income, not as an appreciation argument. Rents now come from real URA
  contracts where available (`rent_source: ura_project_bed` / `ura_project`).
- **The future-infrastructure score has NO measured forward power** (marginal ≈ 0
  district-level). "Transformation upside" is narrative until proven — don't let
  it carry a rating.
- **Liquidity is an exit-risk gate, not a forward driver** (marginal ≈ 0/negative
  once value is in). Penalize genuinely thin names; don't push a fairly-priced,
  liquid-enough unit up.
- **The ceiling is low: the full model explains ~6–13% of forward variance.**
  Small `score_1000` gaps are noise. Reserve **high** confidence for cases where
  physical/catalyst evidence (not the score) is decisive; treat near-ties as Neutral.

## Investment rubric (5–7 year hold)

### 1. Value first, then appreciation (see the priors section above)

The forward signal is **value (cheap vs district peers) + region**, NOT trailing
appreciation. Lead with value; use the appreciation rate only for the story.

From `factual_data` — value (primary):
- `relative_value.premium_vs_age_adjusted_median_pct` — negative = cheap for its
  age vs district peers (the strongest robust forward signal). Also
  `relative_value.premium_vs_new_launch_implied_pct`.
- `psf_premium_vs_ura_median_pct` (signed) and `psf_cohort_txns` — how cheap/dear
  vs same-size sales, and how many sales back that read (thin cohort → low trust).

From `factual_data.appreciation` — story/context (secondary, ~0 forward power):
- `annual_rate_pct` + `data_source` — `ura_*` reliable; `regional_baseline` means no
  project data (research it). **Trailing CAGR does not predict forward returns** —
  read it for the narrative, not as the forward number.
- `momentum` — flat-to-contrarian; informational only.
- `raw_rate_before_bias_adjustment_pct` — if present, the system already discounted
  new-launch developer-pricing inflation. **Do not override the adjusted rate with a
  portal headline CAGR.** Only override with resale-to-resale evidence (and a stated
  catalyst with a date).

Research: catalysts (MRT, govt zones, en-bloc), supply, developer reputation.

Regional forward priors (realized 2024–26, replaces the old inverted table):
**OCR ≈ +4.0%/yr ≥ RCR ≈ +3.5% ≫ CCR ≈ +0.7%.** Region is a strong but
regime-bound prior — judge the specific project, and don't treat CCR/prestige as a
forward edge (nor over-bet OCR; CCR can mean-revert).

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

**An implausible yield is a verify-first signal, not a bonus** (the mirror of
the deep-discount rule). Yield = rent ÷ price, so a mis-scraped/stale/bait
price or a non-comparable format (strata villa, dual-key, combined unit)
manufactures a spectacular yield from a perfectly real rent — e.g. a 5,349sqft
strata unit at an artifact $636psf "earned" ~8% gross on the project's genuine
URA 4BR rent. SG condo gross yields above ~5.5% are almost always a price or
rent artifact: before crediting one, (a) check the ask against the project's
own 12-month prints, (b) check `rent_source` — `ura_project_bed`/`ura_project`
are real URA contracts, `district_*`/`fallback` are synthetic constants, and
(c) check the unit's format/sqft is the thing the rent series actually priced.
v3.6.1 tanh-caps the MMR yield credit, but the dossier still displays the raw
`gross_yield_pct` — the number itself stays wrong until you verify it.

### 5. Costs & taxes

`roi_projections` / `roi_sensitivity` already include BSD, ABSD, SSD, property
tax. SSD is why holds are modeled at 5–7yr.

### Relative value: new launch vs resale, age-adjusted

`factual_data.relative_value` (when district peer coverage allows) answers
"is this condo cheap or dear *for its age*?" using real URA transacted PSF:

- `new_launch_median_psf` — what new launches (≤3yr) actually transact at in
  this district: the current new-launch market price
- `implied_fair_psf_from_new_launch` — new-launch median minus the age slope.
  v3.4: recalibrated to the hedonic ~**1.6%/yr** (≈ CCR $34 / RCR $27 / OCR $23
  per psf-yr, freehold ×0.6) — about half the old $40–60 folk-rule, which
  over-discounted old leaseholds and made them look spuriously cheap-for-age.
  Still a heuristic — verify against the market.
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

### Developer (reference, v3.3)

`factual_data.developer` (present for backfilled / previously-evaluated projects)
gives the attributed `developer` / `developer_group`, a `confidence`, and the
group's other projects in our data (`also_built_in_our_data`). Treat it as a
**research prompt, not a verdict**: confirm build quality, defect history (CONQUAS
/ news), and delivery track record yourself. `portfolio_median_appreciation_pct`
is context only — past CAGR is a weak forward signal (the v3.3 backtest), so a
strong-appreciating portfolio is not a guarantee. If the block is absent, research
the developer from scratch (it's still a real factor). Low `confidence` = verify
the attribution before relying on it.

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
