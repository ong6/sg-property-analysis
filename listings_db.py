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
import fcntl
import json
import math
import os
import re
import shutil
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_FILE = os.path.join(_DATA_DIR, "listings_db.json")
SHEET_FILE = os.path.join(_DATA_DIR, "listings_sheet.csv")
_DB_LOCK_FILE = DB_FILE + ".lock"

# Fields surfaced in the CSV "sheet" (the critical info an agent searches on).
SHEET_COLUMNS = [
    "project_name", "district", "region", "beds", "baths",
    "price", "psf", "sqft", "mmr", "score_1000", "scored_at",
    "tenure", "built_year",
    "floor_level", "facing", "mrt_info", "total_units",
    "first_seen", "last_seen", "times_seen", "last_price_change",
    "price_trend", "lowest_price", "drop_from_peak_pct", "status", "url",
]

# Fields copied verbatim from a scraped listing into its stored record.
_TRACKED_FIELDS = [
    "title", "project_name", "district", "district_name", "region",
    "beds", "baths", "sqft", "psf", "price", "property_type", "tenure",
    "floor_level", "furnishing", "facing", "built_year", "top_year",
    "latitude", "longitude", "developer", "total_units", "mrt_info",
    "listing_agent", "agent_phone", "image_url", "url",
    # Parsed by the scraper but previously dropped here (measured 0% coverage)
    # — description/tags are where "PES"/"loft"/"auction" live in words.
    "listing_date", "address", "description", "tags",
    "floor_area_sqm", "land_area_sqft",
    # Which of the 4 scraper strategies produced the row — DOM reports a price
    # range's minimum while JSON reports the midpoint, so a cross-strategy
    # price delta is a measurement artifact, not a price event.
    "extraction_strategy",
]

# ---------------------------------------------------------------------------
# Ingest validation (#14): annotate, never drop. Every artifact class the
# scorer's v3.6-v3.9 trust layer fights (mis-scraped sqft/beds, strata
# formats, insane psf) used to enter the DB unflagged; these flags are written
# at upsert time so consumers never have to re-infer from price geometry what
# the scrape already said.
# ---------------------------------------------------------------------------

# Mirror of FullScorer._BEDROOM_SQFT_RANGES (scoring/full_scorer.py) — kept as
# a local copy so ingest never imports the scoring stack. Keep the two tables
# in sync. CONTRACT: the scorer treats `bedroom_sqft_mismatch` in
# `ingest_flags` exactly like its own detection.
_BEDROOM_SQFT_RANGES = {
    1: (400, 850),
    2: (600, 1200),
    3: (850, 1800),
    4: (1200, 2500),
    5: (1600, 3500),
}

# Apartment-format property types. Anything else (cluster house, terrace,
# strata landed...) must not be benchmarked against apartment medians — the
# v3.6 strata-villa artifact, now visible at ingest.
_APARTMENT_FORMATS = {
    "condominium", "apartment", "executive condominium",
    "walk-up", "walk-up apartment",
}

_PSF_SANE_RANGE = (600.0, 6500.0)

# Keyword flags scanned over title + description + tags.
_KEYWORD_FLAGS = [
    ("format_pes", re.compile(r"\bpes\b|\bpatio\b|ground floor", re.I)),
    ("format_loft", re.compile(r"\bloft\b|double volume|\bvoid\b", re.I)),
    ("auction", re.compile(r"\bauction\b|\bmortgagee\b|below valuation", re.I)),
]


def compute_ingest_flags(record: dict) -> list[str]:
    """Data-quality + format annotations for one record (pure; see
    _annotate_ingest for the upsert-time writer)."""
    flags: list[str] = []
    beds, sqft = record.get("beds"), record.get("sqft")
    price, psf = record.get("price"), record.get("psf")

    expected = _BEDROOM_SQFT_RANGES.get(beds) if beds else None
    if expected and sqft and not (expected[0] <= sqft <= expected[1]):
        flags.append("bedroom_sqft_mismatch")

    if price and sqft and psf:
        recomputed = price / sqft
        if abs(psf - recomputed) / recomputed > 0.03:
            flags.append("psf_inconsistent")

    ptype = (record.get("property_type") or "").strip().lower()
    if ptype and ptype not in _APARTMENT_FORMATS:
        flags.append("format_nonapartment")

    if psf and not (_PSF_SANE_RANGE[0] <= psf <= _PSF_SANE_RANGE[1]):
        flags.append("psf_out_of_range")

    tags = record.get("tags") or []
    text = " ".join(
        [str(record.get("title") or ""), str(record.get("description") or "")]
        + [str(t) for t in tags]
    )
    for flag, rx in _KEYWORD_FLAGS:
        if rx.search(text):
            flags.append(flag)
    return flags


