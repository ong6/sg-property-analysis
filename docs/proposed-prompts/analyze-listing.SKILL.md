<!-- PROPOSAL (not active). Drafted by audit workflow 2026-06. Review before replacing the live skill at .claude/skills/analyze-listing/SKILL.md -->

---
name: analyze-listing
description: Flow B — evaluate one specific PropertyGuru listing and give a clear Buy/Neutral/Avoid call on that exact unit with confidence. Use when the user pastes a single listing URL ("analyze this: <listing link>").
---

# Analyze a Single Listing (Flow B)

**Altitude**: should the user buy THIS unit, at THIS price, today? The verdict is
the deliverable — a clear **Strong Buy / Buy / Neutral / Avoid** with a calibrated
confidence, grounded in this unit's absolute price and PSF, how cheap it is versus
its district peers, its floor/facing/stack, and the development's fundamentals.

You are the evaluator. The algo's MMR / `score_1000` is **one input, not the
answer**. Form your view from `factual_data` and your own research first; use
`algo_reference` and past evaluations only to pressure-test it (anti-anchoring).

---

## 0. Purpose check (do this before anything else)

Every computed metric (yield, ROI, liquidity, MMR) assumes an **investment**
purpose, 5–7yr hold. If the request hints at **own-stay** — or is ambiguous —
**ask the user which it is before rating.** Own-stay flips the rubric: livability,
layout, facing, noise, schools and commute outweigh yield and exit liquidity, and
"oversized unit" becomes a feature, not a flag. State explicitly which rubric you
applied. Full table: `docs/evaluation-rubric.md` → Purpose check.

---

## What the data actually predicts (priors — read before you reason)

These come from a point-in-time URA backtest (65,072 txns, 2021–2026,
split-sample corrected; `backtest.py` / `backtest_ext.py`). They are the priors
you start from. Do **not** re-derive them; do not let a portal headline override
them. The MMR weights already encode most of this — your job is to apply the same
priors in the parts of the judgement the algo can't see.

**Signals that DO predict forward PSF appreciation (in order):**
1. **Absolute price cheapness** (`log_psf` level). The single strongest robust
   forward signal (multivariate std-β ≈ −0.14). Cheap *in absolute dollars* wins.
   A genuinely low PSF/quantum unit is the #1 thing to look for. NOTE: the MMR
   does **not** use absolute price level as a forward signal today — so this is a
   judgement *you* must add on top of the score.
2. **Cheap vs district peers** (`psf_vs_district`, age-adjusted). Univariate
   rho ≈ −0.26 — strong; cheap-relative-to-peers wins. This is captured by
   `factual_data.relative_value.premium_vs_age_adjusted_median_pct` (negative =
   cheap for its age). Weight it heavily.
3. **Liquidity** (`txn_volume`). Mildly positive univariate (rho ≈ +0.11), ~0 once
   you control for price — so treat liquidity as an **exit-risk** factor (can you
   sell?), not a forward-return engine. Don't double-count it as appreciation.

**Signals that DO NOT predict (stop treating these as forward edges):**
- **Trailing appreciation CAGR**: forward rho ≈ 0. A high past CAGR is NOT a
  forward guarantee. Only credit it if you can name a *specific, live catalyst*
  (confirmed MRT <500m, en-bloc, govt zone) — otherwise it's noise.
- **Momentum**: rho ≈ 0, mildly contrarian. Accelerating recent prices is not a
  buy reason on its own.
- **Freehold**: forward rho ≈ −0.09 to −0.14 (freehold mildly *under*performs on
  forward PSF growth), and hedonically commands ~0% cross-sectional PSF premium
  once you control for region+size. Freehold is a **downside-protection /
  own-stay** quality, **not** a forward-appreciation edge. Don't pay a premium
  "because freehold" expecting it to compound faster — it historically didn't.
- **Regional baselines are stale and inverted.** Config's
  `REGIONAL_APPRECIATION_BASELINES` (CCR 4.5% / RCR 5.8% / OCR 3.7%) are the
  *opposite* of realized forward returns in-panel (CCR +0.7%/yr, RCR +3.5%,
  **OCR +4.0%**). Do not lean on the region label as a return prior. The
  rubric/profile region prose is a coarse location descriptor only — judge the
  specific project, and if anything tilt *toward* OCR/RCR value over CCR prestige
  for forward growth.

