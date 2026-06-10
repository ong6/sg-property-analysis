"""Quick scorer for fast filtering of listings (Phase 1).

Uses only scraped data - no external lookups.
Designed to quickly filter out poor properties before detailed analysis.

v2.0: Aligned with new scoring system thresholds.
"""

import re
from datetime import datetime
from typing import Any, Optional

from scoring.models import QuickScore
from utils.geo import normalize_district

# Import config values
try:
    from config import SCORE_TIER1_MIN, SCORE_TIER2_MIN
except ImportError:
    SCORE_TIER1_MIN = 60
    SCORE_TIER2_MIN = 45


class QuickScorer:
    """
    Quick scoring engine for initial property filtering.

    v2.0 Scoring tiers (aligned with full scorer):
    - Tier 1 (score >= 60): Keep for detailed analysis
    - Tier 2 (score 45-59): Maybe, review if needed
    - Tier 3 (score < 45): Reject
    """

    TIER_1_MIN = SCORE_TIER1_MIN  # From config (default 60)
    TIER_2_MIN = SCORE_TIER2_MIN  # From config (default 45)

    # High-potential districts (dynamic - based on district scorer results)
    # These are districts with score >= 70 from DistrictScorer
    HIGH_POTENTIAL_DISTRICTS = {
        "D03", "D05", "D14", "D15",  # RCR Prime
        "D19", "D22",  # High-potential OCR (Punggol, Jurong)
        "03", "05", "14", "15", "19", "22", "3", "5"  # Numeric variants
    }

    def __init__(self):
        self._current_year = datetime.now().year

    def score(self, listing: dict[str, Any]) -> QuickScore:
        """
        Score a listing quickly using only scraped data.

        Args:
            listing: Dictionary with listing data

        Returns:
            QuickScore with score, tier, and breakdown
        """
        breakdown: dict[str, Any] = {}

        # === Hard Filters (auto-reject) ===
        remaining_lease = self._calculate_remaining_lease(listing)
        if remaining_lease is not None and remaining_lease < 60:
            return QuickScore(
                score=0,
                tier=3,
                reason=f"Remaining lease {remaining_lease} years < 60 years minimum",
                breakdown={"rejected": "lease_too_short"},
            )

        score = 0

        # === PSF Value (0-20 pts) ===
        psf = listing.get("psf")
        psf_score = 0
        if psf:
            if psf < 1800:
                psf_score = 20
            elif psf < 2000:
                psf_score = 15
            elif psf < 2200:
                psf_score = 10
            elif psf < 2400:
                psf_score = 5
        score += psf_score
        breakdown["psf_value"] = {"psf": psf, "points": psf_score}

        # === MRT Proximity (0-15 pts) ===
        mrt_distance = self._parse_mrt_distance(listing.get("mrt_info"))
        mrt_score = 0
        if mrt_distance is not None:
            if mrt_distance <= 300:
                mrt_score = 15
            elif mrt_distance <= 500:
                mrt_score = 12
            elif mrt_distance <= 800:
                mrt_score = 8
            elif mrt_distance <= 1000:
                mrt_score = 4
        score += mrt_score
        breakdown["mrt_proximity"] = {"distance_m": mrt_distance, "points": mrt_score}

        # === Tenure (0-10 pts) ===
        # 99-year leasehold preferred for short-term investment (better PSF, less capital tied up)
        tenure = (listing.get("tenure") or "").lower()
        tenure_score = 0
        if "99" in tenure or "99-year" in tenure:
            # Check if remaining lease is good
            if remaining_lease is None or remaining_lease >= 90:
                tenure_score = 10
            elif remaining_lease >= 80:
                tenure_score = 8
            elif remaining_lease >= 70:
                tenure_score = 5
        elif "freehold" in tenure or "999" in tenure:
            tenure_score = 7  # Less preferred for short-term due to premium
        score += tenure_score
        breakdown["tenure"] = {
            "tenure": tenure,
            "remaining_lease": remaining_lease,
            "points": tenure_score,
        }

        # === Property Age (0-10 pts) ===
        # v2.2: Scores physical condition only.
        # Data reliability bias is handled by full scorer (confidence scaling,
        # new-launch discount, momentum scoring).
        built_year = listing.get("built_year")
        age_score = 0
        current_year = self._current_year
        if built_year:
            age = current_year - built_year
            if age < 0:
                age = 0
            # 3-7yr sweet spot (matches full scorer): brand-new units score
            # slightly below max to avoid double-rewarding launch-premium pricing.
            if age <= 2:
                age_score = 8   # Brand new — premium pricing risk
            elif age <= 7:
                age_score = 10  # Sweet spot
            elif age <= 12:
                age_score = 8   # Good condition
            elif age <= 17:
                age_score = 5   # Aging
            elif age <= 21:
                age_score = 3
        score += age_score
        breakdown["property_age"] = {
            "built_year": built_year,
            "age_years": current_year - built_year if built_year else None,
            "points": age_score,
        }

        # === District (0-10 pts) ===
        # High-potential districts preferred (based on v2.0 district scoring)
        district = normalize_district(listing.get("district", "")) or listing.get("district", "")
        district_score = 0
        if district:
            # Normalize district code
            d = district.upper().replace("D", "")
            # High potential: RCR Prime + high-scoring OCR (D19 Punggol, D22 Jurong)
            if d in {"3", "03", "5", "05", "14", "15", "19", "22"}:
                district_score = 10  # High potential districts
            elif d in {"4", "04", "18", "20", "25"}:
                district_score = 7  # Medium potential (GSW, Tampines, AMK, Woodlands)
            elif d in {"12", "13", "16", "17", "23", "26", "27"}:
                district_score = 5  # Other OCR/RCR
            elif d in {"1", "01", "2", "02", "6", "06", "7", "07", "9", "09", "10", "11"}:
                district_score = 4  # CCR (high entry cost, lower yield)
        score += district_score
        breakdown["district"] = {"district": district, "points": district_score}

        # === Bedroom Configuration (0-8 pts) ===
        # 2BR most rentable, 3BR good for families
        beds = listing.get("beds")
        beds_score = 0
        if beds == 2:
            beds_score = 8
        elif beds == 3:
            beds_score = 6
        elif beds == 1:
            beds_score = 4
        elif beds == 4:
            beds_score = 3
        score += beds_score
        breakdown["bedrooms"] = {"beds": beds, "points": beds_score}

        # === Size Efficiency (0-7 pts) ===
        # Optimal sqft per bedroom for rental efficiency
        sqft = listing.get("sqft")
        efficiency_score = 0
        if sqft and beds:
            sqft_per_bed = sqft / beds
            if beds == 2:
                # 2BR optimal: 400-550 sqft/bed
                if 400 <= sqft_per_bed <= 550:
                    efficiency_score = 7
                elif 350 <= sqft_per_bed <= 600:
                    efficiency_score = 5
            elif beds == 3:
                # 3BR optimal: 350-450 sqft/bed
                if 350 <= sqft_per_bed <= 450:
                    efficiency_score = 7
                elif 300 <= sqft_per_bed <= 500:
                    efficiency_score = 5
            elif beds == 1:
                # 1BR optimal: 500-700 sqft total
                if 500 <= sqft <= 700:
                    efficiency_score = 7
                elif 450 <= sqft <= 800:
                    efficiency_score = 5
        score += efficiency_score
        breakdown["efficiency"] = {
            "sqft": sqft,
            "beds": beds,
            "sqft_per_bed": round(sqft / beds, 1) if sqft and beds else None,
            "points": efficiency_score,
        }

        # === Development Size (0-5 pts) ===
        # Larger developments = more amenities, better liquidity
        total_units = listing.get("total_units")
        dev_score = 0
        if total_units:
            if total_units >= 600:
                dev_score = 5
            elif total_units >= 400:
                dev_score = 4
            elif total_units >= 200:
                dev_score = 3
            elif total_units >= 100:
                dev_score = 2
        score += dev_score
        breakdown["dev_size"] = {"total_units": total_units, "points": dev_score}

        # === Red Flags (-15 pts max) ===
        red_flags = []
        red_flag_penalty = 0

        # West-facing (hot in afternoon)
        facing = (listing.get("facing") or "").lower()
        if "west" in facing:
            red_flag_penalty += 2
            red_flags.append("west_facing")

        # Old property (needs more maintenance, harder resale)
        if built_year:
            age = current_year - built_year
            if age > 20:
                red_flag_penalty += 5
                red_flags.append(f"very_old_property_{age}yrs")
            elif age > 15:
                red_flag_penalty += 3
                red_flags.append(f"old_property_{age}yrs")

        # Small development (liquidity risk)
        if total_units and total_units < 100:
            red_flag_penalty += 3
            red_flags.append("small_development")

        # Low remaining lease (already penalized in tenure, but extra penalty)
        if remaining_lease and remaining_lease < 70:
            red_flag_penalty += 4
            red_flags.append("low_lease")

        score -= min(red_flag_penalty, 15)  # Cap at -15
        score = max(0, score)  # Prevent negative quick scores
        breakdown["red_flags"] = {"flags": red_flags, "penalty": red_flag_penalty}

        # Calculate tier
        tier = self._determine_tier(score)

        return QuickScore(
            score=score,
            tier=tier,
            reason=None,
            breakdown=breakdown,
        )

    def _determine_tier(self, score: int) -> int:
        """Determine tier based on score."""
        if score >= self.TIER_1_MIN:
            return 1
        elif score >= self.TIER_2_MIN:
            return 2
        else:
            return 3

    # Typical gap between lease commencement and TOP (years)
    _LEASE_START_OFFSET = 3

    def _calculate_remaining_lease(self, listing: dict[str, Any]) -> Optional[int]:
        """Calculate remaining lease years from tenure and built year.

        For 99-year leases, the lease starts when the developer acquires the
        land, typically 3-4 years before TOP. We subtract this offset from
        built_year to avoid overstating remaining lease.
        """
        tenure = (listing.get("tenure") or "").lower()
        current_year = self._current_year

        if "freehold" in tenure or "999" in tenure:
            return 999  # Effectively infinite

        # Extract lease years (e.g., "99-year leasehold")
        lease_match = re.search(r"(\d+)[\s-]?year", tenure)
        if lease_match:
            lease_years = int(lease_match.group(1))
        elif "99" in tenure:
            lease_years = 99
        else:
            return None

        # Prefer explicit lease_start_year if available
        lease_start = listing.get("lease_start_year")
        if lease_start:
            elapsed = current_year - lease_start
            return max(0, lease_years - elapsed)

        # Need built year or TOP year to calculate remaining
        start_year = listing.get("built_year") or listing.get("top_year")
        if not start_year:
            # If no year info, use conservative mid-life estimate for 99-year
            if lease_years == 99:
                return 75  # Assume ~24 years old — conservative, avoids masking old leaseholds
            return None

        # For 99-year leases, subtract offset to approximate lease commencement
        if lease_years == 99:
            estimated_lease_start = start_year - self._LEASE_START_OFFSET
            elapsed = current_year - estimated_lease_start
        else:
            elapsed = current_year - start_year

        remaining = lease_years - elapsed
        return max(0, remaining)

    def _parse_mrt_distance(self, mrt_info: Optional[str]) -> Optional[int]:
        """Extract MRT distance in meters from mrt_info string."""
        if not mrt_info:
            return None

        # Common patterns: "300m", "5 min walk", "5 mins", "300 m"
        mrt_info = mrt_info.lower()

        # Direct distance in meters
        m_match = re.search(r"(\d+)\s*m(?:eters?)?(?:\s|$|,)", mrt_info)
        if m_match:
            return int(m_match.group(1))

        # Walking time (estimate 80m per minute)
        min_match = re.search(r"(\d+)\s*min", mrt_info)
        if min_match:
            minutes = int(min_match.group(1))
            return minutes * 80  # Approximate distance

        return None


def filter_listings(
    listings: list[dict],
    min_tier: int = 2,
) -> dict[str, list[tuple[dict, QuickScore]]]:
    """
    Filter and categorize listings by tier.

    Args:
        listings: List of listing dictionaries
        min_tier: Minimum tier to include (1, 2, or 3)

    Returns:
        Dict with "tier1", "tier2", "rejected" lists of (listing, score) tuples
    """
    scorer = QuickScorer()
    results = {"tier1": [], "tier2": [], "rejected": []}

    for listing in listings:
        qs = scorer.score(listing)
        if qs.tier == 1:
            results["tier1"].append((listing, qs))
        elif qs.tier == 2:
            results["tier2"].append((listing, qs))
        else:
            results["rejected"].append((listing, qs))

    # Sort by score within each tier
    for tier in results:
        results[tier].sort(key=lambda x: x[1].score, reverse=True)

    return results