def _annotate_ingest(record: dict) -> None:
    """Write `ingest_flags` (always, so [] means checked-and-clean) and
    `psf_recomputed` (the trustworthy price/sqft, when computable)."""
    price, sqft = record.get("price"), record.get("sqft")
    if price and sqft:
        record["psf_recomputed"] = round(price / sqft, 1)
    record["ingest_flags"] = compute_ingest_flags(record)


# ---------------------------------------------------------------------------
# Batch sanity gate (#14b): one PropertyGuru redesign must not poison the DB.
# The 4-strategy scraper cascade degrades silently; before upserting a batch
# we check cheap invariants and refuse the whole batch loudly when they break.
# ---------------------------------------------------------------------------

BATCH_GATE_MIN_BATCH = 10
BATCH_GATE_MIN_PASS_SHARE = 0.90
_PRICE_SANE_RANGE = (200_000, 50_000_000)
_SQFT_SANE_RANGE = (250.0, 20_000.0)


class BatchSanityError(RuntimeError):
    """A scrape batch failed the pre-upsert invariants — the batch is
    REJECTED in full, never partially stored."""


def _sanity_fail_reason(listing: dict) -> Optional[str]:
    price, sqft, psf = listing.get("price"), listing.get("sqft"), listing.get("psf")
    if not price or not (_PRICE_SANE_RANGE[0] <= price <= _PRICE_SANE_RANGE[1]):
        return "price_out_of_range"
    if not sqft or not (_SQFT_SANE_RANGE[0] <= sqft <= _SQFT_SANE_RANGE[1]):
        return "sqft_out_of_range"
    if psf:  # psf is derivable, so its absence is not a scrape regression
        implied = price / sqft
        if abs(psf - implied) / implied > 0.05:
            return "psf_mismatch"
    return None


def check_batch_sanity(listings: list[dict]) -> tuple[bool, dict]:
    """Pre-upsert invariants over a scraped batch. Returns (ok, report).

    Batches smaller than BATCH_GATE_MIN_BATCH are never gated (too small to
    read a failure share from); larger batches must have >=90% of rows with a
    sane price, sane sqft, and any stored psf within 5% of price/sqft.
    """
    n = len(listings)
    passed = 0
    fail_reasons: dict[str, int] = {}
    for listing in listings:
        reason = _sanity_fail_reason(listing)
        if reason is None:
            passed += 1
        else:
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
    share = (passed / n) if n else 1.0
    ok = n < BATCH_GATE_MIN_BATCH or share >= BATCH_GATE_MIN_PASS_SHARE
    return ok, {
        "batch": n,
        "passed": passed,
        "pass_share": round(share, 3),
        "fail_reasons": fail_reasons,
    }


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
    # Atomic write: a crash mid-dump must not truncate the DB (load_db would
    # then silently return an empty store and the next save would erase
    # everything ever scraped).
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DB_FILE)


@contextmanager
def file_lock(lock_path: str, timeout: float = 30.0):
    """Cross-process advisory lock via fcntl.flock (kernel-owned).

    Replaces the old O_EXCL marker-file lock, whose steal-on-timeout heuristic
    deleted a *legitimately held* lock and whose holder's cleanup then
    unlinked the thief's lock (no ownership token) — three writers racing. An
    flock dies with its process, so there is no stale-lock case and no steal
    path; timeout is LOCK_NB + polling. On timeout we proceed UNLOCKED with a
    loud warning rather than drop the caller's save (same forgiving behavior
    as before). Shared helper — also used for mmr_history.csv appends.
    """
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    locked = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    print(f"WARNING: could not acquire {lock_path} within "
                          f"{timeout}s; proceeding unlocked", file=sys.stderr)
                    break
                time.sleep(0.05)
        yield
    finally:
        if locked:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


