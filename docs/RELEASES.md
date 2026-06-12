# MMR Release History (v3.0 – v3.9)

Condensed change log + measured evidence for the scoring algorithm. The
backtest harnesses (`backtest.py`, `backtest_ext.py`) are the source of truth,
not the constants — re-run `python backtest_ext.py` whenever the URA panel
grows or a weight changes. Current operating rules live in `CLAUDE.md` and
`docs/evaluation-rubric.md`; this file is the *why we got here*.

## Pre-v3 milestones

- **2026-02** — Initial release: PropertyGuru scraper + URA enrichment + legacy /100 scorer (now display-only).
- **2026-06-02** — Repo refresh: Claude Code flows (analyze-development / analyze-listing / market-scan), `--score` tool, git-tracked evaluation memory, listings DB/sheet.

## v3.0 / v3.1 — De-bias overhaul + referee learnings (2026-06-06/08)

**What changed:**
- New `scoring/mmr.py`: Elo-style continuous, uncapped (base 1500), logistic-normalized to `score_1000`; missing data = neutral, never penalized.
- Relative-value framework (`scoring/relative_value.py`): age-adjusts district peers' URA PSF (then ~$50/psf/yr flat; superseded by v3.7 piecewise).
- Arena `--fight` (pairwise tournament, per-bedroom brackets) + mandatory AI-referee protocol; young-resale guard.
- v3.1: referee learnings baked in — boutique-volatility haircut (thin+unstable appreciation series → 0.7× confidence), psf_value scaled by txn reliability, age_value scaled by peer count.

**Why / evidence:** bias bugs (null liquidity keys, dead psf_premium pipeline, 0%→2% appreciation coercion); first referee runs found the penthouse artifact (oversized units read as cheap PSF). Pattern established: **when the referee finds a systematic artifact class, fix the metric, don't demote forever.**

## v3.2 — Size-aware PSF + thin-cohort damping (2026-06-08)

**What changed:**
- URA cache emits per-size-band median PSF + txn count; listings compare to *same-size* transactions, not the project-pooled median.
- Cohort damping when same-size cohort < `MIN_BAND_TXNS` (5): appreciation floor 0.55, liquidity floor 0.40; no size data = neutral.
- Per-district `floor_factors` applied in `_check_psf_overpricing`.

**Why / evidence:** The Luxurie 452sqft 1BR inherited the building's 7.31% CAGR + 184-txn liquidity and ranked ~#10 (841→737 post-fix). De-confounded floor effect: most of the raw floor-PSF gap was actually size.

## v3.3 — Backtest-driven reweight (2026-06-08)

**What changed:**
- New `backtest.py`: point-in-time URA panel replay (~65k txns, 2021–26, 13 districts) — as-of-T features vs realized forward resale PSF.
- Reweight: appreciation slope 7.5→4.0/pp, momentum 8.0→3.0, relative-value 0.4→0.8 tanh-capped ±20.

**Why / evidence:** trailing appreciation ρ≈+0.06 (~0 forward power), momentum ρ≈−0.03 (flat/contrarian), cheap-vs-district ρ≈−0.24 (strongest, right sign). Re-rank was real: 23% of 6,338 listings changed tier.

## v3.4 — Audit fix pass: de-inverted baselines, value un-throttled (2026-06-09)

**What changed:**
- Regional baselines **de-inverted**: CCR 4.5/RCR 5.8/OCR 3.7 → CCR 3.0 / RCR 3.7 / OCR 4.0 (compressed; later widened in v3.5c). Momentum weight → **0**. Relative-value cap 20→**28**. Freehold bonus +4.0→**+1.0**. Age slope halved to ~1.6%/yr (superseded by v3.7).
- `price_band` reshaped to exit-liquidity-only; `future` made upside-only (stops penalizing data-poor listings).
- Arena de-contradicted: appreciation dim 0.30→0.10, value 0.25→0.40. Circularity fixes (`--score` fake txn count dropped; synthetic-rent yield confidence cut). IRAS 2024+ tax brackets.

**Why / evidence:** realized forward **OCR > RCR ≫ CCR** (old table was backwards); total forward **R² < 0.10** — confidence ceiling written into rubric. Two audit P0s *overturned* during implementation: absolute-price component dropped (region in disguise, std_β −0.04 controlled); "floor correction is dead code" rejected (1,335/1,337 cache projects have floor_factors).

## v3.4.1 — Size-aware relative value (2026-06-10)

Relative/age value now compares each district peer on its same-size-band median PSF; arena PSF data-artifact guard ($650–$6,000). Fixed the recurring "1BR artificially cheap" artifact at source (Le Regal 1BR −41%→−15%).

## v3.5 — Untested levers measured + floor de-attenuation (2026-06-10)

**What changed:**
- `backtest_ext.py` measured the remaining levers; floor-factor estimator replaced with within-(project × size-band) fixed-effects slope (±8% clamp), recomputed for 13 districts.
- Appreciation slope 4→**3**, txn-volume weight 10→**6** (exit-risk insurance only).

