"""Data models for scoring system.

Scoring System v2.1 (100 pts max, no URA bias):
- Rental Yield: 15 pts
- Capital Appreciation: 30 pts (uses real transaction data for ALL properties)
- Future Potential: 20 pts
- Liquidity: 25 pts
- Cost Efficiency: 10 pts
- Red Flags: -10 pts max

Max possible: 100 pts for ALL properties equally.
"""

from dataclasses import dataclass, field
from typing import Optional

try:
    from config import SCORE_TIER1_MIN, SCORE_TIER2_MIN
except ImportError:
    SCORE_TIER1_MIN = 60
    SCORE_TIER2_MIN = 45


@dataclass
class QuickScore:
    """Result of quick scoring (Phase 1 filter)."""
    score: int  # 0-100
    tier: int  # 1 = keep, 2 = maybe, 3 = reject
    reason: Optional[str] = None
    breakdown: dict = field(default_factory=dict)

    @property
    def tier_label(self) -> str:
        labels = {1: "Tier 1 (Keep)", 2: "Tier 2 (Maybe)", 3: "Rejected"}
        return labels.get(self.tier, "Unknown")


@dataclass
class FutureScore:
    """Result of future potential scoring (20 pts max)."""
    future_mrt_score: float = 0  # 0-6 pts
    govt_zone_score: float = 0  # 0-6 pts
    transformation_score: float = 0  # 0-4 pts
    supply_score: float = 0  # 0-4 pts

    # Details for debugging/display
    nearest_future_mrt: Optional[str] = None
    future_mrt_distance_m: Optional[int] = None
    future_mrt_line: Optional[str] = None
    govt_zones: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return (
            self.future_mrt_score
            + self.govt_zone_score
            + self.transformation_score
            + self.supply_score
        )

    def to_dict(self) -> dict:
        return {
            "total": round(self.total, 1),
            "future_mrt": {
                "score": round(self.future_mrt_score, 1),
                "station": self.nearest_future_mrt,
                "distance_m": self.future_mrt_distance_m,
                "line": self.future_mrt_line,
            },
            "govt_zone": {
                "score": round(self.govt_zone_score, 1),
                "zones": self.govt_zones,
            },
            "transformation": round(self.transformation_score, 1),
            "supply_constraint": round(self.supply_score, 1),
        }


@dataclass
class DistrictScore:
    """Score result for a single district (for district discovery)."""
    district: int
    name: str
    region: str  # CCR, RCR, OCR
    total_score: float

    # Component scores (0-100 scale each, then weighted)
    historical_score: float = 0  # 30% weight
    liquidity_score: float = 0  # 20% weight
    future_infra_score: float = 0  # 25% weight
    govt_priority_score: float = 0  # 20% weight
    supply_score: float = 0  # 5% weight

    # Catalysts for display
    key_catalysts: list[str] = field(default_factory=list)

    # Raw data for reference
    historical_appreciation: float = 0
    median_psf: int = 0
    future_mrt_lines: list[str] = field(default_factory=list)
    govt_zones: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "district": self.district,
            "name": self.name,
            "region": self.region,
            "total_score": round(self.total_score, 1),
            "scores": {
                "historical": round(self.historical_score, 1),
                "liquidity": round(self.liquidity_score, 1),
                "future_infra": round(self.future_infra_score, 1),
                "govt_priority": round(self.govt_priority_score, 1),
                "supply": round(self.supply_score, 1),
            },
            "key_catalysts": self.key_catalysts,
        }


@dataclass
class CostBreakdown:
    """Detailed cost breakdown for a property investment."""
    # Upfront costs
    purchase_price: int
    bsd: int  # Buyer's Stamp Duty
    absd: int = 0  # Additional BSD (0 for SC first property)
    legal_fee_buy: int = 3500

    # Annual holding costs (per year)
    property_tax: float = 0
    mcst: float = 0
    agent_rental_fee: float = 0  # 0.5 months/year avg
    vacancy_cost: float = 0  # 1.5 months/year rent lost
    repairs_insurance: float = 1800

    # Exit costs
    ssd: int = 0  # Seller's Stamp Duty (0 if hold > 4 years)
    agent_sale_commission: float = 0  # 2% of sale price
    legal_fee_sell: int = 3000

    @property
    def total_upfront(self) -> int:
        return self.purchase_price + self.bsd + self.absd + self.legal_fee_buy

    @property
    def annual_holding(self) -> float:
        return (
            self.property_tax
            + self.mcst
            + self.agent_rental_fee
            + self.vacancy_cost
            + self.repairs_insurance
        )

    @property
    def total_exit(self) -> float:
        return self.ssd + self.agent_sale_commission + self.legal_fee_sell


@dataclass
class ROIResult:
    """ROI calculation result for different holding periods."""
    hold_years: int
    purchase_price: int
    estimated_exit_price: int
    total_rental_income: float
    total_holding_costs: float
    total_upfront_costs: int
    total_exit_costs: float

    gross_rental_yield: float  # Annual gross rental / purchase price
    net_rental_yield: float  # After costs
    capital_gain: int  # Exit price - purchase price
    total_return: float  # Capital gain + net rental income - exit costs
    roi_percent: float  # Total return / total investment * 100
    annualized_roi: float  # Geometric mean annual return

    # Per-year breakdown
    monthly_rent_estimate: float = 0
    annual_rent_gross: float = 0
    annual_rent_net: float = 0

    @property
    def total_investment(self) -> int:
        return self.total_upfront_costs


