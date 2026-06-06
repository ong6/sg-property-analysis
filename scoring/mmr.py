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
"""

import math
from typing import Any, Optional

try:
    from config import (
        MMR_BASE,
        MMR_NORM_CENTER,
        MMR_NORM_SCALE,
    )
except ImportError:
    MMR_BASE = 1500
    MMR_NORM_CENTER = 1500
    MMR_NORM_SCALE = 55

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

    # --- Appreciation (rate in %/yr, confidence-weighted, linear) ---
    apr_pct = (scored.appreciation_rate or 0.0) * 100
    source = scored.appreciation_source or "default"
    txn = sb_cap.get("appreciation_rate", {}).get("transaction_count") or 0
    if source in ("default", "regional_baseline"):
        conf = 0.35  # baseline guess, low weight (continuous analog of the legacy cap)
    elif source == "agent_override":
        conf = 1.0
    else:
        conf = 0.5 + 0.5 * min(1.0, txn / 50.0)
    comps["appreciation"] = round(30.0 * ((apr_pct - 4.0) / 2.5) * conf, 2)
    meta_conf = conf

    # --- Momentum (already a small signed number) ---
    momentum = sb_cap.get("momentum", {}).get("value")
    comps["momentum"] = round(8.0 * momentum, 2) if momentum is not None else 0.0

    # --- PSF vs market (SYMMETRIC: discount positive, premium negative) ---
    premium_pct = sb_flags.get("psf_premium_pct")
    if premium_pct is not None:
        comps["psf_value"] = round(-0.8 * premium_pct, 2)
    else:
        ratio = sb_cap.get("psf_vs_median", {}).get("ratio")
        comps["psf_value"] = round(80.0 * (1.0 - ratio), 2) if ratio else 0.0

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
    liq_txn = sb_liq.get("transaction_volume", {}).get("count") or txn or 0
    comps["txn_volume"] = round(10.0 * math.tanh(liq_txn / 40.0), 2)

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
    new = round(30.0 * ((new_rate_pct - 4.0) / 2.5) * 1.0, 2)
    comps["appreciation"] = new
    mmr = mmr_result["mmr"] - old + new
    return {
        "mmr": round(mmr, 1),
        "score_1000": normalize_mmr(mmr),
        "components": comps,
        "meta": {"appreciation_confidence": 1.0, "appreciation_rate_pct": round(new_rate_pct, 2)},
    }
