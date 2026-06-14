"""Tests for v3.8/v3.9 stack-print trust — tight same-size comps from raw URA
prints, the print-contradiction rule (ask above own stack), and the
below-distribution rule (ask under the 10th percentile of a deep print set) —
plus the Jun-2026 audit hardening:

- #4 normalized prints join ('SUITES @ KATONG' ↔ URA 'SUITES@ KATONG') and an
  explicit `no_ura_prints` export instead of a silent skip
- #5 stale-window anchor: 24mo cutoff at TODAY, raw stale print sets never
  serve as comps, newest print exported as `stack_prints_latest`
- v3.8.1 stale-print indexing: a stale same-size print set is re-valued in
  today's terms via the district price index and served as a LOW-TRUST comp
  set (stale=True): halved blend weight, forced-thin damping cohort, raised
  flag thresholds (+15 above / p10*0.90 below), `stack_premium_pct` exported
  to the mmr suspect damp only past the +8 noise margin, `stack_prints_stale`
  exported. No computable district index → neutral (the pre-v3.8.1 outcome)
- #6 floor-aware tight comps: prints adjusted to the listing's floor tier (or
  same-tier restricted) so fairly-priced high-floor units stop reading "above
  their own prints"; PES-stack detection (low_floor_share) preserved
- n_tight 5-7 corridor: bait asks below every in-window print fire
  `ask_below_stack_prints` even when the set is too thin for the p10 rule
- print hygiene: multi-unit / land rows dropped, resale-only comp sets
- cost-efficiency / buyer-pool missing-data neutrality
- blend-consistent cohort counts, quick-gate backfill, cross-agent contracts
  (unit_group dedupe, ingest_flags, rent confidence)
"""

import os
from datetime import datetime
from types import SimpleNamespace

import pytest

import scoring.full_scorer as fs
from scoring.full_scorer import FullScorer, build_cohort_stats, score_and_filter


