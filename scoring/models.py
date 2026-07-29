"""Data models for the scoring system.

The ranking signal is MMR (scoring/mmr.py — continuous, uncapped, displayed
as score_1000). The legacy /100 category points are still populated on
ScoredListing for report context, but they do not drive ranking or tiers
whenever an MMR score is present (see final_tier).
"""

from dataclasses import dataclass, field
from typing import Optional

try:
    from config import (
        SCORE_TIER1_MIN,
        SCORE_TIER2_MIN,
        SCORE1000_TIER1_MIN,
        SCORE1000_TIER2_MIN,
    )
except ImportError:
    SCORE_TIER1_MIN = 60
    SCORE_TIER2_MIN = 45
    SCORE1000_TIER1_MIN = 650
    SCORE1000_TIER2_MIN = 450


# Map the AI's 4-point rating onto a clear, simple verdict for the final report.
_VERDICT_MAP = {
    "strong buy": "BUY",
    "buy": "BUY",
    "neutral": "NEUTRAL",
    "avoid": "AVOID",   # i.e. "no buy"
}


def verdict_from_rating(rating: Optional[str]) -> Optional[str]:
    """Collapse a rating (Strong Buy/Buy/Neutral/Avoid) to BUY / NEUTRAL / AVOID.

    Unknown labels degrade honestly: negative phrasing maps to AVOID, then
    buy-ish to BUY, hold-ish to NEUTRAL, and anything else to None (no verdict)
    rather than silently coercing to NEUTRAL.
    """
    if not rating:
        return None
    key = rating.strip().lower()
    if key in _VERDICT_MAP:
        return _VERDICT_MAP[key]
    # Negative phrasings first — they may contain the word "buy".
    if any(neg in key for neg in ("avoid", "sell", "no buy", "no-buy", "don't buy", "do not buy", "pass")):
        return "AVOID"
    if "buy" in key:
        return "BUY"
    if "neutral" in key or "hold" in key:
        return "NEUTRAL"
    return None


@dataclass
class QuickScore:
    """Result of quick scoring (Phase 1 filter)."""
    score: int  # 0-100
    tier: int  # 1 = keep, 2 = maybe, 3 = reject
    reason: Optional[str] = None
    breakdown: dict = field(default_factory=dict)


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
    vacancy_cost: float = 0  # ~0.75 months/year rent lost (see cost_parameters.json)
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


@dataclass
class ROIResult:
    """ROI calculation result for different holding periods."""
    hold_years: int
    purchase_price: int
    estimated_exit_price: int
    total_rental_income: float  # Net of holding costs AND rental income tax
    total_holding_costs: float
    total_upfront_costs: int  # Cash deployed at purchase (financed: downpayment + entry costs)
    total_exit_costs: float

    gross_rental_yield: float  # Annual gross rental / purchase price
    net_rental_yield: float  # After holding costs (pre income tax — market-comparable stat)
    capital_gain: int  # Exit price - purchase price
    total_return: float  # Capital gain + net rental - exit costs - entry costs - mortgage interest
    roi_percent: float  # Total return / total investment * 100 (cash-on-cash when financed)
    annualized_roi: float  # Geometric mean annual return

    # Per-year breakdown
    monthly_rent_estimate: float = 0
    annual_rent_gross: float = 0
    annual_rent_net: float = 0

    # Entry costs + rental income tax (audit #11 / income-tax fix, Jun 2026)
    entry_costs: int = 0  # BSD + ABSD + legal (buy) — deducted from total_return
    total_income_tax: float = 0  # Tax on net letting profit over the hold
    marginal_income_tax_rate: float = 0  # Assumption used (0 = untaxed)

    # Optional financing (ltv=0 -> all-cash; every field below stays 0)
    ltv: float = 0
    mortgage_rate_pct: float = 0
    loan_amount: int = 0
    downpayment: int = 0
    total_mortgage_interest: float = 0
    remaining_principal_at_exit: float = 0


