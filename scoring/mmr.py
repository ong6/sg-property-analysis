"""MMR rating system (v3) — continuous, uncapped, then normalized to /1000.

Why this exists: the legacy 100-point system bucketed every metric into
integer points and clamped each category, which compressed all real-world
listings into a ~32-68 band. The MMR layer replaces buckets with continuous
functions and removes category caps, so differences between listings are
actually expressed. The raw MMR is unbounded (Elo-style, centered at 1500);
a logistic transform maps it onto a stable 0-1000 display scale.

Design rules (anti-bias):
- Missing data contributes 0 (neutral), it is never treated as bad data.
- Price-vs-market is signed: a discount adds, a premium subtracts (the legacy
  scorer only ever rewarded discounts). NOTE — since v3.6 the two sides are
  deliberately ASYMMETRIC: discounts past MMR_DISCOUNT_TRUST_KNEE_PCT are
  knee-compressed, and when the reading is suspect (bed/sqft mismatch, thin
  cohort, or own-stack prints contradicting the discount) the POSITIVE side is
  damped to MMR_SUSPECT_VALUE_FACTOR — premiums are never damped. Cheapness is
  treated as verify-first; dearness at face value.
- No double counting: age, lease, dev size and PSF premium are continuous
  components here, so only red flags NOT already expressed continuously
  (west facing, suspicious low PSF, oversized layout, bed/sqft mismatch)
  carry an extra penalty.
- Appreciation confidence scales continuously with transaction count;
  baseline/default rates get a fixed low confidence instead of a cliff cap.
- v3.2 size-cohort awareness: PSF is compared against SAME-SIZE transactions
  (a 1BR vs other small units, not the project's pooled median that mixes in
  penthouses), and a unit whose own size-cohort barely trades can only
  partially claim the project's pooled appreciation/liquidity — so a thin
  high-PSF unit no longer inherits a big project's strength wholesale.
"""

import math
from typing import Any, Optional

try:
    from config import (
        MMR_BASE,
        MMR_NORM_CENTER,
        MMR_NORM_SCALE,
        MIN_BAND_TXNS,
        COHORT_FLOOR_APPRECIATION,
        COHORT_FLOOR_LIQUIDITY,
        MMR_APPRECIATION_SLOPE,
        MMR_APPRECIATION_CENTER_PCT,
        MMR_MOMENTUM_WEIGHT,
        MMR_RELVALUE_SLOPE,
        MMR_RELVALUE_CAP,
        MMR_TXN_VOLUME_WEIGHT,
        MMR_YIELD_SLOPE_PTS_PER_PP,
        MMR_YIELD_CENTER_PCT,
        MMR_YIELD_CAP,
        MMR_DISCOUNT_TRUST_KNEE_PCT,
        MMR_DISCOUNT_EXCESS_CREDIT,
        MMR_SUSPECT_VALUE_FACTOR,
        MMR_LOW_FLOOR_SHARE_THRESHOLD,
        MMR_LOW_FLOOR_VALUE_FACTOR,
        MMR_LOW_FLOOR_PENALTY,
        MMR_PRICE_BAND_CAP,
        MMR_FUTURE_WEIGHT,
        MMR_COST_WEIGHT,
        MMR_REGION_SLOPE,
        MMR_STRUCTURAL_FLOOR_CAP,
        REGIONAL_APPRECIATION_BASELINES,
    )
except ImportError:
    # Fallbacks mirror config.py (kept in sync; only used if config import fails).
    MMR_BASE = 1500
    MMR_NORM_CENTER = 1505
    MMR_NORM_SCALE = 29
    MIN_BAND_TXNS = 5
    COHORT_FLOOR_APPRECIATION = 0.55
    COHORT_FLOOR_LIQUIDITY = 0.40
    MMR_APPRECIATION_SLOPE = 3.0
    MMR_APPRECIATION_CENTER_PCT = 4.0
    MMR_MOMENTUM_WEIGHT = 0.0
    MMR_RELVALUE_SLOPE = 0.8
    MMR_RELVALUE_CAP = 28.0
    MMR_TXN_VOLUME_WEIGHT = 6.0
    MMR_YIELD_SLOPE_PTS_PER_PP = 10.0
    MMR_YIELD_CENTER_PCT = 3.2
    MMR_YIELD_CAP = 20.0
    MMR_DISCOUNT_TRUST_KNEE_PCT = 25.0
    MMR_DISCOUNT_EXCESS_CREDIT = 0.25
    MMR_SUSPECT_VALUE_FACTOR = 0.25
    MMR_LOW_FLOOR_SHARE_THRESHOLD = 0.7
    MMR_LOW_FLOOR_VALUE_FACTOR = 0.5
    MMR_LOW_FLOOR_PENALTY = 7.0
    MMR_PRICE_BAND_CAP = 11.0
    MMR_FUTURE_WEIGHT = 0.0
    MMR_COST_WEIGHT = 0.5
    MMR_REGION_SLOPE = 3.0
    MMR_STRUCTURAL_FLOOR_CAP = 3.1
    REGIONAL_APPRECIATION_BASELINES = {"CCR": 0.028, "RCR": 0.037, "OCR": 0.042}

