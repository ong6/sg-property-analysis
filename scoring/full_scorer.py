"""Full scoring engine for property investment analysis.

Implements the complete 100-point scoring system optimized for
short-medium term (3-7 year) condo investment in Singapore.

SCORING SYSTEM v2.2 (new-launch bias correction):
- Rental Yield: 15 pts (yields mechanically low at $2M+)
- Capital Appreciation: 30 pts (uses real transaction data for ALL properties)
- Future Potential: 20 pts (infrastructure, govt plans, growth factors)
- Liquidity: 25 pts (important for exit)
- Cost Efficiency: 10 pts
- Red Flags: -10 pts max

Max possible: 100 pts for ALL properties equally.

v2.2 changes:
- NEW: New-launch appreciation bias correction. Properties ≤7 years old have
  their excess appreciation (above regional baseline) discounted by a decay
  curve to account for the developer-to-market price transition effect.
- NEW: URA data now separates "New Sale" vs "Resale" transactions. When a
  project has significant new-sale proportion, resale-only CAGR is preferred.
- CHANGED: Property age scoring rebalanced. Sweet spot moved from ≤5yr to 3-7yr
  to avoid double-rewarding young properties with both inflated appreciation
  and maximum age points.
"""

import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Optional

from scoring.costs import CostCalculator
from scoring.models import QuickScore, ScoredListing, ROIResult, FutureScore
from scoring.quick_scorer import QuickScorer
from scoring.rental_estimator import RentalEstimator
from scoring.roi import ROICalculator
from scoring.future_scorer import FutureScorer
from utils.geo import get_mrt_distance, get_tenant_pool_score, find_nearest_mrt, normalize_district

# New launch bias adjustment config
try:
    from config import (
        REGIONAL_APPRECIATION_BASELINES,
        DEFAULT_REGIONAL_APPRECIATION,
        NEW_LAUNCH_DISCOUNT_CURVE,
        NEW_SALE_PROPORTION_THRESHOLD,
        SCORE_WEIGHT_RENTAL_YIELD,
        SCORE_WEIGHT_CAPITAL_APPRECIATION,
        SCORE_WEIGHT_FUTURE_POTENTIAL,
        SCORE_WEIGHT_LIQUIDITY,
        SCORE_WEIGHT_COST_EFFICIENCY,
    )
except ImportError:
    REGIONAL_APPRECIATION_BASELINES = {"CCR": 0.045, "RCR": 0.058, "OCR": 0.037}
    DEFAULT_REGIONAL_APPRECIATION = 0.04
    NEW_LAUNCH_DISCOUNT_CURVE = {1: 0.60, 3: 0.45, 5: 0.30, 7: 0.15}
    NEW_SALE_PROPORTION_THRESHOLD = 0.20
    SCORE_WEIGHT_RENTAL_YIELD = 15
    SCORE_WEIGHT_CAPITAL_APPRECIATION = 30
    SCORE_WEIGHT_FUTURE_POTENTIAL = 20
    SCORE_WEIGHT_LIQUIDITY = 25
    SCORE_WEIGHT_COST_EFFICIENCY = 10

# Optional transaction scrapers for real appreciation data
try:
    from scrapers.transaction_scraper import TransactionScraper
    _HAS_TRANSACTION_SCRAPER = True
except ImportError:
    _HAS_TRANSACTION_SCRAPER = False

# URA scraper for official government transaction data (5 years history)
try:
    from scrapers.ura_scraper import URAScraper, load_ura_csv
    _HAS_URA_SCRAPER = True
except ImportError:
    _HAS_URA_SCRAPER = False


# Load district data
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_DISTRICT_MEDIANS_FILE = os.path.join(_DATA_DIR, "district_medians.json")


_district_data_cache: dict | None = None


def _load_district_data() -> dict:
    """Load district median data (module-level cache)."""
    global _district_data_cache
    if _district_data_cache is None:
        if os.path.exists(_DISTRICT_MEDIANS_FILE):
            with open(_DISTRICT_MEDIANS_FILE) as f:
                _district_data_cache = json.load(f)
        else:
            _district_data_cache = {"medians": {}, "district_info": {}}
    return _district_data_cache


