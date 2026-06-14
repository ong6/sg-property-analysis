"""Tests for cost calculation utilities."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring.costs import CostCalculator


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
        """SSD within 1 year: 16% (post Jul-2025 schedule)."""
        assert self.calc.calculate_ssd(2_500_000, 0) == 400_000
        assert self.calc.calculate_ssd(2_500_000, 0.5) == 400_000

    def test_ssd_1_to_2_years(self):
        """SSD 1-2 years: 12%."""
        assert self.calc.calculate_ssd(2_500_000, 1) == 300_000
        assert self.calc.calculate_ssd(2_500_000, 1.5) == 300_000

    def test_ssd_2_to_3_years(self):
        """SSD 2-3 years: 8%."""
        assert self.calc.calculate_ssd(2_500_000, 2) == 200_000
        assert self.calc.calculate_ssd(2_500_000, 2.5) == 200_000

    def test_ssd_3_to_4_years(self):
        """SSD 3-4 years: 4%."""
        assert self.calc.calculate_ssd(2_500_000, 3) == 100_000
        assert self.calc.calculate_ssd(2_500_000, 3.5) == 100_000

    def test_ssd_after_4_years(self):
        """SSD after 4 years: 0%."""
        assert self.calc.calculate_ssd(2_500_000, 4) == 0
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
        """$30k-$45k at 20% (IRAS 2024+ non-owner-occupied)."""
        # AV = $45,000 -> Tax = $3,600 + 15k*0.20 = $3,600 + $3,000 = $6,600
        assert self.calc.calculate_property_tax(45_000) == 6_600

    def test_property_tax_typical_investment(self):
        """Test typical investment property AV (IRAS 2024+ schedule)."""
        # Monthly rent ~$5,000 = AV $60,000
        # Tax = $3,600 + 15k*0.20 + 15k*0.28 = $3,600 + $3,000 + $4,200 = $10,800
        assert self.calc.calculate_property_tax(60_000) == 10_800

    def test_property_tax_high_av(self):
        """Test high annual value property (top marginal 36%, IRAS 2024+)."""
        # AV = $85,000
        # Tax = $3,600 + $3,000 + $4,200 + 25k*0.36 = $10,800 + $9,000 = $19,800
        assert self.calc.calculate_property_tax(85_000) == 19_800


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

        # Vacancy cost: 0.75 months * $5000 = $3,750 (~6% vacancy)
        assert breakdown.vacancy_cost == 3_750

        # Agent rental: 0.5 months * $5000 = $2,500
        assert breakdown.agent_rental_fee == 2_500


class TestCostParameters:
    """The ROI-side assumptions live in cost_parameters.json."""

    def test_income_tax_and_mortgage_params_present(self):
        params = CostCalculator().params
        assert params.get("marginal_income_tax_rate") == 0.15
        assert params.get("mortgage_rate_pct") == 3.5

    def test_vacancy_tier_table_present(self):
        tiers = CostCalculator().params.get("vacancy_tiers")
        assert tiers is not None
        # The mid tier equals the legacy flat default (continuity).
        assert tiers["mid_months"] == 0.75
        assert tiers["low_months"] < tiers["mid_months"] < tiers["high_months"]


class TestVacancyTiers:
    """Vacancy prior tiered by (beds, price band) — format-aware void."""

    def setup_method(self):
        self.calc = CostCalculator()

    def test_low_tier_small_mass_market(self):
        """1-2BR at <=$1.5M let fastest -> low (0.5mo)."""
        assert self.calc.resolve_vacancy_months(1, 1_000_000) == 0.5
        assert self.calc.resolve_vacancy_months(2, 1_500_000) == 0.5  # boundary

    def test_mid_tier_default(self):
        """2-3BR in the $1.5-3M band -> mid (0.75mo == old flat default)."""
        assert self.calc.resolve_vacancy_months(2, 2_000_000) == 0.75
        assert self.calc.resolve_vacancy_months(3, 2_500_000) == 0.75

    def test_high_tier_large_or_luxury(self):
        """4BR+ OR >$3M let slowest -> high (1.2mo)."""
        assert self.calc.resolve_vacancy_months(4, 2_000_000) == 1.2  # beds drives it
        assert self.calc.resolve_vacancy_months(2, 3_500_000) == 1.2  # price drives it

    def test_fallback_when_beds_or_price_absent_equals_flat_default(self):
        """Missing beds or price -> the unchanged flat 0.75mo prior."""
        flat = self.calc.params.get("vacancy_months_per_year", 0.75)
        assert flat == 0.75
        assert self.calc.resolve_vacancy_months(None, 1_000_000) == flat
        assert self.calc.resolve_vacancy_months(1, None) == flat
        assert self.calc.resolve_vacancy_months(None, None) == flat

    def test_holding_costs_use_tier_when_no_override(self):
        """calculate_annual_holding_costs resolves the tier from (beds, price)."""
        # 1BR $1.0M -> low (0.5mo): vacancy_cost = 0.5 * rent
        low = self.calc.calculate_annual_holding_costs(
            monthly_rent=4000, sqft=550, beds=1, price=1_000_000
        )
        assert low["vacancy_months"] == 0.5
        assert low["vacancy_cost"] == 4000 * 0.5
        # 4BR $2.0M -> high (1.2mo)
        high = self.calc.calculate_annual_holding_costs(
            monthly_rent=8000, sqft=1600, beds=4, price=2_000_000
        )
        assert high["vacancy_months"] == 1.2
        assert high["vacancy_cost"] == 8000 * 1.2

    def test_holding_costs_explicit_override_wins(self):
        """An explicit vacancy_months_per_year overrides the tier."""
        out = self.calc.calculate_annual_holding_costs(
            monthly_rent=5000, sqft=1000, beds=1, price=1_000_000,
            vacancy_months_per_year=0.9,
        )
        assert out["vacancy_months"] == 0.9
        assert out["vacancy_cost"] == 5000 * 0.9

    def test_holding_costs_fallback_when_beds_price_absent(self):
        """No beds/price (legacy callers) -> flat 0.75, unchanged behavior."""
        out = self.calc.calculate_annual_holding_costs(monthly_rent=5000, sqft=1000)
        assert out["vacancy_months"] == 0.75
        assert out["vacancy_cost"] == 5000 * 0.75


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
