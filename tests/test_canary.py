"""Tests for the pre-scan canary and the score-version cohort alias.

CANARY. A full scan is ~123 page loads over 18 scopes. The failure that
actually happened was not an abort — it was a SILENT fallback: the JSON path
moved (props.pageProps.listingData -> props.pageProps.pageData.data), the
extractor quietly dropped to a lower strategy, and months of enrichment were
lost while every scan reported success. One page up front catches that.

ALIAS. score_version is CONFIG_VERSION + a hash of config.py. When the hash
INPUT changed on 2026-07-29 (comments stripped so documenting a constant stops
restamping the book), the digest moved even though no weight did — which would
split the registered v3.12 cohort in half.
"""

import pytest

import calibrate_forward as cf
import config
import scrapers.propertyguru as pg
from models import Listing, ScrapeStats, ScrapedPage, SearchParams


def _listings(n, strategy="__NEXT_DATA__", **kw):
    out = []
    for i in range(n):
        l = Listing(id=str(i), title="Test Condo", price=1_400_000,
                    url=f"https://pg/{i}")
        l.sqft, l.psf, l.district = 700.0, 2000.0, "D19"
        l.extraction_strategy = strategy
        for k, v in kw.items():
            setattr(l, k, v)
        out.append(l)
    return out


def _scraper(page):
    s = pg.PropertyGuruScraper.__new__(pg.PropertyGuruScraper)
    s._stats = ScrapeStats()
    s._scrape_single_page = lambda params, n: page
    return s


def _page(listings, total_results=300, total_pages=15):
    return ScrapedPage(page_number=1, total_results=total_results,
                       total_pages=total_pages, listings=listings)


PARAMS = SearchParams(districts=[19], beds=[3])


class TestCanary:
    def test_a_healthy_page_passes(self):
        r = _scraper(_page(_listings(20))).canary(PARAMS)
        assert r["ok"] is True and r["reason"] is None
        assert r["scope"] == "D19|3BR" and r["strategy"] == "__NEXT_DATA__"

    def test_a_silent_fallback_is_caught(self):
        # The real failure mode: 20 perfectly sane listings, extracted the
        # wrong way. Everything downstream would call this a successful scan.
        r = _scraper(_page(_listings(20, strategy="dom"))).canary(PARAMS)
        assert r["ok"] is False
        assert r["checks"]["primary_strategy"] is False
        assert r["checks"]["listing_count"] is True     # nothing else looks wrong
        assert "primary_strategy" in r["reason"]

    def test_a_thin_page_is_caught(self):
        r = _scraper(_page(_listings(3))).canary(PARAMS)
        assert r["ok"] is False and r["checks"]["listing_count"] is False

    def test_mangled_rows_are_caught(self):
        r = _scraper(_page(_listings(20, sqft=60.0))).canary(PARAMS)   # sqm-as-sqft
        assert r["ok"] is False and r["checks"]["sane_share"] is False

    def test_unparsed_pagination_is_caught(self):
        r = _scraper(_page(_listings(20), total_results=0, total_pages=0)).canary(PARAMS)
        assert r["ok"] is False and r["checks"]["pagination_parsed"] is False

    def test_an_empty_page_is_caught(self):
        assert _scraper(_page([])).canary(PARAMS)["ok"] is False
        assert _scraper(None).canary(PARAMS)["ok"] is False

    def test_it_never_raises(self):
        # A canary that explodes must not be worse than no canary at all.
        s = pg.PropertyGuruScraper.__new__(pg.PropertyGuruScraper)
        s._stats = ScrapeStats()

        def boom(params, n):
            raise RuntimeError("browser died")

        s._scrape_single_page = boom
        r = s.canary(PARAMS)
        assert r["ok"] is False and "browser died" in r["reason"]

    def test_it_costs_exactly_one_page(self):
        seen = []
        s = pg.PropertyGuruScraper.__new__(pg.PropertyGuruScraper)
        s._stats = ScrapeStats()
        s._scrape_single_page = lambda params, n: seen.append(n) or _page(_listings(20))
        s.canary(PARAMS)
        assert seen == [1]


class TestFallbackIsLoud:
    def test_a_fallback_strategy_warns(self, caplog):
        s = pg.PropertyGuruScraper.__new__(pg.PropertyGuruScraper)
        s._stats = ScrapeStats()
        with caplog.at_level("WARNING"):
            s._record_page_stats(_page(_listings(20)), "dom")
        assert any("fell back" in m for m in caplog.messages)

    def test_the_primary_strategy_is_quiet(self, caplog):
        s = pg.PropertyGuruScraper.__new__(pg.PropertyGuruScraper)
        s._stats = ScrapeStats()
        with caplog.at_level("WARNING"):
            s._record_page_stats(_page(_listings(20)), "__NEXT_DATA__")
        assert not [m for m in caplog.messages if "fell back" in m]


class TestVersionAlias:
    def test_the_hash_change_does_not_split_the_cohort(self):
        assert (cf.canonical_version(config.score_version())
                == cf.canonical_version("3.12+0e388936")), (
            "the 9,029-listing registered cohort must stay whole")

    def test_an_unknown_version_is_left_alone(self):
        assert cf.canonical_version("3.13+deadbeef") == "3.13+deadbeef"

    def test_a_real_recalibration_would_still_start_its_own_clock(self):
        # The alias map is only for digest changes proven score-identical.
        assert "3.13+deadbeef" not in cf.VERSION_ALIASES
        assert all(k.startswith("3.12+") for k in cf.VERSION_ALIASES)
