"""Master PropertyGuru listings store — "the sheet".

This is the continuously-updated, deduplicated database of every listing the
tool has ever scraped. It is designed to be grown a little on every run (and
in bulk via `invest.py --update-db`), so that over time it becomes a rich,
AI-searchable inventory of the market.

Two artifacts, kept in sync:
  - data/listings_db.json   — source of truth (full records + history)
  - data/listings_sheet.csv — flat export for browsing / Google Sheets import

Each listing is keyed by its stable PropertyGuru id (falling back to URL).
On every upsert we:
  - refresh the critical fields,
  - bump `times_seen` and `last_seen`,
  - append to `price_history` whenever the price changes (so price *drops*
    are visible over time — a strong buy signal),
  - keep `first_seen` and the originating `source` flow.

The store is intentionally simple JSON so Claude Code (or any agent) can read,
grep, and reason over it directly without a database engine.
"""

from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_FILE = os.path.join(_DATA_DIR, "listings_db.json")
SHEET_FILE = os.path.join(_DATA_DIR, "listings_sheet.csv")

# Fields surfaced in the CSV "sheet" (the critical info an agent searches on).
SHEET_COLUMNS = [
    "project_name", "district", "region", "beds", "baths",
    "price", "psf", "sqft", "tenure", "built_year",
    "floor_level", "facing", "mrt_info", "total_units",
    "first_seen", "last_seen", "times_seen", "last_price_change",
    "price_trend", "status", "url",
]

