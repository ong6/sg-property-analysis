"""Tests for realsmart.sg project-page parsing and slug resolution.

Fixture-based on purpose — no network. The page mixes two layouts and a naive
parse silently returns wrong numbers rather than failing, which is exactly the
kind of bug a test has to hold down.
"""

import realsmart
import realsmart_cache as rc


# Trimmed from the real https://realsmart.sg/p/regentville render (2026-07-29),
# keeping every structural quirk that broke the first parser:
#   * header stats are LABEL / SUBTITLE / VALUE, and the subtitle "(past 1y)"
#     contains a digit that a naive reader grabs instead of the real figure
#   * stat tiles are VALUE / LABEL (the reverse order)
#   * a highlights badge "100%" / "Profitable" appears ABOVE the real
#     "584" / "Profitable" count tile
REGENTVILLE = """
D19OCRNorth EastHougang

REALSCOREⓘ

Profitability rank across Singapore

3.7

Annual Returns

Avg annualized profit (past 1y)

4.7%

Near

$1M+ HDB

100%

Profitable

Top 20

Regentville price, PSF and transaction trends

67%

HDB Buyers

$980.1M+

Total Transacted

16-07-1996

First Transacted

09-06-2026

Last Transacted

1,393

Total Transactions

71.5%

% Profitable

584

Profitable

235

Unprofitable

163

> 6% Annualized

9.2 yrs

Avg Holding

$97.6M

Total Profits

Tenure

99 yrs from 24/04/1996

Completion

1999

Total Units

580
"""

# An uncompleted project: profitability/rental legitimately absent.
NEW_LAUNCH = """
REALSCOREⓘ

Profitability rank across Singapore

N.A.

Tenure

99 yrs from 01/01/2025

Completion

2029
"""


class TestParseProjectPage:
    def test_header_stats_skip_the_subtitle(self):
        d = rc.parse_project_page(REGENTVILLE)
        assert d["realscore"] == 3.7
        # "(past 1y)" must not be read as the return figure
        assert d["annual_return_pct"] == 4.7

    def test_value_then_label_tiles(self):
        d = rc.parse_project_page(REGENTVILLE)
        assert d["pct_profitable"] == 71.5
        assert d["avg_holding_yrs"] == 9.2
        assert d["total_transacted"] == "$980.1M+"
        assert d["total_transactions"] == 1393

    def test_badge_does_not_steal_the_count_tile(self):
        # "100%"/"Profitable" sits above "584"/"Profitable"; the count is 584.
        d = rc.parse_project_page(REGENTVILLE)
        assert d["profitable_txns"] == 584
        assert d["unprofitable_txns"] == 235

    def test_resale_denominator_is_derived(self):
        # The honest base for the percentage: a high % on six resales is noise.
        d = rc.parse_project_page(REGENTVILLE)
        assert d["resale_txns"] == 819

    def test_project_detail_rows(self):
        d = rc.parse_project_page(REGENTVILLE)
        assert d["completion"] == "1999"
        assert d["total_units"] == "580"

    def test_uncompleted_project_yields_none_not_an_error(self):
        d = rc.parse_project_page(NEW_LAUNCH)
        assert d["realscore"] is None
        assert d["pct_profitable"] is None
        assert d["resale_txns"] is None
        assert d["completion"] == "2029"

    def test_empty_input_is_safe(self):
        for junk in ("", None, "no stats here at all"):
            d = rc.parse_project_page(junk)
            assert d["realscore"] is None and d["resale_txns"] is None


class TestCount:
    def test_rejects_percentages_and_junk(self):
        assert rc._count("584") == 584
        assert rc._count("1,393") == 1393
        assert rc._count("100%") is None      # the badge
        assert rc._count("9.2 yrs") is None
        assert rc._count("") is None


class TestSlugResolution:
    def test_normalize(self):
        assert realsmart.normalize("JadeScape") == "jadescape"
        assert realsmart.normalize("The Continuum") == "the-continuum"
        assert realsmart.normalize("8 @ Mount Sophia") == "8-mount-sophia"

    def test_expands_the_abbreviation_propertyguru_uses(self, monkeypatch):
        # PG writes "West Bay Condo"; realsmart has "west-bay-condominium".
        idx = {"slugs": ["west-bay-condominium", "jadescape"]}
        monkeypatch.setattr(realsmart, "load_slugs", lambda: idx)
        assert realsmart.resolve("West Bay Condo") == "west-bay-condominium"
        assert realsmart.resolve("JadeScape") == "jadescape"

    def test_unresolvable_returns_none_not_a_guess(self, monkeypatch):
        # A guessed URL that happens to 200 on a DIFFERENT project would attach
        # the wrong REALSCORE to a listing — worse than having none.
        monkeypatch.setattr(realsmart, "load_slugs", lambda: {"slugs": ["other"]})
        assert realsmart.resolve("Nonexistent Place") is None
        url, ok = realsmart.url_for("Nonexistent Place")
        assert ok is False and url.endswith("/nonexistent-place")
