# Ranking System — Improvement Plan & Audit

**Written:** 2026-06-09 · **Method:** point-in-time URA backtests (`backtest.py`,
new `backtest_ext.py`) on the 65,072-txn panel (43k resale, 2021–2026) + a full
read of every scorer, flow, and data file + an 8-way subagent audit with
spot-verification of every high-severity claim.

This doc answers four questions you asked:
1. **What goes into a condo's valuation?** → §2 variable inventory.
2. **Does each variable actually predict price/returns?** → §3 backtest results.
3. **What's broken or missing?** → §4 flaws, §5 gaps.
4. **What do we do about it?** → §6 plan, §7 improved flow prompts.

> **One-line summary:** v3.3 got the *composite weights* roughly right (forward
> ρ≈0.23 ≈ the in-sample ceiling), but several **individual components are
> mis-signed or use stale/inverted constants**, the system's **single strongest
> predictor (absolute price cheapness) is unused**, the **arena re-inflates the
> appreciation weight the backtest killed**, and the **AI-prompt layer hands the
> model the answer (rank + score) before asking it to think.** None of this is
> fatal — but the forward signal is weak (R²<0.10), so the wins are in removing
> wrong-signed noise and calibrating confidence, not in chasing precision.

---

## Loop iterations — 2026-06-11 (10-min improvement loop, 12:49–13:45)

**Iter 1 (12:49, [FEATURE]/housekeeping):** the entire v3.5c + v3.6 +
code-review pass (~26k changed lines incl. the ui.py tabbed rewrite, poller.py,
dashboard.py) was sitting **uncommitted** — blocking clean per-iteration
commits. Validated the tree: 3 stale `test_ui` tests still passed data to
`render_index()` (now zero-arg, embeds JSON itself) — rewritten to monkeypatch
`load_rankings`; **123/123 tests pass**. `--score` smoke (Amber Park 2BR → 609,
Florence 2BR → 593, both sane); mmr_history last-3000 score_1000 mean 549 /
sd 167, no blowup. No weight changes (v3.6 config was already backtest-validated
at ρ +0.288). Committed as the baseline. *Watch item:* `TestAgeSweetSpot::
test_brand_new_scores_below_sweet_spot` failed once in a full-suite run, passed
on two re-runs (order-dependent flake — likely shared cache state; not chased).

## v3.5c — residual-gap sweep ("clear gaps until none left", 2026-06-10)

A systematic pass over every remaining known gap. Composite forward-ρ unchanged
at **+0.288**; raw-MMR mean 1512.0 = the norm center exactly (no recalibration);
122/122 tests.

| Gap | What was done |
|---|---|
| **MRT source regression (self-inflicted, caught in review)** | The v3.5b centroid backfill silently overrode `mrt_info` walking distances with straight-line estimates (`get_mrt_distance` preferred coords). Fixed priority: listing coords > portal walking distance > project centroid (`utils/geo.py`). |
| **MRT station list** | Added Punggol Coast (NE18, Dec-2024) and Hume (DT4, Feb-2025); TEL stage 4 verified present; Founders' Memorial correctly excluded (opens 2028). 142 stations. |
| **District benchmarks were 7 hand-set districts, stale** | New `build_district_benchmarks.py`: psf (12mo URA resale medians), rental_psf + per-bed rents (12mo real contracts), avg_yield for **27 districts**, written to `district_medians.json` + refreshed `district_profiles.json` price/rent fields. Old folk values were up to ~30% off (D19 rent 3.60 hand-set vs 4.77 measured). |
| **Single-listing flows scored with cohort_stats={} (audit 4.12)** | `FullScorer` now seeds cohort distributions from the 6k-listing DB when no batch is supplied (module-cached, ~0.1s). |
| **project_units join misses** | Safe normalization (apostrophes, @/at, punctuation) + slash-split aliases for the GIS layer's shared-site names ("THE PALETTE/D'NEST", units flagged `shared_site`). DB coverage 4,629 → **5,050/6,349 (80%)**. Remaining misses are new launches not yet in the dwelling-units layer (structural). |
| **Floor factors: one curve per district** | Per-project FE curve where a project's own txns carry >=40 cross-tier contrast obs (`project_floor_factors`); 132 projects now self-calibrated (median spread 7.2%, p10–p90 1.9–14.2% — real heterogeneity, e.g. Parc Esta ~0.9%/tier vs district ~3%). District FE fallback otherwise. |
| **"Single bull regime" caveat** | **PART 5h** (new): URA non-landed price index by locality, 81 rolling 2yr windows 2004–2026. **OCR out-returned CCR in every regime** — down +1.75pp/yr (and falls less), flat +4.9pp, bull +1.6pp; OCR won 43/81 quarters. The OCR>CCR tilt is structural → baselines widened CCR 3.0→2.8 / OCR 4.0→**4.2** (spread 1.4pp, still ~70% of the all-regime ~2pp average). Profiles synced. |
| **ROI rent flat + basis undisclosed** | Rent now compounds at `ROI_RENT_GROWTH_PER_YEAR` (2%/yr, config); `roi_projections` carries an explicit ALL-CASH basis note so the AI can't misread it as leveraged ROI. |

