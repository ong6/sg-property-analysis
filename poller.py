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

import listings_db

DATA_DIR = os.path.join(BASE, "data")
POLL_STATE_FILE = os.path.join(DATA_DIR, "poll_state.json")
MMR_HISTORY_CSV = os.path.join(DATA_DIR, "mmr_history.csv")

DEFAULT_DISTRICTS = [3, 5, 14, 15]
DEFAULT_BEDS = [2, 3]
DEFAULT_MAX_PAGES = 2  # date-desc search → first pages are the newest listings
DEFAULT_INTERVAL_MINS = 360


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
    same number a batch re-score would give it. Returns (n_scored, scored_rows)
    where scored_rows are compact dicts for the poll log.
    """
    if not keys:
        return 0, []
    from invest import load_ura_cache
    from scoring.full_scorer import FullScorer, build_cohort_stats

    today = datetime.now().strftime("%Y-%m-%d")
    scored_rows: list[dict] = []
    # Hold the DB lock for the whole read-modify-write: a concurrent session's
    # upsert between our load and save would otherwise be dropped.
    with listings_db._db_lock():
        db = listings_db.load_db()
        store = db["listings"]
        usable = [r for r in store.values() if r.get("price") and r.get("sqft") and r.get("psf")]
        scorer = FullScorer(ura_data=load_ura_cache(), cohort_stats=build_cohort_stats(usable))

        for key in keys:
            r = store.get(key)
            if not r or not (r.get("price") and r.get("sqft") and r.get("psf")):
                continue
            try:
                s = scorer.score(r)
            except Exception:
                continue
            r["mmr"] = s.mmr
            r["score_1000"] = s.score_1000
            r["scored_at"] = today
            scored_rows.append({
                "id": key,
                "project_name": r.get("project_name") or r.get("title"),
                "district": r.get("district"),
                "beds": r.get("beds"),
                "price": r.get("price"),
                "psf": r.get("psf"),
                "score_1000": s.score_1000,
                "url": r.get("url"),
            })
        if scored_rows:
            listings_db.save_db(db)
            listings_db.export_sheet(db=db)

    if scored_rows:
        write_header = not os.path.exists(MMR_HISTORY_CSV)
        with open(MMR_HISTORY_CSV, "a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(["scored_at", "id", "project_name", "district", "beds",
                                 "price", "psf", "mmr", "score_1000"])
            for row in scored_rows:
                rec = db["listings"].get(row["id"], {})
                writer.writerow([today, row["id"], row["project_name"], row["district"],
                                 row["beds"], row["price"], row["psf"],
                                 rec.get("mmr"), row["score_1000"]])
    return len(scored_rows), scored_rows


def run_poll(
    districts: list[int] | None = None,
    beds: list[int] | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    headless: bool = True,
    min_price: int | None = None,
    max_price: int | None = None,
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
            stats = listings_db.upsert_listings(
                listings, source={"flow": "poll", "districts": districts})
            state.update({k: stats[k] for k in ("added", "updated", "price_changes")})

        after = listings_db.load_db()["listings"]
        new_keys = [k for k in after if k not in before]
        changed_keys = [k for k, r in after.items()
                        if k in before and (r.get("price") or 0) != before[k]]
        n_scored, scored_rows = _score_keys(new_keys + changed_keys)
        state["scored"] = n_scored
        scored_rows.sort(key=lambda r: r.get("score_1000") or 0, reverse=True)
        state["new_listings"] = [r for r in scored_rows if r["id"] in set(new_keys)]
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
    ap.add_argument("--loop", action="store_true", help="poll forever on an interval")
    ap.add_argument("--interval-mins", type=int, default=DEFAULT_INTERVAL_MINS)
    args = ap.parse_args()

    districts = [int(d) for d in args.districts.split(",")] if args.districts else None
    beds = [int(b) for b in args.beds.split(",")] if args.beds else None

    while True:
        run_poll(districts=districts, beds=beds, max_pages=args.max_pages,
                 headless=not args.no_headless,
                 min_price=args.min_price, max_price=args.max_price)
        if not args.loop:
            break
        print(f"Next poll in {args.interval_mins} min (Ctrl-C to stop)", file=sys.stderr)
        try:
            time.sleep(args.interval_mins * 60)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
