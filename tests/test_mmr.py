"""Tests for the MMR scoring system (v3)."""

from scoring.full_scorer import FullScorer
from scoring.mmr import normalize_mmr, apply_appreciation_override


def _score(listing, ura=None):
    return FullScorer(ura_data=ura or {}).score(listing)


BASE = {
    "id": "1",
    "title": "Test Condo",
    "project_name": "Test Condo",
    "url": "https://example.com/1",
    "price": 2_000_000,
    "sqft": 900,
    "psf": 2222,
    "beds": 2,
    "district": "D15",
    "tenure": "99-year leasehold",
    "built_year": 2021,
}


class TestNormalization:
    def test_center_maps_to_500(self):
        from config import MMR_NORM_CENTER
        assert normalize_mmr(MMR_NORM_CENTER) == 500

    def test_monotonic(self):
        assert normalize_mmr(1400) < normalize_mmr(1500) < normalize_mmr(1600)

    def test_bounded(self):
        assert 0 <= normalize_mmr(900) <= 1000
        assert 0 <= normalize_mmr(2100) <= 1000


class TestMissingDataNeutrality:
    """Missing data must contribute 0 — never treated as bad data."""

    def test_bare_listing_scores_near_base(self):
        s = _score({"id": "1", "title": "Bare", "url": "u", "price": 2_000_000, "sqft": 900})
        # No MRT, no district, no tenure, no age data → those components are 0
        comps = s.mmr_components
        assert comps["mrt"] == 0.0
        assert comps["lease"] == 0.0
        assert comps["age"] == 0.0
        assert comps["dev_size"] == 0.0


class TestSymmetry:
    """PSF premium subtracts what an equal discount adds (legacy was one-sided)."""

    def test_psf_premium_negative_discount_positive(self):
        ura = {
            "test condo": {
                "project_name": "Test Condo",
                "annualized_appreciation": 4.0,
                "transaction_count": 60,
                "avg_psf_current": 2222,
                "source": "ura_5yr_cagr",
            }
        }
        over = _score(dict(BASE, psf=2444), ura)   # +10%
        under = _score(dict(BASE, psf=2000), ura)  # -10%
        assert over.mmr_components["psf_value"] < 0
        assert under.mmr_components["psf_value"] > 0
        assert abs(over.mmr_components["psf_value"] + under.mmr_components["psf_value"]) < 1.0


class TestAgeSweetSpot:
    def test_brand_new_scores_below_sweet_spot(self):
        from scoring.mmr import _age_points
        assert _age_points(0) < _age_points(5)
        assert _age_points(5) == _age_points(7)
        assert _age_points(25) < 0  # uncapped decline


class TestConfidence:
    def test_baseline_rate_gets_low_confidence(self):
        s = _score(BASE)  # no URA data → regional_baseline
        meta = s.score_breakdown["mmr"]["meta"]
        assert meta["appreciation_confidence"] == 0.35

    def test_rich_data_gets_full_confidence(self):
        ura = {
            "test condo": {
                "project_name": "Test Condo",
                "annualized_appreciation": 5.0,
                "transaction_count": 100,
                "source": "ura_5yr_cagr",
            }
        }
        s = _score(BASE, ura)
        assert s.score_breakdown["mmr"]["meta"]["appreciation_confidence"] == 1.0


class TestOverride:
    def test_appreciation_override_moves_score(self):
        s = _score(BASE)
        result = {"mmr": s.mmr, "components": s.mmr_components}
        up = apply_appreciation_override(result, 8.0)
        down = apply_appreciation_override(result, 0.0)
        assert up["mmr"] > s.mmr
        assert down["mmr"] < s.mmr
        assert up["score_1000"] > s.score_1000 > down["score_1000"]

    def test_zero_override_is_honored(self):
        s = _score(BASE)
        result = apply_appreciation_override({"mmr": s.mmr, "components": s.mmr_components}, 0.0)
        # 0% appreciation must produce a strongly negative component, not be
        # ignored (v3.3 slope 4.0/pp at full confidence: 4*(0-4) = -16)
        assert result["components"]["appreciation"] <= -15


