<!-- PROPOSAL (not active). Drafted by audit workflow 2026-06. Review before replacing the live skill at .claude/skills/arena-cycle/SKILL.md -->

---
name: arena-cycle
description: Run the full condo arena cycle — refresh/score the listings DB, run the bracketed tournament, referee the results (mandatory), join the perfect-info profile layer, and surface champions worth a deep evaluation, with confidence calibrated to what the data can actually predict. Use when the user wants updated rankings, says "run the arena/fight", or after new listings/URA data land.
---

# Arena Cycle (score → fight → referee → deep-dive)

The arena ranks **stats**. Your job is to certify which stats deserve to rank,
correct for what the algorithm doesn't yet use, and report each champion with a
confidence that matches how little the future is actually knowable. The cycle is
**not done until you (the AI referee) have verified the results** — refereeing is
the flow, not an optional extra.

Before you touch the data, read the two priors below. They change every verdict.

---

## Prior 1 — Purpose check (do this first, every run)

All computed metrics (yield, ROI, liquidity, MMR) assume an **investment** purpose
(5–7yr hold). If the user's request suggests **own-stay** — or is ambiguous —
**ask before you rate.** Own-stay inverts the rubric (livability, layout, facing,
noise, schools, commute over yield/liquidity; "oversized unit" flips from flag to
feature). Full table: `docs/evaluation-rubric.md`. State which rubric you applied.

## Prior 2 — What the data actually predicts (baked-in backtest findings)

These come from a point-in-time URA backtest (`backtest.py` / `backtest_ext.py`)
on a 65,072-transaction panel (2021–2026, split-sample corrected). **Treat them as
ground truth — do not re-derive, and do not let a portal headline override them.**
The single most important fact: **total forward R² is only ~0.06–0.09 — the model
explains under 10% of forward-return variance.** Everything below is a weak edge in
a noisy world. Calibrate accordingly (see "Confidence calibration").

**Signals that DO predict forward PSF appreciation (in order of strength):**
- **Absolute cheapness (low `log_psf`)** — the #1 robust forward signal
  (univariate ρ≈−0.26; strongest marginal in multivariate, std_β≈−0.14).
  Cheap-in-absolute-dollars wins. The arena does **not** currently use this as a
  forward signal — *you must add it by eye*: among similar contenders, lean toward
  the one with the lower absolute PSF / lower entry price.
- **Cheap-vs-district (`psf_vs_district` / `relative_value`)** — strong
  (univariate ρ≈−0.26). A unit priced below age-adjusted district peers tends to
  appreciate more. This one MMR does use (relative-value slope was lifted in v3.3).
- **Liquidity (`txn_volume`)** — mild positive standalone (ρ≈+0.11), washes to ~0
  once you control for price. Treat depth as **exit insurance, not an appreciation
  driver** — it tells you whether you can sell, not how much you'll make.

**Signals that DO NOT predict (stop rewarding them):**
- **Trailing appreciation CAGR — forward ρ≈0.** A high past CAGR is *not* a forward
  guarantee. Never endorse a champion because its `appreciation_rate_pct` is high.
  Demand a **named, verifiable catalyst** (MRT <500m, govt zone, en-bloc) instead.
- **Momentum — ρ≈0, mildly contrarian.** Accelerating recent prices are if anything
  a slight negative. Do not treat momentum as a buy reason.
- **Freehold — forward ρ≈−0.09 to −0.14 (freehold UNDERperforms on forward PSF
  appreciation).** Hedonically, freehold carries ~0% cross-sectional PSF premium
  once you control for region + size. Freehold is a *durability/own-stay* feature,
  **not a forward investment edge** — never rank a contender up for being freehold.

**Two known config biases — correct for them manually until the code is fixed:**
- **Regional baselines are stale and INVERTED.** Config
  `REGIONAL_APPRECIATION_BASELINES` says CCR 4.5 / RCR 5.8 / OCR 3.7. Realized
  forward returns were **CCR +0.7 / RCR +3.5 / OCR +4.0%/yr** — the *opposite*
  ordering. Do not lean on the region label for appreciation; if anything OCR ≳ RCR
  ≫ CCR forward. Judge the specific project's catalyst and price, not its region.
- **Age decay is ~2× too steep.** Config `AGE_PSF_SLOPE_BY_REGION` assumes
  $40–60/psf/yr; the hedonic shows leasehold decays ~1.6%/yr ≈ **$27/psf/yr**.
  So `relative_value`'s age adjustment over-credits old leasehold units as "cheap
  for their age." When an old leasehold ranks on a big negative
  `premium_vs_age_adjusted_median_pct`, **haircut that edge** before believing it.

**Floor — the price-level control you must apply.** Floor is a major *price-setter*
(hedonic +7.7%/PSF tier) but is **not in MMR as a forward signal**. Consequence:
the arena cannot see floor, so a high-floor listing looks "expensive vs district"
(penalized) and a low-floor one looks "cheap" (rewarded) — both spuriously. When
you judge whether a contender is genuinely cheap, **control for floor**: a low-PSF
unit that is low-floor / facing a road may not be cheap at all, and a high-PSF unit
on a high floor may be fairly priced. Use `floor_level` from the listing and the
profile (Step 4) before trusting a `relative_value` edge.

**Composite verdict:** current MMR-like weights reach forward ρ≈+0.23, essentially
the in-sample ceiling (~0.22–0.26). The weights are near-optimal — **the algo is
about as good as a stats model gets here, which is still <10% R².** Your value-add
is (a) the two unused signals (absolute price, floor), (b) the manual config
corrections above, and (c) verifying the stats are real. You cannot out-predict
noise; you can avoid endorsing artifacts and overclaiming.

---

## Steps

### 1. (Optional) Refresh data
Only if the user wants new listings or the DB is stale.
