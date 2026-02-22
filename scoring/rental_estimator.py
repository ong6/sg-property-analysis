"""Rental yield estimation for property listings."""

import json
import os
from typing import Any, Optional

# Load district medians
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_DISTRICT_MEDIANS_FILE = os.path.join(_DATA_DIR, "district_medians.json")

_district_data: dict = {}


def _load_district_data() -> dict:
    """Load district median data."""
    global _district_data
    if not _district_data:
        if os.path.exists(_DISTRICT_MEDIANS_FILE):
            with open(_DISTRICT_MEDIANS_FILE) as f:
                _district_data = json.load(f)
        else:
            # Defaults for target districts
            _district_data = {
                "medians": {
                    "D03": {"psf": 2100, "rental_psf": 3.80, "avg_yield": 3.35},
                    "D05": {"psf": 2000, "rental_psf": 3.60, "avg_yield": 3.35},
                    "D14": {"psf": 2200, "rental_psf": 3.70, "avg_yield": 3.30},
                    "D15": {"psf": 1900, "rental_psf": 3.50, "avg_yield": 3.40},
                }
            }
    return _district_data


class RentalEstimator:
    """
    Estimate rental income for properties.

    Priority:
    1. Same-condo rental data (most accurate)
    2. District median rental PSF
    3. Market fallback
    """

    DEFAULT_RENTAL_PSF = 3.50  # Market average fallback

    def __init__(self, condo_rental_data: Optional[dict] = None):
        """
        Initialize with optional condo-level rental data.

        Args:
            condo_rental_data: Dict mapping project_name -> median rent_psf
        """
        self.condo_medians = condo_rental_data or {}
        self.district_data = _load_district_data()

    def update_condo_data(self, condo_name: str, rent_psf: float):
        """Add or update condo rental data."""
        self.condo_medians[condo_name.lower()] = rent_psf

    def estimate(self, listing: dict[str, Any]) -> dict:
        """
        Estimate rental for a listing.

        Returns:
            Dict with:
            - monthly_rent: Estimated monthly rent
            - annual_rent: Estimated annual rent
            - gross_yield: Gross rental yield percentage
            - rent_psf: Rent per square foot per month
            - source: Data source ("same_condo", "district_median", "fallback")
        """
        sqft = listing.get("sqft")
        price = listing.get("price", 0)

        if not sqft or not price:
            return {
                "monthly_rent": 0,
                "annual_rent": 0,
                "gross_yield": 0,
                "rent_psf": 0,
                "source": "unavailable",
            }

        rent_psf, source = self._get_rent_psf(listing)
        monthly_rent = sqft * rent_psf
        annual_rent = monthly_rent * 12
        gross_yield = (annual_rent / price) * 100 if price > 0 else 0

        return {
            "monthly_rent": round(monthly_rent, 2),
            "annual_rent": round(annual_rent, 2),
            "gross_yield": round(gross_yield, 2),
            "rent_psf": round(rent_psf, 2),
            "source": source,
        }

    def _get_rent_psf(self, listing: dict[str, Any]) -> tuple[float, str]:
        """
        Get rental PSF from best available source.

        Returns:
            Tuple of (rent_psf, source)
        """
        # Priority 1: Same condo data
        project_name = listing.get("project_name", "")
        if project_name:
            normalized = project_name.lower().strip()
            if normalized in self.condo_medians:
                return self.condo_medians[normalized], "same_condo"

            # Try partial match
            for condo_name, rent_psf in self.condo_medians.items():
                if condo_name in normalized or normalized in condo_name:
                    return rent_psf, "same_condo"

        # Priority 2: District median
        district = listing.get("district", "")
        if district:
            # Normalize district code
            d = district.upper()
            if not d.startswith("D"):
                d = f"D{int(d):02d}"

            medians = self.district_data.get("medians", {})
            if d in medians:
                return medians[d].get("rental_psf", self.DEFAULT_RENTAL_PSF), "district_median"

        # Priority 3: Fallback
        return self.DEFAULT_RENTAL_PSF, "fallback"

    def estimate_yield_score(self, listing: dict[str, Any]) -> dict:
        """
        Calculate rental yield score for scoring system.

        Returns score breakdown:
        - gross_yield_score: 0-12 pts based on yield percentage
        - Additional context for scoring
        """
        rental = self.estimate(listing)
        gross_yield = rental["gross_yield"]

        # Score based on gross yield
        if gross_yield >= 4.0:
            yield_score = 12
        elif gross_yield >= 3.5:
            yield_score = 9
        elif gross_yield >= 3.2:
            yield_score = 6
        elif gross_yield >= 3.0:
            yield_score = 3
        else:
            yield_score = 0

        return {
            "gross_yield": gross_yield,
            "gross_yield_score": yield_score,
            "monthly_rent": rental["monthly_rent"],
            "rent_psf": rental["rent_psf"],
            "source": rental["source"],
        }


def estimate_rental(listing: dict, condo_data: Optional[dict] = None) -> dict:
    """Convenience function to estimate rental for a single listing."""
    estimator = RentalEstimator(condo_data)
    return estimator.estimate(listing)


def estimate_gross_yield(
    price: int, sqft: float, district: Optional[str] = None
) -> float:
    """
    Quick estimate of gross yield without full listing data.

    Args:
        price: Purchase price
        sqft: Floor area in square feet
        district: Optional district code (e.g., "D05")

    Returns:
        Estimated gross yield percentage
    """
    district_data = _load_district_data()

    rent_psf = RentalEstimator.DEFAULT_RENTAL_PSF
    if district:
        d = district.upper()
        if not d.startswith("D"):
            d = f"D{int(d):02d}"
        medians = district_data.get("medians", {})
        if d in medians:
            rent_psf = medians[d].get("rental_psf", rent_psf)

    annual_rent = sqft * rent_psf * 12
    return (annual_rent / price) * 100 if price > 0 else 0
