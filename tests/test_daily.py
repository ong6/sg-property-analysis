"""Tests for the daily scan — the AI gate, verdict readback, digest, store push."""

import json
import os
from datetime import datetime, timedelta

import daily


def _row(rid, score=700, beds=2, name="Test Condo", **kw):
    r = {"id": rid, "project_name": name, "district": "D15", "beds": beds,
         "price": 1_500_000, "psf": 2000.0, "score_1000": score,
         "url": f"https://pg/{rid}"}
    r.update(kw)
    return r


def _rec(rid, **kw):
    r = {"id": rid, "price": 1_500_000, "sqft": 750.0, "psf": 2000.0,
         "status": "active", "unit_group": "test condo|2|333", "ingest_flags": []}
    r.update(kw)
    return r


class TestGate:
    def test_above_gate_shortlisted_below_gate_rejected(self):
        rows = [_row("a", score=700), _row("b", score=649, name="Other Condo")]
        db = {"a": _rec("a"), "b": _rec("b")}
        short, rej = daily.select_candidates(rows, db, min_score=650)
        assert [c["id"] for c in short] == ["a"]
        assert [c["id"] for c in rej] == ["b"]
        assert "below gate" in rej[0]["gate_reason"]

    def test_shortlist_is_score_ordered_and_capped(self):
        rows = [_row(str(i), score=650 + i, name=f"Condo {i}") for i in range(6)]
        db = {str(i): _rec(str(i)) for i in range(6)}
        short, rej = daily.select_candidates(rows, db, min_score=650, max_ai=3)
        assert [c["id"] for c in short] == ["5", "4", "3"]     # best first
        assert len(rej) == 3
        assert all("over the daily AI cap" in c["gate_reason"] for c in rej)

    def test_same_condo_same_beds_deduped_within_the_day(self):
        rows = [_row("a", score=700), _row("b", score=690)]   # same name + beds
        db = {"a": _rec("a"), "b": _rec("b")}
        short, rej = daily.select_candidates(rows, db)
        assert [c["id"] for c in short] == ["a"]
        assert "already shortlisted today" in rej[0]["gate_reason"]

    def test_different_bed_count_is_a_separate_call(self):
        rows = [_row("a", score=700, beds=2), _row("b", score=690, beds=3)]
        db = {"a": _rec("a"), "b": _rec("b")}
        short, _ = daily.select_candidates(rows, db)
        assert {c["id"] for c in short} == {"a", "b"}

    def test_cooldown_blocks_same_unit_type_only(self):
        rows = [_row("a", score=700, beds=2), _row("b", score=700, beds=3)]
        db = {"a": _rec("a"), "b": _rec("b")}
        cooldown = {("test-condo", 2): "2026-07-01"}
        short, rej = daily.select_candidates(rows, db, cooldown_index=cooldown)
        assert [c["id"] for c in short] == ["b"]
        assert "cooldown" in rej[0]["gate_reason"]

    def test_wildcard_cooldown_blocks_every_bed_count(self):
        rows = [_row("a", beds=2), _row("b", beds=3)]
        db = {"a": _rec("a"), "b": _rec("b")}
        short, rej = daily.select_candidates(
            rows, db, cooldown_index={("test-condo", "*"): "2026-07-01"})
        assert short == []
        assert len(rej) == 2

    def test_stale_and_incomplete_records_never_reach_the_agent(self):
        rows = [_row("a", name="A"), _row("b", name="B"), _row("c", name="C")]
        db = {"a": _rec("a", status="stale"),
              "b": _rec("b", sqft=None),
              "c": _rec("c")}
        short, rej = daily.select_candidates(rows, db)
        assert [c["id"] for c in short] == ["c"]
        reasons = {c["id"]: c["gate_reason"] for c in rej}
        assert "stale" in reasons["a"]
        assert "incomplete" in reasons["b"]

    def test_unscored_row_is_rejected_not_crashed(self):
        rows = [_row("a", score=None)]
        short, rej = daily.select_candidates(rows, {"a": _rec("a")})
        assert short == [] and rej[0]["gate_reason"] == "not scored"

    def test_row_missing_from_db_is_rejected(self):
        # A listing scored in the poll but absent from the DB snapshot has no
        # price/sqft/psf to gate on — it must fall out, not raise.
        short, rej = daily.select_candidates([_row("ghost")], {})
        assert short == [] and "incomplete" in rej[0]["gate_reason"]

    def test_ingest_flags_ride_along_for_the_prompt(self):
        db = {"a": _rec("a", ingest_flags=["bedroom_sqft_mismatch"])}
        short, _ = daily.select_candidates([_row("a")], db)
        assert short[0]["ingest_flags"] == ["bedroom_sqft_mismatch"]
        assert "bedroom_sqft_mismatch" in daily._agent_prompt(short[0])
        assert "--from-db a" in daily._agent_prompt(short[0])


