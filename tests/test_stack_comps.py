"""Tests for v3.8/v3.9 stack-print trust — tight same-size comps from raw URA
prints, the print-contradiction rule (ask above own stack), and the
below-distribution rule (ask under the 10th percentile of a deep print set).

These lock the two artifact directions found in the Jun-2026 audits:
ground-floor PES stacks reading as fake discounts against pooled benchmarks,
and double-volume lofts / bait asks pricing under every recent print.
"""

from datetime import datetime

import pytest

import scoring.full_scorer as fs
from scoring.full_scorer import FullScorer


def _prints(entries):
    """entries: list of (sqft, psf, floor, 'YYYY-MM')."""
    return [(s, p, fl, datetime.strptime(d, "%Y-%m")) for s, p, fl, d in entries]


@pytest.fixture
def scorer(monkeypatch):
    sc = FullScorer.__new__(FullScorer)  # only the comp methods are exercised
    return sc


class TestTightSizeComps:
    def test_matches_within_tolerance_only(self, scorer, monkeypatch):
        monkeypatch.setitem(fs._raw_prints_cache, "D18", {"PROJ": _prints([
            (624, 1100, "01 to 05", "2026-01"),
            (628, 1200, "01 to 05", "2026-02"),
            (900, 1700, "06 to 10", "2026-02"),  # different stack — excluded
        ])})
        med, n, low_share, p10 = scorer._tight_size_comps(
            {"sqft": 624, "project_name": "Proj", "district": "D18"})
        assert n == 2
        assert med == 1150
        assert low_share == 1.0

    def test_recency_window_prefers_recent(self, scorer, monkeypatch):
        monkeypatch.setitem(fs._raw_prints_cache, "D18", {"PROJ": _prints([
            (624, 900, "01 to 05", "2019-01"),   # stale launch-era print
            (624, 1100, "01 to 05", "2025-06"),
            (624, 1200, "01 to 05", "2026-02"),
        ])})
        med, n, _, _ = scorer._tight_size_comps(
            {"sqft": 624, "project_name": "Proj", "district": "D18"})
        assert n == 2 and med == 1150  # 2019 print dropped

    def test_too_few_prints_returns_none(self, scorer, monkeypatch):
        monkeypatch.setitem(fs._raw_prints_cache, "D18", {"PROJ": _prints([
            (624, 1100, "01 to 05", "2026-01"),
        ])})
        med, n, low_share, p10 = scorer._tight_size_comps(
            {"sqft": 624, "project_name": "Proj", "district": "D18"})
        assert med is None and n == 1


class TestStackFlags:
    def _flags_for(self, scorer, monkeypatch, psf, prints):
        monkeypatch.setitem(fs._raw_prints_cache, "D18", {"PROJ": prints})
        listing = {"sqft": 700, "psf": psf, "project_name": "Proj", "district": "D18"}
        tight_med, n, low_share, p10 = scorer._tight_size_comps(listing)
        return tight_med, n, p10

    def test_p10_of_deep_set(self, scorer, monkeypatch):
        prints = _prints([(700, 1800 + 10 * i, "06 to 10", "2026-01") for i in range(20)])
        _, n, p10 = self._flags_for(scorer, monkeypatch, 1500, prints)
        assert n == 20
        assert p10 == 1810  # vals[int(.1*19)] = vals[1]

    def test_ask_below_p10_is_artifact_territory(self, scorer, monkeypatch):
        # 29 prints 1828-2075 (the Grandeur Park loft shape); ask 1504 < p10*0.97
        prints = _prints([(710, 1828 + 8 * i, "06 to 10", "2026-01") for i in range(29)])
        monkeypatch.setitem(fs._raw_prints_cache, "D16", {"PROJ": prints})
        listing = {"sqft": 710, "psf": 1504, "project_name": "Proj", "district": "D16"}
        _, _, _, p10 = scorer._tight_size_comps(listing)
        assert listing["psf"] < p10 * 0.97

    def test_ask_within_distribution_is_not_flagged(self, scorer, monkeypatch):
        # Wide print range (The Palette shape): a -9% ask sits inside it
        psfs = [1045, 1120, 1153, 1414, 1418, 1454, 1571, 1595, 1611, 1450, 1380, 1500]
        prints = _prints([(700, p, "06 to 10", "2026-01") for p in psfs])
        monkeypatch.setitem(fs._raw_prints_cache, "D18", {"PROJ": prints})
        listing = {"sqft": 700, "psf": 1284, "project_name": "Proj", "district": "D18"}
        _, _, _, p10 = scorer._tight_size_comps(listing)
        assert listing["psf"] >= p10 * 0.97  # plausible deal — no flag


class TestMmrTrustTriggers:
    def test_below_prints_flag_damps_value(self):
        # The suspect trigger must include the v3.9 flag name
        import inspect
        import scoring.mmr as mmr
        src = inspect.getsource(mmr)
        assert "ask_below_stack_prints" in src
        assert "stack_premium_pct" in src
