"""Rental estimator — v3.5 real-rent (URA rental cache) source priority."""

import scoring.rental_estimator as re_mod
from scoring.rental_estimator import RentalEstimator


def _with_cache(monkeypatch, cache):
    monkeypatch.setattr(re_mod, "_rental_cache", cache)


BASE = {
    "project_name": "Testview Residences",
    "district": "D15",
    "beds": 2,
    "sqft": 700,
    "price": 1_400_000,
}

CACHE = {
    "testview residences|15": {
        "project_name": "TESTVIEW RESIDENCES",
        "district": "D15",
        "rent_psf": 5.0,
        "contracts": 24,
        "window_months": 12,
        "by_beds": {
            "2": {"monthly_rent": 3800, "rent_psf": 5.4, "contracts": 11},
        },
    }
}


def test_ura_project_bed_preferred(monkeypatch):
    _with_cache(monkeypatch, CACHE)
    est = RentalEstimator()
    result = est.estimate(dict(BASE))
    assert result["source"] == "ura_project_bed"
    assert result["rent_psf"] == 5.4
    assert result["monthly_rent"] == round(700 * 5.4, 2)


def test_ura_project_fallback_when_bed_unmatched(monkeypatch):
    _with_cache(monkeypatch, CACHE)
    est = RentalEstimator()
    result = est.estimate(dict(BASE, beds=4))
    assert result["source"] == "ura_project"
    assert result["rent_psf"] == 5.0


def test_explicit_condo_medians_still_win(monkeypatch):
    _with_cache(monkeypatch, CACHE)
    est = RentalEstimator(condo_rental_data={"testview residences": 6.1})
    result = est.estimate(dict(BASE))
    assert result["source"] == "same_condo"
    assert result["rent_psf"] == 6.1


def test_no_fuzzy_join(monkeypatch):
    """A different project in the same district must NOT inherit cached rents."""
    _with_cache(monkeypatch, CACHE)
    est = RentalEstimator()
    result = est.estimate(dict(BASE, project_name="Seaview Residences"))
    assert result["source"] != "ura_project"
    assert result["source"] != "ura_project_bed"


def test_wrong_district_no_join(monkeypatch):
    _with_cache(monkeypatch, CACHE)
    est = RentalEstimator()
    result = est.estimate(dict(BASE, district="D14"))
    assert result["source"] not in ("ura_project", "ura_project_bed")
