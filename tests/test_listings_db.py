"""Core listings-DB behaviors: upsert lifecycle, price history, search/stats
filtering on status, and the sheet export."""

import csv

import pytest

import listings_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
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


class TestUpsert:
    def test_add_then_update(self, tmp_db):
        stats = listings_db.upsert_listings([_listing()])
        assert (stats["added"], stats["updated"]) == (1, 0)
        stats = listings_db.upsert_listings([_listing()])
        assert (stats["added"], stats["updated"]) == (0, 1)
        rec = listings_db.load_db()["listings"]["100000001"]
        assert rec["times_seen"] == 2

    def test_keyless_listing_skipped(self, tmp_db):
        stats = listings_db.upsert_listings([{"title": "no id, no url"}])
        assert stats["skipped"] == 1
        assert stats["total"] == 0

    def test_price_change_appends_history(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        stats = listings_db.upsert_listings([_listing(price=1_350_000)])
        assert stats["price_changes"] == 1
        rec = listings_db.load_db()["listings"]["100000001"]
        assert [e["price"] for e in rec["price_history"]] == [1_400_000, 1_350_000]

    def test_retains_new_tracked_fields(self, tmp_db):
        listings_db.upsert_listings([_listing(
            listing_date="Feb 05, 2026", address="12 Test Rd",
            description="Nice unit", tags=["Freehold"],
            floor_area_sqm=65.0, land_area_sqft=None)])
        rec = listings_db.load_db()["listings"]["100000001"]
        assert rec["listing_date"] == "Feb 05, 2026"
        assert rec["address"] == "12 Test Rd"
        assert rec["description"] == "Nice unit"
        assert rec["tags"] == ["Freehold"]
        assert rec["floor_area_sqm"] == 65.0


class TestSearchAndStats:
    def test_search_excludes_stale_by_default(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        db = listings_db.load_db()
        db["listings"]["100000001"]["status"] = "stale"
        listings_db.save_db(db)
        assert listings_db.search(query="Test Condo") == []
        assert len(listings_db.search(query="Test Condo", include_stale=True)) == 1

    def test_stats_counts_stale(self, tmp_db):
        listings_db.upsert_listings([_listing(), _listing(id="2", beds=3, sqft=1000.0)])
        db = listings_db.load_db()
        db["listings"]["2"]["status"] = "stale"
        listings_db.save_db(db)
        s = listings_db.stats()
        assert (s["total"], s["active"], s["stale"]) == (2, 1, 1)


class TestSheetExport:
    def test_sheet_written_with_columns(self, tmp_db):
        listings_db.upsert_listings([_listing()])
        path = listings_db.export_sheet()
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["project_name"] == "Test Condo"
        assert rows[0]["status"] == "active"
        assert set(listings_db.SHEET_COLUMNS) <= set(rows[0].keys())
