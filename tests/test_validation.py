"""Tests for the validation harness.

The harness exists to stop numbers flattering themselves, so most of these are
tests that it says NO: that random reads as random, that a null baseline cannot
be beaten by accident, that a ratio of two noise estimates is refused rather
than printed. The one positive test — planted signal is recovered — is what
stops the rest from passing vacuously.
"""

import math
import random

import pytest

from validation import algos, metrics, panel


def _rows(n=200, seed=1, signal=0.0):
    """Synthetic panel. `signal` is how strongly psf_vs_dist drives the outcome."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        v = rng.uniform(-0.3, 0.3)
        out.append({
            "project": f"P{i}", "district": f"D{i % 12:02d}",
            "psf_vs_dist": v,
            "trailing_cagr": rng.uniform(-0.05, 0.15),
            "momentum": rng.uniform(-0.05, 0.05),
            "txn_vol": rng.randint(0, 80),
            "excess_fwd": -signal * v + rng.gauss(0, 0.05),
        })
    return out


class TestMetricsAreHonest:
    def test_random_scores_read_as_random(self):
        rows = _rows(signal=0.0)
        _, scores = algos.score_panel("random", rows)
        m = metrics.evaluate(rows, scores, reps=200)
        assert m["rho_lo"] < 0 < m["rho_hi"], "a null CI must straddle zero"
        assert not m["rho_significant"]

    def test_planted_signal_is_recovered(self):
        # If this fails the other tests are vacuous — the harness would be
        # incapable of detecting anything at all.
        rows = _rows(signal=1.0)
        _, scores = algos.score_panel("psf_vs_dist", rows)
        m = metrics.evaluate(rows, scores, reps=200)
        assert m["rho"] > 0.5 and m["rho_significant"]

    def test_the_ci_is_an_interval_not_a_cluster_count(self):
        # bt._cluster_boot_rho returns (lo, hi, n_clusters). Read as
        # (rho, lo, hi) it printed the cluster COUNT as the upper bound — a
        # "95% CI" of [+0.068, +26.000].
        rows = _rows(signal=0.5)
        _, scores = algos.score_panel("psf_vs_dist", rows)
        m = metrics.evaluate(rows, scores, reps=200)
        for k in ("rho", "rho_lo", "rho_hi"):
            assert -1.0 <= m[k] <= 1.0, f"{k}={m[k]} is not a correlation"
        assert m["rho_lo"] <= m["rho"] <= m["rho_hi"]

    def test_permutation_p_is_uniformish_under_the_null(self):
        ps = []
        for seed in range(12):
            rows = _rows(seed=seed, signal=0.0)
            _, scores = algos.score_panel("random", rows)
            ps.append(metrics.hit_rate_at_k(scores, [r["excess_fwd"] for r in rows],
                                            reps=200, seed=seed)["p_value"])
        assert sum(1 for p in ps if p < 0.05) <= 2, f"too many false positives: {ps}"

    def test_decile_lift_needs_enough_rows(self):
        assert metrics.decile_lift([1, 2, 3], [0.1, 0.2, 0.3]) is None


class TestDegradation:
    def test_a_real_drop_is_flagged(self):
        dev = {"rho": 0.20, "rho_significant": True}
        assert metrics.degradation(dev, {"rho": 0.05})["overfit_flag"] is True

    def test_a_holding_signal_is_not_flagged(self):
        dev = {"rho": 0.20, "rho_significant": True}
        assert metrics.degradation(dev, {"rho": 0.18})["overfit_flag"] is False

    def test_noise_over_noise_is_refused(self):
        # The first real run printed +4.34 for trailing_cagr and +0.68 for
        # RANDOM. Neither had a DEV signal to degrade from.
        assert metrics.degradation({"rho": 0.039, "rho_significant": False},
                                   {"rho": 0.027}) is None
        assert metrics.degradation({"rho": 0.02, "rho_significant": True},
                                   {"rho": 0.09}) is None


class TestBaselines:
    def test_district_median_has_no_ranking_information(self):
        rows = _rows(signal=1.0)
        _, scores = algos.score_panel("district_median", rows)
        assert len(set(scores)) == 1, "the no-skill baseline must not rank"

    def test_random_is_deterministic_across_runs(self):
        rows = _rows()
        a = algos.score_panel("random", rows)[1]
        b = algos.score_panel("random", rows)[1]
        assert a == b, "reruns must be reproducible"

    def test_an_algorithm_that_throws_abstains(self, monkeypatch):
        def boom(r):
            raise ValueError("nope")
        monkeypatch.setitem(algos.ALGOS, "psf_vs_dist", (boom, True))
        kept, scores = algos.score_panel("psf_vs_dist", _rows())
        assert kept == [] and scores == []

    def test_rows_missing_a_feature_are_dropped_not_zeroed(self):
        rows = _rows(20)
        rows[0]["psf_vs_dist"] = None
        kept, scores = algos.score_panel("psf_vs_dist", rows)
        assert len(kept) == 19 and len(scores) == 19


class TestSplitsAreLegal:
    """The splits must have room for their own features and outcomes.

    A first attempt used 2022.46/2024.46 at window=2.0. build_panel tolerates a
    missing lookback by setting trailing_cagr=None, so the early split silently
    produced rows with no trailing features and every algorithm needing them
    abstained on all of them.
    """

    DATA_START, DATA_END = 2021.38, 2026.46
    LOOKBACK = 3.0          # build_panel's `old` window is (T-3, T-2]

    def test_both_splits_have_a_full_feature_lookback(self):
        for s in (panel.DEV_SPLIT, panel.VAL_SPLIT):
            assert s - self.LOOKBACK >= self.DATA_START, (
                f"split {s} needs data back to {s - self.LOOKBACK:.2f}")

    def test_both_splits_have_a_complete_outcome_window(self):
        for s in (panel.DEV_SPLIT, panel.VAL_SPLIT):
            assert s + panel.DEFAULT_WINDOW <= self.DATA_END

    def test_the_outcome_windows_do_not_overlap(self):
        # Overlapping windows share transactions, which makes the second split
        # a restatement of the first rather than a test of it.
        a = (panel.DEV_SPLIT, panel.DEV_SPLIT + panel.DEFAULT_WINDOW)
        b = (panel.VAL_SPLIT, panel.VAL_SPLIT + panel.DEFAULT_WINDOW)
        assert a[1] <= b[0] or b[1] <= a[0], f"{a} overlaps {b}"


class TestTenureParser:
    """The piece most likely to be silently wrong — a bad parse doesn't crash,
    it files a decaying asset as freehold and the harness happily measures the
    wrong thing. Both real-world traps below were live in build_panel's
    substring flag when this parser was written."""

    AT = 2024.4

    def test_freehold_any_case(self):
        for s in ("Freehold", "FREEHOLD", "freehold"):
            assert panel.parse_tenure(s, self.AT) == ("freehold", None, None)

    def test_99_year_with_commencement(self):
        kind, rem, age = panel.parse_tenure(
            "99 yrs lease commencing from 1996", self.AT)
        assert kind == "leasehold"
        assert age == pytest.approx(28.4)
        assert rem == pytest.approx(70.6)

    def test_years_spelling_variant(self):
        # URA mostly writes "yrs"; 3 rows in the live data write "years".
        kind, rem, age = panel.parse_tenure(
            "99 years lease commencing from 2022", self.AT)
        assert kind == "leasehold"
        assert rem == pytest.approx(99 - 2.4)

    def test_the_1999_substring_trap(self):
        # "99 yrs lease commencing from 1999" contains "999". build_panel's
        # flag reads it as freehold; Caribbean at Keppel Bay has ~74 years
        # left. The parser must not fall for it.
        kind, rem, age = panel.parse_tenure(
            "99 yrs lease commencing from 1999", self.AT)
        assert kind == "leasehold"
        assert rem == pytest.approx(73.6)

    def test_999_year_is_economically_freehold(self):
        for s in ("999 yrs lease commencing from 1885",
                  "9999 yrs lease commencing from 1826",
                  "999999 yrs lease commencing from 1827",
                  "999 years leasehold"):
            assert panel.parse_tenure(s, self.AT)[0] == "freehold"

    def test_colonial_9xx_leases_are_freehold_despite_no_999(self):
        # 956 from 1928, 929 from 1953 — no "999" substring, so build_panel
        # calls them leasehold; with ~850 years left they trade as freehold.
        for s in ("956 yrs lease commencing from 1928",
                  "929 yrs lease commencing from 1953"):
            assert panel.parse_tenure(s, self.AT)[0] == "freehold"

    def test_short_and_odd_terms(self):
        kind, rem, age = panel.parse_tenure(
            "60 yrs lease commencing from 2013", self.AT)
        assert kind == "leasehold" and rem == pytest.approx(48.6)
        kind, rem, _ = panel.parse_tenure(
            "103 yrs lease commencing from 2011", self.AT)
        assert kind == "leasehold" and rem == pytest.approx(89.6)

    def test_term_without_commencement_keeps_kind_but_not_remaining(self):
        # "99 years leasehold" (232 live rows): the term is known, the clock's
        # start is not. Inventing a start would fabricate the panel's
        # strongest-evidenced feature, so remaining must be None.
        assert panel.parse_tenure("99 years leasehold", self.AT) == \
            ("leasehold", None, None)

    def test_malformed_and_missing(self):
        for s in ("", None, "n/a", "lease", "yrs lease commencing from",
                  "99 yrs lease commencing from 19",
                  "leasehold 99 yrs"):
            assert panel.parse_tenure(s, self.AT) == (None, None, None)


class TestTenureAttachment:
    SPLIT = 2024.4

    def _txn(self, proj, t, tenure):
        return {"project": proj, "district": "D05", "t": t, "tenure": tenure}

    def test_modal_tenure_wins_over_first_row(self):
        # Same reason build_panel went modal: a first-row read keys the
        # feature to file load order on mixed/dirty-tenure projects.
        txns = ([self._txn("A", 2023.0, "Freehold")]
                + [self._txn("A", 2023.5, "99 yrs lease commencing from 1996")] * 3)
        rows = [{"project": "A", "district": "D05"}]
        panel._attach_tenure(rows, txns, self.SPLIT)
        assert rows[0]["tenure_kind"] == "leasehold"
        assert rows[0]["remaining_lease"] == pytest.approx(70.6)

    def test_post_split_txns_are_ignored(self):
        # Tenure is a fixed attribute so this costs nothing — but the
        # no-look-ahead rule stays uniform rather than argued per-feature.
        txns = ([self._txn("A", 2023.0, "99 yrs lease commencing from 1996")]
                + [self._txn("A", 2025.0, "Freehold")] * 5)
        rows = [{"project": "A", "district": "D05"}]
        panel._attach_tenure(rows, txns, self.SPLIT)
        assert rows[0]["tenure_kind"] == "leasehold"

    def test_project_with_no_pre_split_tenure_gets_none(self):
        rows = [{"project": "GHOST", "district": "D05"}]
        panel._attach_tenure(rows, [], self.SPLIT)
        assert rows[0]["tenure_kind"] is None
        assert rows[0]["remaining_lease"] is None
        assert rows[0]["lease_age"] is None


class TestTenureAlgos:
    def _row(self, kind, remaining=None, age=None, i=0):
        return {"project": f"P{i}", "district": "D10", "tenure_kind": kind,
                "remaining_lease": remaining, "lease_age": age}

    def test_lease_decay_puts_freehold_above_every_leasehold(self):
        fh = algos.lease_decay(self._row("freehold"))
        lh = algos.lease_decay(self._row("leasehold", remaining=94.0))
        assert fh > lh > algos.lease_decay(self._row("leasehold", remaining=45.0))

    def test_lease_decay_abstains_without_a_parse(self):
        assert algos.lease_decay(self._row(None)) is None
        # "99 years leasehold": kind known, clock start unknown -> abstain
        # rather than score a made-up remaining.
        assert algos.lease_decay(self._row("leasehold", remaining=None)) is None

    def test_enbloc_is_an_interaction_not_a_main_effect(self):
        old_lh = algos.enbloc_leasehold_age(self._row("leasehold", age=35.0))
        young_lh = algos.enbloc_leasehold_age(self._row("leasehold", age=5.0))
        fh = algos.enbloc_leasehold_age(self._row("freehold"))
        assert old_lh > young_lh > fh == 0.0
        assert algos.enbloc_leasehold_age(self._row(None)) is None

    def test_inf_scores_survive_the_metrics(self):
        # lease_decay emits math.inf for freehold; the rank-based metrics must
        # treat that as "tied at the top", not blow up or emit NaN.
        rng = random.Random(3)
        rows, scores = [], []
        for i in range(60):
            fh = i % 3 == 0
            r = self._row("freehold" if fh else "leasehold",
                          remaining=None if fh else 90 - i, i=i)
            r["district"] = f"D{i % 6:02d}"
            r["excess_fwd"] = rng.gauss(0, 0.05)
            rows.append(r)
            scores.append(algos.lease_decay(r))
        assert all(s is not None for s in scores)
        m = metrics.evaluate(rows, scores, reps=100)
        assert m["rho"] is not None and not math.isnan(m["rho"])
        assert -1.0 <= m["rho"] <= 1.0


class TestPanelId:
    def test_it_changes_with_content_not_just_parameters(self):
        rows = [{"project": "A", "district": "D19", "excess_fwd": 0.01}]
        same = [{"project": "A", "district": "D19", "excess_fwd": 0.01}]
        other = [{"project": "A", "district": "D19", "excess_fwd": 0.02}]
        assert panel.panel_id(rows, 2024.4, 1.0, 5) == panel.panel_id(same, 2024.4, 1.0, 5)
        assert panel.panel_id(rows, 2024.4, 1.0, 5) != panel.panel_id(other, 2024.4, 1.0, 5)

    def test_parameters_are_visible_in_the_id(self):
        rows = [{"project": "A", "district": "D19", "excess_fwd": 0.01}]
        assert panel.panel_id(rows, 2024.4, 1.0, 5).startswith("s2024.4-w1-m5-")