**Price-level mechanics (what sets PSF *now* — control for these when judging
whether a unit is genuinely cheap vs just structurally low-PSF):**
- **Floor**: +7.7% PSF per floor tier. A high-floor unit *should* cost more — so a
  high PSF on a high floor is not "overpriced," and a low PSF on a ground/low
  floor is not "a bargain." Normalize the asking PSF for floor tier before
  calling it cheap or dear. Floor is a real price driver the MMR does not use as a
  forward signal.
- **Size**: bigger units have lower PSF (−13% per log-unit of sqft). Never judge a
  small unit's PSF against the project-pooled median (which mixes in big units /
  penthouses) — use the size-cohort comparison and check `psf_cohort_txns` (thin
  cohort = weak signal, MMR already damps it).
- **Region**: RCR +17.5%, CCR +20.5% vs OCR on PSF level (cross-sectional, not
  forward) — this is why an OCR unit's lower PSF is partly structural.
- **Lease decay** ≈ 1.6%/yr ≈ ~$27/psf/yr — config assumes ~$40–60/psf/yr, i.e.
  roughly **2× too steep**. So the age penalty baked into `relative_value`'s
  implied-fair-PSF is probably overstating how much an older unit "should" be
  discounted; a resale flagged "cheap for its age" may be *less* cheap than it
  looks, and one flagged "dear for its age" may be fairer than it looks. Sanity-
  check the age adjustment, don't take it literally.

**The R² ceiling — calibrate confidence, never overclaim.** The full composite
reaches forward rho ≈ +0.23, which is ~the in-sample optimal ceiling (≈0.22–0.26):
the weights are near-optimal, BUT total forward **R² is only ~0.06–0.09**. The
model — and any model built on this data — explains **<10% of forward-return
variance.** That means:
- Even a clean Strong-Buy on the data deserves at most **medium-high** confidence
  on the *appreciation* leg. Reserve "high" for cases where a hard, verifiable
  catalyst or a large absolute-price discount does the work, not the score.
- Two listings that look similar on the numbers are, forward, mostly a coin flip.
  Say so. Express the verdict as a tilt, not a certainty.
- The durable edges are the *cheap* ones (absolute + relative price). Lean the
  thesis on those; treat everything else as tie-breakers.

---

## Steps

### 1. Recall — references, not truth
```bash
python invest.py --recall "<condo name>"
python invest.py --search-db "<condo name>"
```
Treat any prior rating as a fallible, possibly-stale reference. Re-verify the
current asking price and conditions before relying on it.

### 2. Gather
```bash
python invest.py --url "https://www.propertyguru.com.sg/listing/..."
```
Writes `output/run_NNN/raw_analysis.json` with one listing entry. Open it and
read `factual_data`, `relative_value`, `roi_projections`, `algo_reference`, and —
if present — `stack_profile` (the perfect-info join, see step 3).

### 3. Join the perfect-info layer (`profiles/`) and research

**Profile join first.** `raw_analysis.json` auto-joins this listing against
`profiles/<condo>.json` (the researched "perfect info" layer: stacks, facings,
views, layouts, known issues), matched by sqft/beds/facing. Check whether
`stack_profile` is present:
- **Present** → use it as ground truth for this unit's facing/view/stack quality
  and `development_known_issues`. State that you used the profile.
- **Absent or low-confidence match** → the profile is missing, stale, or didn't
  match this stack. If stack/facing/view is load-bearing for the verdict, build
  it first:
  ```bash
  python invest.py --recall "<condo>"   # confirms no usable profile
  ```
  then run the **`research-development`** flow to create
  `profiles/<condo>.json`, and re-run step 2 so the join populates. If you
  proceed without it, **record the gap** (see step 5 verification ledger).

**Then research (your view first):**
- Project reviews, build quality, defects, developer delivery/CONQUAS history,
  current legal/structural issues.
- Recent **resale-to-resale** transactions for this project AND its district
  peers: is this unit's PSF genuinely cheap, normalizing for floor tier and size?
  `factual_data.psf_premium_vs_ura_median_pct` is signed (negative = below recent
  txns — investigate *why*: motivated seller vs a defect/lease/facing problem).
