"""Tests for ROI calculation."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring.roi import ROICalculator, quick_roi_estimate


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
        """Capital gain should reflect appreciation (with decay model)."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        # Default appreciation is 2% per year with 3% annual decay
        roi = self.calc.calculate(listing, hold_years=5)

        # Exit price should be above purchase (appreciation > 0)
        assert roi.estimated_exit_price > 2_400_000
        # But below naive compound (due to appreciation decay)
        naive_exit = int(2_400_000 * (1.02 ** 5))
        assert roi.estimated_exit_price < naive_exit

        assert roi.capital_gain > 0

    def test_total_return_calculation(self):
        """Total return = capital gain + net rental - exit costs - ENTRY costs.

        Audit #11: BSD/ABSD/legal-buy are sunk at purchase — they used to
        inflate only the denominator while never reducing the return.
        """
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        roi = self.calc.calculate(listing, hold_years=5, monthly_rent=5000)

        # Note: total_rental_income is net rental (after holding costs + income tax)
        expected_return = (
            roi.capital_gain + roi.total_rental_income
            - roi.total_exit_costs - roi.entry_costs
        )
        assert abs(roi.total_return - expected_return) < 1
        # Entry costs = BSD + legal for SC first property ($2.4M: 89,600 + 3,500)
        assert roi.entry_costs == 89_600 + 3_500
        # And they live in BOTH the numerator and the denominator
        assert roi.total_upfront_costs == 2_400_000 + roi.entry_costs

    def test_entry_costs_reduce_return_vs_pre_fix(self):
        """The corrected return is lower than the old (buggy) identity by
        exactly the entry costs."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        roi = self.calc.calculate(listing, hold_years=5, monthly_rent=5000)
        old_buggy_return = roi.capital_gain + roi.total_rental_income - roi.total_exit_costs
        assert abs((old_buggy_return - roi.total_return) - roi.entry_costs) < 1

    def test_rental_income_tax_applied(self):
        """Net letting profit is taxed at the marginal income tax rate."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        taxed = ROICalculator(buyer_type="SC", property_count=0)
        untaxed = ROICalculator(buyer_type="SC", property_count=0,
                                marginal_income_tax_rate=0.0)

        roi_taxed = taxed.calculate(listing, hold_years=5, monthly_rent=5000)
        roi_untaxed = untaxed.calculate(listing, hold_years=5, monthly_rent=5000)

        assert roi_taxed.marginal_income_tax_rate == 0.15  # cost_parameters.json
        assert roi_taxed.total_income_tax > 0
        assert roi_untaxed.total_income_tax == 0
        # Tax reduces net rental income and total return by exactly the tax
        assert abs(
            (roi_untaxed.total_rental_income - roi_taxed.total_rental_income)
            - roi_taxed.total_income_tax
        ) < 1
        assert roi_taxed.total_return < roi_untaxed.total_return
        # Gross yield is a market-comparable stat — untouched by income tax
        assert roi_taxed.gross_rental_yield == roi_untaxed.gross_rental_yield
        assert roi_taxed.net_rental_yield == roi_untaxed.net_rental_yield

    def test_income_tax_never_negative(self):
        """Loss-making lets (rent below holding costs) owe no income tax."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        roi = self.calc.calculate(listing, hold_years=5, monthly_rent=500)
        assert roi.total_income_tax == 0

    def test_holding_costs_scale_with_rent_growth(self):
        """Rent-linked holding costs (tax/agent/vacancy) follow the 2%/yr rent
        path instead of staying flat."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        roi = self.calc.calculate(listing, hold_years=5, monthly_rent=5000)
        annual_holding_y1 = roi.annual_rent_gross - roi.annual_rent_net
        # Strictly greater than flat-times-years because later years cost more
        assert roi.total_holding_costs > annual_holding_y1 * 5

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

    def test_quick_estimate_excludes_entry_costs(self):
        """Audit #11: quick estimate also deducts entry costs (BSD + legal)
        and taxes net letting profit, consistent with the full calculator."""
        from scoring.costs import CostCalculator

        result = quick_roi_estimate(
            price=1_000_000, sqft=500, hold_years=5, annual_appreciation=0.0,
        )
        assert result["capital_gain"] == 0

        bsd = CostCalculator().calculate_bsd(1_000_000)
        exit_costs = int(result["exit_price"] * 0.02) + 3000
        monthly_rent = 500 * 3.5
        annual_rent = monthly_rent * 12
        annual_costs = (500 * 0.35 * 12) + (monthly_rent * 1.25) + 1800 + (annual_rent * 0.15)
        net_annual = annual_rent - annual_costs
        net_annual -= max(0.0, net_annual) * 0.15  # marginal income tax
        expected = net_annual * 5 - exit_costs - bsd - 3500
        assert abs(result["total_return"] - expected) < 2


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
        """Different buyer types should affect ABSD — in the RETURN, not just
        the denominator (audit #11)."""
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

        # ABSD is sunk: PR's total return is lower by exactly the ABSD
        absd = int(2_400_000 * 0.05)
        assert abs((roi_sc.total_return - roi_pr.total_return) - absd) < 1

        # PR should have lower ROI
        assert roi_pr.roi_percent < roi_sc.roi_percent

    def test_sc_second_property_absd_hits_return(self):
        """SC 2nd property (20% ABSD) — the case the bug overstated by ~19pp."""
        listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

        calc = ROICalculator(buyer_type="SC", property_count=1)
        roi = calc.calculate(listing, hold_years=5, monthly_rent=5000)
        absd = int(2_400_000 * 0.20)
        assert roi.entry_costs == 89_600 + absd + 3_500
        expected_return = (
            roi.capital_gain + roi.total_rental_income
            - roi.total_exit_costs - roi.entry_costs
        )
        assert abs(roi.total_return - expected_return) < 1


