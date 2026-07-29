#!/usr/bin/env python3
"""Per-PROJECT realsmart.sg facts, fetched once and cached.

Why per-project and not per-listing: REALSCORE, % profitable and annualized
return are properties of the *development*, so twenty listings in one condo
share one answer. Deduping by project turns "thousands of fetches" into a few
dozen, which keeps this a research lookup rather than a crawl — realsmart's ToS
is personal-use — and makes it fast enough to run BEFORE the gate instead of
inside the agent step.

Values barely move month to month, so the cache has a long TTL and a scan
normally fetches nothing at all.

    python realsmart_cache.py --projects "JadeScape,Regentville"   # warm these
    python realsmart_cache.py --from-db --districts 16,18,19       # warm a scope
    python realsmart_cache.py --show "Regentville"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import listings_db
import realsmart

DATA_DIR = os.path.join(BASE, "data")
CACHE_FILE = os.path.join(DATA_DIR, "realsmart_projects.json")
STORE = os.environ.get(
    "PF_STORE", os.path.expanduser("~/Sideproject/personal-data-store"))
FETCH_TOOL = os.path.join(
    STORE, ".claude", "skills", "web-extract", "scripts", "fetch.py")

CACHE_TTL_DAYS = 45          # project-level stats are slow-moving
POLITE_DELAY_S = 2.0         # spacing between fetches; this is a slow lookup
FETCH_TIMEOUT_S = 180


def load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _merge_save(new_entries: dict) -> dict:
    """Merge entries into the on-disk cache under a lock, and return the result.

    Read-modify-write, so it MUST be serialized: two warm runs in parallel each
    loaded the cache, added their own projects and wrote the whole dict back,
    and the second write silently erased the first's entries. That is exactly
    how ten freshly-fetched projects vanished — the fetches all succeeded and
    reported success, and the data was simply overwritten.

    Merging (rather than writing a whole snapshot) also means a stale in-memory
    copy can no longer drop another process's rows.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    with listings_db.file_lock(CACHE_FILE + ".lock"):
        cache = load_cache()
        cache.update(new_entries)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cache, f, indent=1, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    return cache


# --------------------------------------------------------------------------- #
# Parsing
#
# The page uses TWO layouts and mixing them up silently yields wrong numbers:
#
#   * stat tiles are VALUE-then-LABEL      "71.5%" / "% Profitable"
#   * the two header stats are LABEL-then-SUBTITLE-then-VALUE
#         "REALSCORE" / "Profitability rank across Singapore" / "3.7"
#
# The subtitles contain digits ("Avg annualized profit (past 1y)"), so a naive
# "first number after the label" read returns 1.0 for a 4.7% figure. Both
# layouts are handled explicitly below.
#
# Tolerant by design: a missing stat is None, never an exception — uncompleted
# projects legitimately show N.A. for profitability and rental.
# --------------------------------------------------------------------------- #
def _num(text: str):
    m = re.search(r"-?\d+(?:\.\d+)?", (text or "").replace(",", ""))
    return float(m.group()) if m else None


def _count(text: str):
    """A bare transaction count — rejects percentages.

    The page carries a highlights badge reading "100%" / "Profitable" ABOVE the
    real "584" / "Profitable" tile. Both match the same label, and taking the
    badge would record a project's profitable-resale COUNT as 100. Counts are
    never written with a % sign, so that alone separates them.
    """
    t = (text or "").strip()
    if "%" in t or not re.fullmatch(r"[\d,]+", t):
        return None
    return _num(t)