# Fields copied verbatim from a scraped listing into its stored record.
_TRACKED_FIELDS = [
    "title", "project_name", "district", "district_name", "region",
    "beds", "baths", "sqft", "psf", "price", "property_type", "tenure",
    "floor_level", "furnishing", "facing", "built_year", "top_year",
    "latitude", "longitude", "developer", "total_units", "mrt_info",
    "listing_agent", "agent_phone", "image_url", "url",
]


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _days_since(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        return (datetime.now() - datetime.strptime(date_str, "%Y-%m-%d")).days
    except ValueError:
        return None


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


def _listing_key(listing: dict) -> Optional[str]:
    """Stable identity for a listing: PropertyGuru id, else URL, else None."""
    key = listing.get("id")
    if key:
        return str(key)
    url = listing.get("url")
    if url:
        return url.split("?")[0].rstrip("/")
    return None


def load_db() -> dict:
    """Load the listings DB. Returns {"updated_at", "listings": {key: record}}."""
    if not os.path.exists(DB_FILE):
        return {"updated_at": None, "listings": {}}
    try:
        with open(DB_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"updated_at": None, "listings": {}}
    data.setdefault("listings", {})
    return data


def save_db(db: dict) -> None:
    db["updated_at"] = _today()
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def _price_trend(price_history: list[dict]) -> str:
    """Classify the price path: 'dropped', 'raised', 'flat', or 'new'."""
    prices = [p.get("price") for p in price_history if p.get("price")]
    if len(prices) < 2:
        return "new"
    if prices[-1] < prices[0]:
        return "dropped"
    if prices[-1] > prices[0]:
        return "raised"
    return "flat"


def upsert_listings(listings: list[dict], source: Optional[dict] = None) -> dict:
    """Insert or update listings in the master DB.

    Args:
        listings: scraped listing dicts (as produced by the scrapers' to_dict()).
        source: optional provenance, e.g. {"flow": "url", "url": "...", "districts": [...]}.

    Returns:
        Stats dict: {added, updated, price_changes, skipped, total}.
    """
    db = load_db()
    store = db["listings"]
    today = _today()
    added = updated = price_changes = skipped = 0

    for listing in listings:
        key = _listing_key(listing)
        if not key:
            skipped += 1
            continue

        price = listing.get("price")
        record = store.get(key)

        if record is None:
            record = {
                "id": key,
                "first_seen": today,
                "last_seen": today,
                "times_seen": 1,
                "status": "active",
                "price_history": [{"date": today, "price": price, "psf": listing.get("psf")}] if price else [],
                "source": source or {},
            }
            for fld in _TRACKED_FIELDS:
                if listing.get(fld) is not None:
                    record[fld] = listing[fld]
            store[key] = record
            added += 1
            continue

        # Existing record: refresh fields, bump counters, track price changes.
        record["last_seen"] = today
        record["times_seen"] = record.get("times_seen", 1) + 1
        record["status"] = "active"
        if source:
            record["source"] = source
        for fld in _TRACKED_FIELDS:
            if listing.get(fld) is not None:
                record[fld] = listing[fld]

        history = record.setdefault("price_history", [])
        last_price = history[-1]["price"] if history else None
        if price and price != last_price:
            history.append({"date": today, "price": price, "psf": listing.get("psf")})
            price_changes += 1
        updated += 1

    save_db(db)
    export_sheet(db=db)
    return {
        "added": added,
        "updated": updated,
        "price_changes": price_changes,
        "skipped": skipped,
        "total": len(store),
    }


def mark_stale(active_keys: set[str], scope_district: Optional[str] = None,
               older_than_days: int = 30) -> int:
    """Mark listings not seen in a refresh as 'stale' (likely sold/withdrawn).

    Only affects records last seen more than `older_than_days` ago, optionally
    limited to a district. Returns the number newly marked stale.
    """
    db = load_db()
    marked = 0
    for key, rec in db["listings"].items():
        if key in active_keys or rec.get("status") == "stale":
            continue
        if scope_district and (rec.get("district") or "") != scope_district:
            continue
        age = _days_since(rec.get("last_seen"))
        if age is not None and age >= older_than_days:
            rec["status"] = "stale"
            marked += 1
    if marked:
        save_db(db)
        export_sheet(db=db)
    return marked


def _record_to_row(rec: dict) -> dict:
    history = rec.get("price_history", [])
    last_change = history[-1]["date"] if len(history) > 1 else ""
    row = {col: rec.get(col, "") for col in SHEET_COLUMNS}
    row["last_price_change"] = last_change
    row["price_trend"] = _price_trend(history)
    return row


def export_sheet(path: Optional[str] = None, db: Optional[dict] = None) -> str:
    """Write the flat CSV sheet from the DB. Returns the path written."""
    db = db if db is not None else load_db()
    path = path or SHEET_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = sorted(
        db["listings"].values(),
        key=lambda r: (r.get("last_seen") or "", r.get("price") or 0),
        reverse=True,
    )
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        for rec in rows:
            writer.writerow(_record_to_row(rec))
    return path


def search(
    query: Optional[str] = None,
    district: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    beds: Optional[list[int]] = None,
    include_stale: bool = False,
    limit: int = 50,
) -> list[dict]:
    """Search the master sheet. Returns matching records, best-match first.

    `query` fuzzy-matches the project name/title. Other args are exact filters.
    """
    db = load_db()
    results = []
    q = _norm(query) if query else None

    for rec in db["listings"].values():
        if not include_stale and rec.get("status") == "stale":
            continue
        if district:
            want = district.upper().replace("D", "").lstrip("0")
            have = str(rec.get("district") or "").upper().replace("D", "").lstrip("0")
            if want != have:
                continue
        price = rec.get("price") or 0
        if min_price and price < min_price:
            continue
        if max_price and price > max_price:
            continue
        if beds and rec.get("beds") not in beds:
            continue

        match_score = 1.0
        if q:
            cand = _norm(rec.get("project_name") or rec.get("title") or "")
            if not cand:
                continue
            match_score = 1.0 if q in cand or cand in q else SequenceMatcher(None, q, cand).ratio()
            if match_score < 0.55:
                continue

        results.append((match_score, rec))

    # Sort: best name match, then most recently seen.
    results.sort(key=lambda t: (t[0], t[1].get("last_seen") or ""), reverse=True)
    return [rec for _, rec in results[:limit]]


def stats() -> dict:
    """Summary stats over the whole DB."""
    db = load_db()
    listings = db["listings"].values()
    by_district: dict[str, int] = {}
    active = stale = price_drops = 0
    for rec in listings:
        d = str(rec.get("district") or "?")
        by_district[d] = by_district.get(d, 0) + 1
        if rec.get("status") == "stale":
            stale += 1
        else:
            active += 1
        if _price_trend(rec.get("price_history", [])) == "dropped":
            price_drops += 1
    return {
        "total": len(db["listings"]),
        "active": active,
        "stale": stale,
        "price_drops": price_drops,
        "by_district": dict(sorted(by_district.items())),
        "updated_at": db.get("updated_at"),
    }


def format_results(records: list[dict]) -> str:
    """Human/AI-readable rendering of search results."""
    if not records:
        return "No listings found in the sheet."
    lines = []
    for rec in records:
        name = rec.get("project_name") or rec.get("title") or "?"
        price = rec.get("price")
        price_str = f"${price/1e6:.2f}M" if price else "$?"
        psf = f"${rec.get('psf'):,.0f}psf" if rec.get("psf") else ""
        beds = f"{rec.get('beds')}BR" if rec.get("beds") else ""
        district = rec.get("district") or ""
        trend = _price_trend(rec.get("price_history", []))
        trend_tag = {"dropped": " ⬇ price dropped", "raised": " ⬆ price raised"}.get(trend, "")
        age = _days_since(rec.get("last_seen"))
        seen = f"seen {age}d ago" if age is not None else ""
        lines.append(
            f"  {name} [{district}] {beds} {price_str} {psf}{trend_tag} "
            f"({rec.get('times_seen', 1)}x, {seen})\n    {rec.get('url', '')}"
        )
    return "\n".join(lines)
