"""Light tests for the backtest harness statistics (Jun-2026 audit wave).

Covers the two pieces the audit flagged as silently wrong / absent:
  - Spearman midranks: argsort().argsort() assigned row-order ranks within
    tie groups (correlated with district file order, ~±0.02 artifact on
    binary/quantized features). Proper midranks must be order-invariant.
  - Cluster-bootstrap CI plumbing: project-cluster resampling for rho and
    standardized OLS betas, with the UNDETERMINED (CI crosses 0) tag.

These run on tiny synthetic panels — no URA CSVs needed.
"""

import random

import numpy as np

import backtest as bt
import backtest_ext as bx


class TestMidranks:
    def test_ties_get_average_rank(self):
        r = bt._midranks([10, 20, 20, 30])
        assert list(r) == [0.0, 1.5, 1.5, 3.0]

    def test_all_tied(self):
        r = bt._midranks([7, 7, 7, 7])
        assert list(r) == [1.5, 1.5, 1.5, 1.5]

    def test_no_ties_is_plain_rank(self):
        r = bt._midranks([3.0, 1.0, 2.0])
        assert list(r) == [2.0, 0.0, 1.0]

    def test_spearman_perfect_monotone(self):
        rho, n = bt._spearman(list(range(8)), [x * 2 + 1 for x in range(8)])
        assert n == 8
        assert abs(rho - 1.0) < 1e-12

    def test_spearman_tie_row_order_invariance(self):
        """The old argsort().argsort() bug: shuffling rows of a tie-heavy
        (binary) feature changed rho. Midranks must not care about row order."""
        x = [0] * 12 + [1] * 12
        y = list(range(24))
        rho1, _ = bt._spearman(x, y)
        pairs = list(zip(x, y))
        rng = random.Random(7)
        for _ in range(5):
            rng.shuffle(pairs)
            rho2, _ = bt._spearman([p[0] for p in pairs], [p[1] for p in pairs])
            assert abs(rho1 - rho2) < 1e-12

    def test_spearman_skips_none(self):
        x = [1, 2, None, 4, 5, 6, 7, 8, 9]
        y = [1, 2, 3, 4, 5, 6, 7, 8, 9]
        rho, n = bt._spearman(x, y)
        assert n == 8
        assert abs(rho - 1.0) < 1e-12


class TestClusterBootstrap:
    def _panel(self, seed=0, n_clusters=60, rows_per=3):
        """Synthetic project-cluster panel with a strong x->y relation."""
        rng = np.random.default_rng(seed)
        x, y, cl = [], [], []
        for c in range(n_clusters):
            base = rng.normal()
            for _ in range(rows_per):
                xv = base + rng.normal(scale=0.1)
                x.append(xv)
                y.append(2.0 * xv + rng.normal(scale=0.1))
                cl.append(f"proj{c}")
        return x, y, cl

    def test_rho_ci_excludes_zero_on_signal(self):
        x, y, cl = self._panel()
        lo, hi, k = bt._cluster_boot_rho(x, y, cl, reps=200, seed=1)
        assert k == 60
        assert lo is not None and lo > 0.5
        rho, _ = bt._spearman(x, y)
        assert lo <= rho <= hi  # CI contains the point estimate

    def test_rho_ci_crosses_zero_on_noise(self):
        x, _y, cl = self._panel()
        rng = np.random.default_rng(9)
        noise = list(rng.normal(size=len(x)))
        lo, hi, _k = bt._cluster_boot_rho(x, noise, cl, reps=200, seed=1)
        assert lo is not None
        assert lo < 0 < hi

    def test_rho_ci_seeded_reproducible(self):
        x, y, cl = self._panel()
        a = bt._cluster_boot_rho(x, y, cl, reps=100, seed=5)
        b = bt._cluster_boot_rho(x, y, cl, reps=100, seed=5)
        assert a == b

    def test_too_few_clusters_returns_none(self):
        lo, hi, k = bt._cluster_boot_rho([1, 2, 3] * 4, [1, 2, 3] * 4,
                                         ["a", "b", "c"] * 4)
        assert lo is None and hi is None and k == 3

    def test_ols_ci_signal_vs_noise_undetermined(self):
        rng = np.random.default_rng(3)
        n = 240
        cl = [f"p{i // 3}" for i in range(n)]
        x1 = rng.normal(size=n)
        x2 = rng.normal(size=n)  # pure noise regressor
        y = 1.5 * x1 + rng.normal(scale=0.5, size=n)
        X = np.column_stack([x1, x2])
        out, r2, nn = bx._ols_ci(X, y, ["signal", "noise"], cl, reps=200, seed=5)
        assert nn == n and r2 > 0.5
        sig = next(o for o in out if o["name"] == "signal")
        noi = next(o for o in out if o["name"] == "noise")
        assert not sig["und"]
        assert sig["lo"] > 0 and sig["lo"] <= sig["std"] <= sig["hi"]
        assert noi["und"]  # CI crosses 0 -> UNDETERMINED
        assert noi["lo"] < 0 < noi["hi"]
        # raw-coef CI brackets the true coefficient
        assert sig["coef_lo"] <= 1.5 <= sig["coef_hi"]


class TestEffectiveN:
    def test_overlap_warning_and_counts(self, capsys):
        rows = []
        for split in (2023.75, 2024.0, 2024.25):
            for p in ("A", "B", "C"):
                rows.append({"project": p, "district": "15", "split": split})
        n_proj = bt.effective_n_report(rows, [2023.75, 2024.0, 2024.25], 2.0)
        out = capsys.readouterr().out
        assert n_proj == 3
        assert "overlap 88%" in out
        assert "WARNING: OVERLAPPING" in out

    def test_clean_mode_no_warning(self, capsys):
        rows = [{"project": "A", "district": "15", "split": s}
                for s in (2022.0, 2024.0)]
        bt.effective_n_report(rows, [2022.0, 2024.0], 2.0)
        out = capsys.readouterr().out
        assert "non-overlapping clean mode" in out
        assert "WARNING" not in out


class TestOpenedParse:
    def test_parse_opened(self):
        assert bx._parse_opened("") is None
        assert bx._parse_opened(None) is None
        y = bx._parse_opened("2022-11")
        assert 2022.8 < y < 2022.9
        y2 = bx._parse_opened("2024")
        assert 2024.4 < y2 < 2024.5
        # an as-of-2023.75 split must exclude TEL4 (2024-06) but keep TEL2
        assert bx._parse_opened("2021-08") <= 2023.75
        assert bx._parse_opened("2024-06") > 2023.75