# Agent appreciation overrides get the same sanity clamp URA rates get at
# ingestion (full_scorer caps to [-5, +15] %/yr) — Jun-2026 audit: overrides
# were the last unclamped positive appreciation channel.
_OVERRIDE_RATE_MIN_PCT = -5.0
_OVERRIDE_RATE_MAX_PCT = 15.0

# Red flags that are NOT already expressed as continuous MMR components.
# (old_property/small_dev/low_lease/psf_overpriced are continuous here.)
_UNIQUE_FLAGS = {"west_facing", "very_low_psf", "oversized_unit", "bedroom_sqft_mismatch"}

# Buyer pool depth — recentered so a shallow pool actually costs points
# instead of every district earning something.
_BUYER_POOL_PTS = {"very_deep": 7.0, "deep": 4.5, "moderate": 1.5, "shallow": -3.0}

# Region mapping (mirrors full_scorer._get_regional_baseline — kept in lockstep:
# the region term below tilts toward the same CCR/RCR/OCR baselines the
# appreciation fallback uses, so a fallback listing must NOT also get the tilt).
_CCR_DISTRICTS = {1, 2, 6, 7, 9, 10, 11}
_RCR_DISTRICTS = {3, 4, 5, 8, 12, 13, 14, 15}


def _region_of(district: Optional[str]) -> Optional[str]:
    """CCR / RCR / OCR for a 'D##' (or '##') district, None if unparseable.

    Everything outside the CCR/RCR sets is OCR — identical partition to
    full_scorer._get_regional_baseline."""
    if not district:
        return None
    d = str(district).upper().replace("D", "").strip()
    try:
        n = int(d)
    except ValueError:
        return None
    if n in _CCR_DISTRICTS:
        return "CCR"
    if n in _RCR_DISTRICTS:
        return "RCR"
    return "OCR"


def _knee_discount(premium_pct: float) -> float:
    """Compress an implausibly deep discount before it earns value points.

    v3.6 data-trust rule (see config): on a live open-market listing, a PSF
    reading more than MMR_DISCOUNT_TRUST_KNEE_PCT below verified comparables is
    far more likely a data artifact (mis-scraped sqft, non-comparable strata
    format, stale/bait price) or a defect than genuine alpha — every such case
    audited in Jun 2026 was rejected on web verification. Discount beyond the
    knee earns marginal credit at MMR_DISCOUNT_EXCESS_CREDIT; premiums
    (positive premium_pct) pass through untouched.
    """
    if premium_pct >= -MMR_DISCOUNT_TRUST_KNEE_PCT:
        return premium_pct
    excess = -premium_pct - MMR_DISCOUNT_TRUST_KNEE_PCT
    return -(MMR_DISCOUNT_TRUST_KNEE_PCT + excess * MMR_DISCOUNT_EXCESS_CREDIT)


def _age_points(age: Optional[float]) -> float:
    """Continuous age curve, v3.7: reshaped to MEASURED forward returns.

    Panel evidence (forward 2yr CAGR by age-at-split, value/region/liquidity
    controlled, n=956): the 0-5yr cohort UNDERperforms (~+1.3%/yr vs +3.3-4.1%
    for everything older — it is still paying off the launch-freshness premium,
    which decays ~3%/yr for the first decade); ages 7-30 are flat-to-positive
    at the margin (the 20-50 bucket was the BEST performer); only beyond ~30yr
    does the marginal turn negative (~-0.24pp/yr per extra year). The old
    3-7yr sweet spot + steep post-15 penalty (-0.6/yr) contradicted that data
    and double-counted leasehold decay already carried by the lease component.
    Post-30 slope is kept mild (0.35/yr, uncapped): it covers non-lease aging
    (maintenance, fittings, en-bloc limbo) on top of the lease penalty.
    """
    if age is None:
        return 0.0
    if age < 0:
        age = 0
    if age <= 7:
        return age * (5.0 / 7.0)  # launch-premium drag fades: 0 → 5
    if age <= 30:
        return 5.0  # measured plateau (≈0/positive forward marginal)
    return 5.0 - (age - 30) * 0.35  # mild uncapped decline (measured -0.24pp/yr)