- `factual_data.relative_value.premium_vs_age_adjusted_median_pct` — your primary
  value read (negative = cheap for its age vs district). Cross-check the implied
  age slope is sane (config over-discounts age ~2×, see priors above).
- Live catalysts only: confirmed future MRT <500m, govt zone, en-bloc potential.
  A trailing CAGR with no named catalyst is not a catalyst.
- This unit's floor/facing/stack vs the development's best (from `stack_profile`).

Full field guide and rating scale: `docs/evaluation-rubric.md`.

### 4. Sanity-check the score (optional, with care)
```bash
python invest.py --score '{"price":...,"sqft":...,"psf":...,"beds":...,"district":"D..","tenure":"...","project_name":"...","appreciation_rate_pct":...,"monthly_rent":...}'
```
⚠ Circularity: if you feed it your own appreciation/rent, it validates internal
consistency, **not** your research — never cite it as independent confirmation of
inputs you supplied. Prefer running it with the *algo's* numbers to see how your
override moves the score, not to launder your own assumptions.

### 5. Fill `agent_evaluation` — mandatory fields + verification ledger

In `output/run_NNN/raw_analysis.json`, fill the listing's `agent_evaluation`:
- `rating` — Strong Buy / Buy / Neutral / Avoid (Avoid = no-buy). **Honest
  verdicts are first-class**: Avoid and low confidence are valid, useful answers.
  Never manufacture a Buy for a single listing the user happened to paste.
- `confidence` — high/medium/low, **calibrated to the <10% R² ceiling**. Anchor it
  to verifiable facts (price discount, catalyst, profile-confirmed stack), not to
  how good the appreciation number looks. If the thesis rests on trailing CAGR,
  momentum, or "it's freehold," confidence is capped at medium at best.
- `rating_rationale` — renders directly under the verdict. Lead with the durable
  signals (absolute cheapness, cheap-vs-district, exit liquidity); state floor/
  size normalization; name catalysts and what would change the call.
- `summary`, `red_flags`, `catalysts`, `rental_assessment`,
  `appreciation_assessment`, `stack_notes` (this unit's stack/floor/facing vs the
  development's best).
- `appreciation_rate_override_pct` + `appreciation_rate_source` — only with
  **resale-to-resale** evidence. Do NOT override an already
  bias-adjusted rate with a portal headline CAGR (re-introduces the new-launch
  inflation the adjustment removed). If you have no resale-to-resale basis, leave
  it null rather than guessing.

**Verification ledger (required).** In `report_level.agent_methodology_notes`,
record explicitly what you could and could **not** verify: profile present/absent,
transaction depth behind the value read, whether the catalyst is confirmed, any
fallback rent (`rental.warning`), thin `psf_cohort_txns`, stale comps. This is how
the <10% ceiling stays honest. Put the one-line verdict in
`report_level.agent_executive_summary`.

### 6. Report (auto-saves evaluation memory; commit `evaluations/`)
```bash
python invest.py --from-review output/run_NNN/raw_analysis.json --output output/run_NNN/final
```

---

## Quick reference — the lens for THIS unit

| Question | Where to look | Prior |
|---|---|---|
| Is it cheap in absolute $? | `price`, `psf` vs comps | **#1 forward signal** (MMR ignores it — add it yourself) |
| Cheap vs district, age-adjusted? | `relative_value.premium_vs_age_adjusted_median_pct` | Strong (rho ≈ −0.26); negative = good |
| High past CAGR? | `appreciation.annual_rate_pct` | ~0 forward power — needs a named live catalyst to count |
| Freehold? | `tenure` | Downside protection, **not** an appreciation edge |
| Region label | `context.region` | Baselines stale/inverted — favour OCR/RCR value over CCR prestige for growth |
| Can I exit? | `liquidity.*`, price band | Exit-risk factor, not a return engine |
| Is the PSF really high/low? | `floor_level`, `sqft` | Normalize: +7.7%/floor tier, −13%/log-sqft before judging |
| How sure can I be? | everything | <10% R² → calibrate confidence; tilt, don't promise |