class TestSizeCohort:
    """v3.2: PSF is judged against same-size units, and a unit whose own
    size-cohort barely trades can't inherit the project's pooled strength."""

    # One project, strong appreciation, deep overall — but a deep 2BR cohort
    # and a 2-transaction small-unit cohort.
    URA = {
        "test condo": {
            "project_name": "Test Condo",
            "annualized_appreciation": 7.0,
            "transaction_count": 120,
            "median_psf": 2000,
            "avg_psf_current": 2000,
            "source": "ura_5yr_cagr",
            "by_size": {
                "window_years": 2,
                "recent_median_psf": 2000,
                "bands": {
                    "800-1050": {"median_psf": 1950, "txn_count": 20},  # deep
                    "0-600": {"median_psf": 2300, "txn_count": 2},      # thin
                },
            },
        }
    }

    def _unit(self, sqft, psf):
        l = dict(BASE)
        l.update(sqft=sqft, psf=psf, price=int(psf * sqft), beds=1 if sqft < 600 else 2)
        return _score(l, self.URA)

    def test_psf_compared_to_same_size_band(self):
        # A 900 sqft unit is compared to the 800-1050 band median (1950), not the
        # pooled 2000 — so 1950 psf reads as roughly at-market for its size.
        s = self._unit(900, 1950)
        assert s.score_breakdown["red_flags"]["psf_cohort_txns"] == 20
        assert abs(s.score_breakdown["red_flags"]["psf_premium_pct"]) < 1.0

    def test_thin_cohort_damps_appreciation_and_liquidity(self):
        deep = self._unit(900, 1950)    # 20-txn cohort → full strength
        thin = self._unit(500, 2300)    # 2-txn cohort → damped
        assert thin.score_breakdown["red_flags"]["psf_cohort_txns"] == 2
        # Same project rate (7%), but the thin-cohort unit claims less of it.
        assert thin.mmr_components["appreciation"] < deep.mmr_components["appreciation"]
        assert thin.mmr_components["txn_volume"] < deep.mmr_components["txn_volume"]

    def test_no_size_data_is_neutral_not_penalized(self):
        # Same listing, but the project has no by_size block → no damping.
        ura_flat = {"test condo": {k: v for k, v in self.URA["test condo"].items()
                                   if k != "by_size"}}
        l = dict(BASE); l.update(sqft=500, psf=2300, beds=1)
        flat = _score(l, ura_flat)
        thin = self._unit(500, 2300)
        assert flat.score_breakdown["red_flags"].get("psf_cohort_txns") is None
        assert flat.mmr_components["appreciation"] > thin.mmr_components["appreciation"]


class TestFloorAdjustment:
    """A unit's PSF is benchmarked against comparable-FLOOR transactions: a low
    floor expects a lower psf (so the same price is dearer), a high floor higher."""

    URA = {
        "test condo": {
            "project_name": "Test Condo",
            "annualized_appreciation": 5.0,
            "transaction_count": 80,
            "median_psf": 2000,
            "avg_psf_current": 2000,
            "source": "ura_5yr_cagr",
            "by_size": {"window_years": 2, "recent_median_psf": 2000,
                        "bands": {"800-1050": {"median_psf": 2000, "txn_count": 30}}},
            "floor_factors": {"basis": "district", "txn_count": 500,
                              "low": 0.92, "mid": 1.0, "high": 1.08},
        }
    }

    def _prem(self, floor):
        l = dict(BASE); l.update(sqft=900, psf=2000, floor_level=floor)
        return _score(l, self.URA).score_breakdown["red_flags"]["psf_premium_pct"]

    def test_low_floor_dearer_high_floor_cheaper(self):
        low, mid, high = self._prem("Low Floor"), self._prem("Mid Floor"), self._prem("High Floor")
        # Same $2000 psf: vs a lowered low-floor benchmark it's a premium; vs a
        # raised high-floor benchmark it's a discount.
        assert low > mid > high
        assert low > 0 > high

    def test_no_floor_is_neutral(self):
        # Unknown floor must not shift the benchmark (mid factor 1.0 == no floor).
        assert abs(self._prem(None) - self._prem("Mid Floor")) < 0.01
        assert abs(self._prem(None)) < 0.01  # $2000 vs $2000 band median