@dataclass
class ScoredListing:
    """A listing with full scoring and ROI analysis.

    Scoring System v2.1 (100 pts max, no URA bias):
    - Rental Yield: 15 pts
    - Capital Appreciation: 30 pts (real transaction data for all)
    - Future Potential: 20 pts
    - Liquidity: 25 pts
    - Cost Efficiency: 10 pts
    - Red Flags: -10 pts max
    """
    # Original listing data
    id: str
    title: str
    price: int
    url: str
    address: Optional[str] = None
    district: Optional[str] = None
    district_name: Optional[str] = None
    beds: Optional[int] = None
    baths: Optional[int] = None
    sqft: Optional[float] = None
    psf: Optional[float] = None
    tenure: Optional[str] = None
    built_year: Optional[int] = None
    project_name: Optional[str] = None
    total_units: Optional[int] = None
    mrt_info: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    image_url: Optional[str] = None
    listing_date: Optional[str] = None
    floor_level: Optional[str] = None
    facing: Optional[str] = None

    # Quick score results
    quick_score: int = 0
    quick_tier: int = 3

    # Full scoring breakdown (v2.1 weights - no URA bias)
    rental_yield_score: float = 0  # 15 pts max
    capital_appreciation_score: float = 0  # 30 pts max
    future_potential_score: float = 0  # 20 pts max
    liquidity_score: float = 0  # 25 pts max
    cost_efficiency_score: float = 0  # 10 pts max
    red_flag_deductions: float = 0  # -10 pts max
    ura_bonus_score: float = 0  # DEPRECATED - kept for compatibility, always 0

    # Future score details
    future_score_details: Optional[FutureScore] = None

    @property
    def total_score(self) -> float:
        """Calculate total score with v2.1 weights (no URA bias).

        Max: 100 pts for all properties equally.
        Agent adjustment capped at [-8, +8].
        """
        base = (
            self.rental_yield_score
            + self.capital_appreciation_score
            + self.future_potential_score
            + self.liquidity_score
            + self.cost_efficiency_score
            - self.red_flag_deductions
        )
        clamped_adj = max(-8.0, min(8.0, self.agent_score_adjustment))
        return base + clamped_adj

    @property
    def final_tier(self) -> int:
        """Tier based on total score (post agent adjustment)."""
        if self.total_score >= SCORE_TIER1_MIN:
            return 1
        if self.total_score >= SCORE_TIER2_MIN:
            return 2
        return 3

    @property
    def final_tier_label(self) -> str:
        labels = {1: "Tier 1 (Recommended)", 2: "Tier 2 (Consider)", 3: "Below Threshold"}
        return labels.get(self.final_tier, "Unknown")

    @property
    def has_ura_data(self) -> bool:
        """Check if this listing has real transaction data (URA or PropertyGuru)."""
        return self.appreciation_source not in {"default", "regional_baseline", "ai_required"}

    @property
    def needs_ai_appreciation(self) -> bool:
        """Whether appreciation rate should be validated by AI research."""
        return self.appreciation_source in {"default", "regional_baseline", "ai_required"}

    # Rental estimates
    estimated_monthly_rent: float = 0
    estimated_gross_yield: float = 0
    rent_source: str = ""  # "same_condo", "district_median", "fallback"

    # Appreciation data (for ROI calculation)
    appreciation_rate: float = 0.02  # Annual appreciation rate (decimal) — adjusted for new-launch bias
    appreciation_source: str = "default"  # "project_history", "transaction_data", "regional_baseline", "default"
    raw_appreciation_rate: float = 0.02  # Original rate before new-launch adjustment
    appreciation_adjustment: float = 0.0  # How much was discounted (decimal, e.g., -0.02 = -2%)
    adjustment_reason: str = ""  # Why adjustment was applied (or "" if none)
    agent_appreciation_rate_pct: Optional[float] = None  # AI-researched override (percent)
    agent_appreciation_source: Optional[str] = None

    # ROI results for different holding periods
    roi_5yr: Optional[ROIResult] = None
    roi_6yr: Optional[ROIResult] = None
    roi_7yr: Optional[ROIResult] = None
    roi_sensitivity: dict = field(default_factory=dict)

    # Cost breakdown
    costs: Optional[CostBreakdown] = None

    # Derived fields
    remaining_lease: Optional[int] = None
    mrt_distance_m: Optional[int] = None
    nearest_mrt: Optional[str] = None

    # Deduplication: additional listing URLs from the same condo
    additional_urls: list[str] = field(default_factory=list)

    # Condo mode: per-unit-type grouping data
    unit_variants: list[dict] = field(default_factory=list)
    unit_summary: Optional[dict] = None

    # Score breakdown details
    score_breakdown: dict = field(default_factory=dict)

    # Agent review fields (filled by AI agent, empty by default)
    agent_summary: Optional[str] = None
    agent_red_flags: list[str] = field(default_factory=list)
    agent_catalysts: list[str] = field(default_factory=list)
    agent_score_adjustment: float = 0.0
    agent_adjustment_reason: Optional[str] = None
    agent_rental_assessment: Optional[str] = None
    agent_appreciation_assessment: Optional[str] = None
    agent_stack_notes: Optional[str] = None
    agent_confidence: Optional[str] = None  # "high" / "medium" / "low"

    def to_dict(self) -> dict:
        """Convert to dictionary, excluding None values."""
        result = {
            "id": self.id,
            "title": self.title,
            "price": self.price,
            "url": self.url,
            "total_score": round(self.total_score, 1),
            "quick_score": self.quick_score,
            "quick_tier": self.quick_tier,
            "final_tier": self.final_tier,
            "has_ura_data": self.has_ura_data,
        }

        # Add optional fields if present
        optional_fields = [
            "address", "district", "district_name", "beds", "baths",
            "sqft", "psf", "tenure", "built_year", "project_name",
            "total_units", "mrt_info", "latitude", "longitude",
            "image_url", "listing_date", "floor_level", "facing",
            "remaining_lease", "mrt_distance_m", "nearest_mrt"
        ]
        for f in optional_fields:
            val = getattr(self, f)
            if val is not None:
                result[f] = val

        # Additional listing URLs (same condo, different units)
        if self.additional_urls:
            result["additional_urls"] = self.additional_urls

        # Condo mode: unit variant data
        if self.unit_variants:
            result["unit_variants"] = self.unit_variants
        if self.unit_summary:
            result["unit_summary"] = self.unit_summary

        # Score breakdown (v2.1)
        result["scores"] = {
            "rental_yield": round(self.rental_yield_score, 1),
            "capital_appreciation": round(self.capital_appreciation_score, 1),
            "future_potential": round(self.future_potential_score, 1),
            "liquidity": round(self.liquidity_score, 1),
            "cost_efficiency": round(self.cost_efficiency_score, 1),
            "red_flags": round(self.red_flag_deductions, 1),
        }

        # Future score details
        if self.future_score_details:
            result["future_details"] = self.future_score_details.to_dict()

        # Rental estimates
        if self.estimated_monthly_rent > 0:
            result["rental"] = {
                "monthly_rent": round(self.estimated_monthly_rent),
                "gross_yield_pct": round(self.estimated_gross_yield, 2),
                "source": self.rent_source,
            }

        # Appreciation data
        appreciation_info = {
            "annual_rate_pct": round(self.appreciation_rate * 100, 2),
            "source": self.appreciation_source,
            "needs_ai_appreciation": self.needs_ai_appreciation,
        }
        if self.agent_appreciation_rate_pct is not None:
            appreciation_info["agent_rate_pct"] = self.agent_appreciation_rate_pct
        if self.agent_appreciation_source:
            appreciation_info["agent_source"] = self.agent_appreciation_source
        if self.appreciation_adjustment != 0:
            appreciation_info["raw_rate_pct"] = round(self.raw_appreciation_rate * 100, 2)
            appreciation_info["adjustment_pct"] = round(self.appreciation_adjustment * 100, 2)
            appreciation_info["adjustment_reason"] = self.adjustment_reason
        result["appreciation"] = appreciation_info

        # ROI results
        roi_results = {}
        for years, roi in [(5, self.roi_5yr), (6, self.roi_6yr), (7, self.roi_7yr)]:
            if roi:
                roi_results[f"{years}yr"] = {
                    "exit_price": roi.estimated_exit_price,
                    "total_return": round(roi.total_return),
                    "roi_pct": round(roi.roi_percent, 2),
                    "annualized_roi_pct": round(roi.annualized_roi, 2),
                }
        if roi_results:
            result["roi"] = roi_results

        if self.roi_sensitivity:
            result["roi_sensitivity"] = self.roi_sensitivity

        # Detailed score breakdown
        if self.score_breakdown:
            result["score_details"] = self.score_breakdown

        # Agent review fields (only include if any are populated)
        has_agent_data = (
            self.agent_summary is not None
            or self.agent_red_flags
            or self.agent_catalysts
            or self.agent_score_adjustment != 0
            or self.agent_confidence is not None
            or self.agent_appreciation_rate_pct is not None
        )
        if has_agent_data:
            result["agent"] = {
                "summary": self.agent_summary,
                "red_flags": self.agent_red_flags,
                "catalysts": self.agent_catalysts,
                "score_adjustment": self.agent_score_adjustment,
                "adjustment_reason": self.agent_adjustment_reason,
                "rental_assessment": self.agent_rental_assessment,
                "appreciation_assessment": self.agent_appreciation_assessment,
                "stack_notes": self.agent_stack_notes,
                "confidence": self.agent_confidence,
                "appreciation_rate_pct": self.agent_appreciation_rate_pct,
                "appreciation_source": self.agent_appreciation_source,
            }

        return result
