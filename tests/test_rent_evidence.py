"""Rent evidence end-to-end: estimator -> FullScorer -> raw_analysis.json.

The AI agent judges whether a yield is trustworthy from
`factual_data.rental.rent_evidence` — a gross yield above ~5.5% is a
verify-first artifact signal, and the contract count is what lets the agent
verify it. The estimator computed that evidence but FullScorer dropped
everything except `confidence`, so every listing's rent_evidence reported
"no evidence" (34 blocks across output/, 0 with a contract count) and the
`capped_note` branch in raw_output was unreachable.

These tests pin the whole chain: the fields must reach the JSON, the cap note
must fire when the cap engages, absent evidence must stay honestly absent —
and none of it may touch a score.
"""

import copy

import scoring.rental_estimator as re_mod
from scoring.full_scorer import FullScorer
from scoring.mmr import compute_mmr
from scoring.raw_output import generate_raw_analysis

CACHE = {
    "testview residences|15": {
        "project_name": "TESTVIEW RESIDENCES",
        "district": "D15",
        "rent_psf": 5.0,
        "contracts": 24,
        "window_months": 12,
        "by_beds": {"2": {"monthly_rent": 3800, "rent_psf": 5.4, "contracts": 11}},
    }
}

LISTING = {
    "id": "t1",
    "title": "Testview Residences 2BR",
    "project_name": "Testview Residences",
    "district": "D15",
    "beds": 2,
    "baths": 2,
    "sqft": 700,
    "price": 1_400_000,
    "psf": 2000,
    "tenure": "Freehold",
    "url": "https://example.test/t1",
}


def _with_cache(monkeypatch, cache):
    monkeypatch.setattr(re_mod, "_rental_cache", cache)
    monkeypatch.setattr(re_mod, "_rental_cache_stale", False)


def _score(listing):
    return FullScorer(ura_data={}, cohort_stats={}).score(copy.deepcopy(listing))


def _evidence(scored):
    raw = generate_raw_analysis([scored], {}, {})
    return raw["listings"][0]["factual_data"]["rental"]["rent_evidence"]


def test_rent_evidence_reaches_raw_analysis(monkeypatch):
    """The regression: a cache-backed rent must carry its contract depth all
    the way into raw_analysis.json, not just its confidence."""
    _with_cache(monkeypatch, CACHE)
    scored = _score(LISTING)

    assert scored.rent_source == "ura_project_bed"
    assert scored.rent_contracts == 11        # the bed-matched contract count
    assert scored.rent_window_months == 12
    assert scored.rent_capped is False

    ev = _evidence(scored)
    assert ev["contracts"] == 11
    assert ev["window_months"] == 12
    assert ev["confidence"] == 0.95
    assert ev["capped"] is False
    assert "capped_note" not in ev


def test_project_pooled_rent_carries_project_contracts(monkeypatch):
    """No bed-matched band -> the pooled project median, and the evidence
    reports the POOLED contract count (24), not the bed one."""
    _with_cache(monkeypatch, CACHE)
    scored = _score({**LISTING, "beds": 4, "sqft": 1500})
    assert scored.rent_source == "ura_project"
    ev = _evidence(scored)
    assert ev["contracts"] == 24
    assert ev["window_months"] == 12


def test_capped_note_fires_when_the_sqft_cap_engages(monkeypatch):
    """A 1,086 sqft '1BR' is the artifact the v3.6.2 cap exists for. The cap
    engaged all along; the note announcing it could never render."""
    _with_cache(monkeypatch, {})
    scored = _score({**LISTING, "beds": 1, "sqft": 1086, "psf": 1289})

    assert scored.rent_capped is True
    ev = _evidence(scored)
    assert ev["capped"] is True
    assert "capped_note" in ev
    assert "verify the unit format" in ev["capped_note"]


def test_absent_evidence_stays_honestly_absent(monkeypatch):
    """A district-median rent has no contracts behind it — the block must say
    so (None), not fabricate depth, and must not carry the cap note."""
    _with_cache(monkeypatch, {})
    scored = _score(LISTING)

    assert scored.rent_source == "district_bedroom"
    ev = _evidence(scored)
    assert ev["source"] == "district_bedroom"
    assert ev["contracts"] is None
    assert ev["window_months"] is None
    assert ev["capped"] is False
    assert "capped_note" not in ev


def test_older_estimator_without_evidence_does_not_crash():
    """Defensive contract (same shape as rent_confidence): an estimator that
    emits no evidence leaves the fields at their neutral defaults."""
    from types import SimpleNamespace

    sc = FullScorer.__new__(FullScorer)
    sc.cohort_stats = {}
    sc.rental_estimator = SimpleNamespace(estimate_yield_score=lambda l: {
        "monthly_rent": 4000, "gross_yield": 3.2, "gross_yield_score": 6,
        "source": "ura_project_bed"})
    scored = SimpleNamespace(mrt_distance_m=None, nearest_mrt=None)
    sc._score_rental_yield({"beds": 2}, scored)
    assert scored.rent_contracts is None
    assert scored.rent_window_months is None
    assert scored.rent_capped is False


def test_rent_evidence_is_metadata_never_a_score_input(monkeypatch):
    """rent_evidence describes the rent BASIS; it must not move a score.
    Mutating every evidence field must leave the MMR bit-identical."""
    _with_cache(monkeypatch, CACHE)
    scored = _score(LISTING)
    before = compute_mmr(scored)

    scored.rent_contracts = 999
    scored.rent_window_months = 24
    scored.rent_capped = True
    after = compute_mmr(scored)

    assert after["mmr"] == before["mmr"]
    assert after["score_1000"] == before["score_1000"]
    assert after["components"] == before["components"]


def test_yield_and_rent_unchanged_by_the_passthrough(monkeypatch):
    """Guard the no-score-change promise at the source: the same inputs must
    still produce the same rent, yield and rental_yield points."""
    _with_cache(monkeypatch, CACHE)
    scored = _score(LISTING)
    assert scored.estimated_monthly_rent == 3800      # bed-matched median, served direct
    assert scored.estimated_gross_yield == round(3800 * 12 / 1_400_000 * 100, 2)
    assert scored.rental_yield_score == scored.score_breakdown["rental_yield"]["total"]
