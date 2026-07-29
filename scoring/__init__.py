"""Scoring package for property investment analysis.

Three score families, all produced by one `FullScorer.score()` pass:

- **MMR** (`mmr.py`) — the ranking axis. Elo-style, base 1500, uncapped and
  continuous, displayed as `score_1000` (500 = market-typical, 650+ =
  recommended tier). Backtest-calibrated; missing data is neutral, never
  penalized.
- **The v3.11 0-100 axes** — `valuation.py` ("priced right today?",
  backtested), `livability.py` (own-stay heuristic, NEVER folded into MMR)
  and `overall.py` (purpose-weighted combine).
- **The legacy 100-point total** (`full_scorer.py` + `models.py`) — rental
  yield 15 / capital appreciation 30 / future potential 20 / liquidity 25 /
  cost efficiency 10 / red flags -10. Retained for report context and as the
  INPUT breakdown MMR reads; it no longer ranks anything.

Weights, thresholds and calibration live in `config.py`; the release-by-release
rationale is in `docs/RELEASES.md`.

NOTE: importing this package eagerly pulls in the whole scoring stack. Prefer
importing the submodule you need (`from scoring.livability import
score_livability`) over the re-exports below.
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
