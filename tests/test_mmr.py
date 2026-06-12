"""Tests for the MMR scoring system (v3)."""

import copy

from scoring.full_scorer import FullScorer
from scoring.mmr import normalize_mmr, apply_appreciation_override, compute_mmr


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


class TestYieldCap:
    """v3.6.1: an implausible computed yield (fake-cheap price / real rent)
    must saturate instead of dominating the score — the last uncapped artifact
    channel after v3.6 capped both value components."""

    def test_artifact_yield_saturates_at_cap(self):
        from config import MMR_YIELD_CAP
        # ~8.6% gy: $635psf strata artifact with a real apartment rent
        s = _score(dict(BASE, price=3_400_000, sqft=5349, psf=636, beds=4,
                        monthly_rent=24_000))
        assert s.mmr_components["yield"] <= MMR_YIELD_CAP

    def test_realistic_yield_nearly_linear(self):
        from config import (MMR_YIELD_SLOPE_PTS_PER_PP, MMR_YIELD_CENTER_PCT)
        from scoring.mmr import compute_mmr
        # Same listing, controlled gy values — realistic yields must keep
        # >=90% of their linear score (the cap only bites artifacts).
        s = _score(dict(BASE))
        for gy, min_retention in ((2.5, 0.95), (3.8, 0.95), (4.7, 0.80)):
            s.estimated_gross_yield = gy
            s.rent_source = "ura_project_bed"
            s.rent_confidence = None  # clear the original estimate's numeric conf
            comps = compute_mmr(s)["components"]
            linear = MMR_YIELD_SLOPE_PTS_PER_PP * (gy - MMR_YIELD_CENTER_PCT) * 0.95
            assert abs(comps["yield"]) >= min_retention * abs(linear)
            assert (comps["yield"] >= 0) == (linear >= 0)


class TestAgeSweetSpot:
    def test_brand_new_scores_below_plateau(self):
        # v3.7 measured shape: young (<7yr) below plateau (launch-premium
        # decay drags forward returns), flat 7-30, mild decline after 30.
        from scoring.mmr import _age_points
        assert _age_points(0) < _age_points(5) < _age_points(7)
        assert _age_points(7) == _age_points(20) == _age_points(30)
        assert _age_points(45) < _age_points(30)  # uncapped post-30 decline


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
        # 0% appreciation must produce a clearly negative component, not be
        # ignored (slope·(0-center) at full confidence; slope is config-tunable)
        from config import MMR_APPRECIATION_SLOPE, MMR_APPRECIATION_CENTER_PCT
        expected = MMR_APPRECIATION_SLOPE * (0.0 - MMR_APPRECIATION_CENTER_PCT)
        assert result["components"]["appreciation"] <= expected + 0.01
        assert result["components"]["appreciation"] < -8


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


# --------------------------------------------------------------------------- #
# Jun-2026 audit fixes — behavioral trust-rule coverage (the only prior
# "test" of the v3.6 damp grepped the source for a string).
# --------------------------------------------------------------------------- #

DEEP_COHORT_URA = {
    "test condo": {
        "project_name": "Test Condo",
        "annualized_appreciation": 5.0,
        "transaction_count": 60,
        "median_psf": 2000,
        "avg_psf_current": 2000,
        "source": "ura_5yr_cagr",
        "by_size": {"window_years": 2, "recent_median_psf": 2000,
                    "bands": {"800-1050": {"median_psf": 2000, "txn_count": 20}}},
    }
}


