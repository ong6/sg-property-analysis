"""Rental yield estimation for property listings."""

import json
import os
from typing import Any, Optional

from utils.geo import normalize_district

# Load district medians
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_DISTRICT_MEDIANS_FILE = os.path.join(_DATA_DIR, "district_medians.json")

_district_data: dict = {}

_RENTAL_CACHE_FILE = os.path.join(_DATA_DIR, "rental_cache.json")
_rental_cache: "dict | None" = None


def _load_rental_cache() -> dict:
    """Real per-project rental medians from URA rental contracts
    (data/rental_cache.json, built by build_rental_cache.py from
    fetch_ura_rentals.py CSVs). Keyed '<project lower>|<district num>'.

    v3.5: replaces the synthetic district-constant rents that made the yield
    component mechanically const/PSF (re-skinned cheapness). Module-level cache."""
    global _rental_cache
    if _rental_cache is None:
        if os.path.exists(_RENTAL_CACHE_FILE):
            with open(_RENTAL_CACHE_FILE) as f:
                _rental_cache = json.load(f).get("projects", {})
        else:
            _rental_cache = {}
    return _rental_cache


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

    DEFAULT_RENTAL_PSF = 3.50  # Market average fallback (bedroom count unknown)

    # Bedroom-aware fallback (small units rent at higher PSF — a flat rate
    # systematically under-yields 1-2BR and over-yields 4BR+). Shape follows
    # covered districts' bedroom_rental_psf, set slightly conservative.
    FALLBACK_RENTAL_PSF_BY_BEDS = {1: 5.0, 2: 4.3, 3: 3.8, 4: 3.4, 5: 3.2}
    # v3.6.2: typical livable sqft per bed count (SG condo norms; midpoints of
    # the scorer's _BEDROOM_SQFT_RANGES, biased toward the rental stock which
    # skews compact). Used to cap the sqft that bed-derived psf rates multiply.
    RENT_TYPICAL_SQFT_BY_BEDS = {1: 550, 2: 800, 3: 1150, 4: 1500, 5: 1900}

    def __init__(self, condo_rental_data: Optional[dict] = None):
        """
        Initialize with optional condo-level rental data.

        Args:
            condo_rental_data: Dict mapping project_name -> median rent_psf
        """
        self.condo_medians = condo_rental_data or {}
        self.district_data = _load_district_data()

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
        # v3.6.2: bed-derived psf rates come from TYPICAL-size units of that
        # bed count and do not extrapolate linearly to oversized sqft — a
        # 807sqft "1BR" priced at small-1BR psf rates yielded a fake $4,963/mo
        # (real fringe-1BR rents ~$3k), which fed a fake ~6% gross yield into
        # the MMR yield channel (and Vetro's 829 strata sqft on a 474sqft
        # livable plate is rent-irrelevant terrace area). Cap the sqft used
        # for rent at 1.25x the bed count's typical size for bed-matched
        # sources; tenants pay for the livable space a bed count implies.
        rent_sqft = sqft
        beds = listing.get("beds")
        if source in ("ura_project_bed", "district_bedroom", "fallback_bedroom"):
            typical = self.RENT_TYPICAL_SQFT_BY_BEDS.get(beds)
            if typical:
                rent_sqft = min(sqft, typical * 1.25)
        monthly_rent = rent_sqft * rent_psf
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

        district = listing.get("district", "")
        beds = listing.get("beds")

        # Priority 1b (v3.5): REAL rents — URA rental-contract medians for this
        # exact project (data/rental_cache.json). Bedroom-matched median when
        # >=3 contracts of that bed type, else the project median. Exact
        # project+district key only (no fuzzy: a wrong project poisons rent).
        if project_name:
            cache = _load_rental_cache()
            if cache:
                dnum = (normalize_district(district) or "").replace("D", "").lstrip("0")
                entry = cache.get(f"{project_name.lower().strip()}|{dnum}")
                if entry:
                    bed_entry = entry.get("by_beds", {}).get(str(beds)) if beds else None
                    if bed_entry:
                        return bed_entry["rent_psf"], "ura_project_bed"
                    return entry["rent_psf"], "ura_project"

        # Priority 2: Bedroom-specific district rental PSF
        if district:
            d = normalize_district(district)
            bedroom_rates = self.district_data.get("bedroom_rental_psf", {})
            if d in bedroom_rates and beds:
                bed_key = str(beds)
                if bed_key in bedroom_rates[d]:
                    return bedroom_rates[d][bed_key], "district_bedroom"

        # Priority 3: District median (fallback for unknown bedroom count)
        if district:
            d = normalize_district(district)
            medians = self.district_data.get("medians", {})
            if d in medians:
                return medians[d].get("rental_psf", self.DEFAULT_RENTAL_PSF), "district_median"

        # Priority 4: Bedroom-aware fallback
        if beds in self.FALLBACK_RENTAL_PSF_BY_BEDS:
            return self.FALLBACK_RENTAL_PSF_BY_BEDS[beds], "fallback_bedroom"

        # Priority 5: Flat fallback (bedroom count unknown)
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