@contextmanager
def _db_lock(timeout: float = 30.0):
    """Cross-process lock for the whole-DB read-modify-write cycle.

    Concurrent sessions (one per listing — see CLAUDE.md) each load, mutate and
    save the full DB; without a lock the second writer silently drops the
    first writer's upserts.
    """
    with file_lock(_DB_LOCK_FILE, timeout=timeout):
        yield


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


# ---------------------------------------------------------------------------
# Unit identity + relist linking (#3b). The primary key is the PG listing id,
# so the same physical unit relisted (new agent, bait re-list) gets a fresh
# id, a fresh first_seen and an empty price history — measured: 18.5% of rows
# are same-unit duplicates, 6,350/6,360 single-point histories, the price-drop
# signal fired twice ever. Records sharing a unit_group are LINKED, never
# merged: a new id records `relisted_from` and seeds its history from the
# group's most recent record, so relists stop looking NEW with no history.
# ---------------------------------------------------------------------------

def _unit_group(record: dict) -> Optional[str]:
    """Secondary unit identity: normalized project | beds | sqft bucket.

    Buckets are 2%-wide in log space, so two areas within ~±1% of each other
    share a bucket (modulo boundary straddles — acceptable for a linking key).
    """
    name = _norm(record.get("project_name") or record.get("title") or "")
    beds = record.get("beds")
    sqft = record.get("sqft")
    if not name or not beds or not sqft or sqft <= 0:
        return None
    bucket = int(round(math.log(float(sqft)) / math.log(1.02)))
    return f"{name}|{beds}|{bucket}"


def _latest_in_group(records: list[dict], exclude_key: Optional[str] = None) -> Optional[dict]:
    """Most recent record in a unit group (by last_seen, then first_seen)."""
    cands = [r for r in records if str(r.get("id")) != str(exclude_key)]
    if not cands:
        return None
    return max(cands, key=lambda r: (r.get("last_seen") or "",
                                     r.get("first_seen") or "", str(r.get("id"))))


def _seed_history(seed_events: list[dict], own_events: list[dict]) -> list[dict]:
    """Merge an ancestor's price history into a relist's, chronologically,
    deduped on (date, price) — idempotent, so the backfill can re-run safely."""
    merged: list[dict] = []
    seen: set = set()
    for ev in list(seed_events) + list(own_events):
        sig = (ev.get("date"), ev.get("price"))
        if sig in seen:
            continue
        seen.add(sig)
        merged.append(dict(ev))
    merged.sort(key=lambda e: e.get("date") or "")
    return merged


def upsert_listings(listings: list[dict], source: Optional[dict] = None) -> dict:
    """Insert or update listings in the master DB.

    Args:
        listings: scraped listing dicts (as produced by the scrapers' to_dict()).
        source: optional provenance, e.g. {"flow": "url", "url": "...", "districts": [...]}.

    Returns:
        Stats dict: {added, updated, price_changes, skipped, total}.
    """
    with _db_lock():
        return _upsert_listings_locked(listings, source)


def _upsert_listings_locked(listings: list[dict], source: Optional[dict]) -> dict:
    db = load_db()
    store = db["listings"]
    today = _today()
    added = updated = price_changes = skipped = 0

    # unit_group index over the existing store, for relist linking (#3b).
    group_index: dict[str, list[dict]] = {}
    for rec in store.values():
        g = rec.get("unit_group")
        if g:
            group_index.setdefault(g, []).append(rec)

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
            group = _unit_group(record)
            if group:
                record["unit_group"] = group
                prev = _latest_in_group(group_index.get(group, []), exclude_key=key)
                if prev is not None:
                    # Same unit under a fresh id: link (never merge) and seed
                    # history so a bait relist's prior price path is visible.
                    record["relisted_from"] = prev.get("id")
                    record["price_history"] = _seed_history(
                        prev.get("price_history", []), record["price_history"])
                group_index.setdefault(group, []).append(record)
            _annotate_ingest(record)
            store[key] = record
            added += 1
            continue

        # Existing record: refresh fields, bump counters, track price changes.
        prev_strategy = record.get("extraction_strategy")
        record["last_seen"] = today
        record["times_seen"] = record.get("times_seen", 1) + 1
        record["status"] = "active"
        # A re-sighting is proof of life — clear staleness bookkeeping (#3).
        record.pop("poll_misses", None)
        record.pop("stale_reason", None)
        record.pop("stale_at", None)
        if source:
            record["source"] = source
        for fld in _TRACKED_FIELDS:
            if listing.get(fld) is not None:
                record[fld] = listing[fld]
        group = _unit_group(record)
        if group:
            record["unit_group"] = group
        _annotate_ingest(record)

        history = record.setdefault("price_history", [])
        last_price = history[-1]["price"] if history else None
        # Cross-strategy price deltas are measurement artifacts, not events:
        # DOM extraction reports a price range's minimum while the JSON paths
        # report the midpoint, so the same listing "changes price" whenever
        # the winning strategy flips (#7b).
        new_strategy = listing.get("extraction_strategy")
        cross_strategy = bool(prev_strategy and new_strategy
                              and prev_strategy != new_strategy)
        if price and price != last_price and not cross_strategy:
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


