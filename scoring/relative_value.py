"""Age-adjusted relative value — is this condo cheap or dear *for its age*?

Comparing a 10-year-old condo's PSF directly against a new launch is unfair:
new launches carry a freshness premium that decays with age. The Singapore
rule of thumb is that each year of age is worth roughly $50 PSF against a
comparable new unit — steeper in high-PSF regions, flatter for freehold.

This module normalizes every peer project's PSF to the subject's age using a
region/tenure-dependent slope, then asks two questions:

1. vs the area: is the subject priced above or below the age-adjusted median
   of its district peers? (premium_vs_age_adjusted_median_pct — negative
   means cheap for its age)
2. vs new launches: given what new launches transact at in this district,
   what *should* the subject cost after age depreciation, and how does its
   asking PSF compare? (premium_vs_new_launch_implied_pct)

Peer data comes from the URA cache (per-project median transacted PSF +
lease-start year + district), i.e. real transactions, not asking prices.

⚠ The slopes are HEURISTICS anchored on the $50/yr folk rule, scaled by
regional PSF levels. Tune in config / verify against current market data.
"""

from statistics import median
from typing import Any, Optional

try:
    from config import AGE_PSF_SLOPE_BY_REGION, FREEHOLD_SLOPE_FACTOR
except ImportError:
    AGE_PSF_SLOPE_BY_REGION = {"CCR": 60.0, "RCR": 50.0, "OCR": 40.0}
    FREEHOLD_SLOPE_FACTOR = 0.6

# Lease start (land acquisition) precedes TOP by ~3 years — same offset the
# lease calculators use.
_LEASE_TO_TOP_OFFSET = 3
# Beyond this age difference, linear $/yr extrapolation is not credible.
_MAX_ADJUST_YEARS = 25
_MIN_PEERS = 5
_MIN_NEW_LAUNCH_PEERS = 2
_NEW_LAUNCH_MAX_AGE = 3

_CCR = {1, 2, 6, 7, 9, 10, 11}
_RCR = {3, 4, 5, 8, 12, 13, 14, 15}


def _region_for_district(district: str) -> str:
    try:
        d = int(str(district).upper().replace("D", "").strip() or 0)
    except ValueError:
        return "OCR"
    if d in _CCR:
        return "CCR"
    if d in _RCR:
        return "RCR"
    return "OCR"


def _slope_for(region: str, tenure: Optional[str]) -> float:
    slope = AGE_PSF_SLOPE_BY_REGION.get(region, AGE_PSF_SLOPE_BY_REGION["OCR"])
    if tenure and ("freehold" in tenure.lower() or "999" in tenure):
        slope *= FREEHOLD_SLOPE_FACTOR
    return slope


def compute_relative_value(
    subject_psf: float,
    subject_age: float,
    district: str,
    tenure: Optional[str],
    ura_data: dict,
    current_year: int,
) -> Optional[dict[str, Any]]:
    """Age-adjusted relative value of a subject unit vs its district peers.

    Returns None when inputs or peer coverage are insufficient (missing data
    is neutral — the caller must not penalize a None).
    """
    if not subject_psf or subject_psf <= 0 or subject_age is None or not district:
        return None

    district = str(district).upper()
    if not district.startswith("D"):
        district = f"D{int(district):02d}" if district.isdigit() else district
    region = _region_for_district(district)
    subject_slope = _slope_for(region, tenure)

    # --- Collect peers: same district, with transacted PSF and derivable age ---
    peers = []
    for entry in ura_data.values():
        if not isinstance(entry, dict) or entry.get("district") != district:
            continue
        psf = entry.get("median_psf") or entry.get("avg_psf_current")
        lease_start = entry.get("lease_start_year")
        if not psf or psf <= 0 or not lease_start:
            continue
        peer_age = current_year - (lease_start + _LEASE_TO_TOP_OFFSET)
        if peer_age < 0:
            peer_age = 0
        age_gap = subject_age - peer_age
        if abs(age_gap) > _MAX_ADJUST_YEARS:
            continue  # too far apart for the linear heuristic
        peer_slope = _slope_for(region, entry.get("tenure"))
        # Normalize the peer's PSF to the subject's age: a NEWER peer is
        # discounted (it would be cheaper at the subject's age), an OLDER
        # peer is marked up.
        adjusted_psf = psf - peer_slope * age_gap
        if adjusted_psf <= 0:
            continue
        peers.append({
            "name": entry.get("project_name"),
            "raw_psf": psf,
            "age": peer_age,
            "adjusted_psf": adjusted_psf,
            "txns": entry.get("transaction_count", 0),
        })

    if len(peers) < _MIN_PEERS:
        return None

    adjusted_median = median(p["adjusted_psf"] for p in peers)
    premium_pct = (subject_psf / adjusted_median - 1) * 100

    result: dict[str, Any] = {
        "district": district,
        "region": region,
        "subject_psf": round(subject_psf),
        "subject_age_years": round(subject_age, 1),
        "peer_count": len(peers),
        "age_adjusted_district_median_psf": round(adjusted_median),
        "premium_vs_age_adjusted_median_pct": round(premium_pct, 1),
        "psf_age_slope_per_year": round(subject_slope, 1),
        "note": (
            "Peers' transacted PSF normalized to the subject's age at "
            f"~${subject_slope:.0f}/psf/yr ({region}"
            f"{', freehold-adjusted' if tenure and 'freehold' in tenure.lower() else ''}). "
            "Negative premium = cheap for its age. Slope is a heuristic — verify."
        ),
    }

    # --- vs new launches: what should this unit cost given new-launch pricing? ---
    new_launches = [p for p in peers if p["age"] <= _NEW_LAUNCH_MAX_AGE]
    if len(new_launches) >= _MIN_NEW_LAUNCH_PEERS:
        nl_median = median(p["raw_psf"] for p in new_launches)
        nl_median_age = median(p["age"] for p in new_launches)
        implied_fair = nl_median - subject_slope * (subject_age - nl_median_age)
        result["new_launch_median_psf"] = round(nl_median)
        result["new_launch_count"] = len(new_launches)
        if implied_fair > 0:
            result["implied_fair_psf_from_new_launch"] = round(implied_fair)
            result["premium_vs_new_launch_implied_pct"] = round(
                (subject_psf / implied_fair - 1) * 100, 1
            )

    return result


def relative_value_for_listing(scored: Any, ura_data: dict, current_year: int) -> Optional[dict]:
    """Convenience wrapper taking a ScoredListing."""
    if not scored.psf or not scored.built_year or not scored.district:
        return None
    age = max(0, current_year - scored.built_year)
    return compute_relative_value(
        subject_psf=scored.psf,
        subject_age=age,
        district=scored.district,
        tenure=scored.tenure,
        ura_data=ura_data,
        current_year=current_year,
    )