class TestSuspectDamp:
    """Each suspect trigger must leave only MMR_SUSPECT_VALUE_FACTOR of the
    POSITIVE side of psf_value, age_value AND (audit #8) yield — the yield
    channel divides rent by the same untrusted price (The Vision bypass)."""

    def _baseline(self):
        # -30% vs a DEEP same-size cohort: deep discount, but NOT suspect.
        l = dict(BASE, psf=1400, price=int(1400 * 900))
        s = _score(l, copy.deepcopy(DEEP_COHORT_URA))
        s.estimated_gross_yield = 4.5          # positive yield ...
        s.rent_source = "ura_project_bed"      # ... on real rental evidence
        s.rent_confidence = None               # clear the original estimate's conf
        s.score_breakdown["relative_value"] = {  # deep age-adjusted discount too
            "premium_vs_age_adjusted_median_pct": -30.0, "peer_count": 10}
        return s

    def _with(self, mutate):
        s = self._baseline()
        mutate(s.score_breakdown)
        return compute_mmr(s)["components"]

    def test_baseline_not_suspect(self):
        comps = compute_mmr(self._baseline())["components"]
        assert comps["psf_value"] > 5 and comps["age_value"] > 5 and comps["yield"] > 5

    def test_each_trigger_damps_value_and_yield(self):
        from config import MMR_SUSPECT_VALUE_FACTOR
        base = compute_mmr(self._baseline())["components"]
        triggers = {
            "bedroom_sqft_mismatch": lambda sb: sb["red_flags"].setdefault("flags", []).append(
                {"flag": "bedroom_sqft_mismatch", "penalty": 2}),
            "ask_below_stack_prints": lambda sb: sb["red_flags"].setdefault("flags", []).append(
                {"flag": "ask_below_stack_prints", "penalty": 2}),
            "print_contradiction": lambda sb: sb["red_flags"].__setitem__("stack_premium_pct", 6.0),
        }
        for name, mutate in triggers.items():
            comps = self._with(mutate)
            for comp in ("psf_value", "age_value", "yield"):
                expected = base[comp] * MMR_SUSPECT_VALUE_FACTOR
                assert abs(comps[comp] - expected) < 0.05, (name, comp, comps[comp], expected)

    def test_thin_cohort_deep_discount_is_suspect(self):
        base = compute_mmr(self._baseline())["components"]
        comps = self._with(lambda sb: sb["red_flags"].__setitem__("psf_cohort_txns", 2))
        # psf_value confidence also shrinks with the cohort → strictly below 0.3×
        assert 0 < comps["psf_value"] < base["psf_value"] * 0.3
        assert abs(comps["age_value"] - base["age_value"] * 0.25) < 0.05
        assert abs(comps["yield"] - base["yield"] * 0.25) < 0.05

    def test_premium_side_not_damped(self):
        # Over-priced readings stay fully penalized even when a trigger fires.
        l = dict(BASE, psf=2600, price=int(2600 * 900))  # +30% premium
        s = _score(l, copy.deepcopy(DEEP_COHORT_URA))
        base_psf = compute_mmr(s)["components"]["psf_value"]
        s.score_breakdown["red_flags"].setdefault("flags", []).append(
            {"flag": "bedroom_sqft_mismatch", "penalty": 2})
        assert base_psf < 0
        assert compute_mmr(s)["components"]["psf_value"] == base_psf


class TestOverrideClampAndDamping:
    """Audit: agent overrides were the last unclamped appreciation channel, and
    the two override paths disagreed on cohort damping."""

    THIN_URA = TestSizeCohort.URA

    def _thin_listing(self):
        l = dict(BASE)
        l.update(sqft=500, psf=2300, price=int(500 * 2300), beds=1)
        s = _score(l, copy.deepcopy(self.THIN_URA))
        assert s.score_breakdown["red_flags"]["psf_cohort_txns"] == 2
        return s

    def test_apply_override_clamps_to_ura_band(self):
        s = _score(BASE)
        result = {"mmr": s.mmr, "components": s.mmr_components}
        assert (apply_appreciation_override(result, 40.0)["components"]["appreciation"]
                == apply_appreciation_override(result, 15.0)["components"]["appreciation"])
        assert (apply_appreciation_override(result, -40.0)["components"]["appreciation"]
                == apply_appreciation_override(result, -5.0)["components"]["appreciation"])

    def test_compute_mmr_override_clamped_and_cohort_damped(self):
        from config import (MMR_APPRECIATION_SLOPE, MMR_APPRECIATION_CENTER_PCT,
                            COHORT_FLOOR_APPRECIATION)
        s = self._thin_listing()
        s.appreciation_source = "agent_override"
        s.appreciation_rate = 0.40  # 40%/yr → must clamp to 15
        comps = compute_mmr(s)["components"]
        expected = (MMR_APPRECIATION_SLOPE * (15.0 - MMR_APPRECIATION_CENTER_PCT)
                    * COHORT_FLOOR_APPRECIATION)  # thin 2-txn cohort → floor 0.55
        assert abs(comps["appreciation"] - expected) < 0.01

    def test_both_override_paths_agree(self):
        from config import COHORT_FLOOR_APPRECIATION
        s = self._thin_listing()
        s.appreciation_source = "agent_override"
        s.appreciation_rate = 0.08
        via_compute = compute_mmr(s)["components"]["appreciation"]
        via_apply = apply_appreciation_override(
            {"mmr": s.mmr, "components": s.mmr_components}, 8.0,
            cohort_appr_factor=COHORT_FLOOR_APPRECIATION)["components"]["appreciation"]
        assert via_compute == via_apply