# ---------------------------------------------------------------------------
# Staleness sweep (#3). Nothing ever wrote status="stale" although search(),
# the UI and cohort stats all filter on it — sold/withdrawn units ranked
# forever and bait asks never aged out. The default poll covers only the
# NEWEST pages per (district, beds) scope, so absence is only meaningful for
# records that should still be near the front of a date-desc search: those
# with a young first_seen. Older deep-page stock is never touched.
# ---------------------------------------------------------------------------

POLL_MISS_STALE_THRESHOLD = 3        # consecutive missed cycles before stale
POLL_MISS_MAX_FIRST_SEEN_DAYS = 45   # only listings young enough to be "front page"


def _district_num(value) -> Optional[str]:
    """Normalize 'D05' / 'd5' / 5 -> '5' for scope comparison."""
    s = str(value or "").upper().replace("D", "").lstrip("0")
    return s or None


def sweep_staleness(scraped: list[dict], districts: list[int],
                    beds: list[int]) -> dict:
    """End-of-poll staleness sweep. Call after the cycle's upsert.

    For every polled (district, beds) scope that actually returned listings
    this cycle, increment `poll_misses` on active records that were NOT in
    the results but demonstrably should have been; at
    POLL_MISS_STALE_THRESHOLD consecutive misses set status="stale" with
    stale_reason="not_in_results". A sighting resets the counter (in upsert).

    "Should have been" is two guards, both required:
      * first_seen <= POLL_MISS_MAX_FIRST_SEEN_DAYS (young enough to plausibly
        sit near the front of a date-desc search), AND
      * first_seen strictly NEWER than the oldest first_seen among the
        listings this cycle returned for the scope — i.e. inside the recency
        window the scraped pages provably covered. A live 30-day-old listing
        merely pushed off page 2 by newer inventory ranks older than the whole
        returned window and is never penalized.

    Scopes with zero returned listings are skipped — a failed or empty scrape
    must not count as evidence of absence.
    """
    seen_keys = {k for k in (_listing_key(l) for l in scraped) if k}
    requested = {(_district_num(d), b) for d in (districts or []) for b in (beds or [])}
    covered: set = set()
    for listing in scraped:
        scope = (_district_num(listing.get("district")), listing.get("beds"))
        if scope in requested:
            covered.add(scope)

    stats = {"scopes": len(covered), "checked": 0, "missed": 0, "newly_stale": 0}
    if not covered:
        return stats

    today = _today()
    with _db_lock():
        db = load_db()
        store = db["listings"]

        # Per-scope coverage bound: oldest first_seen among the returned
        # listings (they were upserted just before this sweep, so they carry
        # DB first_seen dates).
        oldest_seen: dict = {}
        for k in seen_keys:
            rec = store.get(k)
            if not rec:
                continue
            scope = (_district_num(rec.get("district")), rec.get("beds"))
            fs = rec.get("first_seen")
            if scope in covered and fs and fs < oldest_seen.get(scope, "9999"):
                oldest_seen[scope] = fs

        changed = False
        for key, rec in store.items():
            if rec.get("status") == "stale":
                continue
            scope = (_district_num(rec.get("district")), rec.get("beds"))
            if scope not in covered:
                continue
            age = _days_since(rec.get("first_seen"))
            if age is None or age > POLL_MISS_MAX_FIRST_SEEN_DAYS:
                continue
            bound = oldest_seen.get(scope)
            if not bound or (rec.get("first_seen") or "") <= bound:
                continue  # outside the window the scrape demonstrably covered
            stats["checked"] += 1
            if key in seen_keys:
                continue  # upsert already reset poll_misses
            rec["poll_misses"] = rec.get("poll_misses", 0) + 1
            stats["missed"] += 1
            changed = True
            if rec["poll_misses"] >= POLL_MISS_STALE_THRESHOLD:
                rec["status"] = "stale"
                rec["stale_reason"] = "not_in_results"
                rec["stale_at"] = today
                stats["newly_stale"] += 1
        if changed:
            save_db(db)
            export_sheet(db=db)
    return stats


