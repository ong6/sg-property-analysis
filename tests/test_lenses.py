"""Tests for the multi-scorer lens registry.

The load-bearing test in this file is the golden one: with only `mmr`
registered, the gate must pick EXACTLY what the pre-lens gate picked. A
refactor that changes which condos get researched, while claiming to be a
refactor, is the failure mode worth spending a 4,422-row fixture on.

Everything else here guards a specific way the registry could rot quietly:
a lens's calibration leaking into config.py (which would restamp all 9,029
stored scores), lens rows landing in mmr_history.csv (which would pollute the
cohorts calibrate_forward waits until ~2027-08 to read), or a partial scoring
pass clobbering a lens that did not run.
"""

import json
import os

import pytest

import config
import weekly
from scoring import lenses

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "gate_golden_pre_lenses.json")


@pytest.fixture
def golden():
    with open(FIXTURE) as f:
        return json.load(f)


def _listing(**kw):
    from scoring.models import ScoredListing
    base = {"id": "x", "title": "Test Condo", "price": 1_400_000,
            "url": "https://pg/x"}
    base.update(kw)
    return ScoredListing(**base)


# The ONE gate_reason whose wording changed deliberately when lenses landed.
# The fixture was captured pre-lens, so the golden test normalises this phrase
# and nothing else: a rejection now names the lens and its bar, because with
# several lenses able to nominate, "which one said no, and by how much" is the
# whole content of the rejection. Any OTHER reason drifting is a real
# regression and must still fail.
_PRE_LENS = "below every lens's bar (mmr "


def _normalise(reason: str) -> str:
    # Only the SINGLE-lens form maps back. A reason listing several lenses
    # ("mmr 400 < 650 · exit_record 12 < 88") has no pre-lens equivalent at
    # all, so rewriting it would invent a fixture entry that never existed.
    if reason.startswith(_PRE_LENS) and "·" not in reason:
        return "below gate (" + reason[len(_PRE_LENS):]
    return reason


@pytest.fixture(autouse=True)
def _only_mmr():
    """Every test starts from the shipped registry; those that add a lens
    restore it afterwards, so ordering between tests cannot leak."""
    saved = dict(lenses.LENSES)
    yield
    lenses.LENSES.clear()
    lenses.LENSES.update(saved)


class TestGoldenEquivalence:
    """mmr-only must reproduce the pre-lens gate exactly."""

    def test_the_gate_picks_exactly_what_it_used_to(self, golden):
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        recs = {k: {**v, "last_seen": today} if v.get("last_seen") == "__TODAY__" else v
                for k, v in golden["recs"].items()}
        for max_ai, exp in golden["expected"].items():
            short, rej = weekly.select_candidates(
                golden["rows"], recs, min_score=golden["min_score"],
                max_ai=int(max_ai))
            assert [c["id"] for c in short] == exp["shortlist_ids"], (
                f"max_ai={max_ai}: the lens gate changed WHICH listings get researched")
            got = {c["id"]: _normalise(c["gate_reason"]) for c in rej}
            for lid, reason in exp["rejected"].items():
                assert got.get(lid) == reason, f"max_ai={max_ai}, {lid}"

    def test_the_normaliser_only_touches_the_phrase_it_claims_to(self):
        # Guard on the guard: if _normalise ever swallowed a real reason, the
        # golden test above would go quietly blind.
        assert _normalise("below every lens's bar (mmr 649 < 650)") == \
            "below gate (649 < 650)"
        for other in ("listing went stale this cycle",
                      "outside the mandate (1BR @ $900,000 — need 3BR<=$1.8M)",
                      "same condo + bed count already shortlisted this scan",
                      "below every lens's bar (mmr 400 < 650 · exit_record 12 < 88)"):
            assert _normalise(other) == other

    def test_the_mmr_lens_is_a_pure_pass_through(self):
        # The wrapper must add nothing: same components, same headline score.
        from scoring.mmr import compute_mmr

        s = _listing()
        direct = compute_mmr(s)
        via = lenses.LENSES["mmr"].score(s, None)
        assert via["score"] == direct["score_1000"]
        assert via["mmr"] == direct["mmr"]
        assert via["components"] == direct["components"]

    def test_mmrs_bar_stays_absolute(self):
        # mmr keeps the 650 tier tradition and the --min-score knob; only
        # lenses without one get percentile-derived bars.
        book = {str(i): {"score_1000": i, "status": "active"} for i in range(200)}
        assert weekly.lens_bars(book, min_score=650)["mmr"] == 650
        assert weekly.lens_bars(book, min_score=700)["mmr"] == 700


class TestVersioning:
    def test_adding_a_lens_does_not_restamp_the_book(self):
        """A lens's calibration must never live in config.py.

        config.py is sha-hashed into score_version, so one constant there
        restamps the vintage of all 9,029 scored listings and splits the
        registered cohort whose first forward read is ~2027-08.
        """
        before = config.score_version()
        lenses.register(lenses.Lens(
            name="toy", score=lambda s, r: {"score": 1.0, "components": {}},
            version=lambda: "toy-1.0", gate_percentile=0.9))
        assert config.score_version() == before

    def test_the_mmr_lens_versions_off_config(self):
        assert lenses.LENSES["mmr"].version() == config.score_version()

    def test_a_fingerprint_ignores_comments(self, tmp_path):
        a = tmp_path / "a.py"
        a.write_text("X = 1\n")
        first = lenses.module_fingerprint(str(a), "t-1")
        a.write_text("# a comment\n\nX = 1\n")
        assert lenses.module_fingerprint(str(a), "t-1") == first
        a.write_text("X = 2\n")
        assert lenses.module_fingerprint(str(a), "t-1") != first

    def test_an_unreadable_module_does_not_raise(self):
        assert lenses.module_fingerprint("/nope/nope.py", "t-1").endswith("unknown")


