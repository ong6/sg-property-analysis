"""Tests for the Valuation score (Phase 2 of the 3-score system).

Valuation answers "is this priced right TODAY?" (50 = fair, >50 = cheap for its
age vs peers, <50 = dear), built from the cheap-vs-peers signals already in
score_breakdown, and sharing MMR's artifact defences so a fake-cheap reading
cannot score high. Synthetic breakdowns — no scoring pipeline needed.
"""

from types import SimpleNamespace

from scoring.valuation import score_valuation


def _scored(rel=None, peer=0, psf=None, cohort=None, stack=None, flags=(), low_floor=None):
    rv = {}
    if rel is not None:
        rv["premium_vs_age_adjusted_median_pct"] = rel
    rv["peer_count"] = peer
    red = {"flags": [{"flag": f} for f in flags]}
    if psf is not None:
        red["psf_premium_pct"] = psf
    if cohort is not None:
        red["psf_cohort_txns"] = cohort
    if stack is not None:
        red["stack_premium_pct"] = stack
    if low_floor is not None:
        red["stack_low_floor_share"] = low_floor
    return SimpleNamespace(score_breakdown={"relative_value": rv, "red_flags": red})


def test_genuine_cheap_scores_high():
    # ~15% below the typical district ask and ~12% below same-size comps — a real
    # bargain on thick comps, scores firmly cheap.
    r = score_valuation(_scored(rel=-15, peer=12, psf=-12, cohort=20))
    assert 80 <= r["score"] <= 90
    assert r["confidence"] == "high"
    assert r["why"].startswith("cheap")


def test_dear_scores_low():
    # priced ~25% above the typical district ask (raw +35% vs transacted peers,
    # whose typical ask premium is ~+10%) — clearly dear.
    r = score_valuation(_scored(rel=35, peer=12))
    assert r["score"] <= 22
    assert r["why"].startswith("dear")


def test_fairly_priced_is_neutral():
    # ~+10% over transacted district peers IS the typical ask (measured DB
    # center), so a listing right at it is fairly priced — neutral, not cheap.
    r = score_valuation(_scored(rel=10, peer=12))
    assert 47 <= r["score"] <= 53


def test_suspect_deep_discount_is_damped():
    # a -40% "discount" on a thin cohort + bed/sqft mismatch must NOT score cheap
    r = score_valuation(_scored(rel=-40, peer=4, psf=-38, cohort=2,
                                flags=("bedroom_sqft_mismatch",)))
    assert r["score"] <= 62              # genuine -28% would be ~90; damped here
    assert "suspect" in r["why"]
    assert not r["why"].startswith("cheap")


def test_oversized_cheap_is_halved():
    full = score_valuation(_scored(rel=-20, peer=12))["score"]
    over = score_valuation(_scored(rel=-20, peer=12, flags=("oversized_unit",)))["score"]
    assert over < full
    assert "oversized" in score_valuation(
        _scored(rel=-20, peer=12, flags=("oversized_unit",)))["why"]


def test_low_floor_stack_damps_cheap():
    # a ground/low-floor stack (≥70% prints at 01-05) is cheap for a structural
    # reason, not a bargain — its cheap reading is halved and not sold as "cheap".
    full = score_valuation(_scored(rel=-20, peer=12))["score"]
    lf = score_valuation(_scored(rel=-20, peer=12, low_floor=1.0))
    assert lf["score"] < full
    assert "low-floor" in lf["why"]
    assert not lf["why"].startswith("cheap")


def test_print_contradiction_damps_cheap():
    # asks ABOVE its own stack prints (stack_premium > 5) => cheapness is artifact
    r = score_valuation(_scored(rel=-18, peer=12, stack=12))
    assert r["score"] <= 62


def test_thin_peers_pulled_toward_neutral():
    thin = score_valuation(_scored(rel=-15, peer=2))["score"]
    thick = score_valuation(_scored(rel=-15, peer=12))["score"]
    assert 50 < thin < thick            # still cheap-ish, but shrunk toward 50


def test_no_data_is_neutral():
    r = score_valuation(SimpleNamespace(score_breakdown={}))
    assert r["score"] == 50
    assert r["confidence"] is None
