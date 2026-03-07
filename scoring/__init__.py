"""Scoring package for property investment analysis.

v2.2 Scoring System:
- Rental Yield: 15 pts (reduced from 30)
- Capital Appreciation: 30 pts (uses actual URA data, new-launch bias corrected)
- Future Potential: 20 pts (MRT, govt zones, transformation)
- Liquidity: 25 pts (transaction volume, district popularity)
- Cost Efficiency: 10 pts
- Red Flags: -10 pts max
"""

from scoring.models import QuickScore, ScoredListing, ROIResult, CostBreakdown, FutureScore, DistrictScore
from scoring.costs import CostCalculator
from scoring.quick_scorer import QuickScorer
from scoring.roi import ROICalculator
from scoring.rental_estimator import RentalEstimator
from scoring.full_scorer import FullScorer
from scoring.future_scorer import FutureScorer
from scoring.district_scorer import DistrictScorer

__all__ = [
    # Models
    "QuickScore",
    "ScoredListing",
    "ROIResult",
    "CostBreakdown",
    "FutureScore",
    "DistrictScore",
    # Scorers
    "CostCalculator",
    "QuickScorer",
    "ROICalculator",
    "RentalEstimator",
    "FullScorer",
    "FutureScorer",
    "DistrictScorer",
]