def normalize_mmr(mmr: float) -> int:
    """Map an unbounded MMR onto the 0-1000 display scale (logistic)."""
    return int(round(1000.0 / (1.0 + math.exp(-(mmr - MMR_NORM_CENTER) / MMR_NORM_SCALE))))


def compute_mmr(scored: Any) -> dict:
    """Compute the uncapped MMR and its component breakdown for a ScoredListing.

    Expects scoring to have run (score_breakdown populated). Missing inputs
    contribute 0. Returns {"mmr", "score_1000", "components"}.
    """
    sb = scored.score_breakdown or {}
    sb_cap = sb.get("capital_appreciation", {})
    sb_liq = sb.get("liquidity", {})
    sb_flags = sb.get("red_flags", {})
    comps: dict[str, float] = {}

    # Cohort depth — how many SAME-SIZE transactions back this unit in its
    # project. Project-wide signals are only weak evidence for a unit type whose
    # own size-cohort barely trades, so they are damped toward neutral when the
    # cohort is thin. Two floors: appreciation (a project trend) damps gently,
    # liquidity (unit-type exit depth) damps harder. None = the project has no
    # size-band data at all → neutral (factor 1.0), per missing-data-is-neutral.
    cohort_txns = sb_flags.get("psf_cohort_txns")
    if cohort_txns is None:
        cohort_appr_factor = cohort_liq_factor = 1.0
    else:
        depth = min(1.0, cohort_txns / MIN_BAND_TXNS)
        cohort_appr_factor = max(COHORT_FLOOR_APPRECIATION, depth)
        cohort_liq_factor = max(COHORT_FLOOR_LIQUIDITY, depth)

    # --- Appreciation (rate in %/yr, confidence-weighted, linear) ---
    apr_pct = (scored.appreciation_rate or 0.0) * 100
    source = scored.appreciation_source or "default"
    txn = sb_cap.get("appreciation_rate", {}).get("transaction_count") or 0
    momentum = sb_cap.get("momentum", {}).get("value")
    if source in ("default", "regional_baseline"):
        conf = 0.35  # baseline guess, low weight (continuous analog of the legacy cap)
    elif source == "agent_override":
        conf = 1.0
        # Audit fix: an asserted rate gets the same sanity clamp URA rates get
        # at ingestion (full_scorer caps to [-5, +15] %/yr) — trust the human's
        # judgement, not an implausible magnitude.
        apr_pct = max(_OVERRIDE_RATE_MIN_PCT, min(_OVERRIDE_RATE_MAX_PCT, apr_pct))
    else:
        conf = 0.5 + 0.5 * min(1.0, txn / 50.0)
        # v3.1 boutique-volatility haircut (referee finding: thin freehold
        # series swing wildly year to year — Suites @ Topaz showed +48% 1yr
        # vs +7.5% annualized on 15 txns). When the series is thin AND either
        # unstable or of UNKNOWN stability (audit #6: a missing momentum must
        # not buy back the confidence the haircut exists to remove), trust it
        # less; a thin series with a measured-stable momentum keeps full weight.
        if txn < 30 and (momentum is None or abs(momentum) >= 0.8):
            conf *= 0.7
    # v3.2: the project CAGR is measured mostly on other unit sizes. For a unit
    # whose own size-cohort barely trades, that rate is weak evidence — damp it.
    # Applied to agent overrides too (audit fix, aligned with
    # apply_appreciation_override): the human asserts the RATE, but cohort
    # damping models exit/trend depth for a thin size-cohort, which an asserted
    # rate does not change.
    conf *= cohort_appr_factor
    # v3.3: slope cut 7.5→4.0 pts/%-pt. The point-in-time URA backtest showed
    # trailing appreciation has ~0 forward predictive power (ρ≈+0.06), so it no
    # longer dominates; it stays a meaningful factor (desirability proxy) but
    # value/yield/liquidity now carry comparable weight. (config-driven.)
    comps["appreciation"] = round(
        MMR_APPRECIATION_SLOPE * (apr_pct - MMR_APPRECIATION_CENTER_PCT) * conf, 2)
    meta_conf = conf

    # --- Momentum (already a small signed number) ---
    # v3.3: weight cut 8.0→3.0 — backtest found momentum flat-to-contrarian
    # (ρ≈-0.03 corrected). Kept small, not removed (direction still informs the
    # AI's qualitative read; magnitude no longer swings the rank).
    momentum = sb_cap.get("momentum", {}).get("value")
    comps["momentum"] = round(MMR_MOMENTUM_WEIGHT * momentum, 2) if momentum is not None else 0.0

    # --- PSF vs market (SYMMETRIC: discount positive, premium negative) ---
    premium_pct = sb_flags.get("psf_premium_pct")
    rel_premium = (sb.get("relative_value") or {}).get("premium_vs_age_adjusted_median_pct")
    flag_names = {f.get("flag") for f in sb_flags.get("flags", [])}
    oversized = "oversized_unit" in flag_names
    # v3.6 data-trust: when the sqft itself is untrusted (bed/sqft mismatch) or
    # an implausibly deep discount rests on a thin same-size cohort, the
    # "cheapness" is presumed artifact until a human verifies it — the positive
    # side of BOTH value components retains only MMR_SUSPECT_VALUE_FACTOR.
    # (Premiums stay fully penalized; see config rationale.)
    deep_discount = any(
        p is not None and p < -MMR_DISCOUNT_TRUST_KNEE_PCT
        for p in (premium_pct, rel_premium))
    # v3.8: a unit asking ABOVE its own stack's recent prints is not cheap, no
    # matter what district/band medians say — ground-floor PES stacks read as
    # 15-20% "cheap" against pooled benchmarks while pricing above their own
    # comps. Prints contradicting the discount ⇒ cheapness is an artifact.
    # v3.9: an ask BELOW the 10th percentile of a deep recent print set
    # (ask_below_stack_prints) is the mirror artifact — bait pricing or a
    # void-format unit (double-volume loft) whose strata sqft deflates the
    # paper PSF. Both directions: prints disagree ⇒ verify-first, not value.
    print_contradiction = (sb_flags.get("stack_premium_pct") or 0) > 5.0
    suspect_discount = ("bedroom_sqft_mismatch" in flag_names
                        or "ask_below_stack_prints" in flag_names
                        or (deep_discount and (cohort_txns or 0) < MIN_BAND_TXNS)
                        or print_contradiction)
    # v3.12: the stack IS ground/low floor (≥70% of its same-size URA prints at
    # floors 01-05). Its low PSF is a structural floor discount, not underpricing,
    # so the cheap-vs-peers reading must not be credited as value. Unlike a
    # suspect mis-scrape the price is REAL (don't nuke to 0.25) — halve the
    # floor-driven value credit; the exit-liquidity penalty is added separately.
    low_floor_stack = (sb_flags.get("stack_low_floor_share") or 0) >= MMR_LOW_FLOOR_SHARE_THRESHOLD
    if premium_pct is not None:
        # v3.6: knee-compressed discount + tanh saturation (same cap as
        # age_value) — an uncapped linear -0.8/% let a -60% artifact earn +48.
        # Slope shared with age_value via MMR_RELVALUE_SLOPE (audit #3: a
        # hardcoded -0.8 desynced the two halves of the value signal when the
        # config constant was tuned).
        psf_value = MMR_RELVALUE_CAP * math.tanh(
            -MMR_RELVALUE_SLOPE * _knee_discount(premium_pct) / MMR_RELVALUE_CAP)
        # v3.1/3.2: a premium/discount is only as trustworthy as the comparable
        # set behind it. Weight by the SAME-SIZE cohort count (the premium is now
        # measured against similar-size units); fall back to project txns when a
        # project has no size data.
        # v3.5: cap the no-size-data fallback. When cohort_txns is None the
        # premium was measured against a size-MIXED pooled median, which must not
        # earn more confidence than a genuine size-matched cohort would (a large
        # project with 300 pooled txns previously hit full confidence on a
        # size-polluted reading, while a confirmed-thin band was damped to ~0.5).
        psf_conf_n = cohort_txns if cohort_txns is not None else min(txn, 15)
        psf_value *= 0.5 + 0.5 * min(1.0, psf_conf_n / 30.0)
    else:
        ratio = sb_cap.get("psf_vs_median", {}).get("ratio")
        psf_value = 80.0 * (1.0 - ratio) if ratio else 0.0
    # Oversized units (penthouse/PES) trade at structurally lower PSF than
    # their project's median — a positive "cheap PSF" reading there is mostly
    # a size artifact, not value (first surfaced by the arena referee). Damp
    # the positive side only; an oversized unit priced ABOVE median is
    # genuinely expensive.
    if oversized and psf_value > 0:
        psf_value *= 0.5
    if suspect_discount and psf_value > 0:
        psf_value *= MMR_SUSPECT_VALUE_FACTOR
    if low_floor_stack and psf_value > 0:
        psf_value *= MMR_LOW_FLOOR_VALUE_FACTOR
    comps["psf_value"] = round(psf_value, 2)

    # --- Lease / tenure ---
    # v3.4: tenure is NOT a forward-return edge. The backtest shows freehold carries
    # ~0 cross-sectional PSF premium (controlling for region+size) and mildly
    # UNDERperforms forward (ρ -0.09..-0.14). What genuinely matters is the DOWNSIDE
    # of a short remaining lease (financing/CPF caps, exit liquidity). So this
    # component is ~flat for any healthy lease and only PENALIZES short ones.
    # (Was: freehold +4.0 and a fresh 99yr up to +7.6 — an unearned tenure upside.)
    tenure = (scored.tenure or "").lower()
    remaining = scored.remaining_lease
    if "freehold" in tenure or "999" in tenure:
        comps["lease"] = 1.0  # no lease risk, but not a forward edge (own-stay/optionality only)
    elif remaining is not None:
        # flat small positive for healthy leases; steepening penalty below ~80yr
        comps["lease"] = round(min(1.0, max(-25.0, (remaining - 80) * 0.4)), 2)
    else:
        comps["lease"] = 0.0

    # --- Age (v3.7 measured curve: ramp 0-7yr, plateau 7-30, mild decline 30+) ---
    age = None
    age_info = sb_cap.get("property_age", {})
    if age_info.get("age_years") is not None:
        age = age_info["age_years"]
    comps["age"] = round(_age_points(age), 2)

    # --- Rental yield (linear around 3.2%, weighted by rent-data quality) ---
    # Uncertain rent estimates pull toward neutral instead of carrying full
    # weight (same principle as appreciation confidence).
    gross_yield = scored.estimated_gross_yield or 0.0
    rent_source = scored.rent_source or ""
    # v3.4: district/fallback rents are a district·bed CONSTANT × sqft, so the
    # resulting gross_yield ≈ const/PSF — a re-skin of the cheap-vs-peers value
    # signal, not independent rental evidence. Down-weight them hard so yield does
    # not double-count value (was district_median 0.75 / fallback 0.4).
    # v3.5: ura_project_bed/ura_project are REAL URA rental-contract medians for
    # the exact project (rental_cache.json) — genuine rental evidence, near-full
    # weight. (Backtest PART 5g: real-rent yield has ~0 forward PRICE signal —
    # the component prices CARRY over the hold, not appreciation.)
    # district_bedroom is still a district·bed constant × sqft (bed-matched but
    # not project rental evidence) — it belongs in the synthetic tier just above
    # district_median, not at 0.9 (a pre-v3.4 leftover that escaped the
    # down-weighting pass and out-ranked real ura_project contracts).
    # Audit #5: an UNKNOWN rent source must not outrank the known synthetic
    # tiers (the old 0.6 default beat district_bedroom 0.55 and
    # district_median 0.5) — 0.3 ranks it below every named source but above
    # the explicit fallbacks.
    rent_conf = {
        "same_condo": 1.0,
        "ura_project_bed": 0.95,
        "ura_project": 0.8,
        "district_bedroom": 0.55,
        "district_median": 0.5,
    }.get(rent_source, 0.15 if rent_source.startswith("fallback") else 0.3)
    # When the rental estimator carried an explicit numeric confidence (copied
    # onto the scored listing as `rent_confidence`), it can only LOWER the
    # source-map prior, never raise it. Defensive: absent/malformed → ignored.
    _rent_confidence = getattr(scored, "rent_confidence", None)
    if _rent_confidence is not None:
        try:
            rent_conf = min(rent_conf, max(0.0, min(1.0, float(_rent_confidence))))
        except (TypeError, ValueError):
            pass
    # v3.5b: slope 18.75→10 pts/pp — real-rent backtest (PART 5g) shows +1pp
    # yield costs ~0.75pp/yr forward price growth, so yield's NET total-return
    # edge is small; it stays weighted as (regime-hedged) carry. (config)
    # v3.6.1: tanh-capped — an implausible computed yield is a price/rent
    # artifact (fake-cheap price ÷ real rent), the last uncapped channel after
    # v3.6 capped psf_value/age_value. Real yields (2.5-5%) stay near-linear.
    if gross_yield > 0:
        yield_raw = MMR_YIELD_SLOPE_PTS_PER_PP * (gross_yield - MMR_YIELD_CENTER_PCT)
        yield_pts = MMR_YIELD_CAP * math.tanh(yield_raw / MMR_YIELD_CAP) * rent_conf
        # Audit #8: gross_yield divides the rent by the SAME untrusted price the
        # suspect triggers just flagged — a flagged mis-scrape must not keep its
        # yield points after psf_value/age_value were damped (The Vision kept
        # ~+19 yield pts this way and still scored 465). Positive side retains
        # MMR_SUSPECT_VALUE_FACTOR; a low-yield reading stays fully penalized.
        if suspect_discount and yield_pts > 0:
            yield_pts *= MMR_SUSPECT_VALUE_FACTOR
        comps["yield"] = round(yield_pts, 2)
    else:
        comps["yield"] = 0.0

    # --- MRT proximity (smooth saturation, no bucket cliffs) ---
    mrt_dist = scored.mrt_distance_m
    comps["mrt"] = round(9.0 * math.tanh((700.0 - mrt_dist) / 600.0), 2) if mrt_dist is not None else 0.0

    # --- Liquidity ---
    # Resale depth is project-wide, but a unit type that rarely trades is hard
    # to exit regardless of the building's total volume — damp by cohort depth.
    # v3.5: weight 10→6 (config). Volume's forward-return content is ~0/negative
    # once value+region are controlled (backtest_ext PART 3/5d); what remains is
    # exit-risk insurance for the 5-7yr hold, which doesn't justify outweighing
    # genuinely predictive components.
    liq_txn = sb_liq.get("transaction_volume", {}).get("count") or txn or 0
    comps["txn_volume"] = round(MMR_TXN_VOLUME_WEIGHT * math.tanh(liq_txn / 40.0) * cohort_liq_factor, 2)

    depth = sb_liq.get("buyer_pool_depth", {}).get("depth")
    comps["buyer_pool"] = _BUYER_POOL_PTS.get(depth, 0.0)

    # dev_size — KEPT as-is. v3.10 measurement CONFIRMED it: std_β +0.130
    # CI[+0.018,+0.239], significant in the joint model (the earlier "marginal
    # +0.045, unvalidated" read is superseded). Larger developments exit more
    # easily / carry more buyer-pool depth; tanh-saturated so a mega-project
    # can't dominate.
    units = scored.total_units
    comps["dev_size"] = round(6.0 * math.tanh((units - 150.0) / 300.0), 2) if units else 0.0

    # --- Price band (EXIT LIQUIDITY only — not a value/forward signal) ---
    # v3.4: reshaped. The old curve REWARDED higher quantum ($2.5M → +5.0 vs
    # $1.5M → +4.0), the opposite of any forward signal and a confound with value.
    # Price band now models ONLY buyer-pool depth / exit risk: neutral (0) across
    # the broad-demand band up to ~$2.2M, declining above as the pool thins.
    # Absolute cheapness is intentionally NOT rewarded here — the backtest showed
    # that signal is mostly the region effect (already in the appreciation
    # baselines); rewarding it again would double-count region.
    price = scored.price or 0
    if price <= 2_200_000:
        comps["price_band"] = 0.0  # broad-demand band — neutral, no quantum reward
    else:
        # Buyer pool thins continuously above the sweet spot. Audit #4: this was
        # the last uncapped value-side channel (-39 raw at $10M); same -2.5 pts
        # per $500k slope near the knee, saturating (tanh) at MMR_PRICE_BAND_CAP
        # so an ultra-luxury quantum reads as thin-exit risk, not a death blow.
        # Near-knee asks barely move (-3.8 at $3M); v3.10 trimmed the cap 15→11
        # to fund the explicit region tilt (which overlaps the CCR-skewed luxury
        # quantum penalty), so the tail saturates earlier (see config note).
        raw = (price - 2_200_000) / 500_000 * 2.5
        comps["price_band"] = round(-MMR_PRICE_BAND_CAP * math.tanh(raw / MMR_PRICE_BAND_CAP), 2)

    # --- Future potential (UPSIDE-ONLY catalyst) ---
    # v3.4: two fixes. (1) Was (score-8.0)*1.2 — a ±24-pt swing that PENALIZED
    # data-poor listings: no coords/profile defaults the sub-scores to ~4, mapping
    # to -4.8, violating "missing data is neutral, never penalized". (2) The future
    # heuristics (MRT/zone/transformation/supply point tables) are un-backtested, so
    # the magnitude was reined in (neutral at the no-signal floor 4, NON-NEGATIVE,
    # slope 1.2→0.7).
    # v3.10: ZEROED via MMR_FUTURE_WEIGHT. Measured at last: univariate ρ +0.061
    # CI[-0.013,+0.143] UNDETERMINED, multivariate std_β -0.024 (negative sign),
    # repeat-sales -0.016 — no forward power in any construction. The
    # future_potential_score stays computed and surfaced in factual_data for the
    # agent's qualitative read; only its MMR contribution goes to 0. (Keep the
    # shape so re-enabling is one weight if a future backtest ever finds signal.)
    comps["future"] = round(max(0.0, scored.future_potential_score - 4.0) * 0.7
                            * MMR_FUTURE_WEIGHT, 2)

    # --- Cost efficiency (recentred from the 0-10 score, v3.10 HALVED) ---
    # cost_efficiency is a deterministic sqft/beds function (mcst / AV / $-per-bed)
    # — partly value-in-disguise. Measured std_β +0.122 CI[+0.018,+0.231]: survives
    # 0 but weak, split-sample UNDETERMINED. Halved (MMR_COST_WEIGHT) from its prior
    # ±5 swing to ±2.5.
    comps["cost"] = round((scored.cost_efficiency_score - 5.0) * MMR_COST_WEIGHT, 2)

    # --- Region tilt (v3.10: the strongest measured forward signal, made
    # explicit for DATA-RICH listings) ---
    # Region only reached the appreciation FALLBACK before: a regional_baseline
    # listing got the regional rate as its appreciation input, but a listing with
    # real project (ura_*) appreciation never saw any regional tilt. Add it as a
    # signed component centred on the mean of the three regional baselines, scaled
    # by MMR_REGION_SLOPE (= MMR_APPRECIATION_SLOPE, the regime-robust choice).
    # GATE (no double-counting): apply ONLY when the appreciation data_source is
    # real project data (not the default/regional_baseline fallback) — a fallback
    # listing already carries the regional rate AS its rate, so tilting again would
    # double-count. Unknown region → 0. Measured: region_delta ranks +0.219
    # CI[+0.129,+0.299]; clean out-of-split composite ρ +0.240 → +0.249.
    # Regime-bound (single bull regime), hence anchored to the conservative slope.
    region = _region_of(scored.district)
    region_uses_fallback = source in ("default", "regional_baseline")
    if region is not None and not region_uses_fallback:
        baselines_pct = [b * 100 for b in REGIONAL_APPRECIATION_BASELINES.values()]
        mean_baseline_pct = sum(baselines_pct) / len(baselines_pct)
        region_pct = REGIONAL_APPRECIATION_BASELINES[region] * 100
        comps["region"] = round(MMR_REGION_SLOPE * (region_pct - mean_baseline_pct), 2)
    else:
        comps["region"] = 0.0

    # --- Age-adjusted relative value vs district peers (symmetric) ---
    # Distinct signal from psf_value (which compares against the SAME
    # project's transactions): this asks whether the unit is cheap or dear
    # for its age against the district's peer projects, normalized at a
    # region-dependent $/psf/yr age slope. Weighted below psf_value since
    # the slope is a heuristic.
    # v3.3: slope doubled 0.4→0.8. This age-adjusted district-relative value is
    # the closest analog to the backtest's strongest forward predictor
    # (cheap-vs-district-peers, ρ≈-0.24), so it earns more weight as appreciation
    # gives some up. Still damped by peer_count below (heuristic age slope).
    rel = sb.get("relative_value") or {}
    # tanh saturation: 0.8 slope near zero, capped at ±CAP so a heavy-tailed or
    # artifact premium can't dominate the score (see config note). rel_premium
    # itself is read earlier (with psf_value) to detect a suspect deep discount.
    if rel_premium is not None:
        age_value = MMR_RELVALUE_CAP * math.tanh(
            -MMR_RELVALUE_SLOPE * _knee_discount(rel_premium) / MMR_RELVALUE_CAP)
    else:
        age_value = 0.0
    # v3.1: thin peer sets make the age-adjusted median unreliable — scale toward
    # neutral when peers are few. v3.4: softened (floor 0.4→0.5, full weight at 10
    # peers not 15) so the strongest region-robust forward signal is not throttled
    # in normally-covered districts.
    peer_count = rel.get("peer_count") or 0
    if peer_count:
        age_value *= max(0.5, min(1.0, peer_count / 10.0))
    if oversized and age_value > 0:
        age_value *= 0.5  # same size-artifact damping as psf_value
    if suspect_discount and age_value > 0:
        age_value *= MMR_SUSPECT_VALUE_FACTOR  # v3.6 data-trust (see psf_value)
    if low_floor_stack and age_value > 0:
        age_value *= MMR_LOW_FLOOR_VALUE_FACTOR  # v3.12 floor-discount ≠ value
    comps["age_value"] = round(age_value, 2)

    # v3.12 ground/low-floor exit-liquidity demotion: a confirmed low-floor stack
    # has a thinner buyer pool and weaker resale — the investor's real concern.
    # A modest fixed penalty (not return-measured; floor isn't in the live book,
    # only in URA prints), surfaced as its own component so it's visible/tunable.
    comps["low_floor"] = round(-MMR_LOW_FLOOR_PENALTY, 2) if low_floor_stack else 0.0

    # --- Unique red flags only (others are continuous components above) ---
    flags = sb_flags.get("flags", [])
    unique_penalty = sum(f.get("penalty", 0) for f in flags if f.get("flag") in _UNIQUE_FLAGS)
    comps["red_flags"] = round(-1.5 * unique_penalty, 2)

    # --- Structural-floor GATE (v3.10b correctness fix) ---
    # When a listing's unit-level value signal is untrustworthy — either NO
    # positive value/yield credit at all (psf_value ≤ 0 AND age_value ≤ 0 AND
    # yield ≤ 0) OR a suspect-damped artifact (bedroom_sqft_mismatch /
    # ask_below_stack_prints / thin-cohort deep discount / stack_premium suspect,
    # whose residual value the v3.6+ trust layer already cut to 25% but left
    # slightly positive) — don't let the UNVALIDATED components add structural
    # credit on top. Cap the SUM of the POSITIVE parts of (future, cost) at
    # MMR_STRUCTURAL_FLOOR_CAP. future is measured ≈0 (already 0); cost is weak
    # (std_β +0.12, split-sample UNDET).
    #
    # Deliberately EXCLUDES dev_size: the Jun-2026 lever measurement VALIDATED it
    # (std_β +0.130 CI[+0.018,+0.239], significant in the joint model) and it is
    # a PROJECT-level liquidity/exit proxy that holds regardless of one unit's
    # value artifact — capping a validated signal is the exact anti-pattern the
    # audit warns against. So a flagged 1BR in a genuinely strong/liquid/
    # appreciating project (Coco Palms 624sf PES) legitimately scores at that
    # project's quality level (~750) once its FAKE discount is damped out; it is
    # NOT forced below 650, and the surfaced red flag is the agent's verify-first
    # signal. This gate only stops UNVALIDATED credit piling on, and is a
    # guardrail should future/cost ever be re-weighted up.
    _value_untrusted = (
        (comps["psf_value"] <= 0 and comps["age_value"] <= 0 and comps["yield"] <= 0)
        or suspect_discount
        or low_floor_stack   # v3.12: floor-driven cheapness isn't validated credit
    )
    if _value_untrusted:
        _gate_keys = ("future", "cost")
        _pos_sum = sum(comps[k] for k in _gate_keys if comps[k] > 0)
        if _pos_sum > MMR_STRUCTURAL_FLOOR_CAP:
            _scale = MMR_STRUCTURAL_FLOOR_CAP / _pos_sum
            for k in _gate_keys:
                if comps[k] > 0:
                    comps[k] = round(comps[k] * _scale, 2)

    mmr = MMR_BASE + sum(comps.values())
    return {
        "mmr": round(mmr, 1),
        "score_1000": normalize_mmr(mmr),
        "components": comps,
        "meta": {"appreciation_confidence": round(meta_conf, 2), "appreciation_rate_pct": round(apr_pct, 2)},
    }


