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