class TestCooldownIndex:
    def test_index_reads_recent_evals_and_ignores_old_ones(self, monkeypatch):
        now = datetime(2026, 7, 27)
        recent = (now - timedelta(days=5)).strftime("%Y-%m-%d")
        old = (now - timedelta(days=100)).strftime("%Y-%m-%d")
        monkeypatch.setattr(daily.eval_memory, "load_index",
                            lambda: {"condos": {"fresh-condo": {}, "old-condo": {}}})
        monkeypatch.setattr(daily.eval_memory, "load_condo", lambda slug: {
            "fresh-condo": {"history": [{"evaluated_at": recent, "as_of": {"beds": 2}}]},
            "old-condo": {"history": [{"evaluated_at": old, "as_of": {"beds": 2}}]},
        }[slug])
        idx = daily._eval_cooldown_index(30, now)
        assert idx == {("fresh-condo", 2): recent}

    def test_entry_without_beds_becomes_a_wildcard(self, monkeypatch):
        now = datetime(2026, 7, 27)
        when = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        monkeypatch.setattr(daily.eval_memory, "load_index",
                            lambda: {"condos": {"c": {}}})
        monkeypatch.setattr(daily.eval_memory, "load_condo",
                            lambda slug: {"history": [{"evaluated_at": when, "as_of": {}}]})
        assert daily._eval_cooldown_index(30, now) == {("c", "*"): when}

    def test_legacy_freetext_as_of_degrades_to_wildcard(self, monkeypatch):
        # Real eval memory holds entries whose `as_of` is a re-judgment note
        # rather than the {price, beds, …} dict — must not raise.
        now = datetime(2026, 7, 27)
        when = (now - timedelta(days=2)).strftime("%Y-%m-%d")
        monkeypatch.setattr(daily.eval_memory, "load_index",
                            lambda: {"condos": {"c": {}}})
        monkeypatch.setattr(daily.eval_memory, "load_condo", lambda slug: {
            "history": [{"evaluated_at": when, "as_of": "2026-06 re-judgment under v3.10b"}]})
        assert daily._eval_cooldown_index(30, now) == {("c", "*"): when}

    def test_junk_history_entries_are_skipped(self, monkeypatch):
        now = datetime(2026, 7, 27)
        monkeypatch.setattr(daily.eval_memory, "load_index",
                            lambda: {"condos": {"c": {}}})
        monkeypatch.setattr(daily.eval_memory, "load_condo", lambda slug: {
            "history": ["a string", None, {"evaluated_at": None}]})
        assert daily._eval_cooldown_index(30, now) == {}

    def test_broken_index_does_not_stop_the_scan(self, monkeypatch):
        def boom():
            raise OSError("index gone")
        monkeypatch.setattr(daily.eval_memory, "load_index", boom)
        assert daily._eval_cooldown_index(30, datetime(2026, 7, 27)) == {}