class TestFinancedROI:
    """Optional leveraged ROI (ltv > 0); all-cash defaults must be unchanged."""

    def setup_method(self):
        self.calc = ROICalculator(buyer_type="SC", property_count=0)
        self.listing = {
            "price": 2_400_000,
            "sqft": 1000,
        }

    def test_ltv_zero_identical_to_default(self):
        """Passing ltv=0 must produce the exact all-cash result."""
        roi_default = self.calc.calculate(self.listing, hold_years=5, monthly_rent=5000)
        roi_ltv0 = self.calc.calculate(self.listing, hold_years=5, monthly_rent=5000,
                                       ltv=0.0, mortgage_rate_pct=3.5)
        assert roi_default == roi_ltv0
        assert roi_default.loan_amount == 0
        assert roi_default.total_mortgage_interest == 0
        assert roi_default.remaining_principal_at_exit == 0

    def test_financed_cash_on_cash(self):
        """At 75% LTV the invested cash is downpayment + entry costs."""
        roi = self.calc.calculate(self.listing, hold_years=5, monthly_rent=5000,
                                  ltv=0.75, mortgage_rate_pct=3.5)
        assert roi.loan_amount == 1_800_000
        assert roi.downpayment == 600_000
        assert roi.total_upfront_costs == 600_000 + roi.entry_costs
        assert roi.total_mortgage_interest > 0
        # 25yr amortization: after 5 years most principal is still outstanding
        assert 0 < roi.remaining_principal_at_exit < 1_800_000

        # Financed identity: interest is the financing cost; principal payments
        # are equity transfers netted at exit
        expected_return = (
            roi.capital_gain + roi.total_rental_income - roi.total_exit_costs
            - roi.entry_costs - roi.total_mortgage_interest
        )
        assert abs(roi.total_return - expected_return) < 1

    def test_mortgage_interest_is_tax_deductible(self):
        """Interest deducts from taxable letting profit before income tax."""
        roi_cash = self.calc.calculate(self.listing, hold_years=5, monthly_rent=5000)
        roi_fin = self.calc.calculate(self.listing, hold_years=5, monthly_rent=5000,
                                      ltv=0.75, mortgage_rate_pct=3.5)
        assert roi_fin.total_income_tax < roi_cash.total_income_tax

    def test_sensitivity_gains_rate_axis_when_financed(self):
        """calculate_sensitivity adds rate_up/rate_down only when ltv > 0."""
        plain = self.calc.calculate_sensitivity(self.listing, periods=[5],
                                                monthly_rent=5000)
        assert set(plain.keys()) == {"downside", "base", "upside"}

        financed = self.calc.calculate_sensitivity(self.listing, periods=[5],
                                                   monthly_rent=5000,
                                                   ltv=0.75, mortgage_rate_pct=3.5)
        assert {"rate_up", "rate_down"} <= set(financed.keys())
        # Higher mortgage rate -> more interest -> lower return
        assert (financed["rate_up"][5].total_return
                < financed["base"][5].total_return
                < financed["rate_down"][5].total_return)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
