"""Rental yield estimation for property listings."""

import json
import os
from datetime import date
from typing import Any, Optional

from utils.geo import normalize_district

# Load district medians
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_DISTRICT_MEDIANS_FILE = os.path.join(_DATA_DIR, "district_medians.json")

_district_data: dict = {}

_RENTAL_CACHE_FILE = os.path.join(_DATA_DIR, "rental_cache.json")
_rental_cache: "dict | None" = None
# Audit #4 (Jun 2026): a stale rental cache silently kept conf 0.95 — track
# the build date and damp confidence 0.85x once it ages past the threshold.
_rental_cache_stale: bool = False
_CACHE_STALE_DAYS = 90


def _load_rental_cache() -> dict:
    """Real per-project rental medians from URA rental contracts
    (data/rental_cache.json, built by build_rental_cache.py from
    fetch_ura_rentals.py CSVs). Keyed '<project lower>|<district num>'.

    v3.5: replaces the synthetic district-constant rents that made the yield
    component mechanically const/PSF (re-skinned cheapness). Module-level cache."""
    global _rental_cache, _rental_cache_stale
    if _rental_cache is None:
        if os.path.exists(_RENTAL_CACHE_FILE):
            with open(_RENTAL_CACHE_FILE) as f:
                blob = json.load(f)
            _rental_cache = blob.get("projects", {})
            built = blob.get("built")
            try:
                age_days = (date.today() - date.fromisoformat(built)).days if built else None
            except (TypeError, ValueError):
                age_days = None
            if age_days is not None and age_days > _CACHE_STALE_DAYS:
                _rental_cache_stale = True
                print(f"[rental_estimator] rental_cache.json built {built} "
                      f"({age_days}d ago > {_CACHE_STALE_DAYS}d) — rent confidence damped 0.85x; "
                      "re-run fetch_ura_rentals.py + build_rental_cache.py")
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

    # Audit #4: base confidence per source — kept in lockstep with the mmr.py
    # rent_conf map (the MMR side takes min(map, rent_confidence)). Scaled down
    # by contract depth / serving window / cache staleness for cache-backed
    # sources; emitted as `confidence` on every successful estimate.
    BASE_CONFIDENCE_BY_SOURCE = {
        "same_condo": 1.0,
        "ura_project_bed": 0.95,
        "ura_project": 0.8,
        "district_bedroom": 0.55,
        "district_median": 0.5,
        "fallback_bedroom": 0.15,
        "fallback": 0.15,
    }
    _CACHE_SOURCES = ("ura_project_bed", "ura_project")

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
            - rent_psf: EFFECTIVE rent per sqft of THIS unit (monthly_rent /
              strata sqft — internally consistent with monthly_rent)
            - source_rent_psf: the comp's raw $/sqft rate the estimate is
              based on (differs from rent_psf when the sqft cap engaged or a
              bed-matched contract median was served directly)
            - source: Data source ("ura_project_bed", "district_median", ...)
            - confidence: 0-1 numeric trust in the estimate (audit #4 —
              base-by-source x contract depth x window x cache staleness)
            - contracts / window_months: cache evidence behind it (None for
              non-cache sources)
            - capped: True when the v3.6.2 sqft cap reduced the rent
        """
        sqft = listing.get("sqft")
        price = listing.get("price", 0)

        if not sqft or not price:
            return {
                "monthly_rent": 0,
                "annual_rent": 0,
                "gross_yield": 0,
                "rent_psf": 0,
                "source_rent_psf": None,
                "source": "unavailable",
                "confidence": 0.0,
                "contracts": None,
                "window_months": None,
                "capped": False,
            }

        basis = self._get_rent_basis(listing)
        source = basis["source"]
        rent_psf = basis["rent_psf"]
        beds = listing.get("beds")
        capped = False

        if basis.get("monthly_rent") is not None:
            # Audit #7 (Jun 2026): when the cache has a bed-matched contract
            # median, serve it DIRECTLY — real same-bed tenants already price
            # the unit's livable space. psf x strata sqft both faked oversized-
            # unit rents (1,086sf "1BR" -> $5,137/mo) and, mirrored, capped
            # genuine large units below their own prints (Costa Rhu 1,250sf
            # 2BR real median $5,225 was being served $4,190).
            monthly_rent = float(basis["monthly_rent"])
        else:
            # v3.6.2: psf rates come from TYPICAL-size units and do not
            # extrapolate linearly to oversized sqft — a 807sqft "1BR" priced
            # at small-1BR psf rates yielded a fake $4,963/mo (real fringe-1BR
            # rents ~$3k), which fed a fake ~6% gross yield into the MMR yield
            # channel (and Vetro's 829 strata sqft on a 474sqft livable plate
            # is rent-irrelevant terrace area). Audit #7: the cap now guards
            # EVERY psf x sqft path whenever the bed count is known — the
            # project-pooled `ura_project` branch used to bypass it and
            # resurrected the exact artifact at higher confidence.
            rent_sqft = sqft
            typical = self.RENT_TYPICAL_SQFT_BY_BEDS.get(beds)
            if typical and sqft > typical * 1.25:
                rent_sqft = typical * 1.25
                capped = True
            monthly_rent = rent_sqft * rent_psf

        annual_rent = monthly_rent * 12
        gross_yield = (annual_rent / price) * 100 if price > 0 else 0
        confidence = self._confidence(
            source, basis.get("contracts"), basis.get("window_months"))

        return {
            "monthly_rent": round(monthly_rent, 2),
            "annual_rent": round(annual_rent, 2),
            "gross_yield": round(gross_yield, 2),
            "rent_psf": round(monthly_rent / sqft, 2),
            "source_rent_psf": round(rent_psf, 2) if rent_psf else None,
            "source": source,
            "confidence": confidence,
            "contracts": basis.get("contracts"),
            "window_months": basis.get("window_months"),
            "capped": capped,
        }

    def _confidence(
        self,
        source: str,
        contracts: Optional[int] = None,
        window_months: Optional[int] = None,
    ) -> float:
        """Numeric trust in a rent estimate (audit #4).

        Base by source (mirrors mmr.py's rent_conf map), then for cache-backed
        sources: x (0.5 + 0.5*min(1, contracts/10)) for contract depth, x 0.9
        when served from the widened 24mo window, x 0.85 when the cache build
        itself is stale (>90d). 3 contracts no longer earn the same 0.95 as 190.
        """
        base = self.BASE_CONFIDENCE_BY_SOURCE.get(
            source, 0.15 if source.startswith("fallback") else 0.6)
        conf = base
        if source in self._CACHE_SOURCES:
            conf *= 0.5 + 0.5 * min(1.0, (contracts or 0) / 10.0)
            if window_months == 24:
                conf *= 0.9
            if _rental_cache_stale:
                conf *= 0.85
        return round(conf, 3)

    def _get_rent_basis(self, listing: dict[str, Any]) -> dict:
        """
        Get the rental basis from the best available source.

        Returns:
            Dict with rent_psf, source, and (when cache-backed) the evidence
            behind it: monthly_rent (bed-matched contract median, served
            directly), contracts, window_months.
        """
        # Priority 1: Same condo data
        project_name = listing.get("project_name", "")
        if project_name:
            normalized = project_name.lower().strip()
            if normalized in self.condo_medians:
                return {"rent_psf": self.condo_medians[normalized], "source": "same_condo"}

            # Try partial match
            for condo_name, rent_psf in self.condo_medians.items():
                if condo_name in normalized or normalized in condo_name:
                    return {"rent_psf": rent_psf, "source": "same_condo"}

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
                        return {
                            "rent_psf": bed_entry["rent_psf"],
                            "source": "ura_project_bed",
                            # bed-matched contract median, served directly (#7)
                            "monthly_rent": bed_entry.get("monthly_rent"),
                            "contracts": bed_entry.get("contracts"),
                            "window_months": entry.get("window_months"),
                        }
                    return {
                        "rent_psf": entry["rent_psf"],
                        "source": "ura_project",
                        "contracts": entry.get("contracts"),
                        "window_months": entry.get("window_months"),
                    }

        # Priority 2: Bedroom-specific district rental PSF
        if district:
            d = normalize_district(district)
            bedroom_rates = self.district_data.get("bedroom_rental_psf", {})
            if d in bedroom_rates and beds:
                bed_key = str(beds)
                if bed_key in bedroom_rates[d]:
                    return {"rent_psf": bedroom_rates[d][bed_key], "source": "district_bedroom"}

        # Priority 3: District median (fallback for unknown bedroom count)
        if district:
            d = normalize_district(district)
            medians = self.district_data.get("medians", {})
            if d in medians:
                return {"rent_psf": medians[d].get("rental_psf", self.DEFAULT_RENTAL_PSF),
                        "source": "district_median"}

        # Priority 4: Bedroom-aware fallback
        if beds in self.FALLBACK_RENTAL_PSF_BY_BEDS:
            return {"rent_psf": self.FALLBACK_RENTAL_PSF_BY_BEDS[beds], "source": "fallback_bedroom"}

        # Priority 5: Flat fallback (bedroom count unknown)
        return {"rent_psf": self.DEFAULT_RENTAL_PSF, "source": "fallback"}

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
            # Audit #4 contract: the scorer copies confidence onto the scored
            # listing as rent_confidence (MMR takes min(map, rent_confidence)).
            # NOTE: contracts/window_months/capped below are currently DROPPED
            # by FullScorer._score_rental_yield, so factual_data.rental
            # .rent_evidence reports them as null/false — see the KNOWN GAP note
            # on ScoredListing.rent_contracts.
            "confidence": rental["confidence"],
            "contracts": rental["contracts"],
            "window_months": rental["window_months"],
            "capped": rental["capped"],
        }