class TestVerdictReadback:
    def _hist(self, url, rating="Buy", when="2026-07-27"):
        return {"evaluated_at": when, "rating": rating, "confidence": "high",
                "summary": "s", "source": {"url": url}}

    def test_matches_on_listing_url_not_just_the_condo(self, monkeypatch):
        monkeypatch.setattr(daily.eval_memory, "load_condo", lambda slug: {"history": [
            self._hist("https://pg/other", rating="Avoid"),
            self._hist("https://pg/a", rating="Buy"),
        ]})
        v = daily.read_verdict({"id": "a", "slug": "c", "url": "https://pg/a"},
                               on_or_after="2026-07-27")
        assert v["rating"] == "Buy"

    def test_falls_back_to_newest_entry_of_the_day(self, monkeypatch):
        monkeypatch.setattr(daily.eval_memory, "load_condo", lambda slug: {
            "history": [self._hist(None, rating="Neutral")]})
        v = daily.read_verdict({"id": "a", "slug": "c", "url": "https://pg/a"},
                               on_or_after="2026-07-27")
        assert v["rating"] == "Neutral"

    def test_ignores_evaluations_predating_this_run(self, monkeypatch):
        monkeypatch.setattr(daily.eval_memory, "load_condo", lambda slug: {
            "history": [self._hist("https://pg/a", when="2026-07-20")]})
        assert daily.read_verdict({"id": "a", "slug": "c", "url": "https://pg/a"},
                                  on_or_after="2026-07-27") is None

    def test_no_memory_file_returns_none(self, monkeypatch):
        monkeypatch.setattr(daily.eval_memory, "load_condo", lambda slug: None)
        assert daily.read_verdict({"id": "a", "slug": "c"}, "2026-07-27") is None


REAL_NOTE = """---
title: Daily finds
updated: 2026-07-01
---

# Daily finds

Some intro prose.

| Date | Project | Type | Price · District | Algo | AI verdict | Summary |
|---|---|---|---|---|---|---|

## What to do with a row

1. Sanity-check it yourself.
"""


class TestRowInsertion:
    def test_rows_land_in_the_table_not_after_the_trailing_prose(self):
        out = daily._insert_rows(REAL_NOTE, ["| ROW |"])
        lines = out.split("\n")
        assert lines[lines.index("|---|---|---|---|---|---|---|") + 1] == "| ROW |"
        # the prose that follows the table must survive, still below it
        assert out.index("| ROW |") < out.index("## What to do with a row")
        assert "1. Sanity-check it yourself." in out

    def test_newest_row_goes_on_top(self):
        once = daily._insert_rows(REAL_NOTE, ["| OLD |"])
        twice = daily._insert_rows(once, ["| NEW |"])
        assert twice.index("| NEW |") < twice.index("| OLD |")

    def test_note_without_a_table_falls_back_to_appending(self):
        out = daily._insert_rows("# Title\n\nprose\n", ["| ROW |"])
        assert out.endswith("| ROW |\n")

    def test_separator_alone_is_not_mistaken_for_a_table(self):
        # A horizontal rule or stray pipe line with no header row above it.
        out = daily._insert_rows("# T\n\n|---|---|\n\nprose\n", ["| ROW |"])
        assert out.endswith("| ROW |\n")


class TestStorePush:
    def _note(self, tmp_path, body=REAL_NOTE):
        p = tmp_path / "daily-finds.md"
        p.write_text(body)
        return str(p)

    def _cand(self, rid="a"):
        return {"id": rid, "project_name": "Test Condo", "beds": 2, "sqft": 750,
                "price": 1_500_000, "psf": 2000.0, "district": "D15",
                "score_1000": 700, "url": f"https://pg/{rid}"}

    def test_only_buy_grade_at_decent_confidence_is_pushed(self, tmp_path):
        path = self._note(tmp_path)
        shortlist = [self._cand("a"), self._cand("b"), self._cand("c"), self._cand("d")]
        verdicts = {
            "a": {"rating": "Buy", "confidence": "high", "summary": "yes"},
            "b": {"rating": "Neutral", "confidence": "high", "summary": "meh"},
            "c": {"rating": "Buy", "confidence": "low", "summary": "unsure"},
            "d": {"rating": "Avoid", "confidence": "high", "summary": "no"},
        }
        added = daily.push_to_store("2026-07-27", shortlist, verdicts, path=path)
        assert [x["url"] for x in added] == ["https://pg/a"]
        text = open(path).read()
        assert "https://pg/a" in text and "https://pg/b" not in text

    def test_strong_buy_counts_and_frontmatter_date_is_bumped(self, tmp_path):
        path = self._note(tmp_path)
        added = daily.push_to_store(
            "2026-07-27", [self._cand("a")],
            {"a": {"rating": "Strong Buy", "confidence": "medium", "summary": "s"}},
            path=path)
        assert len(added) == 1
        assert "updated: 2026-07-27" in open(path).read()

    def test_rerunning_a_day_does_not_duplicate_rows(self, tmp_path):
        path = self._note(tmp_path)
        v = {"a": {"rating": "Buy", "confidence": "high", "summary": "s"}}
        daily.push_to_store("2026-07-27", [self._cand("a")], v, path=path)
        again = daily.push_to_store("2026-07-27", [self._cand("a")], v, path=path)
        assert again == []
        assert open(path).read().count("https://pg/a") == 1

    def test_missing_note_is_skipped_never_created(self, tmp_path):
        path = str(tmp_path / "nope" / "daily-finds.md")
        added = daily.push_to_store(
            "2026-07-27", [self._cand("a")],
            {"a": {"rating": "Buy", "confidence": "high", "summary": "s"}}, path=path)
        assert added == [] and not os.path.exists(path)

    def test_pipes_and_newlines_cannot_break_the_table(self, tmp_path):
        path = self._note(tmp_path)
        daily.push_to_store(
            "2026-07-27", [self._cand("a")],
            {"a": {"rating": "Buy", "confidence": "high",
                   "summary": "a | b\nc"}}, path=path)
        row = [l for l in open(path).read().splitlines() if "https://pg/a" in l]
        assert len(row) == 1 and "\n" not in row[0]
        assert row[0].count("|") == 8   # 7 columns -> 8 delimiters, no injected cell