class FullScorer:
    """
    Complete scoring engine for property investment analysis.

    SCORING SYSTEM v2.1 (100 points max, no URA bias):
    - Rental Yield Potential: 15 pts
    - Capital Appreciation: 30 pts (real transaction data for ALL properties)
    - Future Potential: 20 pts (MRT, govt zones, transformation)
    - Liquidity & Exit Risk: 25 pts
    - Cost Efficiency: 10 pts
    - Red Flag Deductions: -10 pts max

    Designed for Singapore Citizen, first property (0% ABSD),
    5-7 year holding period, $2.2M-$2.7M price range.
    """

    # Score weights (v2.1 - from config)
    WEIGHT_RENTAL_YIELD = SCORE_WEIGHT_RENTAL_YIELD
    WEIGHT_CAPITAL_APPRECIATION = SCORE_WEIGHT_CAPITAL_APPRECIATION
    WEIGHT_FUTURE_POTENTIAL = SCORE_WEIGHT_FUTURE_POTENTIAL
    WEIGHT_LIQUIDITY = SCORE_WEIGHT_LIQUIDITY
    WEIGHT_COST_EFFICIENCY = SCORE_WEIGHT_COST_EFFICIENCY

    # Buyer pool depth scoring (replaces static district popularity)
    # Derived from district_profiles.json: condo density + HDB upgrader pool + employment centers
    BUYER_POOL_SCORES = {
        "very_deep": 7,  # Dense condo belt + large HDB upgrader pool (D14, D15, D19)
        "deep": 5,       # Substantial condo stock + good upgrader catchment
        "moderate": 3,   # Decent but smaller buyer pool
        "shallow": 1,    # Few condos, niche market
    }

    # Price sweet spot for liquidity
    LIQUIDITY_SWEET_SPOT = (1_800_000, 2_500_000)

    # Tier thresholds (v2.0 - adjusted for better distribution)
    TIER1_MIN_SCORE = 60  # Was 65 - now with broader range
    TIER2_MIN_SCORE = 45  # Was 45 - unchanged

    def __init__(
        self,
        condo_rental_data: Optional[dict] = None,
        transaction_data: Optional[dict] = None,
        fetch_appreciation: bool = False,
        ura_data: Optional[dict] = None,
    ):
        """
        Initialize full scorer.

        Args:
            condo_rental_data: Dict mapping project_name -> median rent_psf
            transaction_data: Dict mapping project_name -> transaction history data
            fetch_appreciation: Whether to fetch real appreciation data from PropertyGuru
            ura_data: Dict mapping project_name -> URA appreciation data (from official govt source)
        """
        self._current_year = datetime.now().year
        self.quick_scorer = QuickScorer()
        self.rental_estimator = RentalEstimator(condo_rental_data)
        self.roi_calculator = ROICalculator()
        self.cost_calculator = CostCalculator()
        self.future_scorer = FutureScorer()  # NEW
        self.district_data = _load_district_data()
        self.district_profiles = self._load_district_profiles()
        self.transaction_data = transaction_data or {}
        self.ura_data = ura_data or {}  # URA official data takes priority
        self.fetch_appreciation = fetch_appreciation and _HAS_TRANSACTION_SCRAPER
        self._transaction_scraper = None
        self._appreciation_cache: dict[str, tuple[float, str]] = {}

    def _get_transaction_scraper(self):
        """Lazy-load transaction scraper."""
        if self._transaction_scraper is None and _HAS_TRANSACTION_SCRAPER:
            self._transaction_scraper = TransactionScraper()
        return self._transaction_scraper

    @staticmethod
    def _load_district_profiles() -> dict:
        """Load district profiles (buyer pool depth, transaction volume, etc.)."""
        profiles_file = os.path.join(_DATA_DIR, "district_profiles.json")
        if os.path.exists(profiles_file):
            with open(profiles_file) as f:
                return json.load(f).get("districts", {})
        return {}

    def _fuzzy_ura_lookup(self, project_key: str) -> Optional[dict]:
        """Look up URA data with fuzzy matching.

        Tries exact match first, then falls back to substring matching
        (stripping parentheticals and normalizing whitespace) to handle
        title variants like "PARC ESTA (Eunos)" vs cache key "parc esta".
        """
        # Exact match
        if project_key in self.ura_data:
            return self.ura_data[project_key]

        if not project_key:
            return None

        # Normalize: strip parentheticals, extra spaces, special chars
        normalized = re.sub(r'\s*\(.*?\)', '', project_key).strip()
        normalized = re.sub(r'\s+', ' ', normalized)

        # Try normalized exact match
        if normalized != project_key and normalized in self.ura_data:
            return self.ura_data[normalized]

        # Substring match (same pattern as RentalEstimator)
        for cache_key in self.ura_data:
            if cache_key in normalized or normalized in cache_key:
                return self.ura_data[cache_key]

        return None

    def _get_appreciation_rate(
        self,
        listing: dict,
    ) -> tuple[float, str]:
        """
        Get appreciation rate for a listing.

        Priority order:
        1. URA resale-only data (if available and property has new-launch bias)
        2. URA official data (5 years history, most reliable)
        3. Pre-fetched transaction data (PropertyGuru)
        4. Live fetch from PropertyGuru API
        5. Default 2% assumption

        Returns:
            Tuple of (annual_rate_decimal, source)
            source: "ura_resale_only", "ura_5yr_cagr", "ura_3yr_avg", "transaction_data", "default"
        """
        project_name = listing.get("title") or listing.get("project_name", "")
        district = listing.get("district", "")
        cache_key = f"{project_name}_{district}".lower()
        project_key = project_name.lower()

        # Check cache first
        if cache_key in self._appreciation_cache:
            return self._appreciation_cache[cache_key]

        # Priority 1: URA official data (most reliable - 5 years govt data)
        data = self._fuzzy_ura_lookup(project_key)
        if data is not None:

            # NEW: Prefer resale-only appreciation if new-launch bias detected
            if data.get("has_new_launch_bias") and data.get("resale_annualized_appreciation") is not None:
                rate = data["resale_annualized_appreciation"] / 100
                rate = max(-0.05, min(0.15, rate))
                source = "ura_resale_only"
                result = (rate, source)
                self._appreciation_cache[cache_key] = result
                return result

            if data.get("annualized_appreciation") is not None:
                rate = data["annualized_appreciation"] / 100  # Convert % to decimal
                rate = max(-0.05, min(0.15, rate))  # Cap between -5% and 15%
                source = data.get("source", "ura_data")
                result = (rate, source)
                self._appreciation_cache[cache_key] = result
                return result

        # Priority 2: Pre-fetched transaction data (PropertyGuru)
        if project_name and cache_key in self.transaction_data:
            data = self.transaction_data[cache_key]
            if data.get("annualized_appreciation") is not None:
                rate = data["annualized_appreciation"] / 100  # Convert % to decimal
                rate = max(-0.05, min(0.10, rate))  # Cap between -5% and 10%
                result = (rate, "transaction_data")
                self._appreciation_cache[cache_key] = result
                return result

        # Priority 3: Fetch live from PropertyGuru API
        if self.fetch_appreciation:
            scraper = self._get_transaction_scraper()
            if scraper:
                try:
                    rate_pct, source = scraper.get_appreciation_rate(
                        project_name,
                        district,
                        default_rate=2.0,
                    )
                    rate = rate_pct / 100  # Convert % to decimal
                    result = (rate, source)
                    self._appreciation_cache[cache_key] = result
                    print(
                        f"  {project_name}: {rate_pct:+.1f}%/yr ({source})",
                        file=sys.stderr,
                    )
                    return result
                except Exception as e:
                    print(f"  {project_name}: failed to fetch ({e})", file=sys.stderr)

        # Fallback: Default 2% appreciation
        return (0.02, "default")

    def _get_regional_baseline(self, listing: dict) -> float:
        """Get regional baseline appreciation rate for a listing's location."""
        district = listing.get("district", "")
        d = district.upper().replace("D", "").strip()
        try:
            d_num = int(d)
        except ValueError:
            return DEFAULT_REGIONAL_APPRECIATION

        # Map district to region (CCR/RCR/OCR)
        ccr_districts = {1, 2, 6, 9, 10, 11}
        rcr_districts = {3, 4, 5, 7, 8, 12, 13, 14, 15, 20}
        # Everything else is OCR

        if d_num in ccr_districts:
            return REGIONAL_APPRECIATION_BASELINES.get("CCR", DEFAULT_REGIONAL_APPRECIATION)
        elif d_num in rcr_districts:
            return REGIONAL_APPRECIATION_BASELINES.get("RCR", DEFAULT_REGIONAL_APPRECIATION)
        else:
            return REGIONAL_APPRECIATION_BASELINES.get("OCR", DEFAULT_REGIONAL_APPRECIATION)

    def _apply_new_launch_adjustment(
        self,
        raw_rate: float,
        listing: dict,
        appreciation_source: str,
    ) -> tuple[float, float, str]:
        """
        Adjust appreciation rate to account for new-launch bias.

        New launches show inflated appreciation in their first ~5-7 years
        because transaction data mixes developer pricing with market pricing.
        This method discounts the "excess" appreciation above the regional
        baseline for young properties.

        Args:
            raw_rate: Raw appreciation rate (decimal, e.g., 0.06 = 6%/yr)
            listing: Listing dict with built_year, district, etc.
            appreciation_source: Source of the raw rate

        Returns:
            Tuple of (adjusted_rate, adjustment_amount, reason)
            adjustment_amount is negative (how much was discounted)
        """
        built_year = listing.get("built_year")
        current_year = self._current_year

        # No adjustment if:
        # - No built_year known (can't determine age)
        # - Source is already "ura_resale_only" (already filtered for bias)
        # - Source is "default" (nothing to adjust)
        # - Rate is negative (no excess to discount)
        if (
            not built_year
            or appreciation_source == "ura_resale_only"
            or appreciation_source == "default"
            or raw_rate <= 0
        ):
            return raw_rate, 0.0, ""

        age = current_year - built_year
        if age < 0:
            age = 0

        # Find applicable discount factor from the decay curve
        discount_factor = 0.0
        for age_max in sorted(NEW_LAUNCH_DISCOUNT_CURVE.keys()):
            if age <= age_max:
                discount_factor = NEW_LAUNCH_DISCOUNT_CURVE[age_max]
                break

        if discount_factor <= 0:
            return raw_rate, 0.0, ""  # Property is mature, no adjustment

        # Get regional baseline
        regional_baseline = self._get_regional_baseline(listing)

        # Only discount the "excess" above regional baseline
        excess = raw_rate - regional_baseline
        if excess <= 0:
            return raw_rate, 0.0, ""  # Rate is at or below baseline, no excess

        adjustment = -(excess * discount_factor)
        adjusted_rate = raw_rate + adjustment

        # Never adjust below the regional baseline
        adjusted_rate = max(adjusted_rate, regional_baseline)
        actual_adjustment = adjusted_rate - raw_rate

        reason = (
            f"New launch bias adj: {age}yr old property, "
            f"{discount_factor:.0%} discount on {excess*100:.1f}% excess "
            f"above {regional_baseline*100:.1f}% regional baseline"
        )

        return adjusted_rate, actual_adjustment, reason

    def score(self, listing: dict[str, Any]) -> ScoredListing:
        """
        Perform full scoring of a listing.

        Args:
            listing: Listing dictionary from scraper

        Returns:
            ScoredListing with complete scoring and ROI analysis
        """
        # Start with quick score
        quick_score = self.quick_scorer.score(listing)

        # Create scored listing
        scored = ScoredListing(
            id=str(listing.get("id", "")),
            title=listing.get("title", ""),
            price=listing.get("price", 0),
            url=listing.get("url", ""),
            address=listing.get("address"),
            district=listing.get("district"),
            district_name=listing.get("district_name"),
            beds=listing.get("beds"),
            baths=listing.get("baths"),
            sqft=listing.get("sqft"),
            psf=listing.get("psf"),
            tenure=listing.get("tenure"),
            built_year=listing.get("built_year"),
            project_name=listing.get("project_name"),
            total_units=listing.get("total_units"),
            mrt_info=listing.get("mrt_info"),
            latitude=listing.get("latitude"),
            longitude=listing.get("longitude"),
            image_url=listing.get("image_url"),
            listing_date=listing.get("listing_date"),
            quick_score=quick_score.score,
            quick_tier=quick_score.tier,
        )

        # Calculate remaining lease
        scored.remaining_lease = self._calculate_remaining_lease(listing)

        # Get MRT distance
        mrt_result = get_mrt_distance(listing)
        if mrt_result:
            scored.nearest_mrt, scored.mrt_distance_m = mrt_result

        # Get appreciation rate FIRST (needed for capital appreciation scoring)
        raw_rate, appreciation_source = self._get_appreciation_rate(listing)

        # Apply new-launch bias adjustment
        adjusted_rate, adjustment, adj_reason = self._apply_new_launch_adjustment(
            raw_rate, listing, appreciation_source,
        )

        scored.raw_appreciation_rate = raw_rate
        scored.appreciation_rate = adjusted_rate  # Use adjusted rate everywhere
        scored.appreciation_source = appreciation_source
        scored.appreciation_adjustment = adjustment
        scored.adjustment_reason = adj_reason

        # Use the adjusted rate for scoring
        appreciation_rate = adjusted_rate

        # Score each category
        breakdown = {}

        # A. Rental Yield Potential (15 pts - reduced from 30)
        rental_scores = self._score_rental_yield(listing, scored)
        scored.rental_yield_score = rental_scores["total"]
        breakdown["rental_yield"] = rental_scores

        # B. Capital Appreciation (30 pts - uses actual URA data, bias-adjusted)
        cap_scores = self._score_capital_appreciation(listing, scored, appreciation_rate, appreciation_source)
        scored.capital_appreciation_score = cap_scores["total"]
        breakdown["capital_appreciation"] = cap_scores

        # C. Future Potential (20 pts - NEW)
        future_score = self.future_scorer.score(listing)
        scored.future_potential_score = future_score.total
        scored.future_score_details = future_score
        breakdown["future_potential"] = future_score.to_dict()

        # D. Liquidity & Exit Risk (20 pts)
        liquidity_scores = self._score_liquidity(listing, scored)
        scored.liquidity_score = liquidity_scores["total"]
        breakdown["liquidity"] = liquidity_scores

        # E. Cost Efficiency (10 pts)
        cost_scores = self._score_cost_efficiency(listing, scored)
        scored.cost_efficiency_score = cost_scores["total"]
        breakdown["cost_efficiency"] = cost_scores

        # F. Red Flags (-10 pts max)
        red_flag_scores = self._score_red_flags(listing, scored)
        scored.red_flag_deductions = red_flag_scores["total"]
        breakdown["red_flags"] = red_flag_scores

        # URA bonus removed in v2.1 — no bias for having cached data
        scored.ura_bonus_score = 0

        scored.score_breakdown = breakdown

        # Calculate ROI for different periods
        if scored.sqft and scored.price:
            roi_results = self.roi_calculator.calculate_multiple_periods(
                listing,
                periods=[5, 6, 7],
                monthly_rent=scored.estimated_monthly_rent if scored.estimated_monthly_rent > 0 else None,
                appreciation_rate=appreciation_rate,
            )
            scored.roi_5yr = roi_results.get(5)
            scored.roi_6yr = roi_results.get(6)
            scored.roi_7yr = roi_results.get(7)

            # Create cost breakdown
            scored.costs = self.cost_calculator.create_cost_breakdown(
                purchase_price=scored.price,
                sqft=scored.sqft,
                monthly_rent=scored.estimated_monthly_rent,
                hold_years=5,
            )

        return scored

    def _score_rental_yield(self, listing: dict, scored: ScoredListing) -> dict:
        """
        Score rental yield potential (15 pts max - reduced from 30).

        v2.0: Weight reduced because yields are mechanically low at $2M+ prices.

        Components:
        - Gross Yield vs Market: 0-6 pts (was 0-12)
        - MRT Proximity: 0-4 pts (was 0-8)
        - Unit Config: 0-3 pts (was 0-5)
        - Tenant Pool: 0-2 pts (was 0-5)
        """
        scores = {"total": 0}

        # Estimate rental
        rental_data = self.rental_estimator.estimate_yield_score(listing)
        scored.estimated_monthly_rent = rental_data["monthly_rent"]
        scored.estimated_gross_yield = rental_data["gross_yield"]
        scored.rent_source = rental_data["source"]

        # Gross Yield Score (0-6 pts) - was 0-12
        raw_yield_score = rental_data["gross_yield_score"]
        yield_score = min(6, round(raw_yield_score / 2))  # Scale down from 12 to 6
        scores["gross_yield"] = {
            "yield_pct": rental_data["gross_yield"],
            "points": yield_score,
        }
        scores["total"] += yield_score

        # MRT Proximity (0-4 pts) - was 0-8
        mrt_score = 0
        mrt_distance = scored.mrt_distance_m
        if mrt_distance is not None:
            if mrt_distance <= 300:
                mrt_score = 4
            elif mrt_distance <= 500:
                mrt_score = 3
            elif mrt_distance <= 800:
                mrt_score = 2
            elif mrt_distance <= 1000:
                mrt_score = 1
        scores["mrt_proximity"] = {
            "distance_m": mrt_distance,
            "station": scored.nearest_mrt,
            "points": mrt_score,
        }
        scores["total"] += mrt_score

        # Unit Configuration (0-3 pts) - was 0-5
        beds = listing.get("beds")
        config_score = 0
        if beds == 2:
            config_score = 3  # Most rentable
        elif beds == 3:
            config_score = 2  # Good for families
        elif beds == 1:
            config_score = 2  # Smaller tenant pool
        elif beds == 4:
            config_score = 1
        scores["unit_config"] = {"beds": beds, "points": config_score}
        scores["total"] += config_score

        # Tenant Pool (0-2 pts) - was 0-5
        lat = listing.get("latitude")
        lon = listing.get("longitude")
        pool_score = 1  # Default
        pool_reason = "Unknown location"
        if lat and lon:
            raw_pool_score, pool_reason = get_tenant_pool_score(lat, lon)
            pool_score = min(2, round(raw_pool_score / 2))  # Scale down
        scores["tenant_pool"] = {"reason": pool_reason, "points": pool_score}
        scores["total"] += pool_score

        return scores

    def _get_transaction_count(self, listing: dict) -> int:
        """Get transaction count for a listing from URA or transaction data."""
        project_name = listing.get("title") or listing.get("project_name", "")
        project_key = project_name.lower()
        district = listing.get("district", "")

        # Check URA data first (fuzzy)
        ura_entry = self._fuzzy_ura_lookup(project_key)
        if ura_entry:
            return ura_entry.get("transaction_count", 0)

        # Check transaction data (compound key)
        txn_key = f"{project_name}_{district}".lower()
        txn_data = self.transaction_data.get(txn_key) or self.transaction_data.get(project_key)
        if isinstance(txn_data, dict):
            return txn_data.get("transaction_count", 0)

        return 0

    def _get_momentum(self, listing: dict) -> tuple[Optional[float], str]:
        """Get appreciation momentum and data coverage for a listing."""
        project_name = listing.get("title") or listing.get("project_name", "")
        project_key = project_name.lower()

        data = self._fuzzy_ura_lookup(project_key)
        if data:
            return (
                data.get("appreciation_momentum"),
                data.get("data_coverage", "none"),
            )

        return None, "none"

    def _score_capital_appreciation(
        self,
        listing: dict,
        scored: ScoredListing,
        appreciation_rate: float,
        appreciation_source: str,
    ) -> dict:
        """
        Score capital appreciation potential (30 pts max).

        v2.2: Includes confidence scaling based on transaction count,
        and momentum scoring based on appreciation trend direction.

        Components:
        - Actual Appreciation Rate: 0-14 pts (reduced from 17, freed pts for momentum)
        - Momentum (trend direction): -2 to +3 pts (NEW)
        - PSF vs District Median: 0-5 pts
        - Tenure: 0-4 pts
        - Property Age: 0-4 pts
        """
        scores = {"total": 0}
        current_year = self._current_year

        # --- Actual Appreciation Rate (0-14 pts, was 0-17) ---
        # Reduced by 3 pts to make room for momentum scoring.
        apr_pct = appreciation_rate * 100
        apr_score = 0

        if apr_pct >= 6.0:
            apr_score = 14  # Outstanding appreciation (>=6%/yr)
        elif apr_pct >= 5.0:
            apr_score = 12  # Excellent (5-6%/yr)
        elif apr_pct >= 4.0:
            apr_score = 10  # Very good (4-5%/yr)
        elif apr_pct >= 3.0:
            apr_score = 7  # Good (3-4%/yr)
        elif apr_pct >= 2.0:
            apr_score = 5  # Average (2-3%/yr)
        elif apr_pct >= 1.0:
            apr_score = 2  # Below average (1-2%/yr)
        elif apr_pct >= 0:
            apr_score = 0  # Flat (0-1%/yr)
        else:
            apr_score = -3  # Negative appreciation (penalty)

        # If using default data (no real transaction data found), cap lower
        if appreciation_source == "default":
            apr_score = min(apr_score, 4)  # Cap at 4 — incentivize getting real data

        # Confidence scaling: reduce score for low transaction counts
        # Full confidence at 50+ txns, scaling down to 60% at <10 txns
        txn_count = self._get_transaction_count(listing)
        confidence = 1.0
        if txn_count > 0 and appreciation_source != "default":
            if txn_count >= 50:
                confidence = 1.0
            elif txn_count >= 20:
                confidence = 0.9
            elif txn_count >= 10:
                confidence = 0.8
            else:
                confidence = 0.6  # Very low confidence
            apr_score = round(apr_score * confidence)

        scores["appreciation_rate"] = {
            "rate_pct": round(apr_pct, 2),
            "source": appreciation_source,
            "points": apr_score,
            "confidence": round(confidence, 2),
            "transaction_count": txn_count,
        }
        scores["total"] += max(0, apr_score)  # Don't go negative in total

        # --- Momentum Scoring (-2 to +3 pts, NEW) ---
        # Rewards accelerating appreciation, penalizes deceleration.
        # A property gaining momentum (recent > long-term) is more attractive
        # than one losing steam (recent < long-term).
        momentum, data_coverage = self._get_momentum(listing)
        momentum_score = 0
        if momentum is not None:
            if momentum >= 0.4:
                momentum_score = 3   # Strong acceleration
            elif momentum >= 0.15:
                momentum_score = 2   # Moderate acceleration
            elif momentum >= 0.0:
                momentum_score = 1   # Stable/slight acceleration
            elif momentum >= -0.2:
                momentum_score = 0   # Mild deceleration (acceptable)
            elif momentum >= -0.5:
                momentum_score = -1  # Significant deceleration
            else:
                momentum_score = -2  # Severe deceleration (warning)

        scores["momentum"] = {
            "value": round(momentum, 3) if momentum is not None else None,
            "data_coverage": data_coverage,
            "points": momentum_score,
        }
        scores["total"] += momentum_score

        # PSF vs District Median (0-5 pts) - was 0-10
        psf = listing.get("psf")
        district = normalize_district(listing.get("district", ""))
        psf_score = 0

        medians = self.district_data.get("medians", {})
        if psf and district in medians:
            median_psf = medians[district].get("psf", 2000)
            ratio = psf / median_psf if median_psf > 0 else 1
            if ratio <= 0.85:
                psf_score = 5  # 15%+ below median
            elif ratio <= 0.95:
                psf_score = 4  # 5-15% below median
            elif ratio <= 1.00:
                psf_score = 3  # At or below median
            elif ratio <= 1.10:
                psf_score = 1  # Up to 10% above
            scores["psf_vs_median"] = {
                "psf": psf,
                "median": median_psf,
                "ratio": round(ratio, 2),
                "points": psf_score,
            }
        else:
            # Fallback scoring
            if psf:
                if psf < 1800:
                    psf_score = 5
                elif psf < 2000:
                    psf_score = 4
                elif psf < 2200:
                    psf_score = 2
                elif psf < 2400:
                    psf_score = 1
            scores["psf_vs_median"] = {"psf": psf, "median": None, "points": psf_score}
        scores["total"] += psf_score

        # Tenure (0-4 pts) - was 0-5
        tenure = (listing.get("tenure") or "").lower()
        remaining = scored.remaining_lease
        tenure_score = 0

        if "freehold" in tenure or "999" in tenure:
            tenure_score = 2  # FH has premium, less upside
        elif "99" in tenure:
            if remaining and remaining >= 90:
                tenure_score = 4
            elif remaining and remaining >= 80:
                tenure_score = 3
            elif remaining and remaining >= 70:
                tenure_score = 1
        scores["tenure"] = {
            "tenure": tenure,
            "remaining_years": remaining,
            "points": tenure_score,
        }
        scores["total"] += tenure_score

        # Property Age (0-4 pts)
        # v2.2: This now scores PHYSICAL CONDITION value only.
        # Data reliability bias is handled separately by:
        #   - Confidence scaling (transaction count)
        #   - New-launch discount (appreciation adjustment)
        #   - Default cap (no-data penalty)
        # So age score reflects: newer = better maintained, less MCST risk.
        built_year = listing.get("built_year")
        age_score = 0
        age = None
        if built_year:
            age = current_year - built_year
            if age < 0:
                age = 0  # Future TOP — treat as brand new
            if age <= 5:
                age_score = 4  # Excellent condition, modern finishes
            elif age <= 10:
                age_score = 3  # Good condition
            elif age <= 15:
                age_score = 2  # Aging but maintained
            elif age <= 20:
                age_score = 1
        scores["property_age"] = {"built_year": built_year, "age_years": age, "points": age_score}
        scores["total"] += age_score

        return scores

    def _score_liquidity(self, listing: dict, scored: ScoredListing) -> dict:
        """
        Score liquidity and exit risk (25 pts max).

        Components:
        - Transaction Volume (12mo): 0-8 pts
        - Buyer Pool Depth: 0-7 pts (condo density + HDB upgrader pool)
        - Development Size: 0-5 pts
        - Price Appeal: 0-5 pts
        """
        scores = {"total": 0}

        # Transaction Volume (0-8 pts)
        project_name = listing.get("project_name", "")
        title = listing.get("title") or project_name or ""
        district = listing.get("district", "")
        # transaction_data is keyed by f"{project}_{district}".lower() from get_batch_appreciation
        txn_cache_key = f"{title}_{district}".lower()
        txn_data = self.transaction_data.get(txn_cache_key) or self.transaction_data.get(project_name.lower())
        txn_count = txn_data.get("transaction_count", 0) if isinstance(txn_data, dict) else (txn_data or 0)
        # Also check URA data for transaction count (fuzzy)
        project_key = title.lower()
        ura_entry = self._fuzzy_ura_lookup(project_key)
        if ura_entry:
            ura_txn = ura_entry.get("transaction_count", 0)
            txn_count = max(txn_count, ura_txn)
        txn_score = 0
        if txn_count >= 50:
            txn_score = 8
        elif txn_count >= 20:
            txn_score = 6
        elif txn_count >= 10:
            txn_score = 4
        elif txn_count >= 5:
            txn_score = 2
        scores["transaction_volume"] = {"count": txn_count, "points": txn_score}
        scores["total"] += txn_score

        # Buyer Pool Depth (0-7 pts) — replaces static district popularity
        # Uses condo density + HDB upgrader pool data from district_profiles.json
        district = normalize_district(listing.get("district", ""))
        d_num = district.upper().replace("D", "").strip()
        profile = self.district_profiles.get(d_num, {})
        buyer_pool = profile.get("buyer_pool_depth", "moderate")
        pool_score = self.BUYER_POOL_SCORES.get(buyer_pool, 3)
        scores["buyer_pool_depth"] = {"district": district, "depth": buyer_pool, "points": pool_score}
        scores["total"] += pool_score

        # Development Size (0-5 pts)
        total_units = listing.get("total_units")
        size_score = 0
        if total_units:
            if total_units >= 600:
                size_score = 5
            elif total_units >= 400:
                size_score = 4
            elif total_units >= 200:
                size_score = 3
            elif total_units >= 100:
                size_score = 1
        scores["dev_size"] = {"total_units": total_units, "points": size_score}
        scores["total"] += size_score

        # Price Appeal (0-5 pts)
        price = listing.get("price", 0)
        price_score = 0
        min_sweet, max_sweet = self.LIQUIDITY_SWEET_SPOT
        if min_sweet <= price <= max_sweet:
            price_score = 5  # Sweet spot
        elif price < min_sweet:
            price_score = 4  # Below sweet spot, still liquid
        elif price <= 3_000_000:
            price_score = 2  # Above sweet spot
        else:
            price_score = 1  # Premium segment
        scores["price_appeal"] = {
            "price": price,
            "sweet_spot": f"${min_sweet:,}-${max_sweet:,}",
            "points": price_score,
        }
        scores["total"] += price_score

        return scores

    def _score_cost_efficiency(self, listing: dict, scored: ScoredListing) -> dict:
        """
        Score cost efficiency (10 pts max).

        Components:
        - MCST Estimate: 0-4 pts
        - Property Tax Band: 0-3 pts
        - Efficiency Ratio: 0-3 pts
        """
        scores = {"total": 0}

        sqft = listing.get("sqft", 0)

        # MCST Estimate (0-4 pts)
        mcst_monthly = sqft * 0.35 if sqft else 0
        mcst_score = 0
        if mcst_monthly > 0:
            if mcst_monthly < 350:
                mcst_score = 4
            elif mcst_monthly < 450:
                mcst_score = 3
            elif mcst_monthly < 550:
                mcst_score = 2
            elif mcst_monthly < 650:
                mcst_score = 1
        scores["mcst"] = {"monthly_estimate": round(mcst_monthly), "points": mcst_score}
        scores["total"] += mcst_score

        # Property Tax Band (0-3 pts)
        # Based on estimated rental
        monthly_rent = scored.estimated_monthly_rent
        annual_value = monthly_rent * 12 if monthly_rent else 0
        tax_score = 0
        if annual_value > 0:
            # Lower quartile roughly < $50k AV
            if annual_value < 50000:
                tax_score = 3
            elif annual_value < 70000:
                tax_score = 2
            else:
                tax_score = 1
        scores["property_tax"] = {
            "annual_value_estimate": round(annual_value),
            "points": tax_score,
        }
        scores["total"] += tax_score

        # Efficiency Ratio (0-3 pts)
        beds = listing.get("beds", 0)
        eff_score = 0
        if sqft and beds:
            sqft_per_bed = sqft / beds
            # Optimal efficiency varies by bed count
            if beds == 1:
                optimal = 500 <= sqft <= 650
            elif beds == 2:
                optimal = 400 <= sqft_per_bed <= 500
            elif beds == 3:
                optimal = 350 <= sqft_per_bed <= 420
            else:
                optimal = sqft_per_bed <= 400

            if optimal:
                eff_score = 3
            elif beds == 2 and 350 <= sqft_per_bed <= 550:
                eff_score = 2
            elif beds == 3 and 300 <= sqft_per_bed <= 480:
                eff_score = 2
            else:
                eff_score = 1
        scores["efficiency_ratio"] = {
            "sqft": sqft,
            "beds": beds,
            "sqft_per_bed": round(sqft / beds, 1) if sqft and beds else None,
            "points": eff_score,
        }
        scores["total"] += eff_score

        return scores

    def _score_red_flags(self, listing: dict, scored: ScoredListing) -> dict:
        """
        Calculate red flag deductions (-10 pts max).

        Red flags:
        - Near industrial zone: -3
        - Near expressway (noise): -2
        - Lease <70 years: -3 (auto-reject if <60)
        - Small dev (<100 units): -2
        - West-facing: -1
        """
        flags = []
        total_penalty = 0

        # West-facing (-1)
        facing = (listing.get("facing") or "").lower()
        if "west" in facing:
            flags.append({"flag": "west_facing", "penalty": 1, "reason": "West-facing unit (hot afternoons)"})
            total_penalty += 1

        # Small development (-2)
        total_units = listing.get("total_units")
        if total_units and total_units < 100:
            flags.append({"flag": "small_dev", "penalty": 2, "reason": f"Small development ({total_units} units)"})
            total_penalty += 2

        # Low remaining lease (-3)
        remaining = scored.remaining_lease
        if remaining and remaining < 70:
            flags.append({"flag": "low_lease", "penalty": 3, "reason": f"Low remaining lease ({remaining} years)"})
            total_penalty += 3

        # Old property (-2 to -4)
        built_year = listing.get("built_year")
        current_year = self._current_year
        if built_year:
            age = current_year - built_year
            if age > 20:
                flags.append({"flag": "very_old_property", "penalty": 4, "reason": f"Very old property ({age} years) - maintenance risk"})
                total_penalty += 4
            elif age > 15:
                flags.append({"flag": "old_property", "penalty": 2, "reason": f"Older property ({age} years)"})
                total_penalty += 2

        # Suspiciously low PSF (-2) - may indicate problems
        psf = listing.get("psf")
        if psf and psf < 1300:
            flags.append({"flag": "very_low_psf", "penalty": 2, "reason": f"Unusually low PSF (${psf:,.0f}) - investigate condition"})
            total_penalty += 2

        # Inefficient large unit (-2)
        sqft = listing.get("sqft")
        beds = listing.get("beds")
        if sqft and beds:
            sqft_per_bed = sqft / beds
            if beds == 2 and sqft_per_bed > 700:
                flags.append({"flag": "oversized_unit", "penalty": 2, "reason": f"Large 2BR ({sqft_per_bed:.0f} sqft/bed) - harder to rent"})
                total_penalty += 2
            elif beds == 3 and sqft_per_bed > 550:
                flags.append({"flag": "oversized_unit", "penalty": 2, "reason": f"Large 3BR ({sqft_per_bed:.0f} sqft/bed) - harder to rent"})
                total_penalty += 2

        # Cap at 10
        total_penalty = min(total_penalty, 10)

        return {"flags": flags, "total": total_penalty}

    def _calculate_remaining_lease(self, listing: dict) -> Optional[int]:
        """Calculate remaining lease years."""
        tenure = (listing.get("tenure") or "").lower()
        current_year = self._current_year

        if "freehold" in tenure or "999" in tenure:
            return 999

        lease_match = re.search(r"(\d+)[\s-]?year", tenure)
        if lease_match:
            lease_years = int(lease_match.group(1))
        elif "99" in tenure:
            lease_years = 99
        else:
            return None

        start_year = listing.get("built_year") or listing.get("top_year")
        if not start_year:
            if lease_years == 99:
                return 90
            return None

        elapsed = current_year - start_year
        return max(0, lease_years - elapsed)