# Label -> (key, converter). These render as value-then-label tiles.
_TILES = {
    "% Profitable": ("pct_profitable", _num),
    "Profitable": ("profitable_txns", _count),
    "Unprofitable": ("unprofitable_txns", _count),
    "> 6% Annualized": ("above_6pct_txns", _count),
    "Avg Holding": ("avg_holding_yrs", _num),
    "Total Profits": ("total_profits", str),
    "Total Transacted": ("total_transacted", str),
    "Total Transactions": ("total_transactions", _count),
    "First Transacted": ("first_transacted", str),
    "Last Transacted": ("last_transacted", str),
    "Sold At Launch": ("pct_sold_at_launch", _num),
    "HDB Buyers": ("pct_hdb_buyers", _num),
}
# Label -> key for the label/subtitle/value header stats.
_HEADERS = {r"^REALSCORE": "realscore", r"^Annual Returns$": "annual_return_pct"}
# Plain label/value rows in the project-details table.
_ROWS = {"Tenure": "tenure", "Completion": "completion",
         "Total Units": "total_units", "Developer": "developer",
         "Property Type": "property_type", "Street": "street"}


def parse_project_page(text: str) -> dict:
    """Pull the stats we care about out of the fetched page text."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out: dict = {k: None for k, _ in
                 list(_TILES.values()) + [(v, None) for v in _HEADERS.values()]}

    for i, ln in enumerate(lines):
        # value-then-label tiles: take the line above, first match wins so the
        # detailed stat beats any duplicate badge elsewhere on the page.
        spec = _TILES.get(ln)
        if spec and i:
            key, conv = spec
            if out.get(key) is None:
                val = conv(lines[i - 1])
                if val is not None:      # a rejected badge leaves the slot open
                    out[key] = val       # for the real tile further down
            continue

        # label / subtitle / value headers: skip forward past the subtitle to
        # the first line that is *just* a number.
        for rx, key in _HEADERS.items():
            if re.search(rx, ln, re.I) and out.get(key) is None:
                for nxt in lines[i + 1:i + 5]:
                    if re.fullmatch(r"[\d.,]+%?", nxt):
                        out[key] = _num(nxt)
                        break
                break

        # plain label/value rows
        if ln in _ROWS and i + 1 < len(lines):
            out.setdefault(_ROWS[ln], lines[i + 1])

    # Derived: the honest denominator behind pct_profitable. A high % on a
    # handful of resales is noise, so callers need the count to judge it.
    p, u = out.get("profitable_txns"), out.get("unprofitable_txns")
    out["resale_txns"] = int(p + u) if p is not None and u is not None else None
    return out


READER_TOOL = os.path.join(
    STORE, ".claude", "skills", "web-extract", "scripts", "reader.py")
RENDER_TIMEOUT_S = 300


def _fetch_text(url: str) -> str | None:
    """Rung 1 — plain fetch of the public /p/<slug> page. Cheap, no browser."""
    try:
        proc = subprocess.run([sys.executable, FETCH_TOOL, url],
                              capture_output=True, text=True,
                              timeout=FETCH_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = proc.stdout or ""
    if "VERDICT: ok" not in out:
        return None
    _, _, body = out.partition("----- CONTENT -----")
    return body or None


def _render_map(project_name: str) -> str | None:
    """Rung 2 — the logged-in /map SPA, rendered.

    realsmart serves at least two /p/<slug> templates: a full one carrying
    REALSCORE and the profitability tiles (JadeScape, Regentville, FLO
    Residence), and a lite one with only basic facts (Palm Gardens). The lite
    template has no REALSCORE at any depth — rendering it does not help — but
    the /map view has the numbers for those same projects.

    So this is a genuine escalation rung, not a retry: only projects whose /p
    page lacks the score pay the cost of a browser render. Needs the persistent
    logged-in profile; if the session has expired the output is the login shell
    and the caller simply records no score.
    """
    q = project_name.upper().replace(" ", "%20")
    url = f"https://realsmart.sg/map?id={q}&mode=c"
    try:
        proc = subprocess.run(
            [sys.executable, READER_TOOL, url, "--wait", "10", "--max", "20000"],
            capture_output=True, text=True, timeout=RENDER_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = proc.stdout or ""
    if "Get Superpowered" in out or "REALSCORE" not in out:
        return None
    return out


def get(project_name: str, cache: dict | None = None,
        refresh: bool = False) -> dict | None:
    """Cached realsmart facts for one project. None when unresolvable."""
    cache = load_cache() if cache is None else cache
    key = realsmart.normalize(project_name)
    if not key:
        return None
    hit = cache.get(key)
    if hit and not refresh:
        age = (time.time() - hit.get("fetched_at", 0)) / 86400
        if age < CACHE_TTL_DAYS:
            return hit

    url, resolved = realsmart.url_for(project_name)
    text = _fetch_text(url)
    rec = parse_project_page(text) if text else {}
    source = "p" if text else None

    # Escalate only when the cheap page carried no score — see _render_map.
    if rec.get("realscore") is None:
        rendered = _render_map(project_name)
        if rendered:
            better = parse_project_page(rendered)
            if better.get("realscore") is not None:
                # Keep any facts the /p page had that /map omits.
                for k, v in rec.items():
                    better.setdefault(k, v)
                    if better.get(k) is None and v is not None:
                        better[k] = v
                rec, source = better, "map"

    if not rec:
        # Record the miss so a scan doesn't retry a dead slug every week.
        miss = {"project": project_name, "url": url, "resolved": resolved,
                "fetched_at": time.time(), "ok": False}
        cache.update(_merge_save({key: miss}))
        return miss

    rec.update({"project": project_name, "url": url, "resolved": resolved,
                "source": source, "fetched_at": time.time(),
                "ok": rec.get("realscore") is not None})
    cache.update(_merge_save({key: rec}))
    return rec


def warm(project_names, refresh: bool = False, verbose: bool = True) -> dict:
    """Fetch every project not already cached. Returns {name: record}."""
    cache = load_cache()
    todo, out = [], {}
    for n in dict.fromkeys(project_names):          # dedupe, keep order
        key = realsmart.normalize(n)
        hit = cache.get(key)
        fresh = hit and (time.time() - hit.get("fetched_at", 0)) / 86400 < CACHE_TTL_DAYS
        if fresh and not refresh:
            out[n] = hit
        else:
            todo.append(n)

    if verbose:
        print(f"realsmart: {len(out)} cached, {len(todo)} to fetch",
              file=sys.stderr)
    for i, n in enumerate(todo, 1):
        rec = get(n, cache=cache, refresh=refresh)
        out[n] = rec
        if verbose:
            mark = (f"REALSCORE {rec.get('realscore')} · "
                    f"{rec.get('pct_profitable')}% profitable"
                    if rec and rec.get("ok") else "NOT FOUND")
            print(f"  [{i}/{len(todo)}] {n}: {mark}", file=sys.stderr)
        if i < len(todo):
            time.sleep(POLITE_DELAY_S)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Warm the realsmart project cache")
    ap.add_argument("--projects", type=str, help="comma-separated project names")
    ap.add_argument("--from-db", action="store_true",
                    help="every project in the listings DB matching --districts")
    ap.add_argument("--districts", type=str, default=None)
    ap.add_argument("--min-score", type=int, default=None,
                    help="only projects with a listing at/above this score_1000")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--show", type=str)
    args = ap.parse_args()

    if args.show:
        rec = get(args.show, refresh=args.refresh)
        print(json.dumps(rec, indent=2, ensure_ascii=False))
        return 0

    names: list[str] = []
    if args.projects:
        names += [p.strip() for p in args.projects.split(",") if p.strip()]
    if args.from_db:
        import listings_db
        want = {int(d) for d in args.districts.split(",")} if args.districts else None
        for r in listings_db.load_db()["listings"].values():
            n = r.get("project_name")
            if not n:
                continue
            if want is not None:
                d = "".join(c for c in str(r.get("district") or "") if c.isdigit())
                if not d or int(d) not in want:
                    continue
            if args.min_score and (r.get("score_1000") or 0) < args.min_score:
                continue
            names.append(n)

    if not names:
        print("nothing to warm (use --projects or --from-db)", file=sys.stderr)
        return 1
    got = warm(names, refresh=args.refresh)
    ok = sum(1 for r in got.values() if r and r.get("ok"))
    print(f"\n{ok}/{len(got)} projects have realsmart data -> {CACHE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
