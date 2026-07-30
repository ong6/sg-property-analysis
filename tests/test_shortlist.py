"""Tests for the shortlist's collection and ranking.

The judgement worth pinning here is what counts as ONE thing on the buy list.
Keying on the condo name alone collapsed genuinely different products and let
the newest verdict speak for all of them: Rivervale Crest's 3BR at $1.268M
(Neutral, 82% of 571 resales profitable) disappeared behind a 1,668 sqft
penthouse marketed as a 4BR (Avoid), and Palm Gardens' 3BR behind its
mislabelled "4BR". The list was hiding the better of the two products.
"""

import json


import shortlist


def _eval_file(base, slug, condo, history):
    evals = base / "evaluations"
    evals.mkdir(exist_ok=True)
    path = evals / f"{slug}.json"
    path.write_text(json.dumps({"condo": condo, "slug": slug, "history": history}))
    return path


def _hist(evaluated_at, rating, url, **kw):
    h = {"evaluated_at": evaluated_at, "rating": rating, "confidence": "medium",
         "summary": f"{rating} summary.", "source": {"url": url}}
    h.update(kw)
    return h


class TestCollectKey:
    """One row per (condo, bed count), not per condo."""

    def _collect(self, monkeypatch, tmp_path, history, listings):
        _eval_file(tmp_path, "rivervale-crest", "Rivervale Crest", history)
        monkeypatch.setattr(shortlist, "BASE", str(tmp_path))
        monkeypatch.setattr(shortlist.rc, "load_cache", lambda: {})
        monkeypatch.setattr(shortlist.listings_db, "load_db",
                            lambda: {"listings": listings})
        return shortlist.collect("2026-07-01")

    def test_two_bed_counts_are_two_rows(self, monkeypatch, tmp_path):
        rows = self._collect(
            monkeypatch, tmp_path,
            [_hist("2026-07-29", "Neutral", "https://pg/3br"),
             _hist("2026-07-29", "Avoid", "https://pg/4br")],
            {"a": {"url": "https://pg/3br", "price": 1_268_000, "beds": 3,
                   "district": "D19", "sqft": 1206.0},
             "b": {"url": "https://pg/4br", "price": 1_880_000, "beds": 4,
                   "district": "D19", "sqft": 1668.0}},
        )
        by_beds = {r["beds"]: r["rating"] for r in rows}
        assert by_beds == {3: "Neutral", 4: "Avoid"}, (
            "the 4BR Avoid must not speak for the 3BR")

    def test_same_bed_count_keeps_only_the_newest(self, monkeypatch, tmp_path):
        rows = self._collect(
            monkeypatch, tmp_path,
            [_hist("2026-07-01", "Buy", "https://pg/old"),
             _hist("2026-07-29", "Avoid", "https://pg/new")],
            {"a": {"url": "https://pg/old", "price": 1_268_000, "beds": 3,
                   "district": "D19", "sqft": 1206.0},
             "b": {"url": "https://pg/new", "price": 1_268_000, "beds": 3,
                   "district": "D19", "sqft": 1206.0}},
        )
        assert [r["rating"] for r in rows] == ["Avoid"]


class TestRanking:
    """Verdict first, then exit record — never the algo score first."""

    @staticmethod
    def _r(**kw):
        r = {"condo": "C", "rating": "Neutral", "beds": 3, "pct_profitable": 90.0,
             "resale_txns": 400, "score_1000": 700, "evaluated_at": "2026-07-29"}
        r.update(kw)
        return r

    def test_verdict_outranks_everything(self):
        rows = [self._r(rating="Avoid", pct_profitable=100.0, score_1000=900),
                self._r(rating="Neutral", pct_profitable=50.0, score_1000=650)]
        assert [r["rating"] for r in sorted(rows, key=shortlist.rank_key)] == [
            "Neutral", "Avoid"]

    def test_profitability_breaks_ties_within_a_verdict(self):
        rows = [self._r(pct_profitable=80.0), self._r(pct_profitable=99.0)]
        got = [r["pct_profitable"] for r in sorted(rows, key=shortlist.rank_key)]
        assert got == [99.0, 80.0]
