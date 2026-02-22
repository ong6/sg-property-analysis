"""Cost calculation utilities for Singapore property investment."""

import json
import os
from dataclasses import dataclass
from typing import Optional

from scoring.models import CostBreakdown


# Load cost parameters
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_COST_PARAMS_FILE = os.path.join(_DATA_DIR, "cost_parameters.json")

_cost_params: dict = {}


def _load_cost_params() -> dict:
    global _cost_params
    if not _cost_params:
        if os.path.exists(_COST_PARAMS_FILE):
            with open(_COST_PARAMS_FILE) as f:
                _cost_params = json.load(f)
        else:
            # Defaults
            _cost_params = {
                "mcst_rate_per_sqft": 0.35,
                "agent_rental_months_per_year": 0.5,
                "agent_sale_commission": 0.02,
                "vacancy_months_per_year": 1.5,
                "legal_fee_buy": 3500,
                "legal_fee_sell": 3000,
                "repairs_per_year": 1500,
                "insurance_per_year": 300,
                "annual_appreciation_estimate": 0.02,
            }
    return _cost_params


class CostCalculator:
    """Calculate all costs associated with property investment."""

    def __init__(self):
        self.params = _load_cost_params()

    def calculate_bsd(self, price: int) -> int:
        """
        Calculate Buyer's Stamp Duty (BSD) for Singapore property.

        IRAS 2025 rates:
        - First $180,000: 1%
        - Next $180,000 ($180k-$360k): 2%
        - Next $640,000 ($360k-$1M): 3%
        - Next $500,000 ($1M-$1.5M): 4%
        - Next $1,500,000 ($1.5M-$3M): 5%
        - Above $3M: 6%
        """
        if price <= 180_000:
            return int(price * 0.01)
        elif price <= 360_000:
            return int(1_800 + (price - 180_000) * 0.02)
        elif price <= 1_000_000:
            return int(5_400 + (price - 360_000) * 0.03)
        elif price <= 1_500_000:
            return int(24_600 + (price - 1_000_000) * 0.04)
        elif price <= 3_000_000:
            return int(44_600 + (price - 1_500_000) * 0.05)
        else:
            return int(119_600 + (price - 3_000_000) * 0.06)

    def calculate_absd(
        self,
        price: int,
        buyer_type: str = "SC",  # SC = Singapore Citizen, PR, Foreigner
        property_count: int = 0,  # 0 = first property
    ) -> int:
        """
        Calculate Additional Buyer's Stamp Duty (ABSD).

        Current rates (2024):
        Singapore Citizen:
        - 1st property: 0%
        - 2nd property: 20%
        - 3rd+: 30%

        PR:
        - 1st property: 5%
        - 2nd property: 30%
        - 3rd+: 35%

        Foreigner:
        - All properties: 60%
        """
        if buyer_type == "SC":
            rates = [0, 0.20, 0.30]
        elif buyer_type == "PR":
            rates = [0.05, 0.30, 0.35]
        else:  # Foreigner
            return int(price * 0.60)

        rate = rates[min(property_count, 2)]
        return int(price * rate)

    def calculate_ssd(self, price: int, hold_years: int) -> int:
        """
        Calculate Seller's Stamp Duty (SSD).

        SSD rates (if sold within holding period):
        - Within 1 year: 12%
        - 1-2 years: 8%
        - 2-3 years: 4%
        - After 3 years: 0%

        Note: For investment analysis with 5-7 year hold, SSD is always 0.
        """
        if hold_years < 1:
            return int(price * 0.12)
        elif hold_years < 2:
            return int(price * 0.08)
        elif hold_years < 3:
            return int(price * 0.04)
        else:
            return 0

    def calculate_property_tax(
        self, annual_value: float, is_owner_occupied: bool = False
    ) -> float:
        """
        Calculate annual property tax based on Annual Value (AV).

        Non-owner-occupied (investment) rates:
        - First $30,000: 12%
        - Next $15,000 ($30k-$45k): 14%
        - Next $15,000 ($45k-$60k): 16%
        - Next $25,000 ($60k-$85k): 18%
        - Next $15,000 ($85k-$100k): 20%
        - Next $30,000 ($100k-$130k): 22%
        - Above $130,000: 24%

        Owner-occupied rates are lower (not implemented as this is for investment).
        """
        if is_owner_occupied:
            # Simplified owner-occupied rates
            if annual_value <= 8_000:
                return 0
            elif annual_value <= 55_000:
                return (annual_value - 8_000) * 0.04
            else:
                return 1_880 + (annual_value - 55_000) * 0.06

        # Non-owner-occupied rates
        tax = 0.0
        remaining = annual_value

        brackets = [
            (30_000, 0.12),
            (15_000, 0.14),
            (15_000, 0.16),
            (25_000, 0.18),
            (15_000, 0.20),
            (30_000, 0.22),
            (float("inf"), 0.24),
        ]

        for bracket_size, rate in brackets:
            if remaining <= 0:
                break
            taxable = min(remaining, bracket_size)
            tax += taxable * rate
            remaining -= taxable

        return tax

    def calculate_mcst(self, sqft: float) -> float:
        """Calculate monthly MCST (maintenance) fees."""
        rate = self.params.get("mcst_rate_per_sqft", 0.35)
        return sqft * rate

    def calculate_annual_holding_costs(
        self,
        monthly_rent: float,
        sqft: float,
    ) -> dict:
        """
        Calculate all annual holding costs.

        Returns dict with:
        - property_tax: Based on annual value (rent * 12)
        - mcst: Monthly MCST * 12
        - agent_rental_fee: 0.5 months rent/year average
        - vacancy_cost: 1.5 months rent lost per year
        - repairs_insurance: Fixed annual cost
        """
        annual_value = monthly_rent * 12
        property_tax = self.calculate_property_tax(annual_value)
        mcst_annual = self.calculate_mcst(sqft) * 12

        agent_months = self.params.get("agent_rental_months_per_year", 0.5)
        vacancy_months = self.params.get("vacancy_months_per_year", 1.5)
        repairs = self.params.get("repairs_per_year", 1500)
        insurance = self.params.get("insurance_per_year", 300)

        return {
            "property_tax": property_tax,
            "mcst": mcst_annual,
            "agent_rental_fee": monthly_rent * agent_months,
            "vacancy_cost": monthly_rent * vacancy_months,
            "repairs_insurance": repairs + insurance,
        }

    def calculate_exit_costs(self, sale_price: int, hold_years: int) -> dict:
        """Calculate all costs at exit/sale."""
        commission_rate = self.params.get("agent_sale_commission", 0.02)
        legal_fee = self.params.get("legal_fee_sell", 3000)

        return {
            "ssd": self.calculate_ssd(sale_price, hold_years),
            "agent_commission": int(sale_price * commission_rate),
            "legal_fee": legal_fee,
        }

    def create_cost_breakdown(
        self,
        purchase_price: int,
        sqft: float,
        monthly_rent: float,
        hold_years: int = 5,
        exit_price: Optional[int] = None,
        buyer_type: str = "SC",
        property_count: int = 0,
    ) -> CostBreakdown:
        """Create a complete cost breakdown for a property investment."""
        if exit_price is None:
            appreciation = self.params.get("annual_appreciation_estimate", 0.02)
            exit_price = int(purchase_price * (1 + appreciation) ** hold_years)

        annual_costs = self.calculate_annual_holding_costs(monthly_rent, sqft)
        exit_costs = self.calculate_exit_costs(exit_price, hold_years)

        return CostBreakdown(
            purchase_price=purchase_price,
            bsd=self.calculate_bsd(purchase_price),
            absd=self.calculate_absd(purchase_price, buyer_type, property_count),
            legal_fee_buy=self.params.get("legal_fee_buy", 3500),
            property_tax=annual_costs["property_tax"],
            mcst=annual_costs["mcst"],
            agent_rental_fee=annual_costs["agent_rental_fee"],
            vacancy_cost=annual_costs["vacancy_cost"],
            repairs_insurance=annual_costs["repairs_insurance"],
            ssd=exit_costs["ssd"],
            agent_sale_commission=exit_costs["agent_commission"],
            legal_fee_sell=exit_costs["legal_fee"],
        )


# Convenience functions
def calculate_bsd(price: int) -> int:
    """Calculate BSD for a given price."""
    return CostCalculator().calculate_bsd(price)


def calculate_property_tax(annual_value: float) -> float:
    """Calculate property tax for a given annual value."""
    return CostCalculator().calculate_property_tax(annual_value)
