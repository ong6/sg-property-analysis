"""Tests for ROI calculation."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring.roi import ROICalculator, calculate_roi, quick_roi_estimate


class TestROICalculator:
    """Test ROI calculation functionality."""

    def setup_method(self):
        self.calc = ROICalculator(buyer_type="SC", property_count=0)

    def test_calculate_basic(self):
        """Test basic ROI calculation."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
            "district": "D05",
        }

        roi = self.calc.calculate(listing, hold_years=5)

        assert roi.hold_years == 5
        assert roi.purchase_price == 2_400_000
        assert roi.estimated_exit_price > roi.purchase_price
        assert roi.total_upfront_costs > 0
        assert roi.roi_percent != 0

    def test_calculate_with_custom_rent(self):
        """Test ROI with custom rental estimate."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        roi = self.calc.calculate(listing, hold_years=5, monthly_rent=5000)

        assert roi.monthly_rent_estimate == 5000
        assert roi.annual_rent_gross == 60_000

    def test_calculate_with_custom_exit_price(self):
        """Test ROI with custom exit price."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        roi = self.calc.calculate(listing, hold_years=5, exit_price=2_800_000)

        assert roi.estimated_exit_price == 2_800_000
        assert roi.capital_gain == 400_000

    def test_upfront_costs_include_bsd(self):
        """Upfront costs should include BSD."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        roi = self.calc.calculate(listing, hold_years=5)

        # BSD for $2.4M = $44,600 + 5% of $900k = $44,600 + $45,000 = $89,600
        expected_bsd = 89_600
        expected_upfront = 2_400_000 + expected_bsd + 3500  # + legal

        assert roi.total_upfront_costs == expected_upfront

    def test_no_ssd_after_4_years(self):
        """SSD should be 0 for hold period > 4 years."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        roi = self.calc.calculate(listing, hold_years=5)

        # SSD is 0 after 3 years
        # Exit costs should not include SSD
        # Exit costs = agent commission (2%) + legal ($3000)
        expected_exit_costs = int(roi.estimated_exit_price * 0.02) + 3000
        assert abs(roi.total_exit_costs - expected_exit_costs) < 100

    def test_gross_yield_calculation(self):
        """Gross yield should be calculated correctly."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        roi = self.calc.calculate(listing, hold_years=5, monthly_rent=5000)

        # Gross yield = (monthly * 12) / price * 100
        expected_yield = (5000 * 12 / 2_400_000) * 100
        assert abs(roi.gross_rental_yield - expected_yield) < 0.01

    def test_net_yield_less_than_gross(self):
        """Net yield should be less than gross yield due to costs."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        roi = self.calc.calculate(listing, hold_years=5, monthly_rent=5000)

        assert roi.net_rental_yield < roi.gross_rental_yield

    def test_capital_gain_with_appreciation(self):
        """Capital gain should reflect appreciation."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        # Default appreciation is 2% per year
        roi = self.calc.calculate(listing, hold_years=5)

        # Expected exit price with 2% annual appreciation
        expected_exit = int(2_400_000 * (1.02 ** 5))
        assert abs(roi.estimated_exit_price - expected_exit) < 1000

        assert roi.capital_gain > 0

    def test_total_return_calculation(self):
        """Total return should be capital gain + net rental - exit costs."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        roi = self.calc.calculate(listing, hold_years=5, monthly_rent=5000)

        # Total return = capital_gain + total_rental_income - total_exit_costs
        # Note: total_rental_income is net rental
        expected_return = roi.capital_gain + roi.total_rental_income - roi.total_exit_costs
        assert abs(roi.total_return - expected_return) < 1

    def test_annualized_roi_positive(self):
        """Annualized ROI should be positive for typical investment."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
            "district": "D05",
        }

        roi = self.calc.calculate(listing, hold_years=5)

        # With 2% appreciation and ~3% yield, should be positive
        assert roi.annualized_roi > 0

    def test_calculate_multiple_periods(self):
        """Should calculate ROI for multiple holding periods."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        results = self.calc.calculate_multiple_periods(listing, periods=[5, 6, 7])

        assert 5 in results
        assert 6 in results
        assert 7 in results

        assert results[5].hold_years == 5
        assert results[7].hold_years == 7

        # Longer hold should generally have higher total return
        assert results[7].total_return > results[5].total_return


class TestQuickROIEstimate:
    """Test quick ROI estimation function."""

    def test_quick_estimate(self):
        """Quick estimate should return reasonable values."""
        result = quick_roi_estimate(
            price=2_400_000,
            sqft=1000,
            hold_years=5,
            annual_appreciation=0.02,
        )

        assert result["purchase_price"] == 2_400_000
        assert result["monthly_rent"] > 0
        assert result["gross_yield_pct"] > 0
        assert result["exit_price"] > result["purchase_price"]
        assert result["capital_gain"] > 0
        assert "roi_pct" in result
        assert "annualized_roi_pct" in result

    def test_quick_estimate_realistic_yield(self):
        """Quick estimate yield should be realistic."""
        result = quick_roi_estimate(price=2_400_000, sqft=1000)

        # Singapore yields typically 1.5-4% depending on price/location
        # Higher-priced properties tend to have lower yields
        assert 1.0 <= result["gross_yield_pct"] <= 5.0


class TestROIEdgeCases:
    """Test edge cases in ROI calculation."""

    def setup_method(self):
        self.calc = ROICalculator()

    def test_missing_sqft(self):
        """Should handle missing sqft gracefully."""
        listing = {
            "price": 2_400_000,
        }

        roi = self.calc.calculate(listing, hold_years=5)

        # Should return empty result
        assert roi.purchase_price == 2_400_000
        assert roi.total_rental_income == 0
        assert roi.annualized_roi == 0

    def test_missing_price(self):
        """Should handle missing price gracefully."""
        listing = {
            "sqft": 1000,
        }

        roi = self.calc.calculate(listing, hold_years=5)

        assert roi.purchase_price == 0
        assert roi.roi_percent == 0

    def test_different_buyer_types(self):
        """Different buyer types should affect ABSD."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        # SC first property (no ABSD)
        calc_sc = ROICalculator(buyer_type="SC", property_count=0)
        roi_sc = calc_sc.calculate(listing, hold_years=5)

        # PR first property (5% ABSD)
        calc_pr = ROICalculator(buyer_type="PR", property_count=0)
        roi_pr = calc_pr.calculate(listing, hold_years=5)

        # PR should have higher upfront costs due to ABSD
        assert roi_pr.total_upfront_costs > roi_sc.total_upfront_costs

        # PR should have lower ROI
        assert roi_pr.roi_percent < roi_sc.roi_percent


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
