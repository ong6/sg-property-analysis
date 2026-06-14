"""Behavioral tests for ingest-time trust (audit Jun 2026):

  #3   staleness sweep counter logic
  #3b  unit_group relist linking + price-history seeding
  #12  fcntl lock ownership (thread-based)
  #14  ingest_flags on synthetic records
  #14b batch sanity gate abort
  #7b  strategy-aware price history (DOM min vs JSON midpoint)
  poll  poll-cycle detail enrichment of new listings (best-effort)
"""

import fcntl
import threading
import time
from datetime import datetime, timedelta

import pytest

import listings_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Redirect the module's file constants into a sandbox."""
    monkeypatch.setattr(listings_db, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(listings_db, "DB_FILE", str(tmp_path / "listings_db.json"))
    monkeypatch.setattr(listings_db, "SHEET_FILE", str(tmp_path / "listings_sheet.csv"))
    monkeypatch.setattr(listings_db, "_DB_LOCK_FILE", str(tmp_path / "listings_db.json.lock"))
    return tmp_path


def _listing(**kw):
    base = {
        "id": "100000001",
        "title": "Test Condo",
        "project_name": "Test Condo",
        "district": "D15",
        "beds": 2,
        "sqft": 700.0,
        "price": 1_400_000,
        "psf": 2000.0,
        "property_type": "Condominium",
        "url": "https://pg.example/listing/for-sale-test-condo-100000001",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# #3b unit identity + relist linking
# ---------------------------------------------------------------------------

class TestRelistLinking:
    def test_unit_group_stamped_on_upsert(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        rec = listings_db.load_db()["listings"]["100000001"]
        assert rec["unit_group"] == listings_db._unit_group(rec)
        assert rec["unit_group"].startswith("test condo|2|")

    def test_relist_links_and_seeds_history(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        listings_db.upsert_listings([_listing(
            id="100000002", price=1_350_000, psf=1928.6,
            url="https://pg.example/listing/for-sale-test-condo-100000002")])
        rec = listings_db.load_db()["listings"]["100000002"]
        assert rec["relisted_from"] == "100000001"
        prices = [e["price"] for e in rec["price_history"]]
        assert prices == [1_400_000, 1_350_000]  # inherited + own ask
        assert listings_db._price_trend(rec["price_history"]) == "dropped"
        # Linked, never merged: the original record still exists untouched.
        assert "100000001" in listings_db.load_db()["listings"]

    def test_same_price_relist_dedupes_event(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        listings_db.upsert_listings([_listing(id="100000002")])
        rec = listings_db.load_db()["listings"]["100000002"]
        # same (date, price) signature — no duplicate event
        assert len(rec["price_history"]) == 1

    def test_sqft_bucket_tolerance(self):
        near = listings_db._unit_group({"project_name": "X", "beds": 2, "sqft": 705.0})
        same = listings_db._unit_group({"project_name": "X", "beds": 2, "sqft": 700.0})
        far = listings_db._unit_group({"project_name": "X", "beds": 2, "sqft": 760.0})
        assert near == same
        assert far != same

    def test_different_unit_not_linked(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        listings_db.upsert_listings([_listing(id="100000003", beds=3, sqft=1100.0)])
        rec = listings_db.load_db()["listings"]["100000003"]
        assert "relisted_from" not in rec

    def test_backfill_idempotent(self, tmp_db):
        old = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        db = listings_db.load_db()
        db["listings"] = {
            "A": {**_listing(id="A"), "first_seen": old, "last_seen": old,
                  "price_history": [{"date": old, "price": 1_400_000, "psf": 2000.0}]},
            "B": {**_listing(id="B", price=1_300_000), "first_seen": listings_db._today(),
                  "last_seen": listings_db._today(),
                  "price_history": [{"date": listings_db._today(),
                                     "price": 1_300_000, "psf": 1857.1}]},
        }
        listings_db.save_db(db)

        stats1 = listings_db.backfill_unit_groups()
        snap1 = listings_db.load_db()["listings"]
        assert stats1["relist_links"] == 1
        assert stats1["histories_seeded"] == 1
        assert snap1["B"]["relisted_from"] == "A"
        assert [e["price"] for e in snap1["B"]["price_history"]] == [1_400_000, 1_300_000]
        assert (tmp_db / "listings_db.json.bak").exists()

        stats2 = listings_db.backfill_unit_groups()
        snap2 = listings_db.load_db()["listings"]
        assert stats2["histories_seeded"] == 0  # nothing new to seed
        assert snap2 == snap1  # byte-identical second run


# ---------------------------------------------------------------------------
# #14 ingest flags (annotate, never drop)
# ---------------------------------------------------------------------------

class TestIngestFlags:
    def test_clean_listing_has_empty_flags(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        rec = listings_db.load_db()["listings"]["100000001"]
        assert rec["ingest_flags"] == []  # checked-and-clean, not unchecked
        assert rec["psf_recomputed"] == 2000.0

    def test_bedroom_sqft_mismatch(self):
        flags = listings_db.compute_ingest_flags({"beds": 1, "sqft": 900.0})
        assert "bedroom_sqft_mismatch" in flags
        assert "bedroom_sqft_mismatch" not in listings_db.compute_ingest_flags(
            {"beds": 1, "sqft": 600.0})

    def test_psf_inconsistent(self, tmp_db):
        listings_db.upsert_listings([_listing(psf=2500.0)])  # true psf 2000
        rec = listings_db.load_db()["listings"]["100000001"]
        assert "psf_inconsistent" in rec["ingest_flags"]
        assert rec["psf_recomputed"] == 2000.0

    def test_format_nonapartment(self):
        assert "format_nonapartment" in listings_db.compute_ingest_flags(
            {"property_type": "Cluster House"})
        assert "format_nonapartment" not in listings_db.compute_ingest_flags(
            {"property_type": "Executive Condominium"})

    def test_psf_out_of_range(self):
        assert "psf_out_of_range" in listings_db.compute_ingest_flags({"psf": 7000.0})
        assert "psf_out_of_range" in listings_db.compute_ingest_flags({"psf": 550.0})
        assert "psf_out_of_range" not in listings_db.compute_ingest_flags({"psf": 2000.0})

    def test_keyword_flags_from_title_description_tags(self):
        assert "format_pes" in listings_db.compute_ingest_flags(
            {"description": "Rare ground floor unit with huge patio"})
        assert "format_loft" in listings_db.compute_ingest_flags(
            {"title": "Loft with double volume living"})
        assert "auction" in listings_db.compute_ingest_flags(
            {"tags": ["Mortgagee Sale"]})

    def test_keyword_word_boundaries(self):
        # 'avoid' must not read as 'void'; 'types' must not read as 'pes'
        flags = listings_db.compute_ingest_flags(
            {"description": "avoid disappointment, all types welcome"})
        assert "format_loft" not in flags
        assert "format_pes" not in flags


# ---------------------------------------------------------------------------
# #14b batch sanity gate
# ---------------------------------------------------------------------------

class TestBatchGate:
    def test_small_batch_never_gated(self):
        bad = [{"price": None, "sqft": None} for _ in range(9)]
        ok, report = listings_db.check_batch_sanity(bad)
        assert ok  # < BATCH_GATE_MIN_BATCH rows: too small to read a share

    def test_poisoned_batch_aborts(self):
        batch = [_listing(id=str(i)) for i in range(7)] + [
            {"id": "x1", "price": 5_000, "sqft": 700.0},     # price insane
            {"id": "x2", "price": 1_400_000, "sqft": 60.0},  # sqm-as-sqft
            {"id": "x3", "price": 1_400_000, "sqft": 700.0, "psf": 4000.0},  # psf mismatch
        ]
        ok, report = listings_db.check_batch_sanity(batch)
        assert not ok
        assert report["passed"] == 7
        assert report["fail_reasons"] == {
            "price_out_of_range": 1, "sqft_out_of_range": 1, "psf_mismatch": 1}

    def test_healthy_batch_passes(self):
        ok, report = listings_db.check_batch_sanity(
            [_listing(id=str(i)) for i in range(12)])
        assert ok and report["pass_share"] == 1.0

    def test_missing_psf_does_not_fail(self):
        ok, _ = listings_db.check_batch_sanity(
            [_listing(id=str(i), psf=None) for i in range(12)])
        assert ok


# ---------------------------------------------------------------------------
# #7b strategy-aware price history
# ---------------------------------------------------------------------------

class TestStrategyPriceHistory:
    def test_cross_strategy_delta_is_not_an_event(self, tmp_db):
        listings_db.upsert_listings([_listing(extraction_strategy="__NEXT_DATA__")])
        stats = listings_db.upsert_listings(
            [_listing(price=1_380_000, extraction_strategy="dom")])
        rec = listings_db.load_db()["listings"]["100000001"]
        assert stats["price_changes"] == 0
        assert len(rec["price_history"]) == 1  # phantom change suppressed
        assert rec["price"] == 1_380_000       # field itself still refreshed
        assert rec["extraction_strategy"] == "dom"

    def test_same_strategy_delta_is_an_event(self, tmp_db):
        listings_db.upsert_listings([_listing(extraction_strategy="dom")])
        stats = listings_db.upsert_listings(
            [_listing(price=1_380_000, extraction_strategy="dom")])
        rec = listings_db.load_db()["listings"]["100000001"]
        assert stats["price_changes"] == 1
        assert len(rec["price_history"]) == 2

    def test_unknown_strategy_keeps_legacy_behavior(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        stats = listings_db.upsert_listings([_listing(price=1_380_000)])
        assert stats["price_changes"] == 1


# ---------------------------------------------------------------------------
# #12 lock ownership (fcntl.flock — kernel-owned, no steal heuristic)
# ---------------------------------------------------------------------------

class TestFileLock:
    def test_mutual_exclusion(self, tmp_path):
        lock = str(tmp_path / "x.lock")
        intervals = []

        def worker():
            with listings_db.file_lock(lock, timeout=5):
                start = time.monotonic()
                time.sleep(0.15)
                intervals.append((start, time.monotonic()))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        (a0, a1), (b0, b1) = sorted(intervals)
        assert a1 <= b0  # critical sections never overlap

    def test_timed_out_waiter_does_not_release_holders_lock(self, tmp_path):
        """The old marker-file lock stole on timeout and its cleanup unlinked
        the thief's lock. With flock, a timed-out waiter proceeds unlocked but
        must leave the holder's lock fully intact."""
        lock = str(tmp_path / "x.lock")
        release = threading.Event()
        held = threading.Event()

        def holder():
            with listings_db.file_lock(lock, timeout=5):
                held.set()
                release.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        assert held.wait(timeout=2)

        # Second acquirer times out and proceeds unlocked (forgiving mode)...
        t0 = time.monotonic()
        with listings_db.file_lock(lock, timeout=0.2):
            pass
        assert time.monotonic() - t0 >= 0.2

        # ...but the holder's lock is STILL held: a non-blocking flock fails.
        import os
        fd = os.open(lock, os.O_CREAT | os.O_RDWR)
        try:
            with pytest.raises(OSError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
            release.set()
            t.join()


# ---------------------------------------------------------------------------
# #3 staleness sweep
# ---------------------------------------------------------------------------

class TestStalenessSweep:
    MARKER = "900000000"

    def _sweep(self, scraped):
        return listings_db.sweep_staleness(scraped, districts=[15], beds=[2])

    def _seed_marker(self, days_old=10):
        """A returned listing that exists in the DB with an OLDER first_seen —
        proves the cycle's coverage window reaches past the target's recency
        (a different unit: same scope, different size)."""
        marker = _listing(id=self.MARKER, sqft=1100.0, price=2_000_000, psf=1818.2)
        listings_db.upsert_listings([marker])
        db = listings_db.load_db()
        db["listings"][self.MARKER]["first_seen"] = (
            datetime.now() - timedelta(days=days_old)).strftime("%Y-%m-%d")
        listings_db.save_db(db)
        return marker

    def test_miss_counter_then_stale(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        marker = self._seed_marker()
        for expected in (1, 2):
            self._sweep([marker])
            rec = listings_db.load_db()["listings"]["100000001"]
            assert rec["poll_misses"] == expected
            assert rec["status"] == "active"
        stats = self._sweep([marker])
        rec = listings_db.load_db()["listings"]["100000001"]
        assert stats["newly_stale"] == 1
        assert rec["status"] == "stale"
        assert rec["stale_reason"] == "not_in_results"
        assert rec["stale_at"] == listings_db._today()

    def test_sighting_resets_counter(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        marker = self._seed_marker()
        self._sweep([marker])
        assert listings_db.load_db()["listings"]["100000001"]["poll_misses"] == 1
        listings_db.upsert_listings([_listing()])  # re-sighted
        rec = listings_db.load_db()["listings"]["100000001"]
        assert "poll_misses" not in rec
        assert rec["status"] == "active"

    def test_old_listings_never_swept(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        db = listings_db.load_db()
        db["listings"]["100000001"]["first_seen"] = (
            datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        listings_db.save_db(db)
        marker = self._seed_marker(days_old=90)
        for _ in range(4):
            self._sweep([marker])
        rec = listings_db.load_db()["listings"]["100000001"]
        assert rec["status"] == "active"
        assert "poll_misses" not in rec

    def test_record_older_than_coverage_window_untouched(self, tmp_db):
        """A live listing merely pushed off the scraped pages by newer
        inventory ranks OLDER than everything returned — never penalized."""
        listings_db.upsert_listings([_listing()])
        db = listings_db.load_db()
        db["listings"]["100000001"]["first_seen"] = (
            datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        listings_db.save_db(db)
        marker = self._seed_marker(days_old=10)  # window reaches only 10d back
        for _ in range(4):
            self._sweep([marker])
        rec = listings_db.load_db()["listings"]["100000001"]
        assert rec["status"] == "active"
        assert "poll_misses" not in rec

    def test_uncovered_scope_untouched(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        # Cycle returned only D10/3BR rows -> D15/2BR absence means nothing.
        stats = self._sweep([_listing(id=self.MARKER, district="D10", beds=3)])
        assert stats["scopes"] == 0
        rec = listings_db.load_db()["listings"]["100000001"]
        assert "poll_misses" not in rec

    def test_empty_scrape_is_a_noop(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        stats = self._sweep([])
        assert stats == {"scopes": 0, "checked": 0, "missed": 0, "newly_stale": 0}

    def test_stale_record_revived_by_upsert(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        marker = self._seed_marker()
        for _ in range(3):
            self._sweep([marker])
        assert listings_db.load_db()["listings"]["100000001"]["status"] == "stale"
        listings_db.upsert_listings([_listing()])
        rec = listings_db.load_db()["listings"]["100000001"]
        assert rec["status"] == "active"
        assert "stale_reason" not in rec and "stale_at" not in rec


# ---------------------------------------------------------------------------
# poll  poll-cycle detail enrichment of new listings (best-effort)
#
# The enrichment path opens a real browser + visits PG detail pages — neither
# is allowed in tests. We stub the two seams: BrowserManager / scraper (so no
# browser launches) and poller._enrich_one (so no network fetch). The behavior
# under test is the orchestration: per-listing try/except, count bookkeeping,
# targeted field merge that re-fires ingest_flags, and that one failure never
# aborts the rest of the batch.
# ---------------------------------------------------------------------------

import poller  # noqa: E402  (after sandbox fixtures defined above)


class _FakeContext:
    """Minimal browser-context stand-in: never touches the network."""
    pages = []
    def new_page(self):
        return None


class _FakeBrowserManager:
    """Drop-in for scrapers.browser.BrowserManager — a no-op context manager."""
    def __init__(self, *a, **kw):
        pass
    def __enter__(self):
        return _FakeContext()
    def __exit__(self, *a):
        return False


class _FakeScraper:
    def __init__(self, context):
        self._context = context
        self._page = None


@pytest.fixture
def stub_browser(monkeypatch):
    """Replace the browser + scraper so _enrich_new_keys never launches one."""
    import scrapers.browser
    import scrapers.propertyguru
    monkeypatch.setattr(scrapers.browser, "BrowserManager", _FakeBrowserManager)
    monkeypatch.setattr(scrapers.propertyguru, "PropertyGuruScraper", _FakeScraper)
    # No real inter-detail sleeping in tests.
    monkeypatch.setattr(poller.time, "sleep", lambda *_a, **_k: None)


class TestPollEnrichment:
    def _seed_new(self, ids):
        listings_db.upsert_listings([
            _listing(id=i, url=f"https://pg.example/listing/for-sale-test-condo-{i}")
            for i in ids])

    def test_enrich_is_best_effort_one_failure(self, tmp_db, stub_browser, monkeypatch):
        """A detail-fetch that raises on ONE listing must not abort the cycle:
        the others still enrich and the failure is counted."""
        self._seed_new(["100000001", "100000002", "100000003"])

        def fake_enrich_one(scraper, listing):
            if listing.id == "100000002":
                raise RuntimeError("boom: detail page 502")
            return {"floor_level": "High Floor", "facing": "North",
                    "latitude": 1.3001, "longitude": 103.8}

        monkeypatch.setattr(poller, "_enrich_one", fake_enrich_one)

        result = poller._enrich_new_keys(
            ["100000001", "100000002", "100000003"], headless=True, cap=20)

        assert result["attempted"] == 3
        assert result["enriched"] == 2          # 001 + 003
        assert result["failed"] == 1            # 002 raised
        # Enriched fields persisted on the survivors...
        store = listings_db.load_db()["listings"]
        assert store["100000001"]["floor_level"] == "High Floor"
        assert store["100000001"]["facing"] == "North"
        assert store["100000003"]["latitude"] == 1.3001
        # ...and the failed listing was left untouched (no partial write).
        assert "floor_level" not in store["100000002"]

    def test_enrich_refreshes_ingest_flags(self, tmp_db, stub_browser, monkeypatch):
        """Merging enrichment fields re-runs compute_ingest_flags so the
        keyword/format flags reflect the now-complete record."""
        self._seed_new(["100000001"])
        monkeypatch.setattr(poller, "_enrich_one",
                            lambda s, l: {"facing": "South"})
        poller._enrich_new_keys(["100000001"], headless=True)
        rec = listings_db.load_db()["listings"]["100000001"]
        assert rec["facing"] == "South"
        # ingest_flags present and consistent with the merged record.
        assert rec["ingest_flags"] == listings_db.compute_ingest_flags(rec)

    def test_enrich_respects_cap(self, tmp_db, stub_browser, monkeypatch):
        """Only `cap` new keys hit detail pages — the rest are left for a
        later cycle (bounds detail-page time at 2s each)."""
        self._seed_new(["100000001", "100000002", "100000003"])
        seen = []
        def fake(scraper, listing):
            seen.append(listing.id)
            return {"facing": "East"}
        monkeypatch.setattr(poller, "_enrich_one", fake)
        result = poller._enrich_new_keys(
            ["100000001", "100000002", "100000003"], cap=2)
        assert result["attempted"] == 2
        assert len(seen) == 2

    def test_enrich_no_keys_is_noop(self, tmp_db, stub_browser):
        assert poller._enrich_new_keys([]) == {
            "enriched": 0, "failed": 0, "attempted": 0}

    def test_enrich_skips_already_populated_field(self, tmp_db, stub_browser, monkeypatch):
        """_apply_enrichment only fills blanks — a detail value for a field the
        record already has must not overwrite it / count as enriched."""
        self._seed_new(["100000001"])
        db = listings_db.load_db()
        db["listings"]["100000001"]["facing"] = "West"
        listings_db.save_db(db)
        monkeypatch.setattr(poller, "_enrich_one",
                            lambda s, l: {"facing": "North", "floor_level": "Low"})
        result = poller._enrich_new_keys(["100000001"])
        rec = listings_db.load_db()["listings"]["100000001"]
        assert rec["facing"] == "West"          # not overwritten
        assert rec["floor_level"] == "Low"      # blank field filled
        assert result["enriched"] == 1

    def test_run_poll_enriches_new_before_scoring(self, tmp_db, stub_browser, monkeypatch):
        """Full cycle: a genuinely-new scrape result gets detail-enriched and
        the counts surface in poll_state; a fetch failure is best-effort."""
        # Sandbox poll_state + history into the tmp dir.
        monkeypatch.setattr(poller, "POLL_STATE_FILE",
                            str(tmp_db / "poll_state.json"))
        monkeypatch.setattr(poller, "MMR_HISTORY_CSV",
                            str(tmp_db / "mmr_history.csv"))
        monkeypatch.setattr(poller, "DATA_DIR", str(tmp_db))

        scraped = [
            _listing(id="100000001",
                     url="https://pg.example/listing/for-sale-test-condo-100000001"),
            _listing(id="100000002",
                     url="https://pg.example/listing/for-sale-test-condo-100000002"),
        ]
        # Stub the network-y collaborators of run_poll.
        import invest
        monkeypatch.setattr(invest, "scrape_listings", lambda **kw: scraped)
        monkeypatch.setattr(listings_db, "sweep_staleness",
                            lambda *a, **k: {"scopes": 0, "checked": 0,
                                             "missed": 0, "newly_stale": 0})
        # One listing's detail fetch raises -> best-effort.
        def fake_enrich_one(scraper, listing):
            if listing.id == "100000002":
                raise RuntimeError("detail 500")
            return {"floor_level": "High Floor", "facing": "North"}
        monkeypatch.setattr(poller, "_enrich_one", fake_enrich_one)
        # Avoid touching the real scoring pipeline / URA cache in this test.
        monkeypatch.setattr(poller, "_score_keys", lambda keys: (0, []))

        state = poller.run_poll(districts=[15], beds=[2], enrich_new=20)

        assert state["ok"] is True
        assert state["enriched"] == 1
        assert state["enrich_failed"] == 1
        rec = listings_db.load_db()["listings"]["100000001"]
        assert rec["floor_level"] == "High Floor"

    def test_run_poll_no_enrich_when_disabled(self, tmp_db, stub_browser, monkeypatch):
        """enrich_new=0 (the --no-enrich path) skips detail fetches entirely."""
        monkeypatch.setattr(poller, "POLL_STATE_FILE",
                            str(tmp_db / "poll_state.json"))
        monkeypatch.setattr(poller, "MMR_HISTORY_CSV",
                            str(tmp_db / "mmr_history.csv"))
        monkeypatch.setattr(poller, "DATA_DIR", str(tmp_db))
        import invest
        monkeypatch.setattr(invest, "scrape_listings", lambda **kw: [_listing()])
        monkeypatch.setattr(listings_db, "sweep_staleness",
                            lambda *a, **k: {"scopes": 0, "checked": 0,
                                             "missed": 0, "newly_stale": 0})
        monkeypatch.setattr(poller, "_score_keys", lambda keys: (0, []))
        called = []
        monkeypatch.setattr(poller, "_enrich_new_keys",
                            lambda *a, **k: called.append(1) or {})
        state = poller.run_poll(districts=[15], beds=[2], enrich_new=0)
        assert called == []                     # never invoked
        assert state["enriched"] == 0
        assert state["enrich_failed"] == 0
