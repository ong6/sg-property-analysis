#!/usr/bin/env python3
"""PropertyGuru poll cycle — pull the newest listings, score them, record state.

One poll cycle:
  1. scrape the configured districts/beds (PG search is date-desc, so the first
     pages per district ARE the latest listings),
  2. upsert into the master listings DB (price drops tracked automatically),
  3. MMR-score just the new / price-changed records (cohort stats built from
     the full DB so the score matches a --score-db run),
  4. write data/poll_state.json — the UI's poll status bar reads this.

Usage:
    python poller.py                          # one cycle, defaults (D3,5,14,15 · 2,3BR)
    python poller.py --districts 3,15 --beds 2 --max-pages 3
    python poller.py --loop --interval-mins 360   # daemon mode (or just cron the one-shot)

ui.py embeds the same run_poll() in a background thread, so running the UI is
normally enough — this CLI exists for cron and manual refreshes.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import traceback
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import config
import listings_db

DATA_DIR = os.path.join(BASE, "data")
POLL_STATE_FILE = os.path.join(DATA_DIR, "poll_state.json")
MMR_HISTORY_CSV = os.path.join(DATA_DIR, "mmr_history.csv")

DEFAULT_DISTRICTS = [3, 5, 14, 15]
DEFAULT_BEDS = [2, 3]
DEFAULT_MAX_PAGES = 2  # date-desc search → first pages are the newest listings
DEFAULT_INTERVAL_MINS = 360
# Detail-page enrichment of the genuinely-new listings each cycle. The scrape
# only carries card-level fields, so floor_level / facing / latitude (and the
# description that the format_pes / format_loft keyword flags key off) are 0%
# populated without a detail visit. We enrich ONLY the new-this-cycle keys
# (not price-changes, not the whole DB) and cap the batch so a busy cycle can't
# spend an unbounded amount of time on detail pages at 2s each.
DEFAULT_ENRICH_NEW = 20
# Enrichment-target fields a detail page can fill (mirrors the scraper's
# _apply_enrichment set). Only these are written back — never price/sqft.
_ENRICH_FIELDS = ["latitude", "longitude", "facing", "floor_level",
                  "furnishing", "total_units", "developer", "facilities"]


def load_poll_state() -> dict:
    if not os.path.exists(POLL_STATE_FILE):
        return {}
    try:
        with open(POLL_STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_poll_state(state: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = POLL_STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, POLL_STATE_FILE)


def _score_keys(keys: list[str]) -> tuple[int, list[dict]]:
    """Score the given DB keys with the same pipeline as --score-db.

    Cohort stats come from the FULL usable DB so a poll-scored listing gets the
    same number a batch re-score would give it. The DB lock is NOT held while
    scoring (it can take minutes): load a snapshot under the lock, score
    unlocked, then re-acquire and merge ONLY the scored fields (mmr /
    score_1000 / scored_at / score_version) by key into a fresh load — a
    concurrent session's upserts in between survive. Returns (n_scored,
    scored_rows) where scored_rows are compact dicts for the poll log.
    """
    if not keys:
        return 0, []
    from invest import load_ura_cache
    from scoring.full_scorer import FullScorer, build_cohort_stats

    today = datetime.now().strftime("%Y-%m-%d")
    version = config.score_version()

    with listings_db._db_lock():
        snapshot = listings_db.load_db()["listings"]

    usable = [r for r in snapshot.values()
              if r.get("price") and r.get("sqft") and r.get("psf")
              and r.get("status") != "stale"]
    scorer = FullScorer(ura_data=load_ura_cache(), cohort_stats=build_cohort_stats(usable))

    scored: dict[str, dict] = {}
    scored_rows: list[dict] = []
    for key in keys:
        r = snapshot.get(key)
        if not r or not (r.get("price") and r.get("sqft") and r.get("psf")):
            continue
        try:
            s = scorer.score(r)
        except Exception:
            continue
        scored[key] = {"mmr": s.mmr, "score_1000": s.score_1000,
                       "scored_at": today, "score_version": version,
                       **s.three_score_fields()}  # valuation / livability / overalls
        scored_rows.append({
            "id": key,
            "project_name": r.get("project_name") or r.get("title"),
            "district": r.get("district"),
            "beds": r.get("beds"),
            "price": r.get("price"),
            "psf": r.get("psf"),
            "mmr": s.mmr,
            "score_1000": s.score_1000,
            "url": r.get("url"),
        })

    if scored_rows:
        with listings_db._db_lock():
            db = listings_db.load_db()
            store = db["listings"]
            merged = False
            for key, fields in scored.items():
                rec = store.get(key)
                if rec is not None:
                    rec.update(fields)
                    merged = True
            if merged:
                listings_db.save_db(db)
                listings_db.export_sheet(db=db)

        # invest.py appends to the same CSV from other processes — take the
        # shared file lock so concurrent appends can't interleave mid-row.
        with listings_db.file_lock(MMR_HISTORY_CSV + ".lock"):
            write_header = not os.path.exists(MMR_HISTORY_CSV)
            with open(MMR_HISTORY_CSV, "a", newline="") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow(["scored_at", "id", "project_name", "district", "beds",
                                     "price", "psf", "mmr", "score_1000", "score_version"])
                for row in scored_rows:
                    writer.writerow([today, row["id"], row["project_name"], row["district"],
                                     row["beds"], row["price"], row["psf"],
                                     row["mmr"], row["score_1000"], version])
    return len(scored_rows), scored_rows


def _enrich_one(scraper, listing) -> dict:
    """Fetch a single detail page and return its enrichment fields.

    Thin seam over the scraper's own detail primitives so the network-touching
    step is isolated (and stubbable in tests). Navigates to the listing's
    detail URL, then reuses the scraper's `_extract_detail_page` to pull
    lat/lng/facing/floor_level/furnishing/total_units/developer. May raise —
    the caller wraps every call in its own try/except so one bad page can't
    abort the cycle.
    """
    from scrapers.propertyguru import (
        PROPERTYGURU_BASE_URL, PAGE_LOAD_TIMEOUT, CLOUDFLARE_WAIT_TIMEOUT,
    )
    from scrapers.browser import wait_for_cloudflare

    url = listing.url
    if not url:
        return {}
    if not url.startswith("http"):
        url = f"{PROPERTYGURU_BASE_URL}{url}"

    page = scraper._context.pages[0] if scraper._context.pages else scraper._context.new_page()
    scraper._page = page
    page.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
    wait_for_cloudflare(page, timeout_ms=CLOUDFLARE_WAIT_TIMEOUT)
    return scraper._extract_detail_page() or {}


def _enrich_new_keys(keys: list[str], headless: bool = True,
                     cap: int = DEFAULT_ENRICH_NEW) -> dict:
    """Detail-enrich the given new-this-cycle DB keys (best-effort).

    Opens one browser, visits each key's detail page (capped at `cap`, with the
    standard 2s inter-detail delay), and merges only the enrichment fields back
    into the DB record under the lock — refreshing `ingest_flags` so the
    keyword/format flags re-fire on any newly-present field. Every detail fetch
    is wrapped in its own try/except: a single failure increments `failed` and
    the cycle continues. Returns {"enriched", "failed", "attempted"}.

    Re-persist is a targeted field merge (NOT a re-upsert) so it never bumps
    times_seen / last_seen or manufactures a phantom price-history event.
    """
    result = {"enriched": 0, "failed": 0, "attempted": 0}
    if not keys:
        return result

    keys = keys[:cap]

    # Snapshot the records we need under the lock; build lightweight Listing
    # objects (id + url + the enrich-target fields, so _apply_enrichment's
    # "only fill blanks" guard sees what's already known).
    from models import Listing
    with listings_db._db_lock():
        store = listings_db.load_db()["listings"]
        targets: list[tuple[str, "Listing"]] = []
        for key in keys:
            rec = store.get(key)
            if not rec or not rec.get("url"):
                continue
            l = Listing(id=str(rec.get("id") or key),
                        title=rec.get("title") or "",
                        price=rec.get("price") or 0,
                        url=rec.get("url"))
            for fld in _ENRICH_FIELDS:
                if rec.get(fld) is not None:
                    setattr(l, fld, rec[fld])
            targets.append((key, l))

    if not targets:
        return result

    from scrapers.browser import BrowserManager
    from scrapers.propertyguru import PropertyGuruScraper
    from config import DETAIL_PAGE_DELAY

    enriched_fields: dict[str, dict] = {}
    try:
        with BrowserManager(headless=headless) as context:
            scraper = PropertyGuruScraper(context)
            for i, (key, listing) in enumerate(targets):
                result["attempted"] += 1
                try:
                    detail = _enrich_one(scraper, listing)
                    changed = {f: detail[f] for f in _ENRICH_FIELDS
                               if detail.get(f) is not None
                               and getattr(listing, f, None) in (None, "", 0, 0.0)}
                    if changed:
                        enriched_fields[key] = changed
                        result["enriched"] += 1
                except Exception as e:  # noqa: BLE001 — best-effort per listing
                    result["failed"] += 1
                    print(f"  enrich failed for {key}: {type(e).__name__}: {e}",
                          file=sys.stderr)
                if i < len(targets) - 1:
                    time.sleep(DETAIL_PAGE_DELAY)
    except Exception as e:  # noqa: BLE001 — browser open / context failure
        # Whole enrich pass failed to even start: don't lose what we collected
        # so far, but record the remainder as failed and keep the cycle alive.
        result["failed"] += len(targets) - result["attempted"]
        print(f"  enrichment browser pass aborted: {type(e).__name__}: {e}",
              file=sys.stderr)

    # Merge collected fields under the lock; refresh ingest_flags per record.
    if enriched_fields:
        with listings_db._db_lock():
            db = listings_db.load_db()
            store = db["listings"]
            merged = False
            for key, fields in enriched_fields.items():
                rec = store.get(key)
                if rec is None:
                    continue
                rec.update(fields)
                rec["ingest_flags"] = listings_db.compute_ingest_flags(rec)
                merged = True
            if merged:
                listings_db.save_db(db)
                listings_db.export_sheet(db=db)

    return result


def run_poll(
    districts: list[int] | None = None,
    beds: list[int] | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    headless: bool = True,
    min_price: int | None = None,
    max_price: int | None = None,
    enrich_new: int = DEFAULT_ENRICH_NEW,
) -> dict:
    """One full poll cycle. Never raises — errors land in the returned state."""
    districts = districts or DEFAULT_DISTRICTS
    beds = beds or DEFAULT_BEDS
    t0 = time.monotonic()
    state: dict = {
        "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ok": False,
        "districts": districts,
        "beds": beds,
        "max_pages": max_pages,
    }

    before = {k: (r.get("price") or 0) for k, r in listings_db.load_db()["listings"].items()}

    try:
        from invest import scrape_listings
        listings = scrape_listings(
            districts=districts, min_price=min_price, max_price=max_price,
            beds=beds, max_pages=max_pages, headless=headless, enrich_top=0,
        )
        state["scraped"] = len(listings)
        if listings:
            # Batch sanity gate (#14b): one PG redesign must not poison the
            # DB. A failing batch aborts the upsert LOUDLY (poll_state.error).
            gate_ok, gate = listings_db.check_batch_sanity(listings)
            state["sanity_gate"] = gate
            if not gate_ok:
                raise listings_db.BatchSanityError(
                    f"scrape batch failed pre-upsert invariants "
                    f"({gate['passed']}/{gate['batch']} sane, "
                    f"fail_reasons={gate['fail_reasons']}) — upsert aborted")
            stats = listings_db.upsert_listings(
                listings, source={"flow": "poll", "districts": districts})
            state.update({k: stats[k] for k in ("added", "updated", "price_changes")})

        after = listings_db.load_db()["listings"]
        new_keys = [k for k in after if k not in before]
        changed_keys = [k for k, r in after.items()
                        if k in before and (r.get("price") or 0) != before[k]]

        # Detail-enrich ONLY the genuinely-new keys BEFORE scoring, so their
        # scores reflect floor/facing data and v3.8 floor-aware comps. Best-
        # effort: a per-listing failure is counted, never fatal.
        if enrich_new > 0 and new_keys:
            enr = _enrich_new_keys(new_keys, headless=headless, cap=enrich_new)
            state["enriched"] = enr["enriched"]
            state["enrich_failed"] = enr["failed"]
            # `attempted` distinguishes the two ways enrichment reports zero:
            # never visited a detail page, vs visited and extracted nothing.
            # Without it both look like "0 enriched / 0 failed" and a silently
            # broken extractor is indistinguishable from a no-op — which is how
            # floor_level/facing sat at 0% coverage across 9k listings while the
            # poll reported success every cycle.
            state["enrich_attempted"] = enr["attempted"]
            if enr["attempted"] and not enr["enriched"]:
                msg = (f"enrichment extracted NOTHING from {enr['attempted']} "
                       f"detail page(s) — the detail extractor is likely broken "
                       f"(PG markup change). floor_level/facing stay empty, and "
                       f"the v3.12 low-floor demotion cannot fire without them.")
                state["enrich_warning"] = msg
                print(f"  ⚠ {msg}", file=sys.stderr)
        else:
            state["enriched"] = 0
            state["enrich_failed"] = 0
            state["enrich_attempted"] = 0

        n_scored, scored_rows = _score_keys(new_keys + changed_keys)
        state["scored"] = n_scored
        scored_rows.sort(key=lambda r: r.get("score_1000") or 0, reverse=True)
        new_set, changed_set = set(new_keys), set(changed_keys)
        state["new_listings"] = [r for r in scored_rows if r["id"] in new_set]
        # Price-changed listings are surfaced separately (the daily scan treats a
        # listing that dropped INTO the gate as a candidate, same as a new one).
        # `old_price` lets a consumer show the drop without re-reading history.
        state["changed_listings"] = [{**r, "old_price": before.get(r["id"])}
                                     for r in scored_rows if r["id"] in changed_set]
        if listings:
            # End-of-poll staleness sweep (#3): young listings absent from the
            # newest pages of their own (district, beds) scope accrue misses
            # and go stale at the threshold — kills phantom "NEW" inventory.
            state["stale_sweep"] = listings_db.sweep_staleness(
                listings, districts=districts, beds=beds)
        state["ok"] = True
    except Exception as e:
        state["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc(file=sys.stderr)

    state["duration_s"] = round(time.monotonic() - t0, 1)
    _save_poll_state(state)

    n_new = len(state.get("new_listings", []))
    status = "ok" if state["ok"] else f"FAILED ({state.get('error')})"
    print(f"\nPoll {status}: {state.get('scraped', 0)} scraped, "
          f"+{state.get('added', 0)} new, {state.get('price_changes', 0)} price changes, "
          f"{state.get('enriched', 0)} enriched/{state.get('enrich_failed', 0)} failed, "
          f"{state.get('scored', 0)} scored, {n_new} new scored "
          f"({state['duration_s']}s) -> {POLL_STATE_FILE}", file=sys.stderr)
    return state


def main():
    ap = argparse.ArgumentParser(description="Poll PropertyGuru for the latest listings")
    ap.add_argument("--districts", type=str, default=None,
                    help=f"comma-separated district numbers (default {DEFAULT_DISTRICTS})")
    ap.add_argument("--beds", type=str, default=None,
                    help=f"comma-separated bed counts (default {DEFAULT_BEDS})")
    ap.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                    help="pages per district — newest first, so 2-3 is plenty")
    ap.add_argument("--min-price", type=int, default=None)
    ap.add_argument("--max-price", type=int, default=None)
    ap.add_argument("--no-headless", action="store_true")
    ap.add_argument("--enrich-new", type=int, default=DEFAULT_ENRICH_NEW,
                    help=f"detail-enrich up to N new-this-cycle listings "
                         f"(floor/facing/lat; default {DEFAULT_ENRICH_NEW})")
    ap.add_argument("--no-enrich", action="store_true",
                    help="skip detail-page enrichment of new listings")
    ap.add_argument("--loop", action="store_true", help="poll forever on an interval")
    ap.add_argument("--interval-mins", type=int, default=DEFAULT_INTERVAL_MINS)
    args = ap.parse_args()

    districts = [int(d) for d in args.districts.split(",")] if args.districts else None
    beds = [int(b) for b in args.beds.split(",")] if args.beds else None
    enrich_new = 0 if args.no_enrich else args.enrich_new

    while True:
        run_poll(districts=districts, beds=beds, max_pages=args.max_pages,
                 headless=not args.no_headless,
                 min_price=args.min_price, max_price=args.max_price,
                 enrich_new=enrich_new)
        if not args.loop:
            break
        print(f"Next poll in {args.interval_mins} min (Ctrl-C to stop)", file=sys.stderr)
        try:
            time.sleep(args.interval_mins * 60)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