**Genuinely unclosable (documented, not hidden):** per-listing coords (centroid
is the floor until detail-page enrichment runs); new-launch units/coords (enter
the GIS layer at TOP); D24 condo rentals (none exist); `mmr_history` forward
calibration (needs ~9 more months); leveraged-ROI modelling (needs buyer
financing inputs the flows don't collect).

## v3.5b — data-gap closure: full 28-district panel, real rents, units+coords (2026-06-10)

This pass went after the §"Still open / data gaps" list directly. New data + harnesses:

**1. Full 28-district URA panel.** `fetch_ura_districts.py` pulled the 15 missing
districts (incl. all of CCR); `ura_cache.json` rebuilt → **2,293 projects** (was
1,337). A windows-1252 encoding crash (ENCHANTÉ, D11) was silently dropping a whole
district during cache builds — fixed in every CSV loader. Re-validation on the
expanded panel (106,749 txns; forward panel 1,460 rows, CCR n=270):
- Composite forward-ρ with shipped v3.5 weights: **+0.288** (beats the in-sample
  optimal linear +0.241 — the weights generalize).
- Region de-inversion CONFIRMED at scale: realized fwd **OCR +3.7 > RCR +3.1 ≫
  CCR +1.9%/yr**; compressed baselines (3.0/3.7/4.0) well calibrated, unchanged.
- **Freehold's forward underperformance was a 13-district artifact** — full panel:
  univariate −0.007, marginal +0.002 ≈ exactly neutral. v3.4's +1.0 treatment
  stands. (Hedonically freehold = +5.5% PSF LEVEL premium — in the price, not a
  return edge.)
- `future` still ≈0/negative marginal; `buyer_pool` STRENGTHENED (std_β +0.16 —
  real content beyond region; its ±7 weight is now evidence-backed).
- trailing CAGR/momentum turn mildly positive (+0.03/+0.06 marginal) on the full
  panel — supports appreciation slope 3.0 (not lower); momentum stays 0 (sign
  flips across panels = not robust).

**2. `dev_size` + `mrt` validated and turned ON in production.** New
`build_project_units.py` ingests the public URA "No of Dwelling Units" GIS layer
(data.gov.sg `d_be71daeab5930f96b90ad2857454d876`) → `data/project_units.json`:
**total units + WGS84 centroid for 3,048 projects** (spot-checked exact: Parc Esta
1,399, Florence 1,410, JadeScape 1,206, Treasure 2,203; 93% URA-cache coverage).
Backtest PART 5f (97% panel join): **MRT distance std_β −0.157** (a real forward
signal — closer wins, suppressed univariately by CCR), dev_size marginal +0.045
(direction right). Production: `FullScorer` now backfills `total_units` and
lat/lng (`coords_source: project_centroid`) from this file — the dev_size
component and the future-MRT proximity sub-score were silently dead for ~every
listing (6,348/6,349 had no units, none had coords). Exact-name join only.

**3. `yield` finally testable — REAL rents.** New `fetch_ura_rentals.py` scrapes
URA PMI rental contracts per postal district (same portal as transactions;
property type label differs: "Non-Landed Housing Development"; CSV export caps at
the 10k most-recent rows, so a `--from/--to` lease-commencement range fetches
point-in-time slices). Backtest PART 5g joins as-of-2023/24 per-project median
rental PSF to the panel: **real-rent gross yield has ~0/mildly NEGATIVE forward
PRICE signal** (univariate −0.11, marginal −0.06 on the partial join — classic
yield compression). Yield's value is **carry** (~1pp yield ≈ 1pp/yr total return),
which is what the MMR component prices for a 5-7yr hold — not appreciation.
Production: `build_rental_cache.py` → `data/rental_cache.json` (per-project,
bed-matched 12-24mo median rents); `rental_estimator` now serves them as
`ura_project_bed` (conf 0.95) / `ura_project` (0.8) ahead of the synthetic
district constants — the v3.4 yield-circularity fix now happens at the SOURCE.

**4. Shipped-score calibration harness wired.** `calibrate_forward.py` joins
`mmr_history.csv` snapshots to later realized URA PSF per project (cohort-level
Spearman + tier tables). Runs now, correctly reports the 2026-06 cohort needs
~9 months of forward window; re-run quarterly (zero new wiring needed).

**Final state of this pass (all shipped):** 122/122 tests pass. Yield slope
compressed 18.75→**10 pts/pp** (`MMR_YIELD_SLOPE_PTS_PER_PP`, PART 5g: +1pp
yield → −0.75pp/yr forward price growth → net total-return edge ~+0.25pp/pp;
compression not elimination — the price-drag estimate is one bull regime).
Real-rent coverage: **4,629/6,349 DB listings (73%)** now price rents from
actual URA contracts (`ura_project_bed` conf 0.95 / `ura_project` 0.8) — e.g.
Botanique 2BR $3,843/mo real vs $2,884 synthetic (district constant was 33%
low). dev_size + future-MRT proximity now live via `project_units.json`
(units + centroids). Full DB re-scored; raw-MMR mean 1504→1512.3 (dev_size
activation + real rents) → `MMR_NORM_CENTER` recalibrated to **1512**
(score_1000 mean 505 / sd 168, tiers stable). Composite forward-ρ on the full
28-district panel: **+0.288**. Data artifacts: `data/ura_rental_D*.csv`
(227k contracts, current + 2023-01→2024-06 as-of slices),
`data/rental_cache.json` (2,226 projects), `data/project_units.json` (3,048
projects). Remaining gaps: D24 has no condo rental contracts; per-listing
(vs project-centroid) coords; the `mmr_history` forward join needs ~9 more
months; all conclusions remain single-regime (2021–26 bull) until a downturn
is in the panel.

## v3.5 — un-tested levers validated + floor de-attenuation + joint re-fit (2026-06-10)

**Backtest first** (`backtest_ext.py` PART 5, new this pass — runs with the main
report): district-level point-in-time joins now cover the levers §3d flagged as
un-tested, plus a within-project floor measurement and an out-of-split joint re-fit.

| Lever | Result | Action |
|---|---|---|
| `future` (govt zone+transformation+supply — the EXACT production score for the coordinate-less listings that dominate the DB) | univ ρ +0.07 = region in disguise; **multivariate std_β −0.003 ≈ 0** | left at the v3.4 reined-in slope; do NOT widen (now measured, not just suspected) |
| `buyer_pool` | std_β +0.03 (weak, mostly region; +0.15 within OCR) | kept at ±7; no evidence to grow or kill |
| Property age (leasehold, age at T) | univ ρ **+0.21** (old = cheap = the value channel), but marginal ≈ −0.002 once value+region controlled | age curve left unchanged — its penalty direction is marginally supported; the univariate positive is already captured by `age_value` |
| Floor | within-project FE: **+3.12%/tier (+6.35% low→high)**; stored cache factors were **+1.44%** | **fixed — see below** |
| `yield`, `dev_size` | **still untestable** (no real per-project rental series in the repo; `total_units` absent from URA panel and 6348/6349 listings) | data gap — see §3d |
| Joint re-fit (ridge, trained T=2023.75+2024.0, **evaluated on held-out T=2024.25**) | ρ **+0.26–0.27** vs current composite +0.235 on the same split; signed weights: value & region dominate; trailing_cagr/momentum/freehold ≈ 0; txn_vol **negative** | appreciation slope 4→3, liquidity weight 10→6 (below) |

**Floor-factor de-attenuation** (`scrapers/ura_scraper.py:district_floor_factors`):
the old estimator divided each txn's PSF by its project+band median *pooled across
floors* — in cells dominated by one tier, that tier's ratio ≈ 1.0 by construction,
so the dominant tier anchored to 1 and the premium collapsed (~1.4% stored vs
~6.4% real). Replaced with a within-(project × size-band) fixed-effects slope
(immune to tier-composition anchoring, ±8% sanity clamp), and `ura_cache.json`
floor_factors were surgically recomputed for all 13 districts (now 1.1–5.6%/tier,
`basis: district_fe`). High-floor listings are no longer systematically over-penalized
(≈2–4 MMR pts swing between a low- and high-floor unit at the same ask).

**Config reweight** (both config-tunable, comments cite PART 5d):
`MMR_APPRECIATION_SLOPE` 4.0→**3.0** (trailing CAGR marginal ≈ 0/negative; kept as
a desirability proxy only) and new `MMR_TXN_VOLUME_WEIGHT` 10→**6** (volume's
univariate +0.10 is value/region in disguise; marginal β negative — it stays only
as exit-risk insurance). Composite forward-ρ with shipped weights: **+0.242**
(was +0.240); 117/117 tests pass; raw-MMR mean drift re-checked (see norm center).

**Bug fixes this pass** (each verified against code + data before fixing):
- `mmr.py` psf_value confidence: the no-size-data fallback used the project-wide
  txn count, so a size-MIXED pooled-median premium could earn full confidence while
  a confirmed-thin band was damped — inverted. Fallback now capped at 15 (≈0.75
  weight). Blast radius today: 13/1337 projects, ~2 listings — invariant fix.
- `invest.py --from-review`: the legacy /100 recompute read the obsolete
  `algo_breakdown` key, so `prev_rate_pct` was always None for modern files and the
  appreciation-score recompute silently dropped every non-rate sub-component. Now
  falls back to `factual_data.appreciation.annual_rate_pct`. (MMR path was correct.)
- `data/district_profiles.json` D3 claimed `greater_southern_waterfront` while the
  zone master says GSW = D4/D5 → D3 was over-scored ~16/100 in district discovery
  and +1 transformation for all D3 listings. Removed (D3 is GSW-adjacent, not in-zone).
- `data/government_zones.json` Tengah now includes D24 (the namesake district;
  profiles already claimed it — the two files disagreed).
- `full_scorer.py`: removed the dead `scored.costs` write (never serialized or read,
  and it used a divergent flat-2% exit-price path — a foot-gun for future readers).
- `costs.py`: owner-occupied 0% property-tax band $8k→$12k AV (IRAS 2025+; path
  currently unused by the investment flows).
- `district_scorer.py` docstring still described the pre-v3.4 inverted baselines.

**Findings investigated and NOT acted on** (don't re-litigate):
- "Young `ura_resale_only` projects get double-discounted" — REFUTED as a bug: the
  <5yr discount of resale-only rates is a documented deliberate choice (early
  "resales" are sub-sale flips bought at developer prices); the `excess<=0` guard
  bounds it, and the backtest can't adjudicate the residual. Left as designed.
- Age sweet-spot curve reshaping — rejected: marginal age effect is ≈0/slightly
  negative once value+region are controlled, so the penalty's direction stands;
  reshaping to chase the +0.21 univariate would double-count the value channel.
- Absolute-price component — still rejected (PART 5d: `log_psf` flips *positive*
  once psf_vs_dist + region are in the model; it has no stable independent sign).
- Arena Elo math, PSF guard, verdict mapping, BSD/ABSD/SSD schedules, rental-
  estimator units, URA fuzzy-join, listings-db dedup — audited, all clean.

**Still open / data gaps:** real rental series (to test `yield`), `total_units`
(to test `dev_size`), only 13/28 districts in the panel (CCR is n=47 — regional
reads are OCR/RCR-dominated), and the `mmr_history` forward-calibration join
(needs scores to age ≥1yr; started Jun 2026).

## v3.4.1 — size-aware relative value (2026-06-10)

`relative_value`/`age_value` is now **size-aware**: each district peer is compared on
its same-size-band median PSF (a 1BR vs other small units), with a per-peer fallback to
the pooled median and a `band_peer_count`/`comparison_basis` for transparency. This fixes
the recurring 1BR "artificially cheap" caveat at the source (e.g. Le Regal 1BR corrected
−41% → −15%); genuine outliers stay flagged. Re-scored + re-fought; raw-MMR mean drift
−0.4 (no recalibration); 117/117 tests pass. **This moves "make relative_value size-aware"
from Deferred → Done.** Also this pass: applied v3.4 priors to all live flow skills; added
an arena data-artifact PSF guard ($650–$6000) that removed the bogus "The Vision 4BR @
$636psf" champion; evaluated + persisted the top 200 condos (2 Strong Buy / 74 Buy / 93
Neutral / 31 Avoid) and re-fought so evals join the rankings.

## v3.4 — IMPLEMENTED (2026-06-09)

The fixes below were applied and verified (117/117 unit tests pass; composite
forward-ρ held at **+0.24**; `score_1000` re-centered to mean 502 / sd 172).

**Two audit findings were OVERTURNED by deeper backtesting during implementation
— and deliberately NOT acted on:**
- **"Add an absolute-price component" (was P0) — DROPPED.** When region is
  controlled, `log_psf`'s forward signal collapses (std_β −0.14 → −0.04). The
  "absolute price is #1" result was the *region effect in disguise* (CCR is
  expensive *and* underperformed). A separate absolute-price component would
  double-count region. The real fix was de-inverting the regional baselines +
  un-throttling relative value. *(verified: `backtest_ext` multivariate + region dummies)*
- **"Floor correction is dead code" (was P1) — REJECTED.** The scorer reads
  `ura_cache.json` (not the CSV), where **1335/1337 projects have `floor_factors`**.
  Floor IS corrected; it's just ~2× understated vs the true within-project premium
  (~1.7% stored vs ~3.7% measured in D15; the hedonic's 7.7% was between-project,
  location-confounded). Left as a minor future refinement; no double-counting
  floor component added. *(verified against the cache + raw URA)*
- Also corrected: `developer_cache.json` is **fully populated (61KB)**, not empty.

**Applied changes** (all data-anchored, regime-aware — see each file's v3.4 comments):

| Area | Change | Files |
|---|---|---|
| Regional baselines | De-inverted: CCR 4.5/RCR 5.8/OCR 3.7 → **CCR 3.0 / RCR 3.7 / OCR 4.0** (compressed, not raw-realized, to avoid regime over-fit) | `config.py`, `data/district_profiles.json` |
| Age slope | $40–60/psf/yr → **~1.6%/yr (CCR 34 / RCR 27 / OCR 23)** per hedonic | `config.py` |
| Momentum | weight 3.0 → **0** (ρ≈0/contrarian; surfaced as meta only) | `config.py`, `mmr.py` |
| Relative value | cap 20 → **28**; peer damping `/15,floor0.4` → `/10,floor0.5` | `config.py`, `mmr.py` |
| Lease/freehold | freehold +4.0 → **+1.0**; positive lease upside capped at +1 (penalty side preserved) | `mmr.py` |
| Price band | quantum-reward curve → **exit-liquidity-only** (neutral ≤$2.2M, declining above) | `mmr.py` |
| Future score | ±24 swing that penalized missing data → **upside-only (0..+11), neutral floor** | `mmr.py` |
| Recent-TEL | operational lines (e.g. TEL 2025) no longer zeroed → counted ~2yr post-opening | `future_scorer.py` |
| Arena | appreciation dim 0.30 → **0.10**; value 0.25 → **0.40** (stop contradicting MMR) | `arena.py` |
| Yield circularity | district/fallback rent confidence 0.75/0.4 → **0.5/0.15** | `mmr.py` |
| `--score` | dropped fake `transaction_count=999` (was inflating liquidity/psf confidence) | `invest.py` |
| `--from-review` | appreciation override now **carries cohort damping**; 2% fallback → regional baseline | `invest.py`, `mmr.py`, `raw_output.py` |
| Eval memory | **validate AI fields** (rating→verdict, confidence, lists) before git-commit | `eval_memory.py` |
| Property tax | stale 7-band cap 24% → **IRAS 2024+ 12/20/28/36** | `costs.py` |
| Norm center | recalibrated 1512 → **1507** | `config.py` |
| Doctrine | rubric + CLAUDE.md + `raw_output` AI instructions → corrected priors + <10% R² confidence ceiling | `docs/evaluation-rubric.md`, `CLAUDE.md`, `raw_output.py` |

**Deferred** (lower value/risk): full `--from-review` re-score (kept targeted validation);
floor-factor magnitude bump; joint ridge reweight; backtesting the `future` lever by
geo-join; ROI financing model + single exit-price helper. Improved flow-skill prompts
live in `docs/proposed-prompts/` — the live `.claude/skills/*` were being edited by a
concurrent session, so they were left untouched (reconcile before applying).

> ⚠️ **Concurrent edits detected.** Many files this session did not touch
> (`scoring/developer.py`, `district_scorer.py`, `roi.py`, `full_scorer.py`,
> `models.py`, `quick_scorer.py`, `rental_estimator.py`, the skills, several tests)
> were modified in parallel. The combined tree passes all 117 tests, but review the
> full `git diff` before committing to confirm the two passes are consistent.

---

## 0. TL;DR — the findings that matter, ranked

| # | Finding | Evidence | Severity |
|---|---------|----------|----------|
| 1 | **Absolute price cheapness (`log_psf`) is the #1 forward predictor — and it's not a scoring component.** MMR only has `price_band`, a U-shaped *buyer-pool* curve that **rewards a $2.5M unit over a $1.5M one** (wrong sign). | backtest_ext PART 3 (std_β −0.14); `mmr.py:223-232` | **P0** |
| 2 | **Regional appreciation baselines are inverted vs realized returns.** Config: CCR 4.5 > RCR 5.8 > OCR 3.7. Realized 2024–26 forward: **OCR +4.0 > RCR +3.5 >> CCR +0.7**. These feed the new-launch discount floor *and* the fallback rate *and* the AI rubric. | `config.py:257-261`; backtest_ext PART 2 | **P0** |
| 3 | **The arena re-inflates appreciation to the top fight weight (0.30)** — handing back exactly the weight v3.3 stripped from MMR. The two ranking systems are internally inconsistent. | `arena.py:24-31` | **P0** |
| 4 | **Freehold gets a flat +4.0 MMR bonus** but freehold *underperforms* forward (ρ −0.09…−0.14) and has **~0% cross-sectional PSF premium** controlling for region+size. | `mmr.py:181`; backtest_ext PART 1+3 | **P1** |
| 5 | **Age-decay slope is ~2× too steep.** Config assumes $40–60/psf/yr; hedonic says **~$27/psf/yr (~1.6%/yr)**. Over-discounts old leaseholds → inflates their "cheap for age" value score (the component v3.3 *lifted*). | `config.py:196`; hedonic Model A | **P1** |
| 6 | **The floor correction is dead code in production.** `_check_psf_overpricing` scales by `floor_factors`, but **`ura_cache.csv` has no floor column** and the bulk build path never writes one. Floor is +7.7%/tier on PSF — the dominant intra-stack driver — and it's unmodeled. | `full_scorer.py:1402-1406`; `ura_cache.csv` header | **P1** |
| 7 | **The AI is shown rank + `score_1000` + full `algo_reference` inline with every listing it must rate**, pre-sorted by score. Anti-anchoring is a sentence of prose against that. Rating distribution: **764 Neutral / 310 Buy / 244 Avoid / 4 Strong Buy** — Strong Buy is dead, ratings cluster central (anchoring signature). | `raw_output.py:228,303`; 621 evals | **P1** |
| 8 | **Recently-completed TEL stations score 0 on "future MRT" and *penalize* nearby units.** `status:"operational"` → `continue`. A unit 100m from a 2025 TEL station loses several MMR points for its connectivity *upgrade*. | `future_infrastructure.json` (TEL operational 2025); `future_scorer.py:135` | **P1** |
| 9 | **`--from-review` never re-scores** — it trusts the MMR/components embedded in a JSON the AI just edited, then commits them to git-tracked memory. Hand-edited or stale facts propagate unchecked. | `invest.py:1418-1464` | **P1** |
| 10 | **Appreciation override bypasses cohort damping** (`conf=1.0` hardcoded), re-introducing the thin-cohort over-ranking v3.2 fixed; **`--score` injects `transaction_count=999`** so the AI's own rate scores at max confidence. | `mmr.py:282-297`; `invest.py:573-579` | **P1** |
| 11 | **Forward returns are only ~6–9% explainable (R²).** The composite ceiling is ρ≈0.25. Tier copy ("650+ recommended") and "high confidence" overstate precision. | backtest_ext PART 3+4 | **P1 (framing)** |
| 12 | **The "future" MMR component is a ±24-pt un-validated swing** — now *larger* than the de-emphasized appreciation slope, and it *penalizes* data-poor (geocode-missing) listings, violating "missing data is neutral." | `mmr.py:235`; `future_scorer.py:152-309` | **P2** |
| 13 | **Property-tax brackets are stale** (cap 24%; IRAS non-owner-occupied top rate is 36% since 2024) → understates holding cost on high-rent units. | `costs.py:158-166` | **P2** |
| 14 | **Synthetic rents are double-counting value.** When rent is a district/bed constant, `gross_yield = const·12/PSF` — a re-skinned inverse-PSF term, scored at 0.75–0.4 confidence, so a cheap unit is credited as both "value" *and* "yield." | `rental_estimator.py:92-94`; `mmr.py:200-205` | **P2** |

---

## 1. How the ranking works today

```
scrape (PropertyGuru) ─┐
                       ▼
   QuickScorer  ──► drop if score<40 (scraped data only, PRE-URA)   ← silent survivorship filter
                       ▼ (survivors)
   FullScorer.score ── enrich (URA cache join, rental est, ROI, costs, district/future)
                       │   ├─ legacy /100 point breakdown (score_breakdown)   ← largely vestigial now
                       │   └─ compute_mmr() reads raw fields + breakdown → 15 continuous components → MMR
                       ▼
   raw_analysis.json ── factual_data + algo_reference(rank, score_1000, components) + empty agent_evaluation
                       ▼
   AI review (you) ── research + fill rating/confidence/rationale            ← rank+score shown inline
                       ▼
   --from-review ── trusts stored MMR (no re-score) + optional appr. override → report → eval_memory (git)
```

Plus an offline **arena** (`--fight`): turns each (condo × unit-type) into a
Fighter, runs a round-robin where each fight is decided by **majority of 6
weighted dimensions** (not by total MMR), Elo-ranks them, marks a Pareto
frontier, and emits a referee packet for you to certify.

**Two scoring systems coexist.** The legacy 0–100 scorer
(`SCORE_WEIGHT_*` in config) still populates `score_breakdown`, but MMR reads the
*raw* fields (`estimated_gross_yield`, `mrt_distance_m`, …) directly and
recomputes its own continuous components. So the legacy yield/MRT *points* are
now display-only (shown in `algo_reference`), while MMR is the real ranking. This
duality is a maintenance hazard — docstrings still describe "v2.2 weights" that
no longer drive anything (`invest.py:11-18`, `models.py:3-12`).

---

## 2. Complete valuation variable inventory + backtest coverage

Every input that moves the ranking, what it's built from, and whether the data
supports its current treatment.

| Variable | MMR component (weight) | Built from / source | Backtested? | Verdict |
|---|---|---|---|---|
| Trailing appreciation | `appreciation` (4.0/pp, conf-wt) | URA per-project CAGR | ✅ | ρ≈0 forward. Correctly de-emphasized (v3.3). Keep low. |
| Momentum | `momentum` (3.0/unit) | URA short-vs-long CAGR | ✅ | ρ≈−0.03 (flat/contrarian). **Wrong sign** — should be ~0 or negative. |
| PSF vs same-size cohort | `psf_value` (≤0.8·premium) | listing PSF vs URA size-band median (×floor factor) | ✅ (proxy) | ρ≈−0.26 univ. Strong. But **floor factor is dead** (#6) and tail is over-damped. |
| Age-adj. value vs district | `age_value` (tanh, cap ±20) | `relative_value.py` (age slope) | ✅ (proxy) | Strongest univ. signal, but **slope 2× too steep** (#5) and **cap over-suppresses** the deep-discount tail. |
| **Absolute price / PSF level** | *(none — only `price_band`)* | listing price/PSF | ✅ | **#1 marginal predictor (std_β −0.14) and UNUSED.** `price_band` has the wrong sign (#1). |
| Tenure / lease | `lease` (freehold +4.0; else (rem−80)·0.4) | tenure string | ✅ | Freehold **+4 unjustified** (#4); leasehold decay curve OK directionally. |
| Property age | `age` (3–7yr sweet-spot curve) | built_year | ⚠️ hedonic | Hedonic confirms decay but at half the assumed slope; sweet-spot curve itself untested. |
| Rental yield | `yield` (15·(gy−3.2)/0.8·conf) | `rental_estimator` waterfall | ❌ (not in panel) | Synthetic-rent circularity (#14); needs real-rent join to test. |
| MRT proximity | `mrt` (9·tanh) | geo distance | ❌ | Never tested; needs geo join. |
| Liquidity (txn volume) | `txn_volume` (10·tanh) | URA txn count | ✅ | ρ≈+0.11 univ, **~0 multivariate** — over-weighted at 10; reframe as exit-risk. |
| Buyer-pool depth | `buyer_pool` (±7) | static `district_profiles.json` label | ❌ | Fixed district constant ≈ region prior; no forward content. |
| Dev size | `dev_size` (6·tanh) | total_units | ❌ | Not in URA per-txn; untested. |
| Price band | `price_band` (±5) | listing price | ✅ | Mis-shaped buyer-pool curve; rewards higher quantum to $2.5M (#1). |
| Future infra | `future` ((score−8)·1.2, ±24) | `future_scorer` + static JSON | ❌ | **Largest un-validated lever**; penalizes missing data + recent-TEL (#8, #12). |
| Cost efficiency | `cost` (score−5) | `costs.py` (BSD/ABSD/SSD/tax) | ❌ (mechanical) | Tax brackets stale (#13); all-cash ROI undisclosed. |
| Red flags | `red_flags` (−1.5·penalty) | west/very-low-PSF/oversized/bed-sqft heuristics | ❌ | Detections, not validated; fine as caveats. |
| Region (implicit) | via baselines + district priors | config + profiles | ✅ | **Inverted** vs realized (#2); double-counted across 3 places. |
| **Floor** | *(none in MMR; dead in psf_value)* | URA Floor Level band | ✅ hedonic | **+7.7%/tier** — a top price driver, effectively unmodeled (#6). |
| Developer | reference-only in `factual_data` | `developer_cache.json` (61KB, populated) | ❌ | Rich but unwired & untested; OK as AI context. |

**Coverage scorecard:** of 19 valuation inputs, **9 are now backtested** (the
price-relevant ones), **10 are not** — and the un-tested set includes the
single largest MMR lever (`future`, ±24). Note `developer_cache.json` is **fully
populated (61KB / 80+ attributions)**, not empty — the issue is it's disconnected
from scoring, not missing.

---

## 3. Backtest results (`backtest.py` + new `backtest_ext.py`)

`backtest.py` (existing) tested 6 features univariately. I added **`backtest_ext.py`**
to cover the gaps you asked about: floor, unit size, absolute price level, a
**multivariate** model (strips collinearity), a **hedonic price model** (what
sets PSF *now*), and **composite-score validation** (does the assembled score
work, not just its parts?). Run: `python backtest_ext.py --split-sample`.

### 3a. Forward-return predictors (split-sample, mean-reversion corrected)

| Feature | Univariate ρ | Multivariate std_β | Read |
|---|---|---|---|
| **Absolute price `log_psf`** | **−0.26** | **−0.14 (strongest)** | cheap-in-dollars wins. **Unused.** |
| Cheap vs district `psf_vs_dist` | −0.26 | −0.06 | strong univ., partly subsumed by log_psf |
| Liquidity `txn_vol` | +0.11 | ~0 | mostly subsumed by value |
| Freehold | −0.09…−0.14 | −0.13 | **underperforms**, survives controls |
| New-sale share | −0.03…−0.16 | ~0 | newer projects mildly underperform fwd |
| Trailing CAGR | ~0 | ~0/neg | no forward power (v3.3 confirmed) |
| Momentum | ~0/−0.03 | ~0 | flat-to-contrarian |
| **Region** | — | — | **OCR +4.0% > RCR +3.5% >> CCR +0.7%/yr** |

### 3b. Hedonic price model — what drives PSF *level* (this is what "value" must control for)

| Term | Effect on PSF | Note |
|---|---|---|
| Floor tier | **+7.7% / tier** (low→mid→high) | MMR's value compare pools floors → systematic bias when factor absent |
| Unit size | **−13% / log-sqft** | bigger = lower PSF → size-cohort comparison is right in principle |
| Region | RCR +17.5%, CCR +20.5% vs OCR | huge level effect (not a forward-return effect) |
| Leasehold age | **−1.6%/yr (~$27/psf/yr)** | config assumes $40–60 → **~2× too steep** |
| Freehold | **−0.5% (~0)** | no cross-sectional premium controlling for region+size+(newness) |

### 3c. Composite validation — *do the assembled weights work?*

- Current MMR-like weights → forward **ρ ≈ +0.23**.
- In-sample **optimal** reweight → ρ ≈ +0.22–0.26.
- **Gap is tiny → v3.3 weights are near the achievable ceiling.** Good news.
- **But the ceiling is low: total forward R² ≈ 0.06–0.09.** <10% of forward
  variance is predictable from these features in this regime. The optimal model
  puts its biggest weights on `log_psf` and `freehold(−)` — neither used by MMR.

### 3d. Still un-tested (need joins the URA panel lacks)

`yield` (needs real rental series), `mrt` distance, `future` infra/govt-zones
(joinable by geo/district — **highest priority to test**, it's the biggest
lever), `dev_size`. **Caveat on everything:** single bull-market regime
(2021–26), all forward returns positive — these are *direction + magnitude*
reads, not laws. Re-run as the panel grows and across a downturn.

---

## 4. Flaws (with evidence & fix)

### P0 — fix first (wrong sign / internal contradiction)

**4.1 `price_band` throws away the best predictor and has the wrong sign.**
`mmr.py:223-232` gives `<$1.8M → +4.0`, `≤$2.5M → +5.0`, declining above — i.e. it
*rewards* a $2.5M unit over a $1.5M one, the opposite of the forward signal.
→ **Add a dedicated absolute-cheapness component** (region/size-detrended
`log_psf`, slope sized so a 1-SD cheaper unit earns ~+8–12 pts) and **re-shape
`price_band` into a monotone exit-liquidity penalty** (flat below ~$2.2M,
declining above) so it stops rewarding quantum.

**4.2 Regional baselines inverted.** `config.py:257-261` ranks CCR/RCR above OCR;
realized forward is the reverse. They feed the **new-launch discount floor**
(`full_scorer.py:520-531`, `adjusted_rate = max(adjusted_rate, regional_baseline)`),
the **fallback rate** (`full_scorer.py:447-451`), AND the AI rubric.
→ Re-set to realized forward means (≈ `{CCR:0.01, RCR:0.035, OCR:0.040}`) or
flatten them; document they are realized-forward, not trailing-index.

**4.3 Arena re-inflates appreciation.** `arena.py:24` weights the `appreciation`
fight dimension at **0.30 (highest)**, `value` at 0.25. Fights are decided by
majority-of-weighted-dimensions, so the arena's headline ranking leans on the
~0-power feature MMR deliberately de-weighted, and is magnitude-blind
(`TIE_MARGIN` is an absolute 0.75). → Re-weight `DIMENSIONS` to mirror the
backtest (value highest, appreciation small), or drive fights from MMR-magnitude
deltas. State explicitly that arena weights are a *second* weighting that must
track the MMR philosophy.

### P1 — high impact

**4.4 Freehold +4.0 unjustified** (`mmr.py:181`). Drop toward 0 (map freehold to
remaining-lease ≈ 99+ and run the same decay curve), or cap at +1 and label it a
non-forward own-stay preference. Also reconsider `FREEHOLD_SLOPE_FACTOR=0.6`
(`config.py:197`) which makes freehold resales look extra "cheap for age."

**4.5 Age slope 2× too steep** (`config.py:196`). Set
`AGE_PSF_SLOPE_BY_REGION` to ≈ half (`{CCR:33, RCR:27, OCR:22}`), or better,
express decay as **~1.6%/yr of local median PSF** so it auto-scales. Re-validate
with `backtest_ext.py` hedonic.

**4.6 Floor correction is dead** (`full_scorer.py:1402-1406`). `ura_cache.csv` has
no `floor_factors`; only `--fetch-ura-districts` builds them (≥20 txns/tier gate).
→ (1) Persist `floor_factors` in the **bulk** `--build-ura-cache` path + the CSV.
(2) Add a district-level fallback floor curve when per-project is thin.
(3) Consider a first-class `floor` MMR component (~+7.7%/tier, hedonic-anchored).

**4.7 Anti-anchoring is structurally undercut** (`raw_output.py:228,303`).
The AI sees `rank`, `score_1000`, and full `algo_reference` next to every listing,
pre-sorted by score. → **Two-pass packet**: emit a *blind* packet (facts only),
collect the AI's preliminary rating, *then* reveal the score for pressure-testing.
At minimum drop the leading `rank` field + the score-descending sort.

**4.8 Recent-TEL penalty** (`future_scorer.py:135,278`; TEL `operational` since
2025). → Add a `recently_operational` window (count a station as a positive
connectivity signal for ~2–3 yrs post-completion) or move present-day MRT
proximity out of the "future" score. Never let a nearby operational line push
`future_potential` below the 8.0 neutral center.

**4.9 `--from-review` trusts stored scores** (`invest.py:1418-1464`). → Re-run
`FullScorer.score` on the restored basics, then layer the override; at minimum
checksum `price·sqft≈psf` and that `algo_reference.mmr` matches its components
before committing to `evaluations/`.

**4.10 Override + `--score` circularity.** `apply_appreciation_override`
(`mmr.py:282-297`) hardcodes `conf=1.0`, bypassing `cohort_appr_factor` (can be
0.55) → thin-cohort over-credit. `--score` injects `transaction_count=999`
(`invest.py:573-579`) → confidence saturates to 1.0 and the thin-series haircut
is disabled. → Carry `cohort_appr_factor` into the override; inject a *neutral*
txn count (or a dedicated low-confidence source) for agent-provided rates.

**4.11 Confidence vs the R²<0.10 ceiling.** Soften tier copy; the rubric should
tell the AI small `score_1000` gaps are noise and reserve high confidence for
*physical/catalyst* evidence, not the score. (Prompt fix, §7.)

**4.12 Single-listing flows score with `cohort_stats={}`** (`invest.py:602`).
The most common "analyze this one listing" path runs the relative-value /
psf-vs-district signals (the best predictors) partially blind, silently. → Seed
`cohort_stats` from the URA cache district distributions for single-listing runs;
emit a note when cohort context is empty.

### P2 — correctness / hygiene

- **4.13 Property-tax brackets stale** (`costs.py:158-166`, cap 24% vs IRAS 36%
  non-owner-occupied since 2024). Update + add `last_verified` dates to all
  statutory schedules (BSD/ABSD/SSD/tax).
- **4.14 Synthetic-rent yield double-counts value** (`rental_estimator.py:92-94`;
  `mmr.py:200-205`). Zero the MMR yield contribution (or flag it derived-from-PSF)
  when `rent_source` is district/fallback; cut district weight to ~0.5, fallback to ~0.15.
- **4.15 `future` is a ±24 un-validated swing that penalizes missing data**
  (`mmr.py:235`). Make the component **0 (neutral) when sub-scores are
  defaulted/missing**; shrink the slope until the future score is backtested.
- **4.16 Region double-counted** across `district_profiles.historical_appreciation`
  (= the config baselines verbatim), the district-scorer 30% historical axis, and
  `buyer_pool` (region-correlated). Delete `historical_appreciation` from the
  profile or null it; derive `buyer_pool` from measured txn density.
- **4.17 Two ROI code paths disagree** (decay in `roi.py:107`; no decay in
  `costs.py:250`); ROI is **all-cash, undisclosed** (no financing). Centralize
  exit-price projection; label output "all-cash ROI."
- **4.18 No AI-field validation** (`eval_memory.save_evaluations_from_review`):
  a typo'd `"Buyy"` rating is committed to git. Validate `rating ∈ {4 labels}`,
  `confidence ∈ {high,med,low}`, list fields are lists.
- **4.19 Vestigial scaffolding:** `--save-eval` duplicates `--from-review`'s
  auto-save; `ura_bonus_score` "always 0"; `agent_score_adjustment` is read+clamped
  but absent from the template (a dead lever); v2.2 docstrings describe a
  superseded scorer.

---

## 5. Gaps (what's missing entirely)

1. **The two strongest signals have no scoring component** — absolute price level
   and floor. (§4.1, §4.6.) *Highest-value addition.*
2. **`future` (the biggest MMR lever) has never been validated.** It's joinable to
   the panel by geo/district — backtest it next, same discipline as v3.3 appreciation.
3. **No calibration loop.** `mmr_history.csv` logs scores but nothing joins them
   forward to realized PSF. Given R²<0.10, the tier language is unbacked. Wire a
   `backtest.py`-style join on `mmr_history` snapshots.
4. **No cross-evaluation calibration.** 621 evals → 4 Strong Buy, 764 Neutral.
   Ratings aren't commensurable across sessions; Strong Buy is dead. Define target
   rating frequencies + anchor verdicts to realized-return bands. (§7.)
5. **`profiles/` (the "perfect-info" layer) is orphaned** — 1 real profile vs 621
   evals; `raw_output` joins `stack_profile` but no skill tells the AI it exists.
   (Fixed in the proposed prompts, §7.)
6. **Quick-filter survivorship** — `min_quick_score=40` drops listings (on stale
   district whitelist + no absolute-price term) before URA enrichment; good cheap
   units in non-whitelisted OCR districts may never reach the AI. Expose the
   rejected list or add an absolute-cheapness term to QuickScore.
7. **Data freshness:** district/future/govt JSONs are 3–4 months stale and only
   *warn*; scoring proceeds on stale medians. Auto-derive from the live URA cache
   or hard-damp toward neutral past a staleness threshold.
8. **No financing / rental-growth in ROI**, no buyer-profile pass-through
   (always assumes SC first-property, 0% ABSD).

---

## 6. The plan (ordered, with concrete deltas)

**Phase 1 — stop the wrong-signed bleeding (config-only, low-risk, ~1 sitting):**
- `REGIONAL_APPRECIATION_BASELINES` → realized-forward (or flat). *(4.2)*
- `AGE_PSF_SLOPE_BY_REGION` → ~half / %-based. *(4.5)*
- Freehold `lease` bonus → ~0; reconsider `FREEHOLD_SLOPE_FACTOR`. *(4.4)*
- `MMR_MOMENTUM_WEIGHT` → 0 (surface momentum as meta only). *(table)*
- `arena.DIMENSIONS` → value-led, appreciation small. *(4.3)*
- Re-run `backtest_ext.py` after each; confirm composite ρ doesn't drop. Add tests.

**Phase 2 — add the missing signals (code + cache):**
- New `price_level` MMR component (region/size-detrended `log_psf`); re-shape
  `price_band` to liquidity-only. *(4.1)*
- Persist `floor_factors` in the bulk cache path; optional `floor` component. *(4.6)*
- Re-fit ALL component weights jointly (ridge/elastic-net) against the panel; snap
  config to the signed slopes, keeping the de-bias philosophy. *(gap #1)*

**Phase 3 — validate the un-tested levers:**
- Backtest `future` infra + govt-zones + MRT distance by geo/district join. Reweight
  or shrink `future` based on the result. *(gap #2)*
- Wire the forward-calibration join on `mmr_history`. *(gap #3)*

**Phase 4 — flow & data hygiene:**
- `--from-review` re-scores + validates AI fields. *(4.9, 4.18)*
- Fix override/`--score` confidence. *(4.10)*
- Seed `cohort_stats` for single-listing flows. *(4.12)*
- Update tax brackets + `last_verified` stamps. *(4.13)*
- Two-pass blind packet for anti-anchoring. *(4.7)*

**Phase 5 — calibration discipline:** rating-frequency targets, verdict↔return
bands, periodic distribution audit in `arena-cycle`. *(gap #4, §7.)*

---

## 7. Improved flow prompts

Drafted improved runbooks for all four ranking flows are in
**`docs/proposed-prompts/`** (marked PROPOSAL — not active). Review, then replace
the live `.claude/skills/<flow>/SKILL.md` if you approve:

- `analyze-listing.SKILL.md` · `analyze-development.SKILL.md`
- `market-scan.SKILL.md` · `arena-cycle.SKILL.md`

Common upgrades baked into all four:
1. **A "what the data actually predicts" section** ranking real forward signals
   (absolute cheapness #1, cheap-vs-district #2, liquidity as exit-risk only) and
   explicitly demoting trailing CAGR (~0), momentum (~0/contrarian), and freehold.
2. **Corrected regional priors** (OCR/RCR > CCR forward) replacing the inverted
   baselines — no more "verify currency" hand-waving over a wrong number.
3. **Hedonic value mechanics** so the AI normalizes asking PSF for floor (+7.7%/tier)
   and size (−13%/log-sqft) *before* calling a unit cheap.
4. **Use the `profiles/` perfect-info layer** (check `stack_profile`; run
   `/research-development` when absent) + a "what I couldn't verify" ledger.
5. **Confidence calibrated to the <10% R² ceiling** — verdicts are tilts, near-ties
   are Neutral, high confidence requires physical/catalyst evidence, not the score.

> ⚠️ These were drafted by audit subagents and lightly reviewed. Treat as
> first drafts; read before shipping. (One subagent also tried to add a
> `stack_profile`/`developer` join to `scoring/raw_output.py` — that code change
> was **reverted**; it's a reasonable idea but belongs in Phase 4, reviewed.)

---

## 8. What changed in this session

- **Added:** `backtest_ext.py` (the extended backtest harness — floor, size,
  absolute price, multivariate, hedonic, composite validation). Kept.
- **Added:** this doc + `docs/proposed-prompts/*` (proposals only).
- **Reverted:** subagent edits to `scoring/raw_output.py` and
  `.claude/skills/analyze-listing/SKILL.md` — restored to HEAD. No scoring
  behavior was changed. (`data/*` and `evaluations/*` changes pre-date this session.)

## 9. v3.6 — discount trust (agent-vs-score divergence audit, 2026-06-11)

**Symptom.** Agent verdicts and the /1000 ranking disagreed violently at the
top: of the 15 highest-scored projects, the agent had rated several
high-confidence **Avoid** (The Vision 938, Thomson Grand 908, Kingsford
Hillview Peak 894) or mis-scrape **Neutral** (Eight Riversuites 945).

**Root cause.** The ranking's dominant signal is cheap-vs-comps, and the
biggest "discounts" in a scraped DB are artifacts: mis-scraped sqft/beds
(700–1086 sqft "1BRs"), strata villas/terraces benchmarked against apartment
medians, stale/bait prices ~30% below trailing prints. The fake cheapness
earned up to +28 (`age_value`, tanh-capped) **plus an uncapped `psf_value`**
(a −60% artifact earned +48 raw), while `very_low_psf` /
`bedroom_sqft_mismatch` cost only −3/−6 as red flags. Net: artifacts float to
ranks #1–15, where the agent (which web-verifies) correctly shoots them down.

**Fix** (`config.py` `MMR_DISCOUNT_*`, `scoring/mmr.py`):
1. **Trust knee** — discount beyond −25% vs verified comps earns marginal
   credit at 25% (`_knee_discount`, applied to both `psf_value` premium and
   `age_value` rel-premium).
2. **psf_value tanh cap** at ±28 (same idiom/cap as `age_value`).
3. **Suspect-discount damp** — when `bedroom_sqft_mismatch` fires or a deep
   discount rests on a thin same-size cohort (< `MIN_BAND_TXNS`), the positive
   side of both value components retains 25%. Premiums stay fully penalized.

**Validation.** 122/122 tests; full-DB rescore mean 499 (no norm shift);
`backtest_ext.py --split-sample` composite forward-ρ unchanged at **+0.288**
(URA panel data is clean — the rule only disarms scraped-listing artifacts).
After rescore the top-15 is 13 Buy / 2 Strong Buy / 1 Neutral; offenders fell
to 702–846. Residual divergence is the *intended* kind: qualitative facts the
algo can't see (developer reputation, defects, format liquidity) and honest
judgment differences (e.g. Hillview Park: agent Buy on freehold+MRT, score 229
on 31-yr age — both defensible readings of the same facts).

## Appendix — methodology caveats

- **One regime.** 2021–26 was a uniform up-market; every forward return is
  positive. Signs and magnitudes here describe *this* regime. Freehold's forward
  underperformance especially may be a "leasehold new-launch ramp outpaced
  freehold resale in a bull run" artifact — re-test across a flat/down market
  before treating it as structural. Don't over-fit.
- **PSF-median index.** Backtests use per-project median resale PSF over 12-mo
  windows (composition shift across sizes is partly controlled via
  `--size-control`). Resale + sub-sale only.
- **Hedonic freehold ~0** is *cross-sectional* (controlling region+size, not age,
  since freehold age isn't in the tenure string) — read it as "freehold carries
  no automatic PSF premium," not "freehold is worthless."
- **Re-validation is the whole point.** Re-run `python backtest_ext.py
  --split-sample` whenever the URA panel grows or you change a weight. The harness
  is the source of truth, not the constants.