class TestStorage:
    def test_merging_never_clobbers_a_lens_that_did_not_run(self):
        rec = {"lens_scores": {"exit_record": {"score": 9.0, "version": "e-1"}}}
        lenses.merge_lens_scores(rec, {"mmr": {"score": 700, "version": "m-1"}})
        assert set(rec["lens_scores"]) == {"exit_record", "mmr"}
        assert rec["lens_scores"]["exit_record"]["score"] == 9.0

    def test_an_empty_merge_is_a_no_op(self):
        rec = {"lens_scores": {"mmr": {"score": 700}}}
        lenses.merge_lens_scores(rec, None)
        assert rec["lens_scores"]["mmr"]["score"] == 700

    def test_mmr_reads_from_the_flat_field(self):
        # score_1000 is mmr's canonical store — SHEET_COLUMNS, ui.py, weekly
        # and shortlist all read it, and it has full book coverage.
        assert lenses.lens_score_of({"score_1000": 812}, "mmr") == 812
        assert lenses.lens_score_of({"lens_scores": {"x": {"score": 5}}}, "x") == 5
        assert lenses.lens_score_of({}, "x") is None

    def test_history_goes_to_its_own_file(self, tmp_path):
        """Never mmr_history.csv — calibrate_forward cohorts off that file."""
        p = tmp_path / "lens_history.csv"
        lenses.append_history([{"scored_at": "2026-07-31", "lens": "mmr", "id": "1",
                                "project_name": "X", "district": "D19", "beds": 3,
                                "price": 1, "psf": 1, "score": 700,
                                "rank_norm": 0.9, "lens_version": "m-1"}], str(p))
        head, row = p.read_text().splitlines()
        assert head.split(",")[:2] == ["scored_at", "lens"]
        assert row.split(",")[1] == "mmr"

    def test_an_empty_history_write_creates_nothing(self, tmp_path):
        p = tmp_path / "none.csv"
        lenses.append_history([], str(p))
        assert not p.exists()


class TestRanking:
    def test_rank_norm_places_a_score_in_the_book(self):
        book = list(range(100))
        assert lenses.rank_norm(50, book) == pytest.approx(0.51, abs=0.02)
        assert lenses.rank_norm(99, book) == 1.0

    def test_a_thin_book_yields_no_rank(self):
        # A percentile over a handful of scores is noise wearing a
        # percentile's clothes, and a noisy bar would let a brand-new lens
        # nominate almost anything.
        assert lenses.rank_norm(5, [1, 2, 3]) is None

    def test_stale_listings_are_not_part_of_the_book(self):
        recs = [{"score_1000": 700, "status": "active"},
                {"score_1000": 900, "status": "stale"}]
        assert lenses.book_scores(recs)["mmr"] == [700]


class TestSecondLens:
    def test_a_second_lens_can_nominate_what_mmr_rejects(self):
        """The whole point: a condo mmr dislikes still reaches the agent."""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")

        lenses.register(lenses.Lens(
            name="exit_record",
            score=lambda s, r: {"score": 99.0, "components": {}},
            version=lambda: "e-1.0", gate_percentile=0.5))

        rows = [{"id": "a", "project_name": "Good Exit Condo", "district": "D19",
                 "beds": 3, "price": 1_400_000, "psf": 1200.0, "score_1000": 400,
                 "url": "https://pg/a"}]
        # A book thick enough for the new lens to hold a bar at all.
        db = {"a": {"id": "a", "price": 1_400_000, "sqft": 1166.0, "psf": 1200.0,
                    "status": "active", "last_seen": today, "ingest_flags": [],
                    "score_1000": 400,
                    "lens_scores": {"exit_record": {"score": 99.0, "version": "e-1.0"}}}}
        for i in range(40):
            db[f"f{i}"] = {"id": f"f{i}", "status": "active", "score_1000": 300 + i,
                           "lens_scores": {"exit_record": {"score": float(i)}}}

        short, rej = weekly.select_candidates(rows, db, min_score=650)
        assert [c["id"] for c in short] == ["a"], (
            "a 400-scoring listing with a top exit record must still be researched")
        assert "exit_record" in (short[0].get("surfaced_by") or [])

    def test_a_lens_that_raises_does_not_sink_the_pass(self):
        def boom(scored, record):
            raise RuntimeError("lens exploded")

        lenses.register(lenses.Lens(name="broken", score=boom,
                                    version=lambda: "b-1", gate_percentile=0.9))
        out = lenses.score_record(_listing(), {})
        assert "mmr" in out and "broken" not in out

    def test_a_lens_that_abstains_is_omitted_not_zeroed(self):
        lenses.register(lenses.Lens(
            name="abstains", score=lambda s, r: {"score": None, "components": {}},
            version=lambda: "a-1", gate_percentile=0.9))
        assert "abstains" not in lenses.score_record(_listing(), {})
