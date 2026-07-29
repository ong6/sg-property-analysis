"""Tests for the shortlist viewer.

The thing worth testing here is not the markup, it is the *judgement* the page
encodes: which pairing of algo score and exit record counts as a value trap,
which projects survive to the viewing shortlist, and that the trust signals
(thin samples, bed-count mislabels) can never be mistaken for good news. Those
live in pure functions, so the tests exercise them directly and only touch
render() to confirm the wiring.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import shortlist
import shortlist_ui as sui

CUT = 800.0   # a stand-in "batch median algo" for the hand-built rows below


def _row(condo="Test Condo", **kw):
    r = {
        "condo": condo, "rating": "Neutral", "confidence": "medium",
        "summary": "A summary.", "rationale": "Because.", "red_flags": [],
        "evaluated_at": "2026-07-28", "price": 1_400_000, "beds": 3,
        "district": "D19", "sqft": 1100.0, "score_1000": 820,
        "url": "https://www.propertyguru.com.sg/listing/for-sale-test-1",
        "mandate": "his", "realscore": 3.8, "pct_profitable": 97.0,
        "resale_txns": 400, "avg_holding_yrs": 6.0,
    }
    r.update(kw)
    return r


class TestRecordBand:
    def test_clean_needs_both_rate_and_depth(self):
        assert sui.record_band(_row(pct_profitable=97.0, resale_txns=400)) == "clean"

    def test_a_perfect_rate_on_six_sales_is_thin_not_clean(self):
        # The headline failure mode this page exists to prevent: 100% of 6 must
        # never render as stronger than 98% of 600.
        assert sui.record_band(_row(pct_profitable=100.0, resale_txns=6)) == "thin"

    def test_missing_record_is_none_not_poor(self):
        assert sui.record_band(_row(pct_profitable=None,
                                    resale_txns=None)) == "none"

    def test_bands_split_at_95_and_90(self):
        assert sui.record_band(_row(pct_profitable=95.0)) == "clean"
        assert sui.record_band(_row(pct_profitable=94.9)) == "mixed"
        assert sui.record_band(_row(pct_profitable=90.0)) == "mixed"
        assert sui.record_band(_row(pct_profitable=89.9)) == "poor"


class TestClassify:
    """The value-trap rule — the single most important judgement on the page."""

    def test_high_algo_plus_bad_record_is_a_trap(self):
        r = _row(score_1000=855, pct_profitable=71.5, resale_txns=819)
        assert sui.classify(r, CUT) == "trap"

    def test_same_bad_record_without_algo_hype_is_merely_weak(self):
        r = _row(score_1000=740, pct_profitable=71.5, resale_txns=819)
        assert sui.classify(r, CUT) == "weak"

    def test_clean_record_holds_however_low_the_algo_scores_it(self):
        r = _row(score_1000=600, pct_profitable=100.0, resale_txns=296)
        assert sui.classify(r, CUT) == "holds"

    def test_high_algo_on_a_thin_sample_is_unproven_not_a_trap(self):
        # Calling this a trap would be manufacturing a verdict out of no data.
        r = _row(score_1000=900, pct_profitable=60.0, resale_txns=5)
        assert sui.classify(r, CUT) == "unproven"

    def test_no_record_at_all_is_unproven(self):
        assert sui.classify(_row(pct_profitable=None, resale_txns=None),
                            CUT) == "unproven"

    def test_blurb_names_both_halves_of_the_pairing(self):
        r = _row(score_1000=855, pct_profitable=71.5, resale_txns=819)
        b = sui.read_blurb(r, CUT)
        assert "855" in b and "71.5" in b and "819" in b


class TestAlgoCutoff:
    def test_is_the_batch_median(self):
        rows = [_row(score_1000=s) for s in (700, 800, 900)]
        assert sui.algo_cutoff(rows) == 800.0

    def test_survives_an_empty_or_unscored_batch(self):
        assert sui.algo_cutoff([]) == 0.0
        assert sui.algo_cutoff([_row(score_1000=None)]) == 0.0


class TestDivergence:
    def test_algo_favourite_with_the_worst_record_scores_highest(self):
        rows = [
            _row("Trap", score_1000=900, pct_profitable=71.0, resale_txns=800),
            _row("Mid", score_1000=800, pct_profitable=90.5, resale_txns=800),
            _row("Good", score_1000=700, pct_profitable=99.0, resale_txns=800),
        ]
        gaps = sui.divergence(rows)
        assert gaps["Trap"] == 2 and gaps["Good"] == -2

    def test_unrecorded_projects_are_not_ranked_at_all(self):
        rows = [_row("Known"), _row("Unknown", pct_profitable=None,
                                    resale_txns=None)]
        assert "Unknown" not in sui.divergence(rows)


class TestPearson:
    def test_detects_the_inverse_relationship(self):
        r = sui.pearson([1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0])
        assert r is not None and round(r, 6) == -1.0

    def test_no_correlation_claimed_without_spread(self):
        assert sui.pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
        assert sui.pearson([1.0], [1.0]) is None


class TestVisitList:
    def test_ranks_on_record_not_on_algo(self):
        rows = [
            _row("Algo darling", score_1000=999, pct_profitable=96.0,
                 resale_txns=200),
            _row("Quiet performer", score_1000=600, pct_profitable=100.0,
                 resale_txns=300),
        ]
        assert [r["condo"] for r in sui.visit_list(rows)] == [
            "Quiet performer", "Algo darling"]

    def test_avoid_never_reaches_the_visit_list(self):
        rows = [_row("Nope", rating="Avoid", pct_profitable=100.0,
                     resale_txns=900)]
        assert sui.visit_list(rows) == []

    def test_thin_evidence_never_reaches_the_visit_list(self):
        rows = [_row("Six sales", pct_profitable=100.0, resale_txns=6)]
        assert sui.visit_list(rows) == []

    def test_bed_count_mislabel_blocks_an_otherwise_clean_project(self, monkeypatch):
        monkeypatch.setattr(sui.bed_bands, "check",
                            lambda p, b, s: {"verdict": "mismatch", "looks_like": 2,
                                             "reason": "893 sqft is a 2BR here"})
        rows = [_row("Fake 3BR", pct_profitable=99.0, resale_txns=500)]
        assert sui.visit_list(rows) == []
        assert [r["condo"] for r in sui.blocked_by_beds(rows)] == ["Fake 3BR"]

    def test_oversize_is_not_treated_as_a_mislabel(self, monkeypatch):
        # bed_bands is explicit that a penthouse being roomy is not a relabel.
        monkeypatch.setattr(sui.bed_bands, "check",
                            lambda p, b, s: {"verdict": "oversize"})
        rows = [_row("Penthouse stack", pct_profitable=99.0, resale_txns=500)]
        assert len(sui.visit_list(rows)) == 1

    def test_a_broken_bed_bands_file_does_not_break_the_viewer(self, monkeypatch):
        def boom(*a):
            raise RuntimeError("no bands file")
        monkeypatch.setattr(sui.bed_bands, "check", boom)
        assert sui.bed_check(_row()) == {}
        assert len(sui.visit_list([_row(pct_profitable=99.0, resale_txns=500)])) == 1


class TestMandateCoverage:
    def test_reports_the_buyer_who_has_nothing_to_look_at(self):
        rows = [
            _row("His trap", mandate="his", score_1000=900,
                 pct_profitable=72.0, resale_txns=800),
            _row("Hers pick", mandate="hers", price=2_200_000,
                 pct_profitable=99.0, resale_txns=800),
        ]
        cov = sui.mandate_coverage(rows, sui.visit_list(rows), CUT)
        by = {c["mandate"]: c for c in cov}
        assert by["his"]["picks"] == [] and by["his"]["reasons"] == {"value trap": 1}
        assert [r["condo"] for r in by["hers"]["picks"]] == ["Hers pick"]

    def test_absent_mandate_is_omitted_rather_than_shown_as_zero(self):
        rows = [_row("Only his", mandate="his")]
        assert [c["mandate"] for c in sui.mandate_coverage(rows, [], CUT)] == ["his"]


class TestFlagFormatting:
    def test_lead_clause_is_promoted_not_rewritten(self):
        out = sui._flag_html("realsmart: only 71.5% profitable — 235 of 819 "
                             "exits sold at a LOSS")
        assert "<b>realsmart: only 71.5% profitable</b>" in out
        assert "235 of 819 exits sold at a LOSS" in out

    def test_short_unsplittable_flag_still_reads_as_a_headline(self):
        out = sui._flag_html("Single access road via Pasir Ris Grove")
        assert "<b>Single access road via Pasir Ris Grove</b>" in out

    def test_long_unsplittable_flag_is_not_set_entirely_in_bold(self):
        long = ("Ask is below ALL 31 same-size-band URA prints of the last 24 "
                "months and even the low-floor prints of this type cleared more "
                "than the ask, which undercuts the floor of its own market")
        out = sui._flag_html(long)
        assert "<b>" not in out and long in out

    def test_flags_are_escaped(self):
        assert "<script>" not in sui._flag_html("<script>alert(1)</script>")

    def test_percentages_lose_the_pointless_trailing_zero(self):
        assert sui._pct(100.0) == "100" and sui._pct(98.6) == "98.6"


class TestProfitCell:
    def test_thin_sample_is_labelled_thin_and_says_so(self):
        cell = sui._profit_cell(_row(pct_profitable=100.0, resale_txns=6))
        assert 'class="depth thin">THIN<' in cell

    def test_deep_sample_is_labelled_deep(self):
        cell = sui._profit_cell(_row(pct_profitable=98.0, resale_txns=600))
        assert 'class="depth deep">DEEP<' in cell

    def test_losses_are_expressed_in_owners_not_just_a_percentage(self):
        cell = sui._profit_cell(_row(pct_profitable=71.5, resale_txns=819))
        assert "233 owners sold at a loss" in cell

    def test_absent_record_reads_as_unknown_not_as_zero(self):
        cell = sui._profit_cell(_row(pct_profitable=None, resale_txns=None))
        assert "no realsmart record" in cell and "0%" not in cell


class TestScatter:
    def test_quadrants_use_the_same_rule_as_the_chips(self):
        rows = [_row(f"P{i}", score_1000=700 + i * 40,
                     pct_profitable=100.0 - i * 8, resale_txns=400)
                for i in range(5)]
        svg = sui.scatter_svg(rows, sui.algo_cutoff(rows))
        for i, r in enumerate(rows):
            kind = sui.classify(r, sui.algo_cutoff(rows))
            assert f'<g id="p{i}" class="pt {kind}"' in svg

    def test_unrecorded_projects_are_not_plotted(self):
        rows = [_row(f"P{i}", pct_profitable=97.0, resale_txns=400)
                for i in range(4)]
        rows.append(_row("Ghost", pct_profitable=None, resale_txns=None))
        svg = sui.scatter_svg(rows, 800.0)
        assert '<g id="p4"' not in svg and "Ghost" not in svg

    def test_no_chart_is_drawn_from_too_few_points(self):
        assert sui.scatter_svg([_row("A"), _row("B")], 800.0) == ""


class TestRender:
    def _page(self, rows):
        return sui.render(rows, "2026-07-28")

    def test_rows_without_a_mandate_are_dropped(self):
        page = self._page([_row("Kept", mandate="his"),
                           _row("Out of region", mandate=None)])
        assert "Kept" in page
        assert "Out of region" not in page

    def test_every_row_keeps_both_working_links(self):
        page = self._page([_row("The Panorama")])
        assert "https://www.propertyguru.com.sg/listing/for-sale-test-1" in page
        assert "realsmart.sg/p/" in page

    def test_the_trap_is_named_on_the_row(self):
        page = self._page([_row("Regentville", score_1000=855,
                                pct_profitable=71.5, resale_txns=819),
                           _row("Panorama", score_1000=757,
                                pct_profitable=100.0, resale_txns=296)])
        assert 'class="read trap"' in page
        assert 'class="read holds"' in page

    def test_zero_buys_reads_as_a_finding_not_as_a_broken_page(self):
        page = self._page([_row("A"), _row("B")])
        assert "Nothing clears the Buy bar." in page
        assert "That is the finding, not a broken page" in page

    def test_a_buy_flips_the_headline(self):
        page = self._page([_row("Winner", rating="Buy")])
        assert "clears the Buy bar." in page
        assert "Nothing clears" not in page

    def test_empty_batch_renders_without_exploding(self):
        page = self._page([])
        assert "Nothing evaluated in this window" in page

    def test_empty_batch_says_nothing_researched_not_nothing_qualifies(self):
        # "no data" and "data, none of it good enough" are different findings.
        page = self._page([])
        assert "Nothing researched in this window" in page
        assert "Nothing clears the Buy bar" not in page

    def test_filter_and_sort_state_is_carried_on_every_row(self):
        page = self._page([_row("A", district="D19", price=1_400_000)])
        for attr in ("data-mandate=", "data-verdict=", "data-read=",
                     "data-district=", "data-price=", "data-algo=", "data-pct=",
                     "data-txns=", "data-gap=", "data-thin=", "data-bedflag="):
            assert attr in page

    def test_hide_thin_filter_targets_thin_and_absent_records_alike(self):
        page = self._page([_row("Thin", pct_profitable=100.0, resale_txns=6),
                           _row("Absent", pct_profitable=None, resale_txns=None),
                           _row("Deep", pct_profitable=98.0, resale_txns=600)])
        assert page.count('data-thin="1"') == 2
        assert page.count('data-thin="0"') == 1

    def test_names_and_summaries_are_escaped(self):
        page = self._page([_row('<img src=x onerror=alert(1)>',
                                summary="</script><script>alert(2)</script>")])
        assert "<img src=x onerror" not in page
        assert "<script>alert(2)</script>" not in page

    def test_detail_colspan_matches_the_header_column_count(self):
        page = self._page([_row("A")])
        n_cols = page.split("<thead>")[1].split("</thead>")[0].count("<th")
        assert f'colspan="{n_cols}"' in page

    def test_district_filter_is_populated_from_the_data(self):
        page = self._page([_row("A", district="D19"), _row("B", district="D23")])
        opts = page.split('id="f-district"')[1].split("</select>")[0]
        assert '<option value="D19">' in opts and '<option value="D23">' in opts


class TestLiveData:
    """Against whatever the store actually holds — no fixtures, no mocks."""

    def test_render_of_the_real_shortlist_does_not_raise(self):
        page = sui.render(shortlist.collect(sui.DEFAULT_SINCE), sui.DEFAULT_SINCE)
        assert "shortlist failed" not in page
        assert page.startswith("<!doctype html>")


class TestHTTP:
    def setup_method(self):
        self.server = HTTPServer(("127.0.0.1", 0), sui.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def teardown_method(self):
        self.server.shutdown()

    def _get(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}", timeout=20) as resp:
            return resp.status, resp.read().decode()

    def test_index_serves_the_page(self):
        status, body = self._get("/")
        assert status == 200 and "Condo shortlist" in body

    def test_api_shortlist_still_serves_json(self):
        status, body = self._get("/api/shortlist")
        assert status == 200
        rows = json.loads(body)
        assert isinstance(rows, list)

    def test_unknown_path_404s(self):
        try:
            self._get("/nope")
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
