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


class TestCostProxy:
    """_cost_proxy must reproduce full_scorer._score_cost_efficiency exactly
    (recentred by -5.0). Bands: MCST sqft*0.35 (0-4), tax from AV (0-3),
    efficiency sqft-per-bed (0-3); neutral midpoints for absent inputs."""

    def test_all_missing_is_neutral_zero(self):
        # absent sqft -> mcst 2.0, absent rent -> tax 1.5, absent beds -> eff 1.5
        # total 5.0 -> recentred 0.0 (missing data is neutral, never penalized)
        assert bx._cost_proxy(None, None, None) == 0.0
        assert bx._cost_proxy(0, 0, 0) == 0.0

    def test_small_efficient_unit_scores_high(self):
        # 550sqft 1BR: mcst 550*0.35=192.5 (<350 ->4), eff 1BR 500<=550<=650 ->3,
        # no rent -> tax 1.5; total 8.5 -> +3.5 recentred
        assert bx._cost_proxy(550, 1) == 3.5

    def test_large_unit_high_mcst_low_eff(self):
        # 2000sqft 4BR: mcst 700 (>=650 ->0), eff spb=500 (>400 ->1), tax 1.5;
        # total 2.5 -> -2.5 recentred (cost DRAGS for a big unit)
        assert bx._cost_proxy(2000, 4) == -2.5

    def test_tax_band_uses_rent(self):
        # rent shifts only the tax sub-score: 1000sqft 3BR
        # mcst 350 (<450 ->3), eff spb=333 (3BR 300<=333<=480 ->2)
        base = bx._cost_proxy(1000, 3)              # no rent: tax 1.5 -> total 6.5 -> +1.5
        hi_rent = bx._cost_proxy(1000, 3, 5000)     # AV 60k (<70k ->2) -> total 7.0 -> +2.0
        lo_rent = bx._cost_proxy(1000, 3, 2000)     # AV 24k (<50k ->3) -> total 8.0 -> +3.0
        assert base == 1.5
        assert hi_rent == 2.0
        assert lo_rent == 3.0

    def test_matches_live_scorer_band_edges(self):
        # mcst band edges: 349sqft->4 (122mo), but eff for tiny sqft 1BR=1
        # just assert monotonic: smaller sqft (lower mcst) never scores worse on mcst
        a = bx._cost_proxy(1400, 2)   # mcst 490 (<550 ->2)
        b = bx._cost_proxy(900, 2)    # mcst 315 (<350 ->4)
        assert b > a


class TestBedsFromSqft:
    def test_bands(self):
        assert bx._beds_from_sqft(None) is None
        assert bx._beds_from_sqft(0) is None
        assert bx._beds_from_sqft(500) == 1
        assert bx._beds_from_sqft(750) == 2
        assert bx._beds_from_sqft(1100) == 3
        assert bx._beds_from_sqft(1600) == 4
        assert bx._beds_from_sqft(2200) == 5


class TestRegionBaselineDelta:
    def test_centered_on_mean(self):
        d = {r: bx._region_baseline_delta(r) for r in ("CCR", "RCR", "OCR")}
        # ordering: OCR > RCR > CCR (the de-inverted forward tilt)
        assert d["OCR"] > d["RCR"] > d["CCR"]
        # centered: deltas sum to ~0
        assert abs(sum(d.values())) < 1e-9
        # CCR negative, OCR positive
        assert d["CCR"] < 0 < d["OCR"]

    def test_unknown_region_is_zero(self):
        assert bx._region_baseline_delta("XXX") == 0.0