def apply_appreciation_override(
    mmr_result: dict, new_rate_pct: float, cohort_appr_factor: float = 1.0
) -> dict:
    """Recompute the MMR after an agent appreciation override.

    Used by --from-review where the full breakdown may not be reconstructable.

    The human asserts the *rate* (so rate-reliability confidence = 1.0), but the
    v3.2 cohort-depth damping models EXIT/TREND depth for a thin size-cohort, which
    an override does not change — so it is still applied (pass `cohort_appr_factor`
    from psf_cohort_txns when available). Defaults to 1.0 (no damping) when unknown.
    The rate is clamped to the same [-5, +15] %/yr band URA rates get at ingestion
    (audit fix: overrides were the last unclamped positive appreciation channel).
    """
    new_rate_pct = max(_OVERRIDE_RATE_MIN_PCT, min(_OVERRIDE_RATE_MAX_PCT, float(new_rate_pct)))
    comps = dict(mmr_result.get("components", {}))
    old = comps.get("appreciation", 0.0)
    conf = 1.0 * max(0.0, min(1.0, cohort_appr_factor))
    new = round(MMR_APPRECIATION_SLOPE * (new_rate_pct - MMR_APPRECIATION_CENTER_PCT) * conf, 2)
    comps["appreciation"] = new
    mmr = mmr_result["mmr"] - old + new
    return {
        "mmr": round(mmr, 1),
        "score_1000": normalize_mmr(mmr),
        "components": comps,
        "meta": {"appreciation_confidence": round(conf, 2), "appreciation_rate_pct": round(new_rate_pct, 2)},
    }
