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


# --------------------------------------------------------------------------- #
# Jun-2026 audit: freehold peers were dropped wholesale; subject was its own peer
# --------------------------------------------------------------------------- #

def _make_freehold_ura(district="D15", n=6, psf=2400):
    """Freehold peers with NO lease_start_year (93% of real freehold cache)."""
    ura = {}
    for i in range(n):
        ura[f"fh {i}"] = {
            "project_name": f"FH {i}",
            "district": district,
            "tenure": "Freehold",
            "lease_start_year": None,
            "median_psf": psf,
            "transaction_count": 30,
            "new_sale_proportion": 0.0,
        }
    return ura


class TestFreeholdPeers:
    """`if not lease_start: continue` dropped ~93% of freehold projects from
    the peer set, making freehold subjects read ~5% structurally dear in
    freehold-heavy districts. They are now included: age ~1 when actively
    selling New Sale units, else unadjusted (raw PSF, flagged)."""

    def test_freehold_peers_without_lease_start_are_included(self):
        rv = compute_relative_value(2400, 12, "D15", "Freehold",
                                    _make_freehold_ura(), CURRENT_YEAR)
        assert rv is not None  # pre-fix: every peer dropped → None
        assert rv["peer_count"] == 6
        assert rv["freehold_unadjusted_peer_count"] == 6
        # included at raw PSF: a subject priced at the peers' level reads ~fair
        assert abs(rv["premium_vs_age_adjusted_median_pct"]) < 1.0

    def test_unknown_age_peers_never_count_as_new_launches(self):
        # the placeholder age equals the subject's (2 ≤ 3) — without the
        # age_known filter these would masquerade as new launches
        rv = compute_relative_value(2400, 2, "D15", "Freehold",
                                    _make_freehold_ura(), CURRENT_YEAR)
        assert rv is not None
        assert "new_launch_median_psf" not in rv

    def test_actively_selling_freehold_peer_gets_young_age(self):
        ura = _make_freehold_ura(n=5)
        for e in ura.values():
            e["new_sale_proportion"] = 0.6  # active launches → derived age ≈ 1
        rv = compute_relative_value(2400, 20, "D15", "Freehold", ura, CURRENT_YEAR)
        # launch pricing normalized DOWN to a 20yr-old subject: a subject asking
        # launch PSF reads clearly dear (unadjusted inclusion would read ~0)
        assert rv["premium_vs_age_adjusted_median_pct"] > 15
        assert rv["freehold_unadjusted_peer_count"] == 0

    def test_leasehold_without_lease_start_still_skipped(self):
        ura = _make_ura(n=4)  # 4 usable peers, below _MIN_PEERS
        ura["mystery"] = {"project_name": "Mystery", "district": "D16",
                          "tenure": "99 yrs leasehold", "median_psf": 2000,
                          "transaction_count": 10}
        assert compute_relative_value(2160, 6, "D16", None, ura, CURRENT_YEAR) is None


class TestSubjectExclusion:
    def test_subject_project_excluded_from_peer_median(self):
        ura = _make_ura()
        ura["subject condo"] = {
            "project_name": "SUBJECT CONDO", "district": "D16",
            "tenure": "99 yrs lease commencing from 2018", "lease_start_year": 2018,
            "median_psf": 50_000,  # absurd self-print that would drag the median
            "transaction_count": 999,
        }
        with_self = compute_relative_value(
            2160, 6, "D16", "99-year leasehold", ura, CURRENT_YEAR)
        excl = compute_relative_value(
            2160, 6, "D16", "99-year leasehold", ura, CURRENT_YEAR,
            subject_name="Subject Condo")  # casing/punctuation-insensitive
        assert excl["peer_count"] == with_self["peer_count"] - 1
        # without the self-print the subject reads (correctly) dearer
        assert (excl["premium_vs_age_adjusted_median_pct"]
                > with_self["premium_vs_age_adjusted_median_pct"])
