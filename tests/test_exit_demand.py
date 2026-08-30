"""Tests for the exit-demand lens (scoring/exit_demand.py).

The load-bearing test is the arithmetic pin: the lens ships EXACTLY the
composite the validation harness measured (validation.algos.exit_demand_
composite), and the two are written in two places for versioning reasons —
the lens's calibration must live in its own fingerprinted module, the algo
must stay a pure panel-row function. A drift between them means the lens is
scoring something the harness never validated, which is the one failure mode
this whole file exists to catch.
"""

import pytest

import config
from scoring import exit_demand, lenses
from validation import algos


def _stats(pair_profit=10, pair_loss=2, family_share=0.6, median_sqft=1000.0,
           total_units=500, tenure_kind="leasehold", resale_vol_6m=8,
           project="TEST PROJECT", district="19"):
    return {"project": project, "district": district,
            "pair_profit": pair_profit, "pair_loss": pair_loss,
            "family_share": family_share, "median_sqft": median_sqft,
            "total_units": total_units, "tenure_kind": tenure_kind,
            "resale_vol_6m": resale_vol_6m}


class _Listing:
    """Only what the lens reads off a ScoredListing."""
    def __init__(self, project_name=None, district=None):
        self.project_name = project_name
        self.district = district


@pytest.fixture
def table(monkeypatch):
    """Inject a small project table so no test touches the URA load."""
    tbl = {
        "the exitful": {"19": _stats(project="THE EXITFUL", district="19")},
        "ambiguous towers": {
            "09": _stats(project="AMBIGUOUS TOWERS", district="09"),
            "19": _stats(project="AMBIGUOUS TOWERS", district="19",
                         pair_profit=0, pair_loss=5),
        },
        "thin record": {"10": _stats(pair_profit=1, pair_loss=1,
                                     project="THIN RECORD", district="10")},
    }
    monkeypatch.setattr(exit_demand, "_TABLE", tbl)
    return tbl


class TestArithmeticPin:
    def test_the_lens_scores_what_the_harness_validated(self):
        """lens _compose == 100 x validation.algos.exit_demand_composite,
        over stats that exercise every component including the boutique flag."""
        cases = [
            _stats(),
            _stats(pair_profit=262, pair_loss=0, family_share=0.9),
            _stats(pair_profit=125, pair_loss=182, family_share=0.2),
            _stats(total_units=100, tenure_kind="freehold", median_sqft=600.0),
            _stats(total_units=None, median_sqft=None),
        ]
        for s in cases:
            row = {"pair_profit": s["pair_profit"], "pair_loss": s["pair_loss"],
                   "family_share": s["family_share"],
                   "median_sqft": s["median_sqft"],
                   "total_units": s["total_units"],
                   "tenure_kind": s["tenure_kind"]}
            expect = algos.exit_demand_composite(row)
            got = exit_demand._compose(s)
            assert got["score"] == pytest.approx(100.0 * expect, abs=0.051), s

    def test_the_constants_agree_with_the_algo_module(self):
        assert exit_demand.MIN_PAIRS == algos.EXIT_MIN_PAIRS
        assert exit_demand.PAIR_ALPHA == algos.EXIT_PAIR_ALPHA

    def test_chiews_examples_order_correctly(self):
        # Tampines Trilliant 262/0 (gold) > a thin 5/0 > Reflections 125/182.
        gold = exit_demand._compose(_stats(pair_profit=262, pair_loss=0))
        thin = exit_demand._compose(_stats(pair_profit=5, pair_loss=0))
        red = exit_demand._compose(_stats(pair_profit=125, pair_loss=182))
        assert gold["score"] > thin["score"] > red["score"]
        assert gold["components"]["pair_record"] > 0.98
        assert red["components"]["pair_record"] < 0.45


class TestBoutiqueFlag:
    def test_it_needs_all_three_conditions(self):
        base = dict(total_units=100, tenure_kind="freehold", median_sqft=600.0)
        flagged = exit_demand._compose(_stats(**base))
        assert flagged["components"]["boutique_ok"] == 0.0
        for change in ({"total_units": 400}, {"tenure_kind": "leasehold"},
                       {"median_sqft": 900.0}):
            ok = exit_demand._compose(_stats(**{**base, **change}))
            assert ok["components"]["boutique_ok"] == 1.0, change
            assert ok["score"] > flagged["score"]

    def test_missing_evidence_is_not_a_penalty(self):
        # No unit count or no sqft: absence of evidence, not a boutique flag.
        for s in (_stats(total_units=None, tenure_kind="freehold",
                         median_sqft=600.0),
                  _stats(total_units=100, tenure_kind="freehold",
                         median_sqft=None)):
            assert exit_demand._compose(s)["components"]["boutique_ok"] == 1.0


