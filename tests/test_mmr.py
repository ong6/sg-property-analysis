"""Tests for the MMR scoring system (v3)."""

from scoring.full_scorer import FullScorer
from scoring.mmr import compute_mmr, normalize_mmr, apply_appreciation_override


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
        # ignored (slope 7.5/pp at full confidence: 30*(-4/4) = -30)
        assert result["components"]["appreciation"] <= -29
