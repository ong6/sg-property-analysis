<!-- PROPOSAL (not active). Drafted by audit workflow 2026-06. Review before replacing the live skill at .claude/skills/market-scan/SKILL.md -->

---
name: market-scan
description: Flow C — scan many candidates across districts and produce a rated shortlist (Buy/Neutral/Avoid each, strongest first). Use when the user asks to find condos in an area, pastes a results-page URL, or wants a comparison across districts/budgets.
---

# Market Scan (Flow C)

**Altitude**: a comparable shortlist. You are screening *many* candidates and
returning a ranked set, each with a Buy/Neutral/Avoid rating + confidence,
strongest first. This is breadth, not depth — but every shortlisted listing
still gets an honest, individually-reasoned call.

> **"No buys in this batch" is a valid — often correct — outcome.** Do not
> manufacture a winner to fill a shortlist. A scan that ends in "all Neutral,
> here's why, here's what would change my mind" is a successful scan.

---

## 0. Purpose check (do this before any rating)

Every metric the scripts compute (yield, ROI, liquidity, MMR) assumes an
**investment** purpose with a 5–7yr hold. If the request hints at **own-stay**
("for my family", "to live in", "near my kid's school") — or is **ambiguous** —
**ask the user which it is before you rate anything.** Don't guess.

Own-stay flips the rubric: livability, layout, facing, noise, schools and
commute outweigh yield and exit liquidity; "oversized unit" flips from a flag to
a feature; low-liquidity boutique blocks can be fine homes. State explicitly
which rubric you applied. Full table: `docs/evaluation-rubric.md` → Purpose check.

---

## 1. The priors you must evaluate with (read before you rate)

We ran a point-in-time URA backtest (`backtest.py` / `backtest_ext.py`) on a
**65,072-transaction panel, 2021–2026, split-sample corrected**. These are
**ground truth for how to weight evidence in a scan** — they override intuition,
portal headlines, and several stale config defaults. Internalize them:

**What actually predicts forward PSF appreciation (use these as your priors):**

| Signal | Forward power | How to use it in a scan |
|---|---|---|
| **Absolute price level** (low $/psf in dollars) | **Strongest** (ρ≈−0.26 univariate; #1 in multivariate, std-β −0.14) | **Cheap-in-absolute-dollars is the #1 robust forward signal.** Prefer the genuinely-cheaper unit, all else equal. |
| **Cheap vs district peers** (`relative_value`, age-adjusted) | Strong (ρ≈−0.26 univariate) | A unit priced below its age-adjusted district median is the next-best edge. Both "cheap" signals point the same way: **value wins.** |
| **Liquidity** (txn volume) | Weak-positive alone (ρ≈+0.11), ~0 once you control for price | Treat as a **risk/exit filter, not a return driver.** Thin liquidity is a reason to lower confidence and demand a discount, not a reason to upgrade a Buy. |
| **Trailing appreciation CAGR** | **~0** (ρ≈0) | **Do NOT reward a high past CAGR as a forward guarantee.** It is a desirability/marketing signal at best. If you cite it, name the *catalyst* that would extend it. |
| **Momentum** | **~0 / mildly contrarian** | A hot recent run is not a forward edge and may mean-revert. Don't chase it. |
| **Freehold** | **Negative** forward (ρ≈−0.09 to −0.14); ~0% cross-sectional PSF premium controlling for region+size | **Freehold is not a forward edge.** Don't upgrade a listing for tenure alone; weigh it for own-stay/legacy preference only, and don't pay a premium expecting outperformance. |

**What sets the PSF level *right now* (hedonic — control for these when judging
"is this cheap?"):**

- **Floor: +7.7% PSF per floor tier.** A high-floor unit is *supposed* to cost
  more — its higher PSF is not "expensive", and a ground/low-floor unit's lower
  PSF is not automatically "cheap". **Control for floor before calling value.**
  When two listings differ only by floor, normalize before comparing PSF.
- **Size: −13% PSF per log-unit of sqft.** Bigger = lower PSF mechanically.
  Never judge a 1BR's PSF against a project- or district-pooled median (which
  mixes in large units / penthouses) — use the same-size band (`relative_value`
  is age-adjusted; check `psf_cohort_txns` for cohort depth before trusting a
  small-unit edge).
- **Region: RCR +17.5%, CCR +20.5% PSF vs OCR.** Expected, not a bargain/penalty.
- **Lease decay: ~1.6%/yr (~$27/psf/yr)** — config assumes $40–60/psf/yr, i.e.
  **roughly 2× too steep.** When you reason about leasehold value, the real-world
  decay is gentler than the algo's age adjustment implies; don't over-penalize a
  mid-age leasehold for age alone.

**Stale / inverted config — do NOT inherit these as forward priors:**

- `REGIONAL_APPRECIATION_BASELINES` (CCR 4.5 / RCR 5.8 / OCR 3.7 %/yr) and the
  rubric's "2024 index baselines" are **inverted vs realized forward returns**:
  realized was **CCR +0.7%/yr, RCR +3.5%, OCR +4.0%.** OCR led, CCR lagged.
  Treat regional labels as *cost/segment context only*, not as a forward
  appreciation ranking. Judge the specific project and its catalyst.

**The confidence ceiling (calibrate everything to this):**

- The full MMR-like composite reaches forward ρ≈+0.23 — **near the in-sample
  optimal ceiling (~0.22–0.26).** Weights are about as good as they get.
- **But total forward R² is only ~0.06–0.09 — the model explains <10% of
  forward-return variance.** This is the single most important calibration fact.
  - **Never present a ranking as deterministic.** It's a tilt, not a forecast.
  - Reserve **high confidence** for things you can verify *factually* now
    (price vs comps, lease, floor, distance, a confirmed MRT/zone catalyst), not
    for predicted appreciation.
  - When two candidates are close on the value signals, say they're close —
    don't invent a tiebreaker the data can't support.
- **Floor and absolute-price are NOT used as forward signals inside MMR today.**
  So `algo_reference` under-weights two of the strongest real signals — **you
  must add them back in your own reasoning** (favor genuinely cheaper units;
  control for floor when reading PSF). This is a place where you should expect
  to *diverge from* the algo ranking, not defer to it.

---

## 2. Steps

### ① Recall (prior judgements)
Before forming views, surface what we already concluded:
