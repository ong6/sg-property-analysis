"""Tests for the age-adjusted relative value framework."""

from scoring.relative_value import (
    compute_relative_value,
    _cum_decay,
    _decay_factor,
    _region_for_district,
)

CURRENT_YEAR = 2026


def _make_ura(district="D16", n=8, base_psf=2400, lease_base=2021):
    """Synthetic district peers: newer peers priced higher (age premium)."""
    ura = {}
    for i in range(n):
        lease_start = lease_base - i  # ages 2, 3, 4, ... (TOP = lease+3)
        age = CURRENT_YEAR - (lease_start + 3)
        ura[f"project {i}"] = {
            "project_name": f"Project {i}",
            "district": district,
            "tenure": "99 yrs lease commencing from %d" % lease_start,
            "lease_start_year": lease_start,
            "median_psf": base_psf - 40 * age,  # perfectly on the OCR slope
            "transaction_count": 50,
        }
    return ura


class TestRegionSlope:
    def test_regions(self):
        assert _region_for_district("D09") == "CCR"
        assert _region_for_district("D15") == "RCR"
        assert _region_for_district("D16") == "OCR"

    def test_freehold_decays_slower(self):
        # Aging a price from 2yr to 10yr discounts a freehold less (factor closer to 1)
        assert _decay_factor(2, 10, "Freehold") > _decay_factor(2, 10, "99-year leasehold")

    def test_piecewise_front_loaded(self):
        # v3.7 measured shape: the first 5 years decay much faster than the
        # 10-15 plateau (launch-freshness premium is front-loaded)
        assert (_cum_decay(5) - _cum_decay(0)) > 3 * (_cum_decay(15) - _cum_decay(10))


class TestRelativeValue:
    def test_fairly_priced_unit_near_zero_premium(self):
        ura = _make_ura()
        # Subject 6 years old, priced exactly on the slope: 2400 - 40*6 = 2160
        rv = compute_relative_value(2160, 6, "D16", "99-year leasehold", ura, CURRENT_YEAR)
        assert rv is not None
        assert abs(rv["premium_vs_age_adjusted_median_pct"]) < 3.0

    def test_overpriced_positive_underpriced_negative(self):
        ura = _make_ura()
        over = compute_relative_value(2160 * 1.15, 6, "D16", "99-year leasehold", ura, CURRENT_YEAR)
        under = compute_relative_value(2160 * 0.85, 6, "D16", "99-year leasehold", ura, CURRENT_YEAR)
        assert over["premium_vs_age_adjusted_median_pct"] > 10
        assert under["premium_vs_age_adjusted_median_pct"] < -10

    def test_new_launch_implied_fair_value(self):
        ura = _make_ura()
        rv = compute_relative_value(2160, 6, "D16", "99-year leasehold", ura, CURRENT_YEAR)
        assert rv.get("new_launch_median_psf") is not None
        # Implied fair value should be close to the slope-consistent price
        assert abs(rv["implied_fair_psf_from_new_launch"] - 2160) < 120

    def test_insufficient_peers_returns_none(self):
        ura = _make_ura(n=3)  # below _MIN_PEERS
        assert compute_relative_value(2160, 6, "D16", None, ura, CURRENT_YEAR) is None

    def test_wrong_district_excluded(self):
        ura = _make_ura(district="D15")
        assert compute_relative_value(2160, 6, "D16", None, ura, CURRENT_YEAR) is None

    def test_missing_inputs_neutral(self):
        ura = _make_ura()
        assert compute_relative_value(0, 6, "D16", None, ura, CURRENT_YEAR) is None
        assert compute_relative_value(2160, None, "D16", None, ura, CURRENT_YEAR) is None
        assert compute_relative_value(2160, 6, "", None, ura, CURRENT_YEAR) is None
