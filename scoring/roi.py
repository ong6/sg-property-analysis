"""ROI calculation for property investments."""

import json
import os
from typing import Optional

from scoring.costs import CostCalculator

# Sensitivity defaults
try:
    from config import (
        ROI_SENSITIVITY_RENT_DELTA_PCT,
        ROI_SENSITIVITY_APPRECIATION_DELTA_PCT,
    )
except ImportError:
    ROI_SENSITIVITY_RENT_DELTA_PCT = 0.10
    ROI_SENSITIVITY_APPRECIATION_DELTA_PCT = 0.015
from scoring.models import CostBreakdown, ROIResult
from scoring.rental_estimator import RentalEstimator


# Load cost parameters
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_COST_PARAMS_FILE = os.path.join(_DATA_DIR, "cost_parameters.json")


def _load_appreciation_rate() -> float:
    """Load annual appreciation estimate from config."""
    if os.path.exists(_COST_PARAMS_FILE):
        with open(_COST_PARAMS_FILE) as f:
            params = json.load(f)
            return params.get("annual_appreciation_estimate", 0.02)
    return 0.02


class ROICalculator:
    """
    Calculate ROI for property investments.

    Optimized for Singapore Citizen, first property (0% ABSD),
    with hold periods of 5-7 years.
    """

    def __init__(
        self,
        buyer_type: str = "SC",
        property_count: int = 0,
        annual_appreciation: Optional[float] = None,
    ):
        """
        Initialize ROI calculator.

        Args:
            buyer_type: "SC" (Singapore Citizen), "PR", or "Foreigner"
            property_count: 0 for first property, 1 for second, etc.
            annual_appreciation: Override default appreciation rate
        """
        self.buyer_type = buyer_type
        self.property_count = property_count
        self.annual_appreciation = annual_appreciation if annual_appreciation is not None else _load_appreciation_rate()
        self.cost_calc = CostCalculator()
        self.rental_estimator = RentalEstimator()

    def calculate(
        self,
        listing: dict,
        hold_years: int = 5,
        monthly_rent: Optional[float] = None,
        exit_price: Optional[int] = None,
        appreciation_rate: Optional[float] = None,
        agent_rental_months_per_year: Optional[float] = None,
        vacancy_months_per_year: Optional[float] = None,
    ) -> ROIResult:
        """
        Calculate ROI for a property investment.

        Args:
            listing: Listing dictionary with price, sqft, etc.
            hold_years: Investment holding period
            monthly_rent: Override rental estimate
            exit_price: Override exit price estimate
            appreciation_rate: Per-listing appreciation rate (overrides default)

        Returns:
            ROIResult with complete ROI breakdown
        """
        purchase_price = listing.get("price", 0)
        sqft = listing.get("sqft", 0)

        if not purchase_price or not sqft:
            return self._empty_result(hold_years, purchase_price)

        # Estimate rental if not provided
        if monthly_rent is None:
            rental_data = self.rental_estimator.estimate(listing)
            monthly_rent = rental_data["monthly_rent"]

        # Use per-listing appreciation or fall back to default
        rate = appreciation_rate if appreciation_rate is not None else self.annual_appreciation

        # Estimate exit price with appreciation decay
        # v2.2: Appreciation decays slightly each year (mean reversion).
        # This models the reality that high appreciation rates don't sustain
        # indefinitely — they tend to normalize over longer hold periods.
        # Decay factor: 0.97x per year (3% annual decay of the rate itself).
        # Example: 6%/yr → 5.82% yr2 → 5.65% yr3 → 5.48% yr4 → ...
        if exit_price is None:
            APPRECIATION_DECAY_PER_YEAR = 0.97
            compounded_price = float(purchase_price)
            for year in range(hold_years):
                year_rate = rate * (APPRECIATION_DECAY_PER_YEAR ** year)
                compounded_price *= (1 + year_rate)
            exit_price = int(compounded_price)

        # Calculate costs
        cost_breakdown = self.cost_calc.create_cost_breakdown(
            purchase_price=purchase_price,
            sqft=sqft,
            monthly_rent=monthly_rent,
            hold_years=hold_years,
            exit_price=exit_price,
            buyer_type=self.buyer_type,
            property_count=self.property_count,
            agent_rental_months_per_year=agent_rental_months_per_year,
            vacancy_months_per_year=vacancy_months_per_year,
        )

        # Annual rental income (gross)
        annual_rent_gross = monthly_rent * 12

        # Annual holding costs
        annual_holding_costs = cost_breakdown.annual_holding

        # Net annual rental income
        annual_rent_net = annual_rent_gross - annual_holding_costs

        # Total rental income over holding period
        total_rental_gross = annual_rent_gross * hold_years
        total_rental_net = annual_rent_net * hold_years
        total_holding_costs = annual_holding_costs * hold_years

        # Exit costs
        exit_costs = self.cost_calc.calculate_exit_costs(exit_price, hold_years)
        total_exit_costs = sum(exit_costs.values())

        # Capital gain
        capital_gain = exit_price - purchase_price

        # Total return
        total_return = capital_gain + total_rental_net - total_exit_costs

        # Total investment (upfront costs)
        total_investment = cost_breakdown.total_upfront

        # ROI calculations
        roi_percent = (total_return / total_investment) * 100 if total_investment > 0 else 0

        # Annualized ROI (geometric mean)
        if total_investment > 0 and total_return > -total_investment:
            final_value = total_investment + total_return
            annualized_roi = ((final_value / total_investment) ** (1 / hold_years) - 1) * 100
        else:
            annualized_roi = 0

        # Gross and net yields
        gross_yield = (annual_rent_gross / purchase_price) * 100 if purchase_price > 0 else 0
        net_yield = (annual_rent_net / purchase_price) * 100 if purchase_price > 0 else 0

        return ROIResult(
            hold_years=hold_years,
            purchase_price=purchase_price,
            estimated_exit_price=exit_price,
            total_rental_income=total_rental_net,
            total_holding_costs=total_holding_costs,
            total_upfront_costs=total_investment,
            total_exit_costs=total_exit_costs,
            gross_rental_yield=round(gross_yield, 2),
            net_rental_yield=round(net_yield, 2),
            capital_gain=capital_gain,
            total_return=round(total_return, 2),
            roi_percent=round(roi_percent, 2),
            annualized_roi=round(annualized_roi, 2),
            monthly_rent_estimate=monthly_rent,
            annual_rent_gross=annual_rent_gross,
            annual_rent_net=annual_rent_net,
        )

    def calculate_multiple_periods(
        self,
        listing: dict,
        periods: list[int] = [5, 6, 7],
        monthly_rent: Optional[float] = None,
        appreciation_rate: Optional[float] = None,
    ) -> dict[int, ROIResult]:
        """
        Calculate ROI for multiple holding periods.

        Args:
            listing: Listing dictionary
            periods: List of holding periods in years
            monthly_rent: Override rental estimate
            appreciation_rate: Per-listing appreciation rate

        Returns:
            Dict mapping years -> ROIResult
        """
        results = {}
        for years in periods:
            results[years] = self.calculate(
                listing, years, monthly_rent,
                appreciation_rate=appreciation_rate,
            )
        return results

    def calculate_sensitivity(
        self,
        listing: dict,
        periods: list[int] = [5, 7],
        monthly_rent: Optional[float] = None,
        appreciation_rate: Optional[float] = None,
        rent_delta_pct: Optional[float] = None,
        appreciation_delta_pct: Optional[float] = None,
    ) -> dict[str, dict[int, ROIResult]]:
        """
        Calculate ROI sensitivity scenarios (downside/base/upside).

        Downside/upsides adjust rent and appreciation rate.
        """
        if monthly_rent is None:
            rental_data = self.rental_estimator.estimate(listing)
            monthly_rent = rental_data["monthly_rent"]

        base_rate = appreciation_rate if appreciation_rate is not None else self.annual_appreciation
        rent_delta = ROI_SENSITIVITY_RENT_DELTA_PCT if rent_delta_pct is None else rent_delta_pct
        appr_delta = ROI_SENSITIVITY_APPRECIATION_DELTA_PCT if appreciation_delta_pct is None else appreciation_delta_pct

        scenarios = {
            "downside": {"rent_mult": 1 - rent_delta, "rate_delta": -appr_delta},
            "base": {"rent_mult": 1.0, "rate_delta": 0.0},
            "upside": {"rent_mult": 1 + rent_delta, "rate_delta": appr_delta},
        }

        results: dict[str, dict[int, ROIResult]] = {}
        for name, cfg in scenarios.items():
            adj_rent = monthly_rent * cfg["rent_mult"]
            adj_rate = base_rate + cfg["rate_delta"]
            # Keep within reasonable bounds (-5% to +15%/yr)
            adj_rate = max(-0.05, min(0.15, adj_rate))
            results[name] = {}
            for years in periods:
                results[name][years] = self.calculate(
                    listing,
                    hold_years=years,
                    monthly_rent=adj_rent,
                    appreciation_rate=adj_rate,
                )
        return results

    def find_optimal_hold_period(
        self,
        listing: dict,
        min_years: int = 4,
        max_years: int = 10,
    ) -> tuple[int, ROIResult]:
        """
        Find optimal holding period for best annualized ROI.

        Args:
            listing: Listing dictionary
            min_years: Minimum holding period (4 to avoid SSD)
            max_years: Maximum holding period to consider

        Returns:
            Tuple of (optimal_years, ROIResult)
        """
        best_years = min_years
        best_result = self.calculate(listing, min_years)

        for years in range(min_years + 1, max_years + 1):
            result = self.calculate(listing, years)
            if result.annualized_roi > best_result.annualized_roi:
                best_years = years
                best_result = result

        return best_years, best_result

    def _empty_result(self, hold_years: int, purchase_price: int) -> ROIResult:
        """Return empty ROI result for invalid inputs."""
        return ROIResult(
            hold_years=hold_years,
            purchase_price=purchase_price,
            estimated_exit_price=purchase_price,
            total_rental_income=0,
            total_holding_costs=0,
            total_upfront_costs=purchase_price,
            total_exit_costs=0,
            gross_rental_yield=0,
            net_rental_yield=0,
            capital_gain=0,
            total_return=0,
            roi_percent=0,
            annualized_roi=0,
        )


