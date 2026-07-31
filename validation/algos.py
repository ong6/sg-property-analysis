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


def lease_decay(r):
    """Longest remaining lease first, freehold above everything.

    The best-evidenced PRICE driver in the literature (Sia 2022, IRER: +1%
    remaining lease -> +1.46% price; 71-85yr leaseholds ~25% below freehold,
    far steeper than Bala's 7%) turned into the naive RETURN bet: decay drags
    short leases, so avoid them. Freehold scores inf — no decay — which makes
    hit@25 here read as "25 freeholds in panel order", i.e. buy-freehold-at-
    random; the tie is real, not a bug.

    Measured on this panel it INVERTS on DEV: rho -0.113 [-0.217,+0.027],
    +0.030 [-0.133,+0.190] VAL, significant nowhere. Short-lease stock did
    not lag its district over these 1-year windows — within leaseholds
    remaining_lease -> excess_fwd is -0.140 [-0.252,+0.006] DEV / -0.067 VAL,
    i.e. the decay drag the literature documents in price LEVELS is invisible
    (sign-flipped, even) in 1-year relative returns inside one bull market.
    What it does expose: cheapness and short lease are nearly one feature
    here — rho(-psf_vs_dist, remaining_lease) = -0.757 DEV / -0.767 VAL among
    leaseholds — so the psf_vs_dist baseline is, to first order, a
    short-lease bet wearing a value label.
    """
    k = r.get("tenure_kind")
    if k == "freehold":
        return math.inf
    if k == "leasehold":
        return r.get("remaining_lease")
    return None


def enbloc_leasehold_age(r):
    """Old leasehold first, freehold neutral — the collective-sale interaction.

    Sia 2022's age dummies flip sign by tenure: leasehold age 31-40 carries
    +0.328 (five-fold the 21-30 band's +0.0638, attributed to en-bloc
    speculation) while freehold age runs negative; JHE 2023 puts the
    redevelopment-optionality premium at 20-29%. So age is an interaction,
    not a main effect, and this scores it as one: leasehold age counts up,
    freehold pins to 0. Two stated approximations: URA has no completion date,
    so freehold age does not exist here and the freehold half of the
    interaction goes untested; and true optionality is GPR slack, which needs
    the Master Plan — age is its computable shadow.

    Collinearity warning: 99-year terms dominate, so within leaseholds this
    is remaining_lease with the sign flipped (age = 99 - remaining). The two
    drivers are one axis on this panel, separated only by where freehold sits
    — which is exactly why lease_decay and this cannot both look good.

    Measured: rho +0.111 [-0.034,+0.216] DEV, -0.020 [-0.189,+0.155] VAL.
    The DEV point estimate leans the way Sia's coefficients say (old
    leasehold outperforming), then the sign flips on VAL — a null, and the
    flip is the tell that the DEV lean was the 2024-2025 old-condo catch-up,
    not a durable driver.
    """
    k = r.get("tenure_kind")
    if k == "freehold":
        return 0.0
    if k == "leasehold":
        return r.get("lease_age")
    return None


# name -> (fn, is_baseline). Order is display order.
ALGOS = {
    "random": (random_scorer(), True),
    "district_median": (district_median, True),
    "psf_vs_dist": (psf_vs_dist, True),
    "mmr_composite": (mmr_composite, False),
    "trailing_cagr": (trailing_cagr, False),
    "lease_decay": (lease_decay, False),
    "enbloc_leasehold_age": (enbloc_leasehold_age, False),
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
