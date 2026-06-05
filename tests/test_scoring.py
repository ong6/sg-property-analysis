"""Tests for scoring system."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring.quick_scorer import QuickScorer, quick_score_listing, filter_listings
from scoring.full_scorer import FullScorer, score_listings
from scoring.rental_estimator import RentalEstimator, estimate_rental


class TestQuickScorer:
    """Test quick scoring functionality."""

    def setup_method(self):
        self.scorer = QuickScorer()

    def test_no_price_rejection(self):
        """Price alone should never hard-reject a listing (no price range filter)."""
        for price in (500_000, 2_000_000, 10_000_000):
            listing = {"price": price, "psf": 1800, "beds": 2}
            score = self.scorer.score(listing)
            assert score.breakdown.get("rejected") is None

    def test_reject_low_lease(self):
        """Listings with < 60 years remaining should be rejected."""
        listing = {
            "price": 2_400_000,
            "psf": 1800,
            "beds": 2,
            "tenure": "99-year leasehold",
            "built_year": 1970,  # ~55 years remaining
        }
        score = self.scorer.score(listing)
        assert score.tier == 3
        assert "lease" in score.reason.lower()

    def test_valid_listing_scores(self):
        """Valid listing should receive a score."""
        listing = {
            "price": 2_400_000,
            "psf": 1800,
            "beds": 2,
            "sqft": 900,
            "tenure": "99-year leasehold",
            "built_year": 2020,
            "district": "D05",
            "mrt_info": "Clementi MRT 300m",
            "total_units": 500,
        }
        score = self.scorer.score(listing)
        assert score.score > 0
        assert score.tier in [1, 2]
        assert score.reason is None

    def test_psf_scoring(self):
        """Lower PSF should score higher."""
        low_psf = {"price": 2_400_000, "psf": 1700, "beds": 2}
        high_psf = {"price": 2_400_000, "psf": 2300, "beds": 2}

        score_low = self.scorer.score(low_psf)
        score_high = self.scorer.score(high_psf)

        assert score_low.breakdown["psf_value"]["points"] > score_high.breakdown["psf_value"]["points"]

    def test_mrt_proximity_scoring(self):
        """Closer MRT should score higher."""
        close_mrt = {"price": 2_400_000, "mrt_info": "200m to MRT", "beds": 2}
        far_mrt = {"price": 2_400_000, "mrt_info": "800m to MRT", "beds": 2}

        score_close = self.scorer.score(close_mrt)
        score_far = self.scorer.score(far_mrt)

        assert score_close.breakdown["mrt_proximity"]["points"] > score_far.breakdown["mrt_proximity"]["points"]

    def test_tenure_scoring_99lh(self):
        """99-year leasehold with good remaining lease should score well."""
        listing = {
            "price": 2_400_000,
            "tenure": "99-year leasehold",
            "built_year": 2020,
            "beds": 2,
        }
        score = self.scorer.score(listing)
        assert score.breakdown["tenure"]["points"] >= 8

    def test_tenure_scoring_freehold(self):
        """Freehold should score lower than 99LH for short-term investment."""
        lh_listing = {
            "price": 2_400_000,
            "tenure": "99-year leasehold",
            "built_year": 2020,
            "beds": 2,
        }
        fh_listing = {
            "price": 2_400_000,
            "tenure": "Freehold",
            "beds": 2,
        }

        score_lh = self.scorer.score(lh_listing)
        score_fh = self.scorer.score(fh_listing)

        # 99LH should score higher for short-term due to better PSF value
        assert score_lh.breakdown["tenure"]["points"] >= score_fh.breakdown["tenure"]["points"]

    def test_district_scoring(self):
        """High-potential districts should score higher than lower-potential ones."""
        high_potential = {"price": 2_400_000, "district": "D05", "beds": 2}
        lower_potential = {"price": 2_400_000, "district": "D28", "beds": 2}

        score_high = self.scorer.score(high_potential)
        score_lower = self.scorer.score(lower_potential)

        assert score_high.breakdown["district"]["points"] > score_lower.breakdown["district"]["points"]

    def test_bedroom_scoring(self):
        """2BR should score highest for rental potential."""
        br2 = {"price": 2_400_000, "beds": 2}
        br3 = {"price": 2_400_000, "beds": 3}
        br1 = {"price": 2_400_000, "beds": 1}

        score_2br = self.scorer.score(br2)
        score_3br = self.scorer.score(br3)
        score_1br = self.scorer.score(br1)

        assert score_2br.breakdown["bedrooms"]["points"] > score_3br.breakdown["bedrooms"]["points"]
        assert score_3br.breakdown["bedrooms"]["points"] > score_1br.breakdown["bedrooms"]["points"]

    def test_red_flag_west_facing(self):
        """West-facing units should receive penalty."""
        west = {"price": 2_400_000, "beds": 2, "facing": "West"}
        east = {"price": 2_400_000, "beds": 2, "facing": "East"}

        score_west = self.scorer.score(west)
        score_east = self.scorer.score(east)

        assert "west_facing" in score_west.breakdown["red_flags"]["flags"]
        assert "west_facing" not in score_east.breakdown["red_flags"]["flags"]

    def test_red_flag_small_development(self):
        """Small developments should receive penalty."""
        small = {"price": 2_400_000, "beds": 2, "total_units": 50}
        large = {"price": 2_400_000, "beds": 2, "total_units": 500}

        score_small = self.scorer.score(small)
        score_large = self.scorer.score(large)

        assert "small_development" in score_small.breakdown["red_flags"]["flags"]
        assert "small_development" not in score_large.breakdown["red_flags"]["flags"]


class TestFilterListings:
    """Test listing filtering functionality."""

    def test_filter_categorizes_correctly(self):
        """Filter should categorize listings into correct tiers."""
        listings = [
            # Good listing (should be tier 1 or 2)
            {
                "price": 2_400_000,
                "psf": 1800,
                "beds": 2,
                "sqft": 900,
                "tenure": "99-year leasehold",
                "built_year": 2020,
                "district": "D05",
                "mrt_info": "300m",
                "total_units": 500,
            },
            # Bad listing (should be rejected)
            {"price": 1_500_000},  # Too cheap
            # Another bad listing
            {"price": 5_000_000},  # Too expensive
        ]

        results = filter_listings(listings)

        # Check categorization
        assert len(results["tier1"]) + len(results["tier2"]) >= 1
        assert len(results["rejected"]) >= 2  # Two out-of-price listings


class TestRentalEstimator:
    """Test rental estimation functionality."""

    def setup_method(self):
        self.estimator = RentalEstimator()

    def test_estimate_with_district_median(self):
        """Should use district median when available."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
            "district": "D05",
        }
        result = self.estimator.estimate(listing)

        assert result["monthly_rent"] > 0
        assert result["gross_yield"] > 0
        assert result["source"] == "district_median"

    def test_estimate_with_condo_data(self):
        """Should prefer same-condo data when available."""
        condo_data = {"test condo": 4.0}  # $4.0/sqft
        estimator = RentalEstimator(condo_data)

        listing = {
            "price": 2_400_000,
            "sqft": 1000,
            "project_name": "Test Condo",
        }
        result = estimator.estimate(listing)

        assert result["monthly_rent"] == 4000  # 1000 * $4.0
        assert result["source"] == "same_condo"

    def test_estimate_fallback(self):
        """Should use fallback when no data available."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
            "district": "D99",  # Unknown district
        }
        result = self.estimator.estimate(listing)

        assert result["monthly_rent"] > 0
        assert result["source"] == "fallback"

    def test_gross_yield_calculation(self):
        """Gross yield should be calculated correctly."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
            "district": "D05",
        }
        result = self.estimator.estimate(listing)

        # Verify yield calculation
        expected_yield = (result["monthly_rent"] * 12 / 2_400_000) * 100
        assert abs(result["gross_yield"] - expected_yield) < 0.01