@dataclass
class ScoredListing:
    """A listing with full scoring and ROI analysis.

    Carries three generations of score, all populated together:
    - `mmr` / `score_1000` — the RANKING axis (scoring/mmr.py).
    - `valuation_score` / `livability_score` / `overall_*` — the v3.11 0-100
      axes (valuation.py / livability.py / overall.py).
    - `total_score` — the legacy 100-point category sum (rental yield 15 /
      capital appreciation 30 / future potential 20 / liquidity 25 / cost
      efficiency 10 / red flags -10). DISPLAY ONLY: it is reported for context
      but does not rank listings or set tiers whenever `mmr` is present
      (see `rank_score` and `final_tier`).
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

    # Future score details
    future_score_details: Optional[FutureScore] = None

    # MMR scoring (v3): uncapped continuous rating + /1000 normalization
    mmr: Optional[float] = None
    score_1000: Optional[int] = None
    mmr_components: dict = field(default_factory=dict)

    # 3-score system (v3.11): two more axes beside MMR (the investment-return
    # axis) + purpose-weighted Overalls. valuation/livability are 0-100.
    valuation_score: Optional[int] = None       # "priced right today?" (backtested)
    valuation_detail: dict = field(default_factory=dict)
    livability_score: Optional[int] = None       # own-stay home quality (heuristic)
    livability_components: dict = field(default_factory=dict)
    overall_own_stay: Optional[int] = None       # purpose-weighted, 0-100
    overall_investment: Optional[int] = None     # purpose-weighted, 0-100

    def three_score_fields(self) -> dict:
        """The v3.11 axes to persist into a listings-DB record, mirroring how
        mmr/score_1000 are written back by the backlog scorer and poller. Only
        present (non-None) values are returned, so an un-computed axis is left
        untouched rather than overwritten with null."""
        out = {}
        for f in ("valuation_score", "livability_score",
                  "overall_own_stay", "overall_investment"):
            v = getattr(self, f, None)
            if v is not None:
                out[f] = v
        return out

    @property
    def rank_score(self) -> float:
        """Preferred sort key: raw MMR when available, else legacy total."""
        return self.mmr if self.mmr is not None else self.total_score

    @property
    def total_score(self) -> float:
        """Sum of the legacy 100-point category scores (display only).

        Max 100 pts for all properties equally. No agent adjustment is applied
        here — an agent's appreciation override re-enters through the MMR path
        (mmr.apply_appreciation_override); only its free-text rationale is kept
        on this model, as `agent_adjustment_reason`.
        """
        base = (
            self.rental_yield_score
            + self.capital_appreciation_score
            + self.future_potential_score
            + self.liquidity_score
            + self.cost_efficiency_score
            - self.red_flag_deductions
        )
        return base

    @property
    def final_tier(self) -> int:
        """Tier from the /1000 MMR scale when available, else the legacy score."""
        if self.score_1000 is not None:
            if self.score_1000 >= SCORE1000_TIER1_MIN:
                return 1
            if self.score_1000 >= SCORE1000_TIER2_MIN:
                return 2
            return 3
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
        """Whether the appreciation rate came from real transactions.

        True for every `ura_*` source, injected `transaction_data`, and an
        `agent_override`; False for the `default` / `regional_baseline`
        fallbacks, which are guesses rather than measurements.
        """
        return self.appreciation_source not in {"default", "regional_baseline"}

    @property
    def needs_ai_appreciation(self) -> bool:
        """Whether appreciation rate should be validated by AI research."""
        return self.appreciation_source in {"default", "regional_baseline"}

    # Rental estimates
    estimated_monthly_rent: float = 0
    estimated_gross_yield: float = 0
    # "same_condo" | "ura_project_bed" | "ura_project" | "district_bedroom"
    # | "district_median" | "fallback_bedroom" | "fallback" | "unavailable"
    rent_source: str = ""
    # Rent evidence (audit #4/#5): numeric confidence (estimator-emitted;
    # MMR takes min(source map, rent_confidence)), URA contract depth and
    # serving window behind a cache-backed rent, and whether the v3.6.2
    # sqft cap engaged.
    rent_confidence: Optional[float] = None
    # Populated since 2026-07-29. These are METADATA about the rent basis, never
    # an input to it — the agent reads them to judge whether a yield is worth
    # trusting, which is what makes the repo's "gross yield >5.5% is a
    # verify-first artifact" rule actionable. A 4.3% yield behind 354 URA
    # contracts and a 7.5% yield behind none are otherwise indistinguishable.
    rent_contracts: Optional[int] = None
    rent_window_months: Optional[int] = None
    rent_capped: bool = False

    # Appreciation data (for ROI calculation)
    appreciation_rate: float = 0.02  # Annual appreciation rate (decimal) — adjusted for new-launch bias
    # One of: "ura_resale_only" / "ura_5yr_cagr" / "ura_3yr_avg" (or whatever
    # `source` the URA cache entry carries), "transaction_data",
    # "agent_override", "regional_baseline", "default".
    appreciation_source: str = "default"
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
    agent_rating: Optional[str] = None  # "Strong Buy" / "Buy" / "Neutral" / "Avoid"
    agent_rating_rationale: Optional[str] = None  # why this rating (always rendered)
    agent_summary: Optional[str] = None
    agent_red_flags: list[str] = field(default_factory=list)
    agent_catalysts: list[str] = field(default_factory=list)
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
        if self.mmr is not None:
            result["mmr"] = round(self.mmr, 1)
            result["score_1000"] = self.score_1000
            if self.mmr_components:
                result["mmr_components"] = self.mmr_components

        # 3-score system axes (persisted alongside score_1000)
        if self.valuation_score is not None:
            result["valuation_score"] = self.valuation_score
        if self.livability_score is not None:
            result["livability_score"] = self.livability_score
        if self.overall_own_stay is not None:
            result["overall_own_stay"] = self.overall_own_stay
        if self.overall_investment is not None:
            result["overall_investment"] = self.overall_investment

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
            if self.rent_confidence is not None:
                result["rental"]["confidence"] = self.rent_confidence
            if self.rent_contracts is not None:
                result["rental"]["contracts"] = self.rent_contracts

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
            self.agent_rating is not None
            or self.agent_summary is not None
            or self.agent_red_flags
            or self.agent_catalysts
            or self.agent_confidence is not None
            or self.agent_appreciation_rate_pct is not None
        )
        if has_agent_data:
            result["agent"] = {
                "rating": self.agent_rating,
                "verdict": verdict_from_rating(self.agent_rating),
                "rating_rationale": self.agent_rating_rationale,
                "summary": self.agent_summary,
                "red_flags": self.agent_red_flags,
                "catalysts": self.agent_catalysts,
                "adjustment_reason": self.agent_adjustment_reason,
                "rental_assessment": self.agent_rental_assessment,
                "appreciation_assessment": self.agent_appreciation_assessment,
                "stack_notes": self.agent_stack_notes,
                "confidence": self.agent_confidence,
                "appreciation_rate_pct": self.agent_appreciation_rate_pct,
                "appreciation_source": self.agent_appreciation_source,
            }

        return result