**Why / evidence:** `future` multivariate std_β ≈ 0 (region in disguise); floor within-project FE **+3.12%/tier** vs ~1.44% stored (~2× attenuated); joint ridge re-fit on held-out split confirms value & region dominate; trailing CAGR/momentum/freehold ≈ 0; txn_vol *negative* marginal.

## v3.5b — Data-gap closure: 28-district panel + real rents (2026-06-10)

**What changed:**
- Full 28-district URA panel (2,293 projects, 106,749 txns; fixed a windows-1252 loader crash that silently dropped D11).
- **Real rents**: `fetch_ura_rentals.py` (227k contracts) → `rental_cache.json`; estimator serves `ura_project_bed` (conf 0.95) ahead of synthetic constants — 73% DB coverage. Yield slope 18.75→**10**.
- `project_units.json` (3,048 projects, units + centroids) turned dev_size and MRT-proximity ON (both silently dead before). `calibrate_forward.py` harness wired. Norm center →1512.

**Why / evidence:** composite forward-ρ **+0.288** (n=1,460, CCR n=270). *[Corrected by the Jun-2026 audit: the "beats in-sample optimal +0.241, weights generalize" claim was a sample-composition artifact — apples-to-apples the config scores +0.225 < +0.241, splits overlap ~87.5% (effective n ≈ 563 projects), and no temporal out-of-sample exists; treat ρ ≈ +0.29 as an in-sample single-regime association. See docs/AUDIT_JUN2026.md P0-1.]* Region de-inversion confirmed at scale (realized fwd OCR +3.7 > RCR +3.1 ≫ CCR +1.9). **Freehold forward-neutral** on the full panel (the 13-district underperformance was an artifact; it's a ~5.5% PSF *level* premium, not a return edge). `buyer_pool` strengthened (std_β +0.16); MRT distance std_β −0.157 (real signal).

## v3.5c — Residual-gap sweep + regime check (2026-06-10)

**What changed:**
- MRT source-priority fixed (listing coords > portal walk distance > centroid); 142-station list; measured district benchmarks for 27 districts (old hand-set values up to ~30% off); single-listing flows seed cohort stats from the 6k DB; per-project floor FE curves where ≥40 contrast obs; ROI rent compounds 2%/yr.
- **Regime check:** 81 rolling 2yr windows 2004–26 — OCR out-returned CCR in *every* regime → baselines widened to **CCR 2.8 / RCR 3.7 / OCR 4.2** (current config values).

**Why / evidence:** ρ unchanged +0.288; the OCR>CCR tilt is structural, not a bull artifact; spread kept at ~70% of the all-regime average for mean-reversion headroom.

## v3.6 — Discount trust (2026-06-11)

**What changed (`MMR_DISCOUNT_*`):**
- **Trust knee:** discounts deeper than **−25% vs verified comps** earn marginal credit at 25% (psf_value and age_value).
- psf_value tanh-capped ±28 (was the last *uncapped* value channel: a −60% artifact earned +48 raw).
- **Suspect damp:** `bedroom_sqft_mismatch` or deep discount on a thin same-size cohort ⇒ positive side of both value components retains 25%; premiums stay fully penalized.

**Why / evidence:** agent-vs-score divergence audit — every audited top-15 was a fake-discount artifact (mis-scraped 700–1,086sqft "1BRs", strata villas vs apartment medians, stale/bait asks ~30% under prints). Post-fix top-15: 13 Buy / 2 Strong Buy / 1 Neutral. Backtest unchanged (ρ +0.288 — URA panel is clean; the rule only disarms scraped-listing artifacts). **Doctrine: a still-extreme discount that survives damping is a verify-first signal, not value.**

## v3.6.1 — Yield cap (2026-06-11)

`MMR_YIELD_CAP = 20` (tanh) — the last uncapped channel (fake-cheap price ÷ real URA rent manufactured ~8% yields worth +46.7 pts; The Vision 696→465). Realistic 2.5–5% yields retain ≥80–97% credit. Plus: yield-trust rule in rubric (>~5.5% gross ⇒ verify), `data_trust` block in raw_output, stale-eval tagging (`PRIORS_FIXED_DATE`), `--eval-stats`; 19 stale evals re-judged, **0 pre-prior Buys survived**.

## v3.6.2 — Rent-sqft cap (2026-06-11)

`rental_estimator.py`: for bed-matched rent sources, sqft is capped at **1.25× RENT_TYPICAL_SQFT_BY_BEDS** ({1:550, 2:800, 3:1150, 4:1500, 5:1900}). Kills the upstream artifact where bed-derived psf rates × oversized/strata sqft manufactured rent (Suites @ Katong 807sqft "1BR": $4,963→$4,228/mo).

## v3.7 — Age recalibration: piecewise curve + return reshape (2026-06-11)

**What changed:**
- **Level:** `AGE_PSF_DECAY_SEGMENTS` piecewise decay replaces flat $/yr: ~3.0%/yr ages 0–10 (≈$50/psf/yr), plateau 10–15 (0.5%/yr), 2.6%/yr at 15–20, 1.0% at 20–30, 1.7% after (multiplicative, district-FE hedonic; freehold ×0.6 retained).
- **Returns:** MMR age curve — ramp 0→5 pts over 0–7yr, **plateau 7–30yr**, mild −0.35/yr after 30 (leasehold decay stays the lease component's job — no double-count).
- Norm center 1512→**1516**.

**Why / evidence:** measured on 16.6k leasehold resales, all 3 regions agree — the old flat ~1.6%/yr under-adjusted new-vs-old ~2× inside 0–10yr. Forward 2yr returns (controlled, n=956): 0–5yr cohort **+1.27%/yr (worst** — still amortizing launch premium) vs +3.3–4.1%/yr for 5–30yr; only 30+ slows. ρ +0.288 unchanged.

## v3.8 — Ground-floor-stack fix: tight same-size comps (2026-06-11, user-reported)

**What changed (`full_scorer.py`, `mmr.py`):**
- **Tight comps:** raw `data/ura_district_D*.csv` prints, **±7% sqft, 24mo window**, blended into the PSF benchmark by print count (n/MIN_BAND_TXNS); tight n becomes the v3.2 damping cohort.
- **Print-contradiction trust rule:** `stack_premium_pct` > +5% ⇒ suspect (positive psf_value AND age_value retain 25%); `ask_above_own_stack_prints` red flag at >+10%; `stack_low_floor_share` exported.

**Why / evidence:** ground-floor PES stacks (patio sqft prices cheap) pooled into coarse size bands read as −18% "cheap" while asking **+20% above their own stack's prints** — 13 of top-50 were low-floor stacks → **13→1** post-fix; genuine deep-cohort discounts unchanged. Listing-side only — backtest never touches this path; ρ +0.288 stands.

## v3.9 — Below-distribution trust (2026-06-11, mirror artifact)

**What changed:** `ask_below_stack_prints` red flag + suspect damp when the ask is below the **10th percentile of a deep (n≥8) recent same-size print set**. Asks inside p10–p50 untouched — that's the genuine-deal zone the ranking exists to find. Fresh UI dedupes same-unit multi-agent rows (×N badge).

**Why / evidence:** the next ranking tier was double-volume lofts / bait asks (710sf "1BR" loft asking $1,504 vs 29 prints all ≥$1,723 — void counts in strata sqft like PES patios); real sellers don't price 15–20% under every recent print, and the ranking *selects* for asks that do. Post-fix top-10 asks sit at pctile 10–38 of their own print distributions. Comps lesson encoded: **always compare vs recent (24mo) prints** — all-time prints falsely accused Archipelago (+16% stale vs −3% recent).

## Open items / unvalidated levers

**The Jun-2026 full-system audit (`docs/AUDIT_JUN2026.md`) is the current
prioritized fix queue** — it supersedes and extends the items below.

- **Single-regime caveat (everything):** all backtest reads are 2021–26 bull; every forward return was positive. Re-run `backtest_ext.py` as the panel grows and across a downturn.
- **Model ceiling:** composite ρ +0.288, forward **R² < 0.10** — small `score_1000` gaps are noise; high confidence requires physical/catalyst evidence.
- **Shipped-score forward calibration:** `calibrate_forward.py` wired but meaningful only from ~mid-2027 (re-run quarterly).
- **`yield` component:** carry estimate from one bull regime; strata-vs-livable-area channel (PES/loft sqft inflation) has no data field — parked behind the v3.6.2 cap.
- **`dev_size`:** marginal +0.045 (direction right, weak). **`future` infra:** measured ≈0/negative — kept upside-only; do not widen.
- **Floor data coverage:** `floor_level` null on bulk-scraped listings (detail-page enrichment only); per-listing coords likewise (project centroid is the floor).
- **Freehold ×0.6 age-slope heuristic:** unmeasured (age not derivable for freehold txns).
- **15–20yr age-decay leg:** possibly a 2006–11 vintage-cohort effect (kept — for *level*, vintage IS pricing).
- **Stale/bait asks just inside the −25% knee:** not actioned (would damp genuine 20–25% discounts) — rubric verify-first rule covers it.
- **Stale evaluations:** pre-2026-06-10 evals carry the recall warning — re-judge opportunistically (all re-judged so far went against the stale eval). **Rating calibration:** Strong Buy ~0.3% of evals (dead label) — monitor via `--eval-stats`.
- **Quick-filter survivorship:** `min_quick_score=40` pre-URA drop, disclosed but unfixed. **Leveraged-ROI/financing model:** all-cash only. **D24 rentals:** none exist (structural).
