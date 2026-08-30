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


# ---- exit demand (Eric Chiew's "always think about exit first") -------------
#
# His computable criteria in his order of importance: (1) profitable:
# unprofitable resale-pair ratio, (2) recent transaction volume, (3) listing
# scarcity, (4) family unit mix, (5) age-adjusted comparable psf, (6) the
# boutique-freehold worst-combination flag. (3) has no historical ground truth
# here — the panel is URA caveats and there is no archive of live listing
# counts as of 2024 — so like MMR's ask-vs-comps channel it is testable only
# prospectively, and this harness does not pretend otherwise. (5) was measured
# and found null (rho +0.043 [-0.095,+0.240] DEV, crediting $50/psf-year
# against the district's median project age), so it is not carried.

# Fewer pairs than this and the record is an anecdote: 3 all-profitable pairs
# smooth to 0.71, well below a 20/1 record at 0.88 — enough evidence to rank,
# not enough to crown.
EXIT_MIN_PAIRS = 3
# Laplace prior. Uninformative on purpose (centered at 0.5, NOT at the 84%
# bull-market base rate): the panel target is district-relative, and shrinking
# toward the market-wide base rate would make thin records free-ride the bull
# market the target exists to difference away.
EXIT_PAIR_ALPHA = 2.0


def exit_pair_record(r):
    """Chiew #1: smoothed share of the project's matched-pair exits that made
    money as of T.

    His gold/red-flag examples (Tampines Trilliant 262/0 vs Reflections
    125/182) order correctly under the smoothing: 262/0 -> 0.99, 5/0 -> 0.78,
    125/182 -> 0.41. This is the DE-CORRELATED core of the framework — on DEV
    its rank correlation with psf_vs_dist is -0.06, i.e. it ranks on an axis
    single-feature cheapness does not see at all. And the pseudo-pairs track
    ground truth: rho +0.77 (n=21) against realsmart's true pct_profitable
    for the projects both sides cover.

    Measured: rho +0.093 [-0.045,+0.208] DEV, +0.076 [-0.040,+0.217] VAL —
    positive both splits, significant neither. Alone it is not a return
    predictor at this horizon; its value is the orthogonality above.
    """
    p, u = r.get("pair_profit"), r.get("pair_loss")
    if p is None or u is None or p + u < EXIT_MIN_PAIRS:
        return None
    return (p + EXIT_PAIR_ALPHA) / (p + u + 2 * EXIT_PAIR_ALPHA)


def exit_liquidity_6m(r):
    """Chiew #2: resale count in the last 6 months — "1-2 transactions means
    no demand and no exit".

    Two honesty notes. The panel's min_txn=5 filter already removes the
    illiquid left tail he is warning about, so this tests the criterion only
    over projects that clear a basic liquidity floor. And the per-unit variant
    (turnover per 100 units) measured significantly NEGATIVE on DEV (rho
    -0.092 [-0.175,-0.001]) — high churn reads as investor stock, not demand —
    so the raw count is scored exactly as he states it, and the harness is
    left to say whether it carries anything.

    Measured: rho -0.017 [-0.164,+0.093] DEV, +0.098 [-0.008,+0.183] VAL — a
    null. On projects liquid enough to enter the panel, MORE liquidity buys
    no extra relative return.
    """
    v = r.get("resale_vol_6m")
    return None if v is None else float(v)


def exit_family_mix(r):
    """Chiew #4: share of the project's pre-T resale stock at >= 900 sqft —
    family stock versus 1-2BR "investor projects".

    Measured: rho +0.134 [+0.054,+0.216] DEV (significant), +0.036
    [-0.076,+0.177] VAL — the family tilt was real in the 2024-2025 window
    and faded in 2025-2026 (degradation 0.27, flagged). Note it is the
    composite's most mmr-correlated input (+0.50 vs mmr_composite ranks on
    DEV), so it dilutes the pair record's orthogonality as it earns its
    weight.
    """
    return r.get("family_share")


def _exit_boutique_ok(r):
    """1.0 unless the project trips Chiew's worst-combination flag: fewer than
    ~250 units AND freehold AND a sub-750sqft median unit. Missing unit count
    or sqft evidence means NO flag — absence of evidence is not a penalty."""
    if r.get("total_units") is None or r.get("median_sqft") is None:
        return 1.0
    boutique = (r["total_units"] < 250
                and r.get("tenure_kind") == "freehold"
                and r["median_sqft"] < 750)
    return 0.0 if boutique else 1.0


def exit_demand_composite(r):
    """The testable Chiew criteria assembled, his priority order as weights.

    All three components are already 0..1 shares, so this is a plain weighted
    sum — no cross-row ranking, which also means the identical arithmetic runs
    off a live listing's project history in scoring/exit_demand.py. Volume is
    deliberately NOT in the composite: measured null as a raw count and
    sign-inverted per unit (see exit_liquidity_6m), and paying weight for a
    null feature just dilutes the two that measure something.

    Measured: rho +0.159 [+0.045,+0.255] DEV (significant), +0.070
    [-0.063,+0.232] VAL (not), against a psf_vs_dist bar of +0.221/+0.066.
    The verdict is NEUTRAL: it does not beat single-feature cheapness
    in-split, it degrades like everything else (0.44, flagged), and its VAL
    hit@25 of 0.64 (p 0.109) is the table's best point estimate without being
    a finding. What it uniquely has is de-correlation — +0.18 against stored
    score_1000 on the live book — which is the property the lens registry
    exists for, and why this registered as a lens despite the null-vs-bar
    read (scoring/exit_demand.py).
    """
    pair = exit_pair_record(r)
    fam = r.get("family_share")
    if pair is None or fam is None:
        return None
    return 0.5 * pair + 0.3 * fam + 0.2 * _exit_boutique_ok(r)


# name -> (fn, is_baseline). Order is display order.
ALGOS = {
    "random": (random_scorer(), True),
    "district_median": (district_median, True),
    "psf_vs_dist": (psf_vs_dist, True),
    "mmr_composite": (mmr_composite, False),
    "trailing_cagr": (trailing_cagr, False),
    "lease_decay": (lease_decay, False),
    "enbloc_leasehold_age": (enbloc_leasehold_age, False),
    "exit_pair_record": (exit_pair_record, False),
    "exit_liquidity_6m": (exit_liquidity_6m, False),
    "exit_family_mix": (exit_family_mix, False),
    "exit_demand_composite": (exit_demand_composite, False),
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
