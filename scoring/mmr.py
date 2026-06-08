"""MMR rating system (v3) — continuous, uncapped, then normalized to /1000.

Why this exists: the legacy 100-point system bucketed every metric into
integer points and clamped each category, which compressed all real-world
listings into a ~32-68 band. The MMR layer replaces buckets with continuous
functions and removes category caps, so differences between listings are
actually expressed. The raw MMR is unbounded (Elo-style, centered at 1500);
a logistic transform maps it onto a stable 0-1000 display scale.

Design rules (anti-bias):
- Missing data contributes 0 (neutral), it is never treated as bad data.
- Price-vs-market is SYMMETRIC: a discount adds, a premium subtracts
  (the legacy scorer only ever rewarded discounts).
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
    )
except ImportError:
    MMR_BASE = 1500
    MMR_NORM_CENTER = 1500
    MMR_NORM_SCALE = 55
    MIN_BAND_TXNS = 5
    COHORT_FLOOR_APPRECIATION = 0.55
    COHORT_FLOOR_LIQUIDITY = 0.40
    MMR_APPRECIATION_SLOPE = 4.0
    MMR_APPRECIATION_CENTER_PCT = 4.0
    MMR_MOMENTUM_WEIGHT = 3.0
    MMR_RELVALUE_SLOPE = 0.8
    MMR_RELVALUE_CAP = 20.0

# Red flags that are NOT already expressed as continuous MMR components.
# (old_property/small_dev/low_lease/psf_overpriced are continuous here.)
_UNIQUE_FLAGS = {"west_facing", "very_low_psf", "oversized_unit", "bedroom_sqft_mismatch"}

# Buyer pool depth — recentered so a shallow pool actually costs points
# instead of every district earning something.
_BUYER_POOL_PTS = {"very_deep": 7.0, "deep": 4.5, "moderate": 1.5, "shallow": -3.0}


def _age_points(age: Optional[float]) -> float:
    """Continuous age curve with the documented 3-7yr sweet spot.

    Brand new (<3yr) scores below peak so launch-premium pricing isn't
    double-rewarded; beyond 15yr the decline is uncapped (maintenance and
    resale drag grow with age).
    """
    if age is None:
        return 0.0
    if age < 0:
        age = 0
    if age <= 2:
        return 2.0 + age  # 2 → 4
    if age <= 7:
        return 5.0  # sweet spot
    if age <= 15:
        return 5.0 - (age - 7) * 0.625  # 5 → 0 at 15
    return -(age - 15) * 0.6  # uncapped decline


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
    momentum_val = sb_cap.get("momentum", {}).get("value")
    if source in ("default", "regional_baseline"):
        conf = 0.35  # baseline guess, low weight (continuous analog of the legacy cap)
    elif source == "agent_override":
        conf = 1.0
    else:
        conf = 0.5 + 0.5 * min(1.0, txn / 50.0)
        # v3.1 boutique-volatility haircut (referee finding: thin freehold
        # series swing wildly year to year — Suites @ Topaz showed +48% 1yr
        # vs +7.5% annualized on 15 txns). When the series is BOTH thin and
        # unstable, trust it less; either alone is fine.
        if txn < 30 and momentum_val is not None and abs(momentum_val) >= 0.8:
            conf *= 0.7
    # v3.2: the project CAGR is measured mostly on other unit sizes. For a unit
    # whose own size-cohort barely trades, that rate is weak evidence — damp it.
    # (Not applied to an explicit agent override: the human asserted that rate.)
    if source != "agent_override":
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
    if premium_pct is not None:
        psf_value = -0.8 * premium_pct
        # v3.1/3.2: a premium/discount is only as trustworthy as the comparable
        # set behind it. Weight by the SAME-SIZE cohort count (the premium is now
        # measured against similar-size units); fall back to project txns when a
        # project has no size data.
        psf_conf_n = cohort_txns if cohort_txns is not None else txn
        psf_value *= 0.5 + 0.5 * min(1.0, psf_conf_n / 30.0)
    else:
        ratio = sb_cap.get("psf_vs_median", {}).get("ratio")
        psf_value = 80.0 * (1.0 - ratio) if ratio else 0.0
    # Oversized units (penthouse/PES) trade at structurally lower PSF than
    # their project's median — a positive "cheap PSF" reading there is mostly
    # a size artifact, not value (first surfaced by the arena referee). Damp
    # the positive side only; an oversized unit priced ABOVE median is
    # genuinely expensive.
    flag_names = {f.get("flag") for f in sb_flags.get("flags", [])}
    oversized = "oversized_unit" in flag_names
    if oversized and psf_value > 0:
        psf_value *= 0.5
    comps["psf_value"] = round(psf_value, 2)

    # --- Lease / tenure (continuous decay for 99yr) ---
    tenure = (scored.tenure or "").lower()
    remaining = scored.remaining_lease
    if "freehold" in tenure or "999" in tenure:
        comps["lease"] = 4.0
    elif remaining is not None:
        comps["lease"] = round(max(-25.0, (remaining - 80) * 0.4), 2)
    else:
        comps["lease"] = 0.0

    # --- Age (3-7yr sweet spot, uncapped old-age decline) ---
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
    rent_conf = {
        "same_condo": 1.0,
        "district_bedroom": 0.9,
        "district_median": 0.75,
    }.get(rent_source, 0.4 if rent_source.startswith("fallback") else 0.6)
    comps["yield"] = round(15.0 * ((gross_yield - 3.2) / 0.8) * rent_conf, 2) if gross_yield > 0 else 0.0

    # --- MRT proximity (smooth saturation, no bucket cliffs) ---
    mrt_dist = scored.mrt_distance_m
    comps["mrt"] = round(9.0 * math.tanh((700.0 - mrt_dist) / 600.0), 2) if mrt_dist is not None else 0.0

    # --- Liquidity ---
    # Resale depth is project-wide, but a unit type that rarely trades is hard
    # to exit regardless of the building's total volume — damp by cohort depth.
    liq_txn = sb_liq.get("transaction_volume", {}).get("count") or txn or 0
    comps["txn_volume"] = round(10.0 * math.tanh(liq_txn / 40.0) * cohort_liq_factor, 2)

    depth = sb_liq.get("buyer_pool_depth", {}).get("depth")
    comps["buyer_pool"] = _BUYER_POOL_PTS.get(depth, 0.0)

    units = scored.total_units
    comps["dev_size"] = round(6.0 * math.tanh((units - 150.0) / 300.0), 2) if units else 0.0

    price = scored.price or 0
    if price <= 0:
        comps["price_band"] = 0.0
    elif price < 1_800_000:
        comps["price_band"] = 4.0
    elif price <= 2_500_000:
        comps["price_band"] = 5.0
    else:
        # Buyer pool thins continuously above the sweet spot (uncapped decline)
        comps["price_band"] = round(5.0 - (price - 2_500_000) / 500_000 * 2.5, 2)

    # --- Future potential (recentred from the 0-20 future score) ---
    comps["future"] = round((scored.future_potential_score - 8.0) * 1.2, 2)

    # --- Cost efficiency (recentred from the 0-10 score) ---
    comps["cost"] = round(scored.cost_efficiency_score - 5.0, 2)

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
    rel_premium = rel.get("premium_vs_age_adjusted_median_pct")
    # tanh saturation: 0.8 slope near zero, capped at ±CAP so a heavy-tailed or
    # artifact premium can't dominate the score (see config note).
    if rel_premium is not None:
        age_value = MMR_RELVALUE_CAP * math.tanh(
            -MMR_RELVALUE_SLOPE * rel_premium / MMR_RELVALUE_CAP)
    else:
        age_value = 0.0
    # v3.1: thin peer sets make the age-adjusted median unreliable —
    # scale toward neutral below ~15 peers (floor 0.4 at the 5-peer minimum).
    peer_count = rel.get("peer_count") or 0
    if peer_count:
        age_value *= max(0.4, min(1.0, peer_count / 15.0))
    if oversized and age_value > 0:
        age_value *= 0.5  # same size-artifact damping as psf_value
    comps["age_value"] = round(age_value, 2)

    # --- Unique red flags only (others are continuous components above) ---
    flags = sb_flags.get("flags", [])
    unique_penalty = sum(f.get("penalty", 0) for f in flags if f.get("flag") in _UNIQUE_FLAGS)
    comps["red_flags"] = round(-1.5 * unique_penalty, 2)

    mmr = MMR_BASE + sum(comps.values())
    return {
        "mmr": round(mmr, 1),
        "score_1000": normalize_mmr(mmr),
        "components": comps,
        "meta": {"appreciation_confidence": round(meta_conf, 2), "appreciation_rate_pct": round(apr_pct, 2)},
    }


def apply_appreciation_override(mmr_result: dict, new_rate_pct: float) -> dict:
    """Recompute the MMR after an agent appreciation override (conf=1.0).

    Used by --from-review where the full breakdown may not be reconstructable.
    """
    comps = dict(mmr_result.get("components", {}))
    old = comps.get("appreciation", 0.0)
    new = round(MMR_APPRECIATION_SLOPE * (new_rate_pct - MMR_APPRECIATION_CENTER_PCT) * 1.0, 2)
    comps["appreciation"] = new
    mmr = mmr_result["mmr"] - old + new
    return {
        "mmr": round(mmr, 1),
        "score_1000": normalize_mmr(mmr),
        "components": comps,
        "meta": {"appreciation_confidence": 1.0, "appreciation_rate_pct": round(new_rate_pct, 2)},
    }