class TestPriceBandCap:
    """Audit #4: price_band was the only uncapped value-side channel (-39 raw
    at $10M). The tanh cap must preserve the normal $1-4M range."""

    def _pb(self, price):
        s = _score(BASE)
        s.price = price
        return compute_mmr(s)["components"]["price_band"]

    def test_broad_band_neutral(self):
        assert self._pb(1_500_000) == 0.0
        assert self._pb(2_200_000) == 0.0

    def test_normal_range_continuity_with_old_linear(self):
        # old linear: -(price - 2.2M)/500k * 2.5
        for price, old in ((2_700_000, -2.5), (3_000_000, -4.0), (4_000_000, -9.0)):
            new = self._pb(price)
            assert old - 0.05 <= new <= old + 1.0, (price, new, old)

    def test_extreme_quantum_saturates(self):
        from config import MMR_PRICE_BAND_CAP
        pb10 = self._pb(10_000_000)
        assert -MMR_PRICE_BAND_CAP <= pb10 <= -0.9 * MMR_PRICE_BAND_CAP
        assert pb10 < self._pb(4_000_000)  # still monotonic


class TestRentConfidenceHygiene:
    """Audit #5: an unknown rent_source defaulted to 0.6, OUTRANKING the known
    synthetic sources; a numeric rent_confidence can only lower, never raise."""

    def _yield(self, source, rc=None):
        s = _score(BASE)
        s.estimated_gross_yield = 4.2
        s.rent_source = source
        s.rent_confidence = rc  # None = no numeric confidence carried
        return compute_mmr(s)["components"]["yield"]

    def test_unknown_source_ranks_below_known_synthetics(self):
        assert (self._yield("brand_new_source") < self._yield("district_median")
                < self._yield("district_bedroom"))
        assert self._yield("fallback_estimate") < self._yield("brand_new_source")

    def test_numeric_rent_confidence_can_only_lower(self):
        full = self._yield("ura_project_bed")          # source prior 0.95
        lowered = self._yield("ura_project_bed", rc=0.4)
        assert abs(lowered - full * 0.4 / 0.95) < 0.05
        # higher numeric confidence must NOT raise a low source prior
        assert self._yield("district_median", rc=0.9) == self._yield("district_median")

    def test_malformed_rent_confidence_ignored(self):
        assert self._yield("district_median", rc="high") == self._yield("district_median")


class TestBoutiqueHaircut:
    """Audit #6: a thin appreciation series with MISSING momentum escaped the
    0.7x haircut — missing data must not buy back confidence."""

    def _conf(self, momentum):
        ura = {"test condo": {"project_name": "Test Condo",
                              "annualized_appreciation": 6.0,
                              "transaction_count": 15, "source": "ura_5yr_cagr"}}
        if momentum is not None:
            ura["test condo"]["appreciation_momentum"] = momentum
        return _score(BASE, ura).score_breakdown["mmr"]["meta"]["appreciation_confidence"]

    def test_thin_series_missing_momentum_is_haircut(self):
        assert self._conf(None) < self._conf(0.1)       # unknown < measured-stable
        assert self._conf(None) == self._conf(1.0)      # unknown == measured-unstable

    def test_thin_but_measured_stable_keeps_full_weight(self):
        assert abs(self._conf(0.1) - 0.65) < 0.011      # 0.5 + 0.5·(15/50), no haircut