class TestRepeatSalesPairs:
    """The +/-5% sqft within-project buy/sell pairing — the P2 methodology fix.
    Each leg used once; nearest-sqft greedy; only pairs with a real holding
    gap (>0.25yr) annualize. Returns median annualized log-return + count."""

    def _tx(self, project, district, t, psf, sqft, sale_type="Resale"):
        return {"project": project, "district": district, "t": t, "psf": psf,
                "sqft": sqft, "sale_type": sale_type}

    def test_simple_pair_annualized_log_return(self):
        # one unit: buy 1000psf @ 1000sqft in yr0, sell 1210psf @ 1000sqft 2yr later
        # ret = log(1210/1000) / dt(=2yr) = 0.09531/... -> ~+4.77%/yr (log return)
        import math
        txns = [self._tx("A", "15", 2022.0, 1000, 1000),
                self._tx("A", "15", 2024.0, 1210, 1000)]
        out = bx._repeat_sales_pairs(txns, 2021.5, 2022.5, 2023.5, 2024.5)
        assert ("A", "15") in out
        ret, n = out[("A", "15")]
        assert n == 1
        assert abs(ret - math.log(1210 / 1000) / 2.0) < 1e-9

    def test_sqft_tolerance_excludes_different_unit(self):
        # sell leg is +10% sqft -> outside the 5% tolerance -> no pair
        txns = [self._tx("A", "15", 2022.0, 1000, 1000),
                self._tx("A", "15", 2024.0, 1210, 1100)]
        out = bx._repeat_sales_pairs(txns, 2021.5, 2022.5, 2023.5, 2024.5)
        assert out == {}

    def test_within_tolerance_pairs(self):
        # +/-4% sqft is inside the 5% window -> pairs
        txns = [self._tx("A", "15", 2022.0, 1000, 1000),
                self._tx("A", "15", 2024.0, 1100, 1040)]
        out = bx._repeat_sales_pairs(txns, 2021.5, 2022.5, 2023.5, 2024.5)
        assert ("A", "15") in out

    def test_each_leg_used_once(self):
        # 2 buys, 1 sell at same sqft -> only ONE pair (no double-count)
        txns = [self._tx("A", "15", 2022.0, 1000, 1000),
                self._tx("A", "15", 2022.1, 1005, 1000),
                self._tx("A", "15", 2024.0, 1200, 1000)]
        out = bx._repeat_sales_pairs(txns, 2021.5, 2022.5, 2023.5, 2024.5)
        assert out[("A", "15")][1] == 1

    def test_nearest_sqft_greedy_match(self):
        # buy 1000sqft should match the 1000sqft sell, not the 1050 one
        txns = [self._tx("A", "15", 2022.0, 1000, 1000),
                self._tx("A", "15", 2024.0, 1100, 1050),   # +5% (boundary)
                self._tx("A", "15", 2024.0, 1200, 1000)]   # exact
        out = bx._repeat_sales_pairs(txns, 2021.5, 2022.5, 2023.5, 2024.5)
        ret, n = out[("A", "15")]
        # matched the exact-sqft sell (1200) -> log(1200/1000)/2
        import math
        assert n == 1
        assert abs(ret - math.log(1200 / 1000) / 2) < 1e-9

    def test_new_sale_legs_excluded(self):
        txns = [self._tx("A", "15", 2022.0, 1000, 1000, sale_type="New Sale"),
                self._tx("A", "15", 2024.0, 1210, 1000, sale_type="New Sale")]
        out = bx._repeat_sales_pairs(txns, 2021.5, 2022.5, 2023.5, 2024.5)
        assert out == {}

    def test_short_hold_excluded(self):
        # buy and sell windows overlap so dt < 0.25 -> no annualizable pair
        txns = [self._tx("A", "15", 2024.0, 1000, 1000),
                self._tx("A", "15", 2024.1, 1010, 1000)]
        out = bx._repeat_sales_pairs(txns, 2023.5, 2024.05, 2024.05, 2024.5)
        assert out == {}

    def test_cross_project_not_paired(self):
        txns = [self._tx("A", "15", 2022.0, 1000, 1000),
                self._tx("B", "15", 2024.0, 1210, 1000)]
        out = bx._repeat_sales_pairs(txns, 2021.5, 2022.5, 2023.5, 2024.5)
        assert out == {}