class TestFullScorer:
    """Test full scoring functionality."""

    def setup_method(self):
        self.scorer = FullScorer()

    def test_full_score_creates_scored_listing(self):
        """Full scoring should create a complete ScoredListing."""
        listing = {
            "id": "12345",
            "title": "Test Condo",
            "price": 2_400_000,
            "url": "https://example.com/12345",
            "psf": 1800,
            "sqft": 1000,
            "beds": 2,
            "tenure": "99-year leasehold",
            "built_year": 2020,
            "district": "D05",
            "mrt_info": "Clementi MRT 300m",
            "total_units": 500,
            "latitude": 1.3151,
            "longitude": 103.7654,
        }

        scored = self.scorer.score(listing)

        # Basic fields
        assert scored.id == "12345"
        assert scored.title == "Test Condo"
        assert scored.price == 2_400_000

        # Score breakdown
        assert scored.rental_yield_score >= 0
        assert scored.capital_appreciation_score >= 0
        assert scored.liquidity_score >= 0
        assert scored.cost_efficiency_score >= 0
        assert scored.red_flag_deductions >= 0

        # Total score
        assert scored.total_score > 0
        assert scored.total_score <= 100

        # ROI results
        assert scored.roi_5yr is not None
        assert scored.roi_6yr is not None
        assert scored.roi_7yr is not None

        # Rental estimate
        assert scored.estimated_monthly_rent > 0
        assert scored.estimated_gross_yield > 0

    def test_full_score_roi_periods(self):
        """ROI should increase with longer hold period (generally)."""
        listing = {
            "id": "12345",
            "title": "Test Condo",
            "price": 2_400_000,
            "url": "https://example.com/12345",
            "psf": 1800,
            "sqft": 1000,
            "beds": 2,
            "tenure": "99-year leasehold",
            "built_year": 2020,
            "district": "D05",
        }

        scored = self.scorer.score(listing)

        # Exit price should increase with time (appreciation)
        assert scored.roi_7yr.estimated_exit_price > scored.roi_5yr.estimated_exit_price

        # Total return should generally increase with time
        assert scored.roi_7yr.total_return > scored.roi_5yr.total_return

    def test_score_multiple_listings(self):
        """score_listings should handle multiple listings."""
        listings = [
            {
                "id": "1",
                "title": "Condo A",
                "price": 2_400_000,
                "url": "https://example.com/1",
                "sqft": 1000,
                "beds": 2,
            },
            {
                "id": "2",
                "title": "Condo B",
                "price": 2_500_000,
                "url": "https://example.com/2",
                "sqft": 1100,
                "beds": 3,
            },
        ]

        scored = score_listings(listings)

        assert len(scored) == 2
        # Should be sorted by score descending
        assert scored[0].total_score >= scored[1].total_score