class TestRelvalueSlopeDesync:
    """Audit #3: psf_value hardcoded -0.8 while age_value used
    MMR_RELVALUE_SLOPE — tuning the config retuned only half the value signal."""

    URA = {"test condo": {"project_name": "Test Condo", "annualized_appreciation": 4.0,
                          "transaction_count": 60, "avg_psf_current": 2222,
                          "source": "ura_5yr_cagr"}}

    def test_psf_value_follows_config_slope(self, monkeypatch):
        import scoring.mmr as mmr_mod
        s = _score(dict(BASE, psf=2000), copy.deepcopy(self.URA))  # ~-10% discount
        base = compute_mmr(s)["components"]["psf_value"]
        monkeypatch.setattr(mmr_mod, "MMR_RELVALUE_SLOPE", mmr_mod.MMR_RELVALUE_SLOPE / 2)
        halved = compute_mmr(s)["components"]["psf_value"]
        assert 0 < halved < base
        assert abs(halved - base / 2) < 0.6  # near-linear region of the tanh


class TestProfileStrictJoin:
    """Audit #10 — the AUTOMATED listing→profile join must not fuzzy-attach a
    different condo's profile ("Kovan Residences" → Avant Residences at 0.88).
    (Tests live here during the parallel audit-fix wave's file-ownership split;
    consider folding into tests/test_profile_memory.py later.)"""

    def _pm(self, tmp_path, monkeypatch):
        import profile_memory as pm
        monkeypatch.setattr(pm, "PROFILE_DIR", str(tmp_path))
        monkeypatch.setattr(pm, "INDEX_FILE", str(tmp_path / "index.json"))
        pm.save_profile({"condo": "Avant Residences", "district": "D19"})
        pm.save_profile({"condo": "The Myst At Cashew", "district": "D23"})
        return pm

    def test_fuzzy_cousin_no_longer_matches(self, tmp_path, monkeypatch):
        pm = self._pm(tmp_path, monkeypatch)
        assert pm.find_profile("Kovan Residences") is None  # ratio 0.88 — rejected

    def test_exact_and_containment_still_match(self, tmp_path, monkeypatch):
        pm = self._pm(tmp_path, monkeypatch)
        assert pm.find_profile("Avant Residences")["condo"] == "Avant Residences"
        assert pm.find_profile("The Myst")["condo"] == "The Myst At Cashew"

    def test_district_mismatch_blocks_strict_join(self, tmp_path, monkeypatch):
        pm = self._pm(tmp_path, monkeypatch)
        assert pm.find_profile("Avant Residences", district="D15") is None
        assert pm.find_profile("Avant Residences", district="D19") is not None

    def test_cli_loose_mode_keeps_old_fuzzy(self, tmp_path, monkeypatch):
        pm = self._pm(tmp_path, monkeypatch)
        m = pm.find_profile("Kovan Residences", strict=False)
        assert m and m["condo"] == "Avant Residences"

    def test_facing_match_is_whole_direction(self):
        import profile_memory as pm
        prof = {"condo": "X", "stacks": [
            {"stack": "01", "beds": 2, "sqft": 900, "facing": "NE"},
            {"stack": "02", "beds": 2, "sqft": 905, "facing": "N"}]}
        m = pm.match_stack({"sqft": 902, "beds": 2, "facing": "North"}, prof)
        assert m["stack"] == "02"  # 'north' must not tie to NE on the first letter

    def test_floor_tiebreak(self):
        import profile_memory as pm
        prof = {"condo": "X", "stacks": [
            {"stack": "L", "beds": 2, "sqft": 900, "floor_range": "01 to 05"},
            {"stack": "H", "beds": 2, "sqft": 905, "floor_range": "13 to 20"}]}
        m = pm.match_stack({"sqft": 902, "beds": 2, "floor_level": "High Floor"}, prof)
        assert m["stack"] == "H"
        assert "floor" in m["basis"]
