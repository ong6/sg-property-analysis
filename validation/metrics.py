#!/usr/bin/env python3
"""Metrics for ranking algorithms, each paired with the null that makes it mean
something.

The design rule here: no metric is reported without either a confidence
interval or a permutation p-value. A bare rho on 342 projects reads as a
finding, and at these sample sizes it very often is not one — the repo has
already been burnt once by quoting an r of -0.7 that was computed inside the
top 12% of its own score range and could not survive three more data points.
"""

from __future__ import annotations

import random

import backtest as bt

TOP_K = 25          # a viewing shortlist's worth
DECILES = 10


def spearman(xs, ys):
    """Rho only. bt._spearman returns (rho, n) — unpacked here so callers
    cannot accidentally use the tuple as a number."""
    rho, _n = bt._spearman(list(xs), list(ys))
    return rho


def spearman_ci(xs, ys, clusters, reps: int = 400, seed: int = 42):
    """(rho, lo, hi) with a cluster bootstrap CI.

    Clustered by district: projects in one district share a local market, so
    treating 342 projects as 342 independent draws overstates precision. The
    repo's existing bootstrap does this correctly and is reused so the two
    harnesses cannot silently disagree — but note its return shape is
    (lo, hi, n_clusters), NOT (rho, lo, hi). Reading it as the latter prints
    the lower bound as the estimate and the cluster COUNT as the upper bound,
    which is exactly what a "95% CI" of [+0.068, +26.000] was.
    """
    lo, hi, _k = bt._cluster_boot_rho(list(xs), list(ys), list(clusters),
                                      reps=reps, seed=seed)
    return spearman(xs, ys), lo, hi


def decile_lift(scores, outcomes, q: int = DECILES):
    """Mean outcome in the top bucket minus the bottom, plus monotonicity.

    `monotone_frac` is the share of adjacent bucket steps that go the right
    way. A high lift with low monotonicity is usually one lucky tail, not a
    ranking — which is exactly the shape an overfit model produces.
    """
    pairs = sorted(zip(scores, outcomes), key=lambda p: p[0])
    n = len(pairs)
    if n < q * 2:
        return None
    edges = [round(i * n / q) for i in range(q + 1)]
    buckets = [[o for _, o in pairs[edges[i]:edges[i + 1]]] for i in range(q)]
    means = [sum(b) / len(b) for b in buckets if b]
    if len(means) < q:
        return None
    steps = [means[i + 1] - means[i] for i in range(len(means) - 1)]
    return {
        "lift": means[-1] - means[0],
        "top_mean": means[-1],
        "bottom_mean": means[0],
        "monotone_frac": sum(1 for s in steps if s > 0) / len(steps),
        "bucket_means": means,
    }


def hit_rate_at_k(scores, outcomes, k: int = TOP_K, reps: int = 1000, seed: int = 42):
    """Share of the top-k that beat their district, against a permutation null.

    The null is the honest part. With ~50% of projects beating their district
    by construction, a top-25 hit rate of 56% looks like skill and is well
    inside what shuffling produces.
    """
    n = len(scores)
    if n < k:
        return None
    pairs = sorted(zip(scores, outcomes), key=lambda p: -p[0])
    observed = sum(1 for _, o in pairs[:k] if o > 0) / k

    outs = list(outcomes)
    rng = random.Random(seed)
    ge = 0
    for _ in range(reps):
        rng.shuffle(outs)
        if sum(1 for o in outs[:k] if o > 0) / k >= observed:
            ge += 1
    return {"hit_rate": observed, "k": k,
            "base_rate": sum(1 for o in outcomes if o > 0) / n,
            "p_value": (ge + 1) / (reps + 1)}


def evaluate(rows: list[dict], scores: list[float], reps: int = 400) -> dict:
    """Every metric for one algorithm on one panel."""
    out = [r["excess_fwd"] for r in rows]
    rho, lo, hi = spearman_ci(scores, out, [r["district"] for r in rows], reps=reps)
    return {
        "n": len(rows),
        "rho": rho, "rho_lo": lo, "rho_hi": hi,
        "rho_significant": (lo is not None and hi is not None
                            and (lo > 0 or hi < 0)),
        "decile": decile_lift(scores, out),
        "hit": hit_rate_at_k(scores, out),
    }


def degradation(dev: dict, val: dict) -> dict | None:
    """VAL rho over DEV rho — the overfit tell. Below ~0.6 the algorithm
    learned the DEV split rather than the market.

    Requires the DEV rho to be SIGNIFICANT, not merely non-zero. Dividing one
    noise estimate by another produces a number with the shape of a finding and
    none of the content: on the first real run this printed +4.34 for
    trailing_cagr and +0.68 for RANDOM, neither of which degraded from anything.
    An algorithm that never established a DEV signal cannot be shown to have
    lost it, and saying so is the honest output.
    """
    d, v = dev.get("rho"), val.get("rho")
    if d is None or v is None or not dev.get("rho_significant") or abs(d) < 0.05:
        return None
    ratio = v / d
    return {"ratio": ratio, "overfit_flag": ratio < 0.6}
