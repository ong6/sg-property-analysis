#!/usr/bin/env python3
"""Algorithms under test, and the baselines they have to beat.

An algorithm here is just `(panel_row) -> float, higher is better`. That is
deliberately narrower than a real scorer: the panel carries only what URA can
prove as of T, so anything needing a live listing (an asking price, a floor, a
facing) cannot be evaluated on it. See validation/panel.py — that is a limit of
the ground truth, not an oversight.

THE BASELINES ARE THE POINT. Three of the five entries below exist to stop a
number from flattering itself:

  random          — if an algorithm's CI overlaps this, it found nothing.
  district_median — buy the district; excess_fwd is 0 by construction, so any
                    positive lift must be earned against "no skill at all".
  psf_vs_dist     — single-feature cheapness, the strongest signal the repo has
                    measured. This is the real bar: an assembled model that
                    cannot beat one feature has added complexity and nothing else.
"""

from __future__ import annotations

import math
import random as _random


def _neg(v):
    return None if v is None else -v


def random_scorer(seed: int = 42):
    """Deterministic noise. Same row -> same score, so reruns are reproducible."""
    def score(r):
        return _random.Random(f"{seed}|{r['district']}|{r['project']}").random()
    return score


def district_median(r):
    """No skill: every project scores identically. Its rho is ~0 by
    construction and its decile lift is the honest zero to beat."""
    return 0.0


def psf_vs_dist(r):
    """Cheap versus district peers, alone and unweighted.

    The literature is against this as a return predictor — below-median usually
    means shorter lease, older stock, or larger units, all compensated — and
    the 2026-07-29 sweep found all twelve top scorers' discounts explained away
    as artifacts. It is here as the bar precisely because the repo's assembled
    model leans on it hardest.
    """
    return _neg(r.get("psf_vs_dist"))


def mmr_composite(r):
    """The shipped weights applied to as-of-T features.

    Reuses backtest_ext._config_composite so this cannot drift from the
    existing harness's notion of the composite. It is NOT the shipped scorer —
    the real MMR reads 17 components off a live listing, most of which the
    panel cannot supply — it is the part of MMR that URA history can test.
    """
    from backtest_ext import _config_composite
    return _config_composite(r)


def trailing_cagr(r):
    """Momentum-chasing, as the deliberately-bad control.

    Included because the literature says it should FAIL: housing shows ~1yr
    momentum but 5yr mean reversion, with roughly a third of excess
    appreciation given back. If the harness cannot show this losing, the
    harness is broken — which makes it the best available test of the test.
    """
    return r.get("trailing_cagr")


# name -> (fn, is_baseline). Order is display order.
ALGOS = {
    "random": (random_scorer(), True),
    "district_median": (district_median, True),
    "psf_vs_dist": (psf_vs_dist, True),
    "mmr_composite": (mmr_composite, False),
    "trailing_cagr": (trailing_cagr, False),
}

BASELINES = [k for k, (_, b) in ALGOS.items() if b]


def score_panel(name: str, rows: list[dict]) -> tuple[list[dict], list[float]]:
    """Score a panel, dropping rows the algorithm cannot score.

    Returns the SURVIVING rows alongside the scores, because comparing an
    algorithm scored on 300 rows against one scored on 342 is the sample-
    composition artifact the Jun-2026 audit caught in PART 4. Callers compare
    on the intersection; see runner._common_rows.
    """
    fn, _ = ALGOS[name]
    kept, scores = [], []
    for r in rows:
        try:
            s = fn(r)
        except Exception:  # noqa: BLE001 — an algorithm that throws just abstains
            s = None
        if s is None or (isinstance(s, float) and math.isnan(s)):
            continue
        kept.append(r)
        scores.append(float(s))
    return kept, scores