def _ym(months_ago):
    """A datetime `months_ago` months before now (day 1)."""
    now = datetime.now()
    m = now.year * 12 + (now.month - 1) - months_ago
    return datetime(m // 12, m % 12 + 1, 1)


def _prints(entries, is_ec=False):
    """entries: list of (sqft, psf, floor, months_ago[, sale_type[, is_ec]]).

    months_ago=None → undated print. sale_type defaults to "Resale".
    is_ec defaults to the function-level `is_ec` (so a whole seeded project can
    be marked EC); a per-entry 6th element overrides it.
    """
    out = []
    for e in entries:
        sqft, psf, floor, months_ago = e[:4]
        sale_type = e[4] if len(e) > 4 else "Resale"
        ec = e[5] if len(e) > 5 else is_ec
        dt = _ym(months_ago) if months_ago is not None else None
        out.append((sqft, psf, floor, dt, sale_type, ec))
    return out


def _seed(monkeypatch, dcode, project, prints):
    monkeypatch.setitem(
        fs._raw_prints_cache, dcode, {fs._pu_normalize(project): prints})


def _seed_district(monkeypatch, dcode, projects):
    """Seed several projects into one district (for district-index tests)."""
    monkeypatch.setitem(
        fs._raw_prints_cache, dcode,
        {fs._pu_normalize(k): v for k, v in projects.items()})


@pytest.fixture
def scorer():
    sc = FullScorer.__new__(FullScorer)  # only the comp methods are exercised
    return sc


@pytest.fixture
def flag_scorer():
    """Scorer with just enough attributes for _score_red_flags."""
    sc = FullScorer.__new__(FullScorer)
    sc.ura_data = {}
    sc.transaction_data = {}
    sc._current_year = datetime.now().year
    return sc


def _stub_scored():
    return SimpleNamespace(remaining_lease=None)


class TestTightSizeComps:
    def test_matches_within_tolerance_only(self, scorer, monkeypatch):
        _seed(monkeypatch, "D18", "Proj", _prints([
            (624, 1100, "01 to 05", 5),
            (628, 1200, "01 to 05", 4),
            (900, 1700, "06 to 10", 4),  # different stack — excluded
        ]))
        med, n, low_share, p10, latest, stale = scorer._tight_size_comps(
            {"sqft": 624, "project_name": "Proj", "district": "D18"})
        assert n == 2
        assert med == 1150
        assert low_share == 1.0

    def test_recency_window_prefers_recent(self, scorer, monkeypatch):
        _seed(monkeypatch, "D18", "Proj", _prints([
            (624, 900, "01 to 05", 30),   # outside the 24mo window
            (624, 1100, "01 to 05", 12),
            (624, 1200, "01 to 05", 4),
        ]))
        med, n, _, _, _, _ = scorer._tight_size_comps(
            {"sqft": 624, "project_name": "Proj", "district": "D18"})
        assert n == 2 and med == 1150  # stale print dropped

    def test_too_few_prints_returns_none(self, scorer, monkeypatch):
        _seed(monkeypatch, "D18", "Proj", _prints([
            (624, 1100, "01 to 05", 5),
        ]))
        med, n, low_share, p10, _, _ = scorer._tight_size_comps(
            {"sqft": 624, "project_name": "Proj", "district": "D18"})
        assert med is None and n == 1

    def test_normalized_name_join(self, scorer, monkeypatch):
        # Audit #4: 'Suites @ Katong' must reach URA's 'SUITES@ KATONG' prints
        _seed(monkeypatch, "D15", "SUITES@ KATONG", _prints([
            (700, 1500, "06 to 10", 5),
            (710, 1550, "06 to 10", 3),
        ]))
        med, n, _, _, _, _ = scorer._tight_size_comps(
            {"sqft": 705, "project_name": "Suites @ Katong", "district": "D15"})
        assert n == 2 and med == 1525


class TestStaleWindow:
    """Audit #5: window anchors to TODAY; raw stale prints never serve as
    comps. (v3.8.1: a stale set with a computable district index comes back
    INDEXED — see TestIndexedStaleComps; without one it stays neutral.)"""

    def test_stale_prints_return_neutral_without_district_index(
            self, scorer, monkeypatch):
        # All prints >24mo old and the district has no trailing-24mo prints to
        # index against → neutral. The old (pre-audit) code fell back to the
        # project's own latest print as anchor and returned this stale set raw.
        _seed(monkeypatch, "D18", "Proj", _prints([
            (700, 1100, "06 to 10", 40),
            (700, 1150, "06 to 10", 36),
            (705, 1180, "06 to 10", 30),
        ]))
        med, n, low_share, p10, latest, stale = scorer._tight_size_comps(
            {"sqft": 700, "project_name": "Proj", "district": "D18"})
        assert med is None and n == 0 and stale is False
        assert latest == _ym(30).strftime("%Y-%m")  # freshness still exported

    def test_undated_prints_never_anchor(self, scorer, monkeypatch):
        _seed(monkeypatch, "D18", "Proj", _prints([
            (700, 1100, "06 to 10", None),
            (700, 1150, "06 to 10", None),
        ]))
        med, n, _, _, latest, _ = scorer._tight_size_comps(
            {"sqft": 700, "project_name": "Proj", "district": "D18"})
        assert med is None and latest is None

    def test_latest_exported_in_red_flags(self, flag_scorer, monkeypatch):
        _seed(monkeypatch, "D18", "Proj", _prints([
            (700, 1100, "06 to 10", 40),
            (700, 1150, "06 to 10", 30),
        ]))
        res = flag_scorer._score_red_flags(
            {"sqft": 700, "psf": 1400, "project_name": "Proj", "district": "D18"},
            _stub_scored())
        assert res["stack_prints_latest"] == _ym(30).strftime("%Y-%m")
        # stale set, no district index → no premium, no print-contradiction flag
        assert "stack_premium_pct" not in res
        assert not any(f["flag"].startswith("ask_") for f in res["flags"])


def _stale_pes_district(monkeypatch, project_prints, dcode="D18",
                        then_psf=1000, cur_psf=1500,
                        then_months_ago=34, cur_months_ago=6):
    """Seed a district where `project_prints` are stale but peer prints form
    both index windows: 5 peers near the stale anchor at `then_psf` and 5
    peers in the trailing 24mo at `cur_psf`."""
    peers = _prints(
        [(900, then_psf, "06 to 10", then_months_ago) for _ in range(5)]
        + [(900, cur_psf, "06 to 10", cur_months_ago) for _ in range(5)])
    _seed_district(monkeypatch, dcode, {"Proj": project_prints, "Peer": peers})


class TestIndexedStaleComps:
    """v3.8.1: stale same-size prints come back district-indexed, marked
    stale=True (low-trust) — re-arming the v3.8 PES protection for stacks
    that stopped trading (the Coco Palms regression)."""

    def _proj(self):
        # 2 stale PES prints: 1000 psf (36mo ago), 1100 psf (34mo ago)
        return _prints([(624, 1000, "01 to 05", 36),
                        (628, 1100, "01 to 05", 34)])

    def test_indexed_math(self, scorer, monkeypatch):
        # District median drifted 1000 → 1500 (ratio 1.5) between the stale
        # anchor (±9mo around the prints' median date) and the trailing 24mo.
        _stale_pes_district(monkeypatch, self._proj())
        med, n, low_share, p10, latest, stale = scorer._tight_size_comps(
            {"sqft": 624, "project_name": "Proj", "district": "D18"})
        assert stale is True and n == 2
        assert med == pytest.approx(1575.0)   # median of 1000*1.5, 1100*1.5
        assert p10 == pytest.approx(1500.0)
        assert low_share == 1.0
        assert latest == _ym(34).strftime("%Y-%m")

    def test_ratio_clamped_both_sides(self, scorer, monkeypatch):
        # Raw ratio 2.0 → clamped to 1.7
        _stale_pes_district(monkeypatch, self._proj(), cur_psf=2000)
        med, _, _, _, _, stale = scorer._tight_size_comps(
            {"sqft": 624, "project_name": "Proj", "district": "D18"})
        assert stale is True
        assert med == pytest.approx(1050 * 1.7)
        # Raw ratio 0.7 → clamped to 0.8
        _stale_pes_district(monkeypatch, self._proj(), cur_psf=700)
        med, _, _, _, _, stale = scorer._tight_size_comps(
            {"sqft": 624, "project_name": "Proj", "district": "D18"})
        assert stale is True
        assert med == pytest.approx(1050 * 0.8)

    def test_thin_anchor_window_is_neutral(self, scorer, monkeypatch):
        # Trailing-24mo district prints exist, but the anchor window holds
        # only the project's own 2 prints (< STALE_INDEX_MIN_DISTRICT_PRINTS)
        # → no index, no comps.
        peers = _prints([(900, 1500, "06 to 10", 6) for _ in range(5)])
        _seed_district(monkeypatch, "D18",
                       {"Proj": self._proj(), "Peer": peers})
        med, n, _, _, _, stale = scorer._tight_size_comps(
            {"sqft": 624, "project_name": "Proj", "district": "D18"})
        assert med is None and stale is False

    def test_single_fresh_print_joins_indexed_set(self, scorer, monkeypatch):
        # 1 in-window print (< 2, not usable alone) + 2 stale → all 3 indexed.
        proj = self._proj() + _prints([(624, 1600, "01 to 05", 3)])
        _stale_pes_district(monkeypatch, proj)
        med, n, _, _, latest, stale = scorer._tight_size_comps(
            {"sqft": 624, "project_name": "Proj", "district": "D18"})
        assert stale is True and n == 3
        assert med == pytest.approx(1650.0)  # median of 1500, 1650, 2400
        assert latest == _ym(3).strftime("%Y-%m")

    def test_coco_palms_regression_shape(self, flag_scorer, monkeypatch):
        # The literal v3.8 audit example: 3 stale PES prints (1073/1161/1195,
        # ~4yrs old), district drifted ~+46% since — indexed comps land ABOVE
        # the $1,391 ask, so the fake band "discount" reads as an ask >10%
        # under every indexed print → ask_below fires, suspect damp arms.
        proj = _prints([(624, 1073, "01 to 05", 58),
                        (624, 1161, "01 to 05", 49),
                        (624, 1195, "01 to 05", 44)])
        _stale_pes_district(monkeypatch, proj, then_psf=1182, cur_psf=1732,
                            then_months_ago=45)
        res = flag_scorer._score_red_flags(
            {"sqft": 624, "psf": 1391, "project_name": "Proj",
             "district": "D18", "beds": 1}, _stub_scored())
        assert res["stack_prints_stale"] is True
        assert res["stack_low_floor_share"] == 1.0
        assert res["stack_premium_pct_indexed"] == pytest.approx(-18.2, abs=0.2)
        assert "stack_premium_pct" not in res  # negative — no contradiction damp
        assert any(f["flag"] == "ask_below_stack_prints" for f in res["flags"])


class TestStaleFlagMatrix:
    """Raised thresholds for indexed comps: ask_above only past +15 (fresh:
    +10), ask_below only under p10*0.90 (fresh: p10*0.97 / below-min), and
    the mmr suspect damp (stack_premium_pct export) only past +8."""

    def _res(self, flag_scorer, monkeypatch, psf):
        # 6 stale prints @1000 → indexed med = p10 = 1500 (district ratio 1.5)
        proj = _prints([(700, 1000, "06 to 10", m)
                        for m in (30, 32, 34, 36, 38, 40)])
        _stale_pes_district(monkeypatch, proj, then_months_ago=35)
        return flag_scorer._score_red_flags(
            {"sqft": 700, "psf": psf, "project_name": "Proj",
             "district": "D18"}, _stub_scored())

    def test_above_15_fires(self, flag_scorer, monkeypatch):
        res = self._res(flag_scorer, monkeypatch, 1740)  # +16%
        assert any(f["flag"] == "ask_above_own_stack_prints"
                   for f in res["flags"])
        assert res["stack_premium_pct"] == pytest.approx(16.0, abs=0.1)

    def test_between_10_and_15_no_flag_but_damp_arms(self, flag_scorer, monkeypatch):
        # +12.7% — the FRESH path would flag this (>10); the indexed path must
        # not (index noise), but past +8 it still feeds the mmr suspect damp.
        res = self._res(flag_scorer, monkeypatch, 1690)
        assert not any(f["flag"].startswith("ask_") for f in res["flags"])
        assert res["stack_premium_pct"] == pytest.approx(12.7, abs=0.1)

    def test_noise_margin_withholds_damp_field(self, flag_scorer, monkeypatch):
        # +6.7% — above mmr's fixed >5 trigger but inside the +8 index-noise
        # margin: stack_premium_pct must be withheld so the damp stays off;
        # the raw indexed number stays visible in stack_premium_pct_indexed.
        res = self._res(flag_scorer, monkeypatch, 1600)
        assert "stack_premium_pct" not in res
        assert res["stack_premium_pct_indexed"] == pytest.approx(6.7, abs=0.1)
        assert not any(f["flag"].startswith("ask_") for f in res["flags"])

    def test_below_p10_but_above_090_no_flag(self, flag_scorer, monkeypatch):
        # -6.7%, under every indexed print but inside the 0.90 margin — the
        # FRESH 5-7 corridor would flag an ask below the minimum print.
        res = self._res(flag_scorer, monkeypatch, 1400)
        assert not any(f["flag"].startswith("ask_") for f in res["flags"])

    def test_below_090_p10_fires(self, flag_scorer, monkeypatch):
        res = self._res(flag_scorer, monkeypatch, 1340)  # < 1500*0.90
        assert any(f["flag"] == "ask_below_stack_prints" for f in res["flags"])


class TestStaleCohortThinning:
    """Indexed comps blend at half weight and force the damping cohort thin."""

    def _ura(self):
        return {"proj": {
            "median_psf": 1700,
            "by_size": {
                "recent_median_psf": 1700,
                "bands": {"600-800": {"median_psf": 1700, "txn_count": 20}},
            },
        }}

    def test_blend_halved_and_cohort_forced_thin(self, scorer, monkeypatch):
        # 6 stale prints, indexed med 1500, against a deep 20-txn band @1700:
        # w = 0.5*min(1, 6/5) = 0.5 → benchmark 1600 (a fresh 6-print set
        # would be pure-tight at 1500); cohort = min(6, MIN_BAND_TXNS-1) = 4,
        # always thin, so v3.2 damping + the v3.6 deep-discount suspect rule
        # stay armed no matter how many stale prints back the index.
        proj = _prints([(700, 1000, "06 to 10", m)
                        for m in (30, 32, 34, 36, 38, 40)])
        _stale_pes_district(monkeypatch, proj, then_months_ago=35)
        scorer.ura_data = self._ura()
        premium, cohort = scorer._check_psf_overpricing(
            {"sqft": 700, "psf": 1600, "project_name": "Proj",
             "district": "D18"})
        assert cohort == 4
        assert premium == pytest.approx(0.0, abs=0.5)

    def test_two_stale_prints_weight_scales_with_n(self, scorer, monkeypatch):
        # n=2 → w = 0.5 * 2/5 = 0.2 → benchmark 0.2*1575 + 0.8*1700 = 1675
        proj = _prints([(700, 1000, "06 to 10", 36),
                        (700, 1100, "06 to 10", 34)])
        _stale_pes_district(monkeypatch, proj)
        scorer.ura_data = self._ura()
        premium, cohort = scorer._check_psf_overpricing(
            {"sqft": 700, "psf": 1675, "project_name": "Proj",
             "district": "D18"})
        assert cohort == 2
        assert premium == pytest.approx(0.0, abs=0.5)


class TestCasaEmeraldNoFalseFlag:
    """The case the audit-#5 fix was FOR, preserved under v3.8.1: a fairly
    priced 2026 ask must not be accused by its project's stale prints."""

    def test_fair_ask_vs_indexed_stale_prints_no_flag(self, flag_scorer, monkeypatch):
        # Replica of Casa Emerald D14: two ~2022 prints (1179/1197), district
        # drift ~+15% since. Raw stale prints would read the 1474.5 ask +24%
        # (false ask_above flag, pre-audit); indexed they read ~+8% → clean.
        proj = _prints([(1119, 1179, "06 to 10", 45),
                        (1044, 1197, "06 to 10", 43)])
        peers = _prints(
            [(900, 1572, "06 to 10", 44) for _ in range(5)]
            + [(900, 1803, "06 to 10", 6) for _ in range(5)])
        _seed_district(monkeypatch, "D14",
                       {"Casa Emerald": proj, "Peer": peers})
        res = flag_scorer._score_red_flags(
            {"sqft": 1119, "psf": 1474.53, "project_name": "Casa Emerald",
             "district": "D14", "beds": 3}, _stub_scored())
        assert res["stack_prints_stale"] is True
        assert res["stack_premium_pct_indexed"] == pytest.approx(8.2, abs=0.2)
        assert not any(f["flag"].startswith("ask_") for f in res["flags"])


class TestFreshPathUnchanged:
    """≥2 in-window prints → the v3.8.1 stale machinery must stay invisible."""

    def test_fresh_set_not_marked_stale(self, flag_scorer, monkeypatch):
        _seed(monkeypatch, "D18", "Proj", _prints([
            (700, 1500, "06 to 10", 5),
            (700, 1520, "06 to 10", 3),
        ]))
        res = flag_scorer._score_red_flags(
            {"sqft": 700, "psf": 1510, "project_name": "Proj",
             "district": "D18"}, _stub_scored())
        assert "stack_prints_stale" not in res
        assert "stack_premium_pct_indexed" not in res
        assert res["stack_premium_pct"] == pytest.approx(0.0, abs=0.5)


class TestNoUraPrints:
    def test_flag_exported_when_project_has_no_prints(self, flag_scorer, monkeypatch):
        monkeypatch.setitem(fs._raw_prints_cache, "D18", {})
        res = flag_scorer._score_red_flags(
            {"sqft": 700, "psf": 1400, "project_name": "Ghost Condo",
             "district": "D18"}, _stub_scored())
        assert res.get("no_ura_prints") is True

    def test_flag_absent_when_prints_exist(self, flag_scorer, monkeypatch):
        _seed(monkeypatch, "D18", "Proj", _prints([
            (700, 1400, "06 to 10", 5),
            (700, 1420, "06 to 10", 4),
        ]))
        res = flag_scorer._score_red_flags(
            {"sqft": 700, "psf": 1410, "project_name": "Proj", "district": "D18"},
            _stub_scored())
        assert "no_ura_prints" not in res

    def test_flag_absent_when_lookup_impossible(self, flag_scorer):
        res = flag_scorer._score_red_flags(
            {"sqft": 700, "psf": 1400}, _stub_scored())  # no name/district
        assert "no_ura_prints" not in res


class TestPrintHygiene:
    def test_loader_drops_multiunit_and_land_rows_and_normalizes(
            self, monkeypatch, tmp_path):
        recent = _ym(3).strftime("%b-%y")
        csv = tmp_path / "ura_district_D15.csv"
        csv.write_text(
            "Project Name,Area (SQFT),Unit Price ($ PSF),Sale Date,"
            "Type of Sale,Type of Area,Number of Units,Floor Level\n"
            f"SUITES@ KATONG,700,\"1,800\",{recent},Resale,Strata,1,06 to 10\n"
            f"SUITES@ KATONG,705,\"1,820\",{recent},Resale,Strata,1,06 to 10\n"
            f"SUITES@ KATONG,700,\"1,200\",{recent},Resale,Strata,4,06 to 10\n"  # bulk
            f"SUITES@ KATONG,700,\"1,100\",{recent},Resale,Land,1,-\n"           # land
        )
        monkeypatch.setattr(fs, "_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(fs, "_raw_prints_cache", {})
        sc = FullScorer.__new__(FullScorer)
        med, n, _, _, _, _ = sc._tight_size_comps(
            {"sqft": 700, "project_name": "Suites @ Katong", "district": "D15"})
        assert n == 2          # hygiene rows dropped
        assert med == 1810     # bulk/land psf never enter the median

    def test_resale_only_when_enough_resales(self, scorer, monkeypatch):
        _seed(monkeypatch, "D18", "Proj", _prints([
            (700, 1600, "06 to 10", 5, "Resale"),
            (700, 1610, "06 to 10", 5, "Resale"),
            (700, 1620, "06 to 10", 4, "Resale"),
            (700, 1630, "06 to 10", 3, "Resale"),
            (700, 2100, "06 to 10", 3, "New Sale"),  # developer pricing
            (700, 2150, "06 to 10", 2, "New Sale"),
        ]))
        med, n, _, _, _, _ = scorer._tight_size_comps(
            {"sqft": 700, "project_name": "Proj", "district": "D18"})
        assert n == 4
        assert med == 1615  # new-sale prints excluded

    def test_mixed_set_kept_when_resales_thin(self, scorer, monkeypatch):
        _seed(monkeypatch, "D18", "Proj", _prints([
            (700, 1600, "06 to 10", 5, "Resale"),
            (700, 1620, "06 to 10", 4, "Resale"),
            (700, 2100, "06 to 10", 3, "New Sale"),
        ]))
        med, n, _, _, _, _ = scorer._tight_size_comps(
            {"sqft": 700, "project_name": "Proj", "district": "D18"})
        assert n == 3  # <4 resales → keep the mixed set (pre-fix behavior)


class TestFloorAwareComps:
    """Audit #6: the tight median must sit on the listing's floor-tier basis."""

    FACTORS = {"low": 0.94, "mid": 1.0, "high": 1.06, "basis": "project_fe"}

    def test_prints_adjusted_to_listing_tier(self, scorer, monkeypatch):
        # All prints are low-floor; the listing is floor 15 (high tier).
        _seed(monkeypatch, "D18", "Proj", _prints([
            (700, 1500, "01 to 05", 5),
            (700, 1500, "01 to 05", 4),
            (705, 1500, "01 to 05", 3),
        ]))
        scorer.ura_data = {"proj": {"floor_factors": self.FACTORS}}
        listing = {"sqft": 700, "project_name": "Proj", "district": "D18",
                   "floor_level": "15"}
        med, n, low_share, _, _, _ = scorer._tight_size_comps(listing)
        assert n == 3
        assert med == pytest.approx(1500 * 1.06 / 0.94, rel=1e-6)
        # A fairly-priced high-floor ask (~+12.8% over the raw low-floor
        # median) now reads ~0% — no spurious print-contradiction.
        assert abs((1690 - med) / med * 100) < 5

    def test_fairly_priced_high_floor_not_flagged(self, flag_scorer, monkeypatch):
        _seed(monkeypatch, "D18", "Proj", _prints([
            (700, 1500, "01 to 05", 5),
            (700, 1500, "01 to 05", 4),
            (705, 1500, "01 to 05", 3),
        ]))
        flag_scorer.ura_data = {"proj": {"floor_factors": self.FACTORS}}
        res = flag_scorer._score_red_flags(
            {"sqft": 700, "psf": 1690, "project_name": "Proj", "district": "D18",
             "floor_level": "15", "beds": 1}, _stub_scored())
        assert abs(res["stack_premium_pct"]) < 5  # no >+5 suspect-damp trip
        assert not any(f["flag"] == "ask_above_own_stack_prints"
                       for f in res["flags"])

    def test_same_tier_restriction_without_factors(self, scorer, monkeypatch):
        # No floor factors anywhere → restrict to the listing's own tier
        # when >= 3 same-tier prints exist.
        _seed(monkeypatch, "D18", "Proj", _prints([
            (700, 1450, "01 to 05", 5),
            (700, 1460, "01 to 05", 5),
            (700, 1470, "01 to 05", 4),
            (700, 1700, "16 to 20", 5),
            (705, 1710, "16 to 20", 4),
            (700, 1720, "16 to 20", 3),
        ]))
        med, n, low_share, _, _, _ = scorer._tight_size_comps(
            {"sqft": 700, "project_name": "Proj", "district": "D18",
             "floor_level": "17"})
        assert n == 3
        assert med == 1710  # high-tier prints only
        assert low_share == 0.0

    def test_pes_stack_detection_still_works(self, flag_scorer, monkeypatch):
        # v3.8 target preserved: a ground-floor PES listing (or one with no
        # floor data) against its own low-floor prints sees NO adjustment —
        # asking +20% over them still fires.
        _seed(monkeypatch, "D18", "Proj", _prints([
            (624, 1160, "01 to 05", 6),
            (628, 1165, "01 to 05", 4),
            (620, 1170, "01 to 05", 3),
        ]))
        res = flag_scorer._score_red_flags(
            {"sqft": 624, "psf": 1400, "project_name": "Proj", "district": "D18",
             "beds": 1}, _stub_scored())
        assert res["stack_low_floor_share"] == 1.0
        assert res["stack_premium_pct"] > 10
        assert any(f["flag"] == "ask_above_own_stack_prints" for f in res["flags"])


class TestBelowPrintsCorridor:
    """n_tight 5-7 corridor: bait ask under every in-window print must flag."""

    def _flags(self, flag_scorer, monkeypatch, psf, prints):
        _seed(monkeypatch, "D18", "Proj", prints)
        res = flag_scorer._score_red_flags(
            {"sqft": 700, "psf": psf, "project_name": "Proj", "district": "D18"},
            _stub_scored())
        return {f["flag"] for f in res["flags"]}

    def test_bait_ask_under_six_prints_fires(self, flag_scorer, monkeypatch):
        prints = _prints([(700, 1800 + 10 * i, "06 to 10", 4) for i in range(6)])
        flags = self._flags(flag_scorer, monkeypatch, 1440, prints)  # 20% under min
        assert "ask_below_stack_prints" in flags

    def test_just_below_min_fires(self, flag_scorer, monkeypatch):
        prints = _prints([(700, 1800 + 10 * i, "06 to 10", 4) for i in range(6)])
        flags = self._flags(flag_scorer, monkeypatch, 1795, prints)
        assert "ask_below_stack_prints" in flags

    def test_ask_inside_distribution_not_flagged(self, flag_scorer, monkeypatch):
        prints = _prints([(700, 1800 + 10 * i, "06 to 10", 4) for i in range(6)])
        flags = self._flags(flag_scorer, monkeypatch, 1815, prints)
        assert "ask_below_stack_prints" not in flags

    def test_thin_set_below_corridor_does_not_fire(self, flag_scorer, monkeypatch):
        # n < MIN_BAND_TXNS stays the thin-cohort damp's job, not this flag's
        prints = _prints([(700, 1800 + 10 * i, "06 to 10", 4) for i in range(4)])
        flags = self._flags(flag_scorer, monkeypatch, 1440, prints)
        assert "ask_below_stack_prints" not in flags

    def test_deep_set_p10_rule_unchanged(self, flag_scorer, monkeypatch):
        # 29 prints 1828+ (the Grandeur Park loft shape); ask 1504 < p10*0.97
        prints = _prints([(710, 1828 + 8 * i, "06 to 10", 4) for i in range(29)])
        flags = self._flags(flag_scorer, monkeypatch, 1504, prints)
        assert "ask_below_stack_prints" in flags

    def test_deep_set_plausible_deal_untouched(self, flag_scorer, monkeypatch):
        # Wide print range (The Palette shape): a -9% ask sits inside it
        psfs = [1045, 1120, 1153, 1414, 1418, 1454, 1571, 1595, 1611, 1450, 1380, 1500]
        prints = _prints([(700, p, "06 to 10", 4) for p in psfs])
        flags = self._flags(flag_scorer, monkeypatch, 1284, prints)
        assert "ask_below_stack_prints" not in flags


class TestBlendedCohort:
    def test_thin_tight_does_not_demote_deep_band(self, scorer, monkeypatch):
        # 2 tight prints against a 20-txn band: benchmark stays 60% band-
        # derived, so the cohort must blend too — round(.4*2 + .6*20) = 13,
        # not 2 (which falsely tripped thin-cohort damping for 487 listings).
        _seed(monkeypatch, "D18", "Proj", _prints([
            (700, 1500, "06 to 10", 5),
            (700, 1500, "06 to 10", 4),
        ]))
        scorer.ura_data = {"proj": {
            "median_psf": 1700,
            "by_size": {
                "recent_median_psf": 1700,
                "bands": {"600-800": {"median_psf": 1700, "txn_count": 20}},
            },
        }}
        premium, cohort = scorer._check_psf_overpricing(
            {"sqft": 700, "psf": 1620, "project_name": "Proj", "district": "D18"})
        assert cohort == 13
        # blended benchmark: .4*1500 + .6*1700 = 1620 → ~0% premium
        assert premium == pytest.approx(0.0, abs=0.5)

    def test_pure_tight_keeps_n_tight(self, scorer, monkeypatch):
        _seed(monkeypatch, "D18", "Proj", _prints([
            (700, 1500, "06 to 10", 5 - (i % 3)) for i in range(6)
        ]))
        scorer.ura_data = {"proj": {
            "median_psf": 1700,
            "by_size": {
                "recent_median_psf": 1700,
                "bands": {"600-800": {"median_psf": 1700, "txn_count": 20}},
            },
        }}
        _, cohort = scorer._check_psf_overpricing(
            {"sqft": 700, "psf": 1500, "project_name": "Proj", "district": "D18"})
        assert cohort == 6  # w=1 → cohort is exactly the tight print count


class TestMissingDataNeutrality:
    def test_bare_listing_cost_efficiency_is_neutral(self):
        from scoring.costs import CostCalculator
        sc = FullScorer.__new__(FullScorer)
        sc.cost_calculator = CostCalculator()
        scores = sc._score_cost_efficiency({}, SimpleNamespace(estimated_monthly_rent=0))
        assert scores["total"] == 5.0  # mmr cost = total - 5 → 0, not -5

    def test_present_data_cost_grading_unchanged(self):
        from scoring.costs import CostCalculator
        sc = FullScorer.__new__(FullScorer)
        sc.cost_calculator = CostCalculator()
        scores = sc._score_cost_efficiency(
            {"sqft": 900, "beds": 2},
            SimpleNamespace(estimated_monthly_rent=4000))
        # 900sqft → mcst ~315 → 4; AV 48k → 3; 450/bed → 3
        assert scores["total"] == 10

    def test_missing_district_buyer_pool_maps_to_zero_mmr(self):
        from scoring.mmr import _BUYER_POOL_PTS
        sc = FullScorer.__new__(FullScorer)
        sc.district_profiles = {}
        sc.transaction_data = {}
        sc.ura_data = {}
        scores = sc._score_liquidity({"price": 2_000_000}, None)
        depth = scores["buyer_pool_depth"]["depth"]
        assert depth is None
        assert _BUYER_POOL_PTS.get(depth, 0.0) == 0.0  # not +1.5 "moderate"
        assert scores["buyer_pool_depth"]["points"] == 3  # legacy /100 unchanged


class TestQuickGateBackfill:
    """Audit P2 survivorship: project-units backfill must run BEFORE the gate."""

    LISTING = {
        "id": "x1", "project_name": "Backfill Towers", "district": "D15",
        "psf": 2300, "price": 2_070_000, "sqft": 900, "beds": 2,
        "tenure": "freehold",
    }

    class _StubScorer:
        def __init__(self, *a, **k):
            pass

        def score(self, listing):
            return SimpleNamespace(rank_score=0, total_units=listing.get("total_units"))

    def test_units_backfilled_before_gate(self, monkeypatch):
        monkeypatch.setattr(fs, "_project_units_cache", {
            "backfill towers": {"total_units": 650, "lat": 1.30, "lng": 103.85}})
        monkeypatch.setattr(fs, "_project_units_norm_index", {})
        monkeypatch.setattr(fs, "FullScorer", self._StubScorer)
        result = score_and_filter([dict(self.LISTING)], min_quick_score=40)
        assert len(result["scored"]) == 1
        assert result["scored"][0].total_units == 650
        assert not result["rejected"]

    def test_same_listing_rejected_without_backfill_data(self, monkeypatch):
        # Negative control: proves the gate WOULD have dropped this listing
        # when no project-units entry exists (quick score 37 < 40).
        monkeypatch.setattr(fs, "_project_units_cache", {})
        monkeypatch.setattr(fs, "_project_units_norm_index", {})
        monkeypatch.setattr(fs, "FullScorer", self._StubScorer)
        result = score_and_filter([dict(self.LISTING)], min_quick_score=40)
        assert not result["scored"]
        assert len(result["rejected"]) == 1


class TestCrossAgentContracts:
    def test_unit_group_deduped_in_cohort_stats(self):
        listings = [
            {"district": "D15", "psf": 2000, "unit_group": "g1"},
            {"district": "D15", "psf": 2000, "unit_group": "g1"},  # dup agent row
            {"district": "D15", "psf": 2000, "unit_group": "g1"},  # dup agent row
            {"district": "D15", "psf": 1000},
            {"district": "D15", "psf": 1000},
        ]
        stats = build_cohort_stats(listings)
        assert stats["psf_by_district"]["D15"] == [1000.0, 1000.0, 2000.0]

    def test_no_unit_group_is_backward_compatible(self):
        listings = [{"district": "D15", "psf": p} for p in (1000, 1500, 2000)]
        stats = build_cohort_stats(listings)
        assert stats["psf_by_district"]["D15"] == [1000.0, 1500.0, 2000.0]

    def test_ingest_flag_mismatch_treated_like_own_detection(self, flag_scorer):
        # sqft/beds within range → own check is silent; ingest flag must fire
        res = flag_scorer._score_red_flags(
            {"beds": 2, "sqft": 800, "psf": 1500,
             "ingest_flags": ["bedroom_sqft_mismatch"]}, _stub_scored())
        assert any(f["flag"] == "bedroom_sqft_mismatch" for f in res["flags"])

    def test_no_ingest_flags_no_crash(self, flag_scorer):
        res = flag_scorer._score_red_flags(
            {"beds": 2, "sqft": 800, "psf": 1500}, _stub_scored())
        assert not any(f["flag"] == "bedroom_sqft_mismatch" for f in res["flags"])

    def test_rent_confidence_copied_when_present(self):
        sc = FullScorer.__new__(FullScorer)
        sc.cohort_stats = {}
        sc.rental_estimator = SimpleNamespace(estimate_yield_score=lambda l: {
            "monthly_rent": 4000, "gross_yield": 3.2, "gross_yield_score": 6,
            "source": "ura_project_bed", "confidence": 0.8})
        scored = SimpleNamespace(mrt_distance_m=None, nearest_mrt=None)
        scores = sc._score_rental_yield({"beds": 2}, scored)
        assert scored.rent_confidence == 0.8
        assert scores["rent_confidence"] == 0.8

    def test_rent_confidence_absent_no_crash(self):
        sc = FullScorer.__new__(FullScorer)
        sc.cohort_stats = {}
        sc.rental_estimator = SimpleNamespace(estimate_yield_score=lambda l: {
            "monthly_rent": 4000, "gross_yield": 3.2, "gross_yield_score": 6,
            "source": "ura_project_bed"})
        scored = SimpleNamespace(mrt_distance_m=None, nearest_mrt=None)
        sc._score_rental_yield({"beds": 2}, scored)
        assert not hasattr(scored, "rent_confidence")


class TestFutureScorerGuards:
    def test_non_numeric_district_is_neutral_not_crash(self):
        from scoring.future_scorer import FutureScorer
        fsc = FutureScorer()
        result = fsc.score({"district": "DISTRICT FIFTEEN"})
        assert result.govt_zone_score == 0
        assert result.transformation_score == 2
        assert result.supply_score == 2


class TestMmrTrustTriggers:
    def test_below_prints_flag_damps_value(self):
        # The suspect trigger must include the v3.9 flag name
        import inspect
        import scoring.mmr as mmr
        src = inspect.getsource(mmr)
        assert "ask_below_stack_prints" in src
        assert "stack_premium_pct" in src


class TestEcCompJoin:
    """v3.10 (audit #4 EC blind spot): EC prints load from ura_district_D{NN}
    _EC.csv and an EC listing benchmarks ONLY against EC prints (a condo
    listing ONLY against condo prints) — never the other type's medians."""

    EC_LISTING = {"sqft": 700, "project_name": "Parc Life", "district": "D27",
                  "property_type": "Executive Condominium"}
    CONDO_LISTING = {"sqft": 700, "project_name": "Parc Life", "district": "D27",
                     "property_type": "Condominium"}

    def _seed_mixed(self, monkeypatch):
        # Same project name has BOTH EC and condo prints in the district file
        # set; the join must keep them separate.
        ec = _prints([(700, 1100, "06 to 10", 5), (705, 1120, "06 to 10", 3)],
                     is_ec=True)
        condo = _prints([(700, 1600, "06 to 10", 5), (705, 1620, "06 to 10", 3)],
                        is_ec=False)
        _seed(monkeypatch, "D27", "Parc Life", ec + condo)

    def test_ec_listing_benchmarks_against_ec_prints(self, scorer, monkeypatch):
        self._seed_mixed(monkeypatch)
        med, n, _, _, _, _ = scorer._tight_size_comps(self.EC_LISTING)
        assert n == 2 and med == 1110  # EC prints only (not the 1610 condo med)

    def test_condo_listing_benchmarks_against_condo_prints(self, scorer, monkeypatch):
        self._seed_mixed(monkeypatch)
        med, n, _, _, _, _ = scorer._tight_size_comps(self.CONDO_LISTING)
        assert n == 2 and med == 1610  # condo prints only

    def test_ec_benchmark_flag_exported(self, flag_scorer, monkeypatch):
        ec = _prints([(700, 1100, "06 to 10", 5), (705, 1120, "06 to 10", 3)],
                     is_ec=True)
        _seed(monkeypatch, "D27", "Parc Life", ec)
        res = flag_scorer._score_red_flags(
            dict(self.EC_LISTING, psf=1110), _stub_scored())
        assert res.get("ec_benchmark") is True
        assert "ec_no_ec_prints" not in res
        assert "no_ura_prints" not in res

    def test_ec_no_ec_prints_when_only_condo_prints(self, flag_scorer, monkeypatch):
        # EC listing, project has ONLY condo prints (EC file unfetched, or the
        # project genuinely has no EC sales) → visible verify-first flag, NOT a
        # silent condo-median benchmark.
        condo = _prints([(700, 1600, "06 to 10", 5), (705, 1620, "06 to 10", 3)],
                        is_ec=False)
        _seed(monkeypatch, "D27", "Parc Life", condo)
        res = flag_scorer._score_red_flags(
            dict(self.EC_LISTING, psf=1400), _stub_scored())
        assert res.get("ec_no_ec_prints") is True
        assert res.get("no_ura_prints") is True  # zero MATCHING prints
        assert "ec_benchmark" not in res

    def test_condo_listing_never_gets_ec_flags(self, flag_scorer, monkeypatch):
        self._seed_mixed(monkeypatch)
        res = flag_scorer._score_red_flags(
            dict(self.CONDO_LISTING, psf=1610), _stub_scored())
        assert "ec_benchmark" not in res and "ec_no_ec_prints" not in res

    def test_graceful_when_ec_file_absent(self, scorer, monkeypatch, tmp_path):
        # No _EC.csv on disk → only the base condo CSV loads; an EC listing
        # simply finds no matching prints (exactly today's pre-fix behavior),
        # no crash.
        recent = _ym(3).strftime("%b-%y")
        csv = tmp_path / "ura_district_D27.csv"
        csv.write_text(
            "Project Name,Area (SQFT),Unit Price ($ PSF),Sale Date,"
            "Type of Sale,Type of Area,Number of Units,Floor Level\n"
            f"PARC LIFE,700,\"1,600\",{recent},Resale,Strata,1,06 to 10\n"
            f"PARC LIFE,705,\"1,620\",{recent},Resale,Strata,1,06 to 10\n"
        )
        monkeypatch.setattr(fs, "_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(fs, "_raw_prints_cache", {})
        ec_med, ec_n, _, _, _, _ = scorer._tight_size_comps(self.EC_LISTING)
        assert ec_med is None and ec_n == 0      # EC listing: no EC prints
        condo_med, condo_n, _, _, _, _ = scorer._tight_size_comps(self.CONDO_LISTING)
        assert condo_n == 2 and condo_med == 1610  # condo path unchanged

    def test_loader_tags_ec_from_suffix_file(self, monkeypatch, tmp_path):
        # End-to-end through _load_district_prints: the _EC.csv prints carry
        # is_ec=True, the base CSV prints is_ec=False.
        recent = _ym(3).strftime("%b-%y")
        header = ("Project Name,Area (SQFT),Unit Price ($ PSF),Sale Date,"
                  "Type of Sale,Type of Area,Number of Units,Floor Level\n")
        (tmp_path / "ura_district_D27.csv").write_text(
            header + f"PARC LIFE,700,\"1,600\",{recent},Resale,Strata,1,06 to 10\n")
        (tmp_path / "ura_district_D27_EC.csv").write_text(
            header + f"PARC LIFE,700,\"1,100\",{recent},Resale,Strata,1,06 to 10\n")
        monkeypatch.setattr(fs, "_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(fs, "_raw_prints_cache", {})
        prints = fs._load_district_prints("D27")[fs._pu_normalize("Parc Life")]
        by_ec = {t[5]: t[1] for t in prints}
        assert by_ec[True] == 1100 and by_ec[False] == 1600
