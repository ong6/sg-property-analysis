"""Rental estimator — v3.5 real-rent (URA rental cache) source priority,
audit #7 (bed-matched serving + universal sqft cap) and audit #4
(contract-depth / window / staleness confidence)."""

import scoring.rental_estimator as re_mod
from scoring.rental_estimator import RentalEstimator


def _with_cache(monkeypatch, cache, stale=False):
    monkeypatch.setattr(re_mod, "_rental_cache", cache)
    monkeypatch.setattr(re_mod, "_rental_cache_stale", stale)


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
    # Audit #7: the bed-matched contract median is served DIRECTLY, not psf x sqft
    assert result["monthly_rent"] == 3800
    # rent_psf is now the EFFECTIVE rate of this unit; the comp rate is labelled
    assert result["rent_psf"] == round(3800 / 700, 2)
    assert result["source_rent_psf"] == 5.4
    assert result["capped"] is False


def test_ura_project_bed_serves_large_unit_real_median(monkeypatch):
    """Mirror distortion fix: a genuine 1,250sf 2BR gets the real bed-matched
    median, not psf x capped-sqft (which used to undershoot real prints)."""
    _with_cache(monkeypatch, CACHE)
    est = RentalEstimator()
    result = est.estimate(dict(BASE, sqft=1250))
    assert result["source"] == "ura_project_bed"
    assert result["monthly_rent"] == 3800  # contract median, not 1000 * 5.4
    assert result["capped"] is False


def test_ura_project_fallback_when_bed_unmatched(monkeypatch):
    _with_cache(monkeypatch, CACHE)
    est = RentalEstimator()
    result = est.estimate(dict(BASE, beds=4))
    assert result["source"] == "ura_project"
    # 700sqft is within 4BR typical size — no cap, psf path
    assert result["monthly_rent"] == round(700 * 5.0, 2)
    assert result["source_rent_psf"] == 5.0


def test_ura_project_branch_caps_oversized_sqft(monkeypatch):
    """Audit #7: the project-pooled branch must not multiply a pooled psf by an
    oversized strata sqft (the 1,086sf '1BR' -> fake $5,137/mo artifact)."""
    _with_cache(monkeypatch, CACHE)
    est = RentalEstimator()
    result = est.estimate(dict(BASE, beds=1, sqft=1086))
    assert result["source"] == "ura_project"  # no 1BR contracts in cache
    capped_sqft = est.RENT_TYPICAL_SQFT_BY_BEDS[1] * 1.25  # 687.5
    assert result["monthly_rent"] == round(capped_sqft * 5.0, 2)
    assert result["capped"] is True
    # effective rate reflects the cap; the comp rate stays labelled
    assert result["rent_psf"] == round(result["monthly_rent"] / 1086, 2)
    assert result["source_rent_psf"] == 5.0


def test_ura_project_no_cap_when_beds_unknown(monkeypatch):
    _with_cache(monkeypatch, CACHE)
    est = RentalEstimator()
    result = est.estimate(dict(BASE, beds=None, sqft=1086))
    assert result["source"] == "ura_project"
    assert result["monthly_rent"] == round(1086 * 5.0, 2)
    assert result["capped"] is False


def test_district_median_caps_with_beds_known(monkeypatch):
    """Audit #7(b): EVERY psf x sqft path caps when the bed count is known."""
    _with_cache(monkeypatch, {})
    est = RentalEstimator()
    est.district_data = {"medians": {"D15": {"rental_psf": 3.5}}}
    result = est.estimate(dict(BASE, beds=1, sqft=1000))
    assert result["source"] == "district_median"
    capped_sqft = est.RENT_TYPICAL_SQFT_BY_BEDS[1] * 1.25
    assert result["monthly_rent"] == round(capped_sqft * 3.5, 2)
    assert result["capped"] is True