class TestDigest:
    def _digest(self, **kw):
        base = dict(day="2026-07-27",
                    poll_state={"ok": True, "districts": [15], "beds": [2],
                                "scraped": 40, "added": 3, "price_changes": 1,
                                "enriched": 3, "duration_s": 60,
                                "new_listings": [{}, {}, {}], "changed_listings": [{}]},
                    shortlist=[], rejected=[], verdicts={}, min_score=650,
                    max_ai=5, dry_run=False, pushed=[])
        base.update(kw)
        return daily.build_digest(**base)

    def test_reports_gate_and_counts(self):
        out = self._digest()
        assert "# Daily scan — 2026-07-27" in out
        assert "score_1000 >= 650" in out
        assert "3 new + 1 price-changed" in out

    def test_failed_poll_is_loud(self):
        out = self._digest(poll_state={"ok": False, "error": "CloudflareBlockedError: x"})
        assert "Poll FAILED" in out and "CloudflareBlockedError" in out

    def test_rejected_listings_are_listed_with_reasons(self):
        rej = [{"id": "b", "project_name": "B", "beds": 2, "price": 1e6,
                "psf": 1500, "district": "D15", "score_1000": 500,
                "gate_reason": "below gate (500 < 650)"}]
        out = self._digest(rejected=rej)
        assert "Algo-only — 1 not sent to the agent" in out
        assert "below gate (500 < 650)" in out

    def test_verdict_block_renders_rating_and_rationale(self):
        cand = {"id": "a", "project_name": "A", "beds": 2, "sqft": 750,
                "price": 1_500_000, "psf": 2000, "district": "D15",
                "score_1000": 700, "url": "https://pg/a", "slug": "a"}
        v = {"rating": "Buy", "confidence": "high", "summary": "good one",
             "rating_rationale": "because", "red_flags": ["lease"], "catalysts": []}
        out = self._digest(shortlist=[cand], verdicts={"a": v})
        assert "🟢 Buy — A" in out and "good one" in out
        assert "**Why:** because" in out and "- lease" in out

    def test_shortlisted_run_that_saved_nothing_is_shown_not_hidden(self):
        cand = {"id": "a", "project_name": "A", "beds": 2, "price": 1e6,
                "psf": 1500, "district": "D15", "score_1000": 700, "slug": "a"}
        out = self._digest(shortlist=[cand], verdicts={"b": {"rating": "Buy"}})
        assert "No verdict saved" in out


class TestDayGuard:
    def test_successful_day_blocks_a_second_run(self):
        state = {"last_run": {"date": "2026-07-27", "poll_ok": True}}
        assert daily.already_done_today(state, "2026-07-27")

    def test_failed_poll_leaves_the_day_open_for_a_retry(self):
        state = {"last_run": {"date": "2026-07-27", "poll_ok": False}}
        assert not daily.already_done_today(state, "2026-07-27")

    def test_new_day_always_runs(self):
        state = {"last_run": {"date": "2026-07-26", "poll_ok": True}}
        assert not daily.already_done_today(state, "2026-07-27")

    def test_no_state_yet(self):
        assert not daily.already_done_today({}, "2026-07-27")
