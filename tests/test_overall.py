"""Tests for the purpose-weighted Overall combiner (Phase 4)."""

from scoring.overall import compute_overall, normalize_purpose


def test_all_neutral_is_50():
    o = compute_overall()
    assert o["own_stay"] == o["investment"] == 50


def test_dream_home_owns_stay_beats_investment():
    # great home (liv 85), fairly priced (50), weak investment (score 420)
    o = compute_overall(valuation=50, livability=85, score_1000=420)
    assert o["own_stay"] > o["investment"]


def test_investor_unit_investment_beats_own_stay():
    # cheap (val 78), strong investment (700), middling home (liv 45)
    o = compute_overall(valuation=78, livability=45, score_1000=700)
    assert o["investment"] > o["own_stay"]


def test_valuation_not_in_investment_overall():
    # valuation is its own axis (≈MMR's own value signal); it must NOT re-enter
    # the investment overall (= MMR + capped livability floor). Own-stay still
    # uses it, so changing valuation moves own_stay but not investment.
    a = compute_overall(valuation=30, livability=60, score_1000=600)
    b = compute_overall(valuation=90, livability=60, score_1000=600)
    assert a["investment"] == b["investment"]
    assert b["own_stay"] > a["own_stay"]


def test_investment_overall_tracks_mmr_plus_floor():
    # with neutral livability, the investment overall is just MMR rescaled
    o = compute_overall(valuation=10, livability=50, score_1000=700)
    assert o["investment"] == 70  # 700/10 + 0 floor; valuation 10 (dear) ignored


def test_livability_demand_floor_is_capped():
    # going from a great-to-live (85) to a perfect (100) home must NOT raise the
    # investment overall further — livability's contribution there is capped.
    a = compute_overall(valuation=60, livability=85, score_1000=600)["investment"]
    b = compute_overall(valuation=60, livability=100, score_1000=600)["investment"]
    assert a == b


def test_livability_floor_downside():
    # a poor-to-live unit drags the investment overall down (exit risk), bounded
    good = compute_overall(valuation=60, livability=70, score_1000=600)["investment"]
    poor = compute_overall(valuation=60, livability=20, score_1000=600)["investment"]
    assert poor < good


def test_livability_never_dominates_investment():
    # max livability cannot turn a weak investment into a strong overall
    o = compute_overall(valuation=40, livability=100, score_1000=400)
    assert o["investment"] <= 55   # capped floor (+6) on a 40 base (MMR), not a takeover


def test_headline_follows_purpose():
    o_inv = compute_overall(valuation=60, livability=80, score_1000=500, purpose="investment")
    assert o_inv["headline"] == o_inv["investment"]
    o_own = compute_overall(valuation=60, livability=80, score_1000=500, purpose="own_stay")
    assert o_own["headline"] == o_own["own_stay"]


def test_purpose_normalization():
    assert normalize_purpose("own-stay") == "own_stay"
    assert normalize_purpose("ownstay") == "own_stay"
    assert normalize_purpose("home") == "own_stay"
    assert normalize_purpose("investment") == "investment"
    assert normalize_purpose(None) == "investment"
    assert normalize_purpose("anything else") == "investment"


def test_missing_score_is_neutral_not_zero():
    # unscored investment axis must not crater the overall
    o = compute_overall(valuation=60, livability=60, score_1000=None)
    assert o["investment"] > 50  # neutral inv 50 + small livability floor (+2)