class TestAbstention:
    """None means "this lens cannot judge this listing" — never a guess."""

    def test_thin_pair_records_abstain(self):
        assert exit_demand._compose(_stats(pair_profit=1, pair_loss=1)) is None

    def test_no_project_name_abstains_before_any_table_build(self, monkeypatch):
        # The guard must run first: score() on a nameless listing is how unit
        # tests and partial records hit this module, and building the URA
        # table for them would cost seconds to say nothing.
        monkeypatch.setattr(exit_demand, "_build_table",
                            lambda *a, **k: pytest.fail("table was built"))
        monkeypatch.setattr(exit_demand, "_TABLE", None)
        assert exit_demand.score(_Listing()) == {"score": None, "components": {}}

    def test_unmatched_project_abstains(self, table):
        out = exit_demand.score(_Listing("No Such Condo", "D19"))
        assert out["score"] is None


class TestLookup:
    def test_normalized_name_and_district_forms_match(self, table):
        for d in ("D19", "19", 19):
            out = exit_demand.score(_Listing("The Exitful", d))
            assert out["score"] is not None
            assert out["components"]["district"] == "19"

    def test_ambiguous_name_without_district_abstains(self, table):
        # Two districts carry AMBIGUOUS TOWERS with opposite exit records —
        # guessing would hand one project the other's history.
        assert exit_demand.score(_Listing("Ambiguous Towers"))["score"] is None

    def test_ambiguous_name_with_district_gets_its_own_record(self, table):
        a = exit_demand.score(_Listing("Ambiguous Towers", "D09"))
        b = exit_demand.score(_Listing("Ambiguous Towers", "D19"))
        assert a["score"] > b["score"]

    def test_unambiguous_name_without_district_matches(self, table):
        assert exit_demand.score(_Listing("The Exitful"))["score"] is not None

    def test_project_name_falls_back_to_the_db_record(self, table):
        out = exit_demand.score(_Listing(),
                                {"project_name": "The Exitful", "district": "D19"})
        assert out["score"] is not None

    def test_two_listings_in_one_project_score_identically(self, table):
        # Exit demand is a property of the project, not the unit or its ask.
        a = exit_demand.score(_Listing("The Exitful", "D19"))
        b = exit_demand.score(_Listing("THE EXITFUL", "19"))
        assert a["score"] == b["score"]


class TestTableBuild:
    def _txn(self, proj="P", t=2024.0, psf=1000.0, sqft=1000.0,
             sale="Resale", tenure="Freehold", floor="06 to 10", district="19"):
        return {"project": proj, "district": district, "t": t, "psf": psf,
                "sqft": sqft, "sale_type": sale, "tenure": tenure,
                "floor": floor}

    def test_pairs_family_and_volume_aggregate_per_project(self):
        txns = [
            # one profitable pair (same sqft + floor tier, >1yr apart)...
            self._txn(t=2020.0, psf=1000.0),
            self._txn(t=2022.0, psf=1200.0),
            # ...one losing pair in another stack type
            self._txn(t=2020.0, psf=1000.0, sqft=700.0, floor="16 to 20"),
            self._txn(t=2023.0, psf=900.0, sqft=700.0, floor="16 to 20"),
            # recent txns for the 6m volume window (t_max = 2024.0)
            self._txn(t=2023.8, psf=1100.0, sqft=500.0),
            self._txn(t=2024.0, psf=1100.0, sqft=500.0),
        ]
        tbl = exit_demand._build_table(txns=txns, units={"p": 300})
        s = tbl["p"]["19"]
        assert (s["pair_profit"], s["pair_loss"]) == (1, 1)
        assert s["resale_vol_6m"] == 2
        # family: 2 of 6 sqft-bearing resales are >= 900... txns at 1000 sqft
        # count, 700/500 do not -> 2/6
        assert s["family_share"] == pytest.approx(2 / 6)
        assert s["total_units"] == 300
        assert s["tenure_kind"] == "freehold"

    def test_new_sales_never_enter_the_exit_record(self):
        txns = [self._txn(t=2020.0, sale="New Sale"),
                self._txn(t=2022.0, sale="New Sale")]
        tbl = exit_demand._build_table(txns=txns, units={})
        assert tbl == {}


class TestRegistration:
    def test_the_lens_is_registered_with_its_own_calibration(self):
        lens = lenses.LENSES["exit_demand"]
        assert lens.gate_percentile == exit_demand.GATE_PERCENTILE

    def test_the_version_fingerprints_the_lens_module(self):
        v = lenses.LENSES["exit_demand"].version()
        assert v.startswith("exit-1.0+")
        assert not v.endswith("unknown")
        assert v == exit_demand.version()

    def test_registering_did_not_restamp_the_book(self):
        # The registry contract's hard rule: no lens constant in config.py.
        # v3.12 is the registered cohort whose forward clock reads ~2027-08.
        assert lenses.LENSES["exit_demand"].version() != config.score_version()

    def test_mmr_is_still_first_in_snake_draft_order(self):
        assert list(lenses.LENSES)[0] == "mmr"