def calculate_roi(
    listing: dict,
    hold_years: int = 5,
    monthly_rent: Optional[float] = None,
) -> ROIResult:
    """
    Convenience function to calculate ROI for a listing.

    Assumes Singapore Citizen, first property.
    """
    calc = ROICalculator()
    return calc.calculate(listing, hold_years, monthly_rent)


def quick_roi_estimate(
    price: int,
    sqft: float,
    hold_years: int = 5,
    annual_appreciation: float = 0.02,
) -> dict:
    """
    Quick ROI estimate without full listing data.

    Args:
        price: Purchase price
        sqft: Floor area in square feet
        hold_years: Holding period
        annual_appreciation: Annual price appreciation rate

    Returns:
        Dict with estimated ROI metrics
    """
    # Rough estimates
    rent_psf = 3.5  # Market average
    monthly_rent = sqft * rent_psf
    annual_rent = monthly_rent * 12

    # Simplified cost estimates
    bsd = CostCalculator().calculate_bsd(price)
    # v2.2: Apply appreciation decay (same as full calculator)
    compounded = float(price)
    for yr in range(hold_years):
        compounded *= (1 + annual_appreciation * (0.97 ** yr))
    exit_price = int(compounded)

    # Annual costs (simplified)
    annual_costs = sqft * 0.35 * 12  # MCST
    annual_costs += monthly_rent * 2  # Vacancy + agent
    annual_costs += 1800  # Repairs/insurance
    annual_costs += annual_rent * 0.15  # Property tax estimate

    net_annual = annual_rent - annual_costs
    total_net_rental = net_annual * hold_years

    capital_gain = exit_price - price
    exit_costs = int(exit_price * 0.02) + 3000  # Agent + legal

    total_return = capital_gain + total_net_rental - exit_costs
    total_investment = price + bsd + 3500

    roi = (total_return / total_investment) * 100
    if roi > -100:
        annualized = ((1 + roi / 100) ** (1 / hold_years) - 1) * 100
    else:
        annualized = 0

    return {
        "purchase_price": price,
        "exit_price": exit_price,
        "monthly_rent": round(monthly_rent),
        "gross_yield_pct": round((annual_rent / price) * 100, 2),
        "capital_gain": capital_gain,
        "total_return": round(total_return),
        "roi_pct": round(roi, 2),
        "annualized_roi_pct": round(annualized, 2),
    }
