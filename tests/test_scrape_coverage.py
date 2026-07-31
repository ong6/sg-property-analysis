"""Tests for the two premises the scan's bookkeeping rests on.

ORDERING. Everything downstream reads page depth as "how far back one scan
reaches" — weekly.POLL_MAX_PAGES, and listings_db.sweep_staleness, which will
only call a listing missing when it sits inside the recency window a date-desc
scrape demonstrably covered. Both are nonsense under a relevance-style
ordering, and the sort params were being omitted whenever they equalled the
defaults, leaving the ordering to PropertyGuru's choice.

BUDGET. `max_pages` was a floor, not a cap: after page 1 the scraper read
total_pages and extended to min(total_pages, 15). A caller asking for 5 pages
could get 15, which is why the 2026-07-28 scan pulled 2,462 listings from 18
scopes against a documented ceiling of 1,800.
"""

import pytest

import scrapers.propertyguru as pg
from models import ScrapeStats, ScrapedPage, SearchParams


@pytest.fixture(autouse=True)
def _no_politeness_delay(monkeypatch):
    """These tests exercise the pagination loop, not the rate limiter — without
    this the module's real inter-page sleep makes the file take ~50s."""
    monkeypatch.setattr(pg, "_sleep_with_jitter", lambda *a, **k: None)


def _listings(n, start=0):
    from models import Listing
    return [Listing(id=str(start + i), title=f"L{start + i}", price=1_000_000,
                    url=f"https://pg/{start + i}") for i in range(n)]


def _scraper(pages):
    """A scraper whose only live part is the pagination loop.

    `pages` maps page_number -> ScrapedPage or None (None = the page came back
    empty, which is the ambiguous case the coverage fields exist to disambiguate).
    """
    s = pg.PropertyGuruScraper.__new__(pg.PropertyGuruScraper)
    s._stats = ScrapeStats()
    s._seen_ids = set()
    s._context = type("C", (), {"pages": [object()], "new_page": lambda self: object()})()
    s._setup_interception = lambda: None
    s._propagate_coordinates = lambda x: None
    s._scrape_single_page = lambda params, n: pages.get(n)
    return s


class TestSortOrderIsExplicit:
    def test_defaults_are_still_emitted(self):
        qs = SearchParams(districts=[19], beds=[3]).to_query_string()
        assert "sort=date" in qs and "order=desc" in qs

    def test_non_defaults_are_emitted_too(self):
        qs = SearchParams(sort="price", order="asc").to_query_string()
        assert "sort=price" in qs and "order=asc" in qs

    def test_a_bare_search_still_carries_the_ordering(self):
        # The premise must hold even when no filter sets any other param.
        assert SearchParams().to_query_string() == "sort=date&order=desc"


class TestMaxPagesIsAHardCap:
    def test_it_does_not_extend_past_the_callers_budget(self):
        pages = {n: ScrapedPage(page_number=n, total_results=300, total_pages=15,
                                listings=_listings(20, n * 100)) for n in range(1, 16)}
        got = _scraper(pages).scrape(SearchParams(districts=[19]), max_pages=5)
        assert len(got) == 100, "5 pages x 20 listings — not 15 pages"

    def test_allow_extend_opts_back_in(self):
        pages = {n: ScrapedPage(page_number=n, total_results=300, total_pages=15,
                                listings=_listings(20, n * 100)) for n in range(1, 16)}
        got = _scraper(pages).scrape(SearchParams(districts=[19]), max_pages=5,
                                     allow_extend=True)
        assert len(got) == 300

    def test_a_short_scope_still_stops_early(self):
        pages = {1: ScrapedPage(page_number=1, total_results=12, total_pages=1,
                                listings=_listings(12))}
        got = _scraper(pages).scrape(SearchParams(districts=[19]), max_pages=5)
        assert len(got) == 12


class TestCoverageBookkeeping:
    def test_a_capped_scope_is_recorded_as_capped_not_complete(self):
        pages = {n: ScrapedPage(page_number=n, total_results=300, total_pages=15,
                                listings=_listings(20, n * 100)) for n in range(1, 16)}
        s = _scraper(pages)
        s.scrape(SearchParams(districts=[19], beds=[3]), max_pages=5)
        cov = s._stats.scope_coverage["D19|3BR"]
        assert cov["capped"] is True and cov["truncated"] is False
        assert cov["total_pages"] == 15 and cov["pages_fetched"] == 5

    def test_going_dark_mid_scope_is_truncated_not_end_of_inventory(self):
        # Page 3 comes back empty while page 1 promised 15 pages: a Cloudflare
        # block or parse regression, NOT the last page.
        pages = {1: ScrapedPage(page_number=1, total_results=300, total_pages=15,
                                listings=_listings(20)),
                 2: ScrapedPage(page_number=2, total_results=300, total_pages=15,
                                listings=_listings(20, 100)),
                 3: None}
        s = _scraper(pages)
        s.scrape(SearchParams(districts=[19], beds=[3]), max_pages=10)
        assert s._stats.scope_coverage["D19|3BR"]["truncated"] is True

    def test_a_genuine_last_page_is_not_truncated(self):
        pages = {1: ScrapedPage(page_number=1, total_results=20, total_pages=1,
                                listings=_listings(20)),
                 2: None}
        s = _scraper(pages)
        s.scrape(SearchParams(districts=[19], beds=[3]), max_pages=5)
        assert s._stats.scope_coverage["D19|3BR"]["truncated"] is False

    def test_summary_totals_reach_across_scopes(self):
        st = ScrapeStats()
        st.scope_coverage = {
            "D19|3BR": {"total_results": 300, "total_pages": 15, "pages_fetched": 5,
                        "listings_seen": 100, "truncated": False, "capped": True},
            "D18|3BR": {"total_results": 40, "total_pages": 2, "pages_fetched": 2,
                        "listings_seen": 40, "truncated": False, "capped": False},
        }
        s = st.coverage_summary()
        assert s["results_available"] == 340 and s["results_seen"] == 140
        assert s["capped_scopes"] == ["D19|3BR"] and s["truncated_scopes"] == []

    def test_stats_merge_keeps_every_scope(self):
        a, b = ScrapeStats(), ScrapeStats()
        a.scope_coverage = {"D19|3BR": {"listings_seen": 100}}
        b.scope_coverage = {"D18|3BR": {"listings_seen": 40}}
        a.merge(b)
        assert set(a.scope_coverage) == {"D19|3BR", "D18|3BR"}


class TestScopeLabel:
    def test_it_names_districts_and_beds(self):
        s = pg.PropertyGuruScraper.__new__(pg.PropertyGuruScraper)
        assert s._scope_label(SearchParams(districts=[19], beds=[3])) == "D19|3BR"
        assert s._scope_label(SearchParams()) == "all|anyBR"