def test_explicit_condo_medians_still_win(monkeypatch):
    _with_cache(monkeypatch, CACHE)
    est = RentalEstimator(condo_rental_data={"testview residences": 6.1})
    result = est.estimate(dict(BASE))
    assert result["source"] == "same_condo"
    assert result["rent_psf"] == 6.1  # 700sqft 2BR: no cap, effective == comp rate
    assert result["source_rent_psf"] == 6.1


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


# --- Audit #4: numeric confidence (depth / window / staleness) ---

def test_confidence_deep_bed_cohort(monkeypatch):
    _with_cache(monkeypatch, CACHE)
    result = RentalEstimator().estimate(dict(BASE))
    # 11 contracts >= 10 -> full depth factor; 12mo window; fresh cache
    assert result["confidence"] == 0.95


def test_confidence_scales_with_contract_depth(monkeypatch):
    shallow = {
        "testview residences|15": {
            "project_name": "TESTVIEW RESIDENCES",
            "district": "D15",
            "rent_psf": 5.0,
            "contracts": 24,
            "window_months": 12,
            "by_beds": {"2": {"monthly_rent": 3800, "rent_psf": 5.4, "contracts": 4}},
        }
    }
    _with_cache(monkeypatch, shallow)
    result = RentalEstimator().estimate(dict(BASE))
    # 0.95 * (0.5 + 0.5 * 4/10) = 0.95 * 0.7
    assert result["confidence"] == round(0.95 * 0.7, 3)
    assert result["contracts"] == 4


def test_confidence_damped_for_24mo_window(monkeypatch):
    widened = {
        "testview residences|15": {
            "project_name": "TESTVIEW RESIDENCES",
            "district": "D15",
            "rent_psf": 5.0,
            "contracts": 24,
            "window_months": 24,
            "by_beds": {"2": {"monthly_rent": 3800, "rent_psf": 5.4, "contracts": 11}},
        }
    }
    _with_cache(monkeypatch, widened)
    result = RentalEstimator().estimate(dict(BASE))
    assert result["confidence"] == round(0.95 * 0.9, 3)
    assert result["window_months"] == 24


def test_confidence_damped_when_cache_stale(monkeypatch):
    _with_cache(monkeypatch, CACHE, stale=True)
    result = RentalEstimator().estimate(dict(BASE))
    assert result["confidence"] == round(0.95 * 0.85, 3)


def test_confidence_project_pooled_uses_project_contracts(monkeypatch):
    _with_cache(monkeypatch, CACHE)
    result = RentalEstimator().estimate(dict(BASE, beds=4))
    # ura_project base 0.8, 24 contracts -> full depth
    assert result["confidence"] == 0.8
    assert result["contracts"] == 24


def test_confidence_non_cache_sources_flat(monkeypatch):
    _with_cache(monkeypatch, {})
    est = RentalEstimator()
    result = est.estimate(dict(BASE))  # D15 2BR -> district_bedroom
    assert result["source"] == "district_bedroom"
    assert result["confidence"] == 0.55
    assert result["contracts"] is None
    assert result["window_months"] is None

    result = est.estimate({"sqft": 700, "price": 1_400_000, "beds": 2})
    assert result["source"] == "fallback_bedroom"
    assert result["confidence"] == 0.15


def test_confidence_always_present(monkeypatch):
    _with_cache(monkeypatch, {})
    est = RentalEstimator()
    assert est.estimate({"sqft": 700, "price": 1_400_000})["confidence"] > 0
    # even an unavailable estimate emits the field (as 0.0)
    assert est.estimate({"price": 1_400_000})["confidence"] == 0.0


def test_yield_score_propagates_evidence(monkeypatch):
    """Contract: the scorer copies confidence/contracts/window/capped from
    estimate_yield_score onto the scored listing (rent_confidence et al.)."""
    _with_cache(monkeypatch, CACHE)
    result = RentalEstimator().estimate_yield_score(dict(BASE))
    assert result["confidence"] == 0.95
    assert result["contracts"] == 11
    assert result["window_months"] == 12
    assert result["capped"] is False
