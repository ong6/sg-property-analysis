"""Tests for cost calculation utilities."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring.costs import CostCalculator, calculate_bsd, calculate_property_tax


class TestBSDCalculation:
    """Test Buyer's Stamp Duty calculations."""

    def setup_method(self):
        self.calc = CostCalculator()

    def test_bsd_below_180k(self):
        """BSD for price <= $180,000 is 1%."""
        assert self.calc.calculate_bsd(100_000) == 1_000
        assert self.calc.calculate_bsd(180_000) == 1_800

    def test_bsd_180k_to_360k(self):
        """BSD for $180k-$360k: $1,800 + 2% of excess."""
        # At $360k: $1,800 + 2% of $180k = $1,800 + $3,600 = $5,400
        assert self.calc.calculate_bsd(360_000) == 5_400

    def test_bsd_360k_to_1m(self):
        """BSD for $360k-$1M: $5,400 + 3% of excess."""
        # At $1M: $5,400 + 3% of $640k = $5,400 + $19,200 = $24,600
        assert self.calc.calculate_bsd(1_000_000) == 24_600

    def test_bsd_1m_to_1_5m(self):
        """BSD for $1M-$1.5M: $24,600 + 4% of excess."""
        # At $1.5M: $24,600 + 4% of $500k = $24,600 + $20,000 = $44,600
        assert self.calc.calculate_bsd(1_500_000) == 44_600

    def test_bsd_1_5m_to_3m(self):
        """BSD for $1.5M-$3M: $44,600 + 5% of excess."""
        # At $3M: $44,600 + 5% of $1.5M = $44,600 + $75,000 = $119,600
        assert self.calc.calculate_bsd(3_000_000) == 119_600

    def test_bsd_above_3m(self):
        """BSD above $3M: $119,600 + 6% of excess."""
        # At $4M: $119,600 + 6% of $1M = $119,600 + $60,000 = $179,600
        assert self.calc.calculate_bsd(4_000_000) == 179_600

    def test_bsd_target_price_range(self):
        """Test BSD for target price range $2.2M-$2.7M."""
        # $2.2M: $44,600 + 5% of $700k = $44,600 + $35,000 = $79,600
        assert self.calc.calculate_bsd(2_200_000) == 79_600

        # $2.45M (example from plan): $44,600 + 5% of $950k = $44,600 + $47,500 = $92,100
        assert self.calc.calculate_bsd(2_450_000) == 92_100

        # $2.7M: $44,600 + 5% of $1.2M = $44,600 + $60,000 = $104,600
        assert self.calc.calculate_bsd(2_700_000) == 104_600


class TestABSDCalculation:
    """Test Additional Buyer's Stamp Duty calculations."""

    def setup_method(self):
        self.calc = CostCalculator()

    def test_sc_first_property(self):
        """Singapore Citizen first property: 0% ABSD."""
        assert self.calc.calculate_absd(2_500_000, "SC", 0) == 0

    def test_sc_second_property(self):
        """Singapore Citizen second property: 20% ABSD."""
        assert self.calc.calculate_absd(2_500_000, "SC", 1) == 500_000

    def test_sc_third_property(self):
        """Singapore Citizen third+ property: 30% ABSD."""
        assert self.calc.calculate_absd(2_500_000, "SC", 2) == 750_000

    def test_pr_first_property(self):
        """PR first property: 5% ABSD."""
        assert self.calc.calculate_absd(2_000_000, "PR", 0) == 100_000

    def test_foreigner(self):
        """Foreigner: 60% ABSD."""
        assert self.calc.calculate_absd(2_000_000, "Foreigner", 0) == 1_200_000


