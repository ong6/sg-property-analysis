<!-- PROPOSAL (not active). Drafted by audit workflow 2026-06. Review before replacing the live skill at .claude/skills/analyze-development/SKILL.md -->

---
name: analyze-development
description: Flow A — evaluate a whole condo development by name or project URL. Produces a development-level verdict plus best/worst stacks, facings and floors, with a Buy/Neutral/Avoid rating per unit type. Use when the user names a condo ("analyze The Continuum", "is Grand Dunman good?") or gives a PropertyGuru project page.
---

# Analyze a Development (Flow A)

**Altitude.** The question is *"is this DEVELOPMENT worth buying into, and within
it, which stacks / facings / floors / unit types to target or avoid?"* You produce
one development-level verdict plus a Buy / Neutral / Avoid rating per unit type,
with stack/facing/floor guidance. You are the evaluator; the MMR score and
`algo_reference` are one input each, never the answer.

---

## 0. Purpose check (do this before any rating)

Every computed metric (yield, ROI, liquidity, MMR) assumes an **investment**
purpose, 5–7yr hold. If the request hints at **own-stay** — or is ambiguous —
**stop and ask the user which it is before rating.** Own-stay inverts much of the
rubric: livability, layout, facing, noise, schools, commute outweigh yield and
exit liquidity, and "oversized unit" flips from flag to feature. State explicitly
which rubric you applied. Full table: `docs/evaluation-rubric.md → Purpose check`.

---

## The evaluation priors you MUST carry in (read before forming a view)

These come from a point-in-time URA backtest (`backtest.py` / `backtest_ext.py`
over a 65,072-txn panel, 2021–2026, split-sample corrected). They are *ground
truth about what predicts forward returns* and they override portal/marketing
intuition and several of the algo's own assumptions. Do **not** re-derive them.

**What actually predicts forward PSF appreciation (use these as your real signals):**

1. **Absolute price cheapness is the #1 robust signal.** Low `log_psf` (cheap in
   absolute dollars per sqft) → forward Spearman ρ ≈ −0.26 univariate, and it is
   the **strongest marginal predictor in the multivariate model** (std β ≈ −0.14).
   Cheaper-in-absolute-dollars units appreciate more, forward. **MMR does not use
   absolute price level as a forward signal today** — so this is *your* edge to
   add: explicitly favour the cheaper absolute-PSF unit types/stacks, all else equal.
2. **Cheap-vs-district peers** (`relative_value`, `psf_vs_district`) → ρ ≈ −0.26.
   Being cheap relative to district peers wins. This is the one value signal the
   MMR already leans on (v3.3); reinforce it.
3. **Liquidity** (`txn_volume`) → ρ ≈ +0.11 univariate, but ~0 once you control
   for price. Treat liquidity as an **exit-risk / can-I-sell** filter, **not** an
   appreciation driver. Don't credit a unit type for being liquid as if it will
   appreciate more.

**What does NOT predict (stop rewarding these):**

4. **Trailing appreciation CAGR → ρ ≈ 0.** A high past CAGR is **not** a forward
   guarantee. Do not extrapolate a portal headline CAGR into your verdict; if you
   cite appreciation, tie it to a *named live catalyst*, not to history.
5. **Momentum → ρ ≈ 0, mildly contrarian.** A "hot, accelerating" project is not
   a forward edge and can be slightly negative. Do not upweight momentum.
6. **Freehold is NOT a forward edge.** Freehold → ρ ≈ −0.09 to −0.14 (freehold
   *underperforms* forward PSF appreciation), and hedonically freehold shows
   ~0% cross-sectional PSF premium once you control for region+size. Treat tenure
   as a **lease-decay / hold-horizon** consideration, **not** a reason to mark a
   freehold development up for appreciation.
7. **Regional baselines in config are stale and INVERTED.** Config
   `REGIONAL_APPRECIATION_BASELINES` says CCR 4.5 / RCR 5.8 / OCR 3.7 %/yr; the
   backtest's *realized forward* returns were **CCR +0.7 / RCR +3.5 / OCR +4.0
   %/yr** — the opposite ordering. Do **not** mark a CCR development up (or an OCR
   one down) on the basis of the region label or the config baseline. Judge the
   specific project; if anything the OCR/RCR forward tilt is mildly positive.

**Hedonic price-LEVEL drivers (these set PSF *now*; control for them when judging
"is this cheap?", do not confuse them with appreciation):**

8. **Floor: +7.7% PSF per floor tier.** Floor is a **price-level driver, not a
   forward-return driver.** A high-floor stack *should* cost more — so when you
   judge whether a stack is "cheap," **control for floor**: a high-floor unit at a
   high PSF may be fairly priced, and a low-floor unit at a low PSF may not be a
   bargain. The "best stack/floor" answer for *value* is the floor band whose PSF
   premium is smaller than the +7.7%/tier hedonic would justify.
9. **Size: −13% PSF per log-unit of sqft** (bigger = lower PSF). Never compare a
   small unit's PSF to a large one's directly; use same-size-band comparisons
   (MMR v3.2 size cohorts) and `factual_data.relative_value`.
10. **Region: RCR +17.5%, CCR +20.5% PSF vs OCR** (cross-sectional level only).
11. **Leasehold age decay ≈ 1.6%/yr (~$27/psf/yr)** — config assumes ~$40–60/psf/yr,
    i.e. **config is ~2× too steep.** When you use `relative_value`'s age
    adjustment, know it likely **over-penalizes** older leasehold; an old leasehold
    development may be *less* of a bargain than the age-adjusted block implies.
    Sanity-check against real resale-to-resale PSF rather than the heuristic slope.

**The confidence ceiling (calibrate, don't overclaim):**

12. The full composite (current MMR-like weights) reaches forward ρ ≈ +0.23 — at
    the in-sample optimal ceiling (~0.22–0.26) — **but total forward R² is only
    ~0.06–0.09.** The model, and therefore you, explain **<10% of forward-return
    variance.** This caps how confident any appreciation call can be. **A "high"
    confidence on appreciation is almost never justified by data alone** — reserve
    it for cases with a hard, near-certain catalyst (e.g. confirmed MRT < 500m
    opening on a known date). Default appreciation confidence to **low/medium** and
    say so. Liquidity, current price level, tenure facts and physical stack facts
    are knowable and can carry higher confidence; *forward appreciation cannot.*

---

## 1. Recall (references, not truth)