def score_listings(
    listings: list[dict],
    condo_rental_data: Optional[dict] = None,
    transaction_data: Optional[dict] = None,
    fetch_appreciation: bool = False,
    ura_data: Optional[dict] = None,
) -> list[ScoredListing]:
    """
    Score a list of listings.

    Args:
        listings: List of listing dictionaries
        condo_rental_data: Optional condo rental data
        transaction_data: Optional transaction history data
        fetch_appreciation: Whether to fetch real appreciation from PropertyGuru API
        ura_data: Optional URA appreciation data (official govt source, highest priority)

    Returns:
        List of ScoredListing objects, sorted by total score descending
    """
    scorer = FullScorer(condo_rental_data, transaction_data, fetch_appreciation, ura_data)
    scored = [scorer.score(listing) for listing in listings]
    scored.sort(key=lambda x: x.total_score, reverse=True)
    return scored


def score_and_filter(
    listings: list[dict],
    min_quick_score: int = 40,
    condo_rental_data: Optional[dict] = None,
    transaction_data: Optional[dict] = None,
    fetch_appreciation: bool = False,
    ura_data: Optional[dict] = None,
) -> dict[str, list[ScoredListing]]:
    """
    Quick filter then full score remaining listings.

    Uses QuickScorer (scraped data only) to pre-filter obvious rejects,
    then runs FullScorer on everything above the threshold.

    Args:
        listings: List of listing dictionaries
        min_quick_score: Minimum quick score to keep for full scoring.
            Listings with score=0 (hard-rejected by price/lease) are always excluded.
            Default 40 is intentionally lenient since quick scorer lacks URA data.
        condo_rental_data: Optional condo rental data
        transaction_data: Optional transaction history data
        fetch_appreciation: Whether to fetch real appreciation from PropertyGuru API
        ura_data: Optional URA appreciation data (official govt source, highest priority)

    Returns:
        Dict with "scored" (full analysis) and "rejected" (quick filtered) lists
    """
    from scoring.quick_scorer import QuickScorer

    # Phase 1: Quick filter using min_quick_score threshold
    quick_scorer = QuickScorer()
    to_score = []
    rejected = []

    for listing in listings:
        qs = quick_scorer.score(listing)
        # score=0 means hard-rejected (price out of range, lease too short)
        if qs.score == 0 or qs.score < min_quick_score:
            rejected.append(listing)
        else:
            to_score.append(listing)

    # Phase 2: Full score everything that passed the quick filter
    scorer = FullScorer(condo_rental_data, transaction_data, fetch_appreciation, ura_data)
    scored = [scorer.score(listing) for listing in to_score]
    scored.sort(key=lambda x: x.total_score, reverse=True)

    return {
        "scored": scored,
        "rejected": rejected,
    }


def prefetch_transaction_data(
    listings: list[dict],
    delay: float = 0.5,
) -> dict[str, dict]:
    """
    Pre-fetch transaction history data for multiple listings.

    More efficient than fetching during scoring as it deduplicates by project name.

    Args:
        listings: List of listing dictionaries
        delay: Delay between API calls

    Returns:
        Dict mapping cache_key -> transaction data
    """
    if not _HAS_TRANSACTION_SCRAPER:
        print("Transaction scraper not available", file=sys.stderr)
        return {}

    from scrapers.transaction_scraper import TransactionScraper

    scraper = TransactionScraper()
    print("\nFetching transaction history data...", file=sys.stderr)
    return scraper.get_batch_appreciation(listings, delay)