class TestSSDCalculation:
    """Test Seller's Stamp Duty calculations."""

    def setup_method(self):
        self.calc = CostCalculator()

    def test_ssd_within_1_year(self):
        """SSD within 1 year: 12%."""
        assert self.calc.calculate_ssd(2_500_000, 0) == 300_000
        assert self.calc.calculate_ssd(2_500_000, 0.5) == 300_000

    def test_ssd_1_to_2_years(self):
        """SSD 1-2 years: 8%."""
        assert self.calc.calculate_ssd(2_500_000, 1) == 200_000
        assert self.calc.calculate_ssd(2_500_000, 1.5) == 200_000

    def test_ssd_2_to_3_years(self):
        """SSD 2-3 years: 4%."""
        assert self.calc.calculate_ssd(2_500_000, 2) == 100_000
        assert self.calc.calculate_ssd(2_500_000, 2.5) == 100_000

    def test_ssd_after_3_years(self):
        """SSD after 3 years: 0%."""
        assert self.calc.calculate_ssd(2_500_000, 3) == 0
        assert self.calc.calculate_ssd(2_500_000, 5) == 0
        assert self.calc.calculate_ssd(2_500_000, 7) == 0


class TestPropertyTaxCalculation:
    """Test property tax calculations for non-owner-occupied."""

    def setup_method(self):
        self.calc = CostCalculator()

    def test_property_tax_first_bracket(self):
        """First $30k at 12%."""
        # AV = $30,000 -> Tax = $3,600
        assert self.calc.calculate_property_tax(30_000) == 3_600

    def test_property_tax_second_bracket(self):
        """$30k-$45k at 14%."""
        # AV = $45,000 -> Tax = $3,600 + $2,100 = $5,700
        assert self.calc.calculate_property_tax(45_000) == 5_700

    def test_property_tax_typical_investment(self):
        """Test typical investment property AV."""
        # Monthly rent ~$5,000 = AV $60,000
        # Tax = $3,600 + $2,100 + $2,400 = $8,100
        assert self.calc.calculate_property_tax(60_000) == 8_100

    def test_property_tax_high_av(self):
        """Test high annual value property."""
        # AV = $85,000
        # Tax = $3,600 + $2,100 + $2,400 + $4,500 = $12,600
        assert self.calc.calculate_property_tax(85_000) == 12_600


class TestMCSTCalculation:
    """Test MCST fee calculations."""

    def setup_method(self):
        self.calc = CostCalculator()

    def test_mcst_typical_unit(self):
        """MCST for typical 1,000 sqft unit at $0.35/sqft."""
        mcst = self.calc.calculate_mcst(1000)
        assert mcst == 350

    def test_mcst_large_unit(self):
        """MCST for larger 1,500 sqft unit."""
        mcst = self.calc.calculate_mcst(1500)
        assert mcst == 525


class TestCostBreakdown:
    """Test complete cost breakdown creation."""

    def setup_method(self):
        self.calc = CostCalculator()

    def test_cost_breakdown_sc_first_property(self):
        """Test cost breakdown for SC first property."""
        breakdown = self.calc.create_cost_breakdown(
            purchase_price=2_450_000,
            sqft=1000,
            monthly_rent=5000,
            hold_years=5,
            buyer_type="SC",
            property_count=0,
        )

        # BSD = $92,100 (calculated above)
        assert breakdown.bsd == 92_100

        # ABSD = 0 for SC first property
        assert breakdown.absd == 0

        # Total upfront
        expected_upfront = 2_450_000 + 92_100 + 0 + 3500
        assert breakdown.total_upfront == expected_upfront

        # SSD = 0 for 5 year hold
        assert breakdown.ssd == 0

    def test_cost_breakdown_annual_costs(self):
        """Test annual holding costs calculation."""
        breakdown = self.calc.create_cost_breakdown(
            purchase_price=2_500_000,
            sqft=1000,
            monthly_rent=5000,
            hold_years=5,
        )

        # MCST: 1000 * 0.35 * 12 = $4,200/year
        assert breakdown.mcst == 4_200

        # Property tax on AV of $60,000
        assert breakdown.property_tax > 0

        # Vacancy cost: 1.5 months * $5000 = $7,500
        assert breakdown.vacancy_cost == 7_500

        # Agent rental: 0.5 months * $5000 = $2,500
        assert breakdown.agent_rental_fee == 2_500


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