class TestROICalculation:
    """Test ROI calculation in full scoring."""

    def setup_method(self):
        self.scorer = FullScorer()

    def test_roi_components(self):
        """ROI result should have all components."""
        listing = {
            "id": "1",
            "title": "Test",
            "price": 2_400_000,
            "url": "https://example.com/1",
            "sqft": 1000,
            "beds": 2,
            "district": "D05",
        }

        scored = self.scorer.score(listing)
        roi = scored.roi_5yr

        assert roi is not None
        assert roi.hold_years == 5
        assert roi.purchase_price == 2_400_000
        assert roi.estimated_exit_price > roi.purchase_price  # Appreciation
        assert roi.total_upfront_costs > roi.purchase_price  # Includes BSD
        assert roi.gross_rental_yield > 0
        assert roi.net_rental_yield < roi.gross_rental_yield  # Costs reduce yield
        assert roi.annualized_roi is not None

    def test_roi_realistic_values(self):
        """ROI values should be realistic for Singapore market."""
        listing = {
            "id": "1",
            "title": "Test",
            "price": 2_400_000,
            "url": "https://example.com/1",
            "sqft": 1000,
            "beds": 2,
            "district": "D05",
        }

        scored = self.scorer.score(listing)
        roi = scored.roi_5yr

        # Gross yield depends on price/location - higher priced properties have lower yields
        # For $2.4M property in D05 with 1000sqft: rent ~$3600/mo = ~1.8% gross yield
        assert 1.0 <= roi.gross_rental_yield <= 5.0

        # Annualized ROI should be realistic (1-8% is typical)
        assert 1.0 <= roi.annualized_roi <= 10.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