def _record_to_row(rec: dict) -> dict:
    history = rec.get("price_history", [])
    last_change = history[-1]["date"] if len(history) > 1 else ""
    row = {col: rec.get(col, "") for col in SHEET_COLUMNS}
    row["last_price_change"] = last_change
    row["price_trend"] = _price_trend(history)
    # Peak-relative drop: first-vs-last hides V-shaped paths; a unit that came
    # down from its peak is a potential motivated-seller signal even if it
    # recovered above its first listed price.
    prices = [p.get("price") for p in history if p.get("price")]
    if prices:
        row["lowest_price"] = min(prices)
        peak = max(prices)
        current = prices[-1]
        row["drop_from_peak_pct"] = round((current - peak) / peak * 100, 1) if peak > 0 else ""
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
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        for rec in rows:
            writer.writerow(_record_to_row(rec))
    os.replace(tmp, path)
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
            if q == cand:
                match_score = 1.0
            elif q in cand or cand in q:
                match_score = 0.95  # containment must not outrank an exact match
            else:
                match_score = SequenceMatcher(None, q, cand).ratio()
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


def backfill_unit_groups() -> dict:
    """One-time (idempotent, atomic) backfill over the existing DB:

      * stamp `unit_group` on every record,
      * link relists chronologically within each group (`relisted_from`) and
        seed each relist's price history from its predecessor's pre-existing
        events, so same-unit duplicate rows stop looking NEW with no history,
      * run the ingest flagger (#14) over every record.

    Records are never merged — only linked. A .bak copy of the DB is made
    before the first run; save is the usual tmp+replace.
    """
    with _db_lock():
        db = load_db()
        store = db["listings"]
        if not store:
            return {"records": 0}

        bak = DB_FILE + ".bak"
        if os.path.exists(DB_FILE) and not os.path.exists(bak):
            shutil.copyfile(DB_FILE, bak)

        flagged = with_group = linked = seeded = 0
        groups: dict[str, list[dict]] = {}
        for rec in store.values():
            g = _unit_group(rec)
            if g:
                rec["unit_group"] = g
                groups.setdefault(g, []).append(rec)
                with_group += 1
            _annotate_ingest(rec)
            if rec["ingest_flags"]:
                flagged += 1

        multi = 0
        for recs in groups.values():
            if len(recs) < 2:
                continue
            multi += 1
            recs.sort(key=lambda r: (r.get("first_seen") or "", str(r.get("id"))))
            for prev, rec in zip(recs, recs[1:]):
                rec["relisted_from"] = prev.get("id")
                linked += 1
                # Seed only events that predate this record's own first
                # sighting — concurrent same-unit rows keep separate paths.
                cutoff = rec.get("first_seen") or ""
                seed = [e for e in prev.get("price_history", [])
                        if (e.get("date") or "") < cutoff]
                if seed:
                    before = len(rec.get("price_history", []))
                    rec["price_history"] = _seed_history(
                        seed, rec.get("price_history", []))
                    if len(rec["price_history"]) > before:
                        seeded += 1

        save_db(db)
        export_sheet(db=db)

    return {
        "records": len(store),
        "with_unit_group": with_group,
        "groups_multi_record": multi,
        "relist_links": linked,
        "histories_seeded": seeded,
        "flagged": flagged,
    }


def _main():
    import argparse

    ap = argparse.ArgumentParser(description="Listings DB maintenance")
    ap.add_argument("--backfill-unit-groups", action="store_true",
                    help="one-time backfill: unit_group + relist links + "
                         "seeded histories + ingest flags (idempotent)")
    args = ap.parse_args()
    if args.backfill_unit_groups:
        print(json.dumps(backfill_unit_groups(), indent=2))
    else:
        ap.print_help()


if __name__ == "__main__":
    _main()
