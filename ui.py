#!/usr/bin/env python3
"""Local listings UI — fresh PropertyGuru listings first, arena rankings second.

The default view answers the question this tool exists for: "what NEW listings
hit the market recently that score well?" — fresh listings from the master DB,
sorted by MMR score, with NEW / price-drop badges. The arena tab keeps the
tournament view. A built-in poller (poller.py) scrapes PropertyGuru on an
interval so the fresh view stays current without manual runs.

Zero third-party dependencies (stdlib http.server + the project's own modules).

Usage:
    python ui.py                          # serve http://127.0.0.1:8642, auto-poll every 6h
    python ui.py --no-poll                # serve only, never scrape
    python ui.py --poll-interval-mins 120 # poll more often
    python ui.py --poll-districts 3,15 --poll-beds 2
    python ui.py --port 9000 --no-browser

JSON APIs (for scripting): /api/fresh · /api/rankings · /api/poll-status
POST /poll triggers a scrape cycle (409 if one is already running).
"""

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import listings_db
import poller
from scoring.livability import score_livability

DATA_DIR = os.path.join(BASE, "data")
ARENA_CSV = os.path.join(DATA_DIR, "arena_results.csv")
DB_FILE = os.path.join(DATA_DIR, "listings_db.json")
DEFAULT_PORT = 8642
FRESH_MAX_AGE_DAYS = 30  # server-side cap; the client narrows further


# ---------------------------------------------------------------------------
# Data: fresh listings (the main view)
# ---------------------------------------------------------------------------
def _days_since(date_str):
    if not date_str:
        return None
    try:
        return (datetime.now() - datetime.strptime(date_str, "%Y-%m-%d")).days
    except ValueError:
        return None


def _eval_lookup() -> dict:
    """{normalized condo name: (latest_rating, latest_date)} from eval memory.

    The fresh view is sorted by score — without the agent's verdict next to it,
    a high-ranking listing the agent rated Avoid reads as a top pick (the
    v3.6 divergence failure mode, at the UI layer)."""
    try:
        import eval_memory
        idx = eval_memory.load_index().get("condos", {})
    except Exception:
        return {}
    out = {}
    for rec in idx.values():
        name = re.sub(r"[^a-z0-9]", "", (rec.get("condo") or "").lower())
        if name and rec.get("latest_rating"):
            out[name] = (rec["latest_rating"], rec.get("latest_date") or "")
    return out


def load_fresh() -> dict:
    """Active listings first seen within FRESH_MAX_AGE_DAYS, score-annotated."""
    db = listings_db.load_db()
    evals = _eval_lookup()
    rows = []
    for key, rec in db["listings"].items():
        if rec.get("status") == "stale":
            continue
        days_old = _days_since(rec.get("first_seen"))
        if days_old is None or days_old > FRESH_MAX_AGE_DAYS:
            continue
        history = rec.get("price_history") or []
        prices = [p.get("price") for p in history if p.get("price")]
        drop_pct = None
        if prices:
            peak = max(prices)
            if peak > 0 and prices[-1] < peak:
                drop_pct = round((prices[-1] - peak) / peak * 100, 1)
        name = rec.get("project_name") or rec.get("title") or "?"
        maps_query = urllib.parse.quote(f"{name} condo Singapore")
        liv = score_livability(rec)
        rows.append({
            "id": key,
            "project_name": name,
            "beds": rec.get("beds"),
            "baths": rec.get("baths"),
            "sqft": rec.get("sqft"),
            "price": rec.get("price"),
            "psf": rec.get("psf"),
            "district": rec.get("district") or "",
            "region": rec.get("region") or "",
            "tenure": rec.get("tenure") or "",
            "built_year": rec.get("built_year"),
            "floor_level": rec.get("floor_level") or "",
            "mrt_info": rec.get("mrt_info") or "",
            "score_1000": rec.get("score_1000"),
            "livability": liv["score"] if liv["signals"] else None,
            "liv_why": liv["why"],
            "agent_rating": (er := evals.get(re.sub(r"[^a-z0-9]", "", name.lower()), ("", "")))[0],
            "agent_eval_date": er[1],
            "scored_at": rec.get("scored_at") or "",
            "first_seen": rec.get("first_seen") or "",
            "days_old": days_old,
            "price_trend": listings_db._price_trend(history),
            "drop_pct": drop_pct,
            "times_seen": rec.get("times_seen", 1),
            "url": rec.get("url") or "",
            "maps_url": f"https://www.google.com/maps/search/?api=1&query={maps_query}",
        })
    # Same physical unit, many agents: collapse identical (project, beds,
    # sqft, price) rows into one — five copies of one Coco Palms ask once
    # occupied ranks 42-46 by themselves. Keep the most recently seen copy.
    best: dict = {}
    for r in rows:
        k = (r["project_name"].lower(), r["beds"], round(r["sqft"] or 0), r["price"])
        cur = best.get(k)
        if cur is None:
            r["dup_count"] = 1
            best[k] = r
        else:
            cur["dup_count"] += 1
            if (r["first_seen"] or "") > (cur["first_seen"] or ""):
                r["dup_count"] = cur["dup_count"]
                best[k] = r
    rows = list(best.values())

    # Best score first, unscored last, newest as tiebreak
    rows.sort(key=lambda r: (-(r["score_1000"] if r["score_1000"] is not None else -1),
                             r["days_old"]))
    return {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "max_age_days": FRESH_MAX_AGE_DAYS, "rows": rows}


# ---------------------------------------------------------------------------
# Data: arena rankings (unchanged behavior from the previous UI)
# ---------------------------------------------------------------------------
def load_rankings() -> dict:
    """Latest arena run joined with listing details.

    Returns {"run_date": str|None, "rows": [dict]} — rows sorted by rank.
    """
    if not os.path.exists(ARENA_CSV):
        return {"run_date": None, "rows": []}

    with open(ARENA_CSV, newline="") as f:
        all_rows = list(csv.DictReader(f))
    if not all_rows:
        return {"run_date": None, "rows": []}

    # Tolerate legacy CSVs without a run_date column (None from DictReader)
    latest = max((r.get("run_date") or "" for r in all_rows), default="")
    if not latest:
        return {"run_date": None, "rows": []}
    rows = [r for r in all_rows if r.get("run_date") == latest]

    # Join listing details by URL
    details = {}
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE) as f:
                db = json.load(f)
            for rec in db.get("listings", {}).values():
                if rec.get("url"):
                    details[rec["url"]] = rec
        except (json.JSONDecodeError, OSError):
            pass

    # Live eval ratings override the fight-time CSV snapshot — re-evaluations
    # land between arena runs and the stale verdict is exactly what this
    # column exists to prevent.
    evals = _eval_lookup()

    out = []
    for r in rows:
        rec = details.get(r.get("url", ""), {})
        name = r.get("project_name") or rec.get("project_name") or rec.get("title") or "?"
        live = evals.get(re.sub(r"[^a-z0-9]", "", name.lower()))
        maps_query = urllib.parse.quote(f"{name} condo Singapore")
        out.append({
            "bracket": r.get("bracket") or "open",
            "rank": int(r["rank"]),
            "project_name": name,
            "beds": r.get("beds") or rec.get("beds") or "",
            "elo": int(float(r["elo"])) if r.get("elo") else None,
            "record": f"{r.get('wins', '?')}-{r.get('losses', '?')}-{r.get('draws', '?')}",
            "win_rate_pct": int(float(r["win_rate_pct"])) if r.get("win_rate_pct") else 0,
            "on_frontier": r.get("on_frontier") == "1",
            "mmr": float(r["mmr"]) if r.get("mmr") else None,
            "score_1000": int(float(r["score_1000"])) if r.get("score_1000") else None,
            "price": int(float(r["price"])) if r.get("price") else None,
            "psf": float(r["psf"]) if r.get("psf") else None,
            "district": r.get("district") or rec.get("district") or "",
            "agent_rating": (live[0] if live else r.get("agent_rating")) or "",
            "agent_eval_date": (live[1] if live else r.get("agent_eval_date")) or "",
            "sqft": rec.get("sqft"),
            "built_year": rec.get("built_year"),
            "tenure": rec.get("tenure") or "",
            "mrt_info": rec.get("mrt_info") or "",
            "url": r.get("url") or "",
            "maps_url": f"https://www.google.com/maps/search/?api=1&query={maps_query}",
        })

    out.sort(key=lambda x: (x["bracket"], x["rank"]))
    return {"run_date": latest, "rows": out}


# ---------------------------------------------------------------------------
# Poller integration: manual SCAN NOW + background auto-poll
# ---------------------------------------------------------------------------
POLL_CFG = {
    "auto": True,
    "interval_mins": poller.DEFAULT_INTERVAL_MINS,
    "districts": None,   # None -> poller defaults
    "beds": None,
    "max_pages": poller.DEFAULT_MAX_PAGES,
    "headless": True,    # --poll-no-headless: visible Chrome passes Cloudflare
}
_POLL_RUN_LOCK = threading.Lock()       # one scrape at a time
_POLL_STATUS = {"running": False, "started_at": None}
_POLL_STATUS_LOCK = threading.Lock()


def _run_poll_guarded() -> bool:
    """Run one poll cycle if none is running. Returns False if busy."""
    if not _POLL_RUN_LOCK.acquire(blocking=False):
        return False
    with _POLL_STATUS_LOCK:
        _POLL_STATUS["running"] = True
        _POLL_STATUS["started_at"] = time.time()
    try:
        poller.run_poll(districts=POLL_CFG["districts"], beds=POLL_CFG["beds"],
                        max_pages=POLL_CFG["max_pages"], headless=POLL_CFG["headless"])
    finally:
        with _POLL_STATUS_LOCK:
            _POLL_STATUS["running"] = False
        _POLL_RUN_LOCK.release()
    return True


def trigger_poll() -> bool:
    """Fire a poll in a background thread. Returns False if one is running."""
    if _POLL_STATUS["running"]:
        return False
    threading.Thread(target=_run_poll_guarded, daemon=True).start()
    return True


def _last_run_age_s() -> "float | None":
    state = poller.load_poll_state()
    try:
        last = datetime.strptime(state.get("last_run", ""), "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - last).total_seconds()
    except ValueError:
        return None


def _auto_poll_loop():
    """Poll whenever the last run is older than the configured interval.

    Checks once a minute; the first poll therefore starts ~60s after launch
    (when due), so the UI always opens instantly even on a stale DB.
    """
    interval_s = POLL_CFG["interval_mins"] * 60
    while True:
        time.sleep(60)
        age = _last_run_age_s()
        if age is None or age >= interval_s:
            _run_poll_guarded()


def poll_status() -> dict:
    state = poller.load_poll_state()
    age = _last_run_age_s()
    out = {
        "running": _POLL_STATUS["running"],
        "running_for_s": round(time.time() - _POLL_STATUS["started_at"])
                         if _POLL_STATUS["running"] and _POLL_STATUS["started_at"] else None,
        "last": state or None,
        "last_age_s": round(age) if age is not None else None,
        "auto": POLL_CFG["auto"],
        "interval_mins": POLL_CFG["interval_mins"],
    }
    if POLL_CFG["auto"] and age is not None and not _POLL_STATUS["running"]:
        out["next_in_s"] = max(0, round(POLL_CFG["interval_mins"] * 60 - age))
    return out


# ---------------------------------------------------------------------------
# Page (plain template — __TOKENS__ replaced server-side, no .format escaping)
# ---------------------------------------------------------------------------
_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Property Finder — Fresh Listings</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>📡</text></svg>">
<style>
  :root { --bg:#0f1419; --panel:#1a2129; --line:#2b3543; --text:#dce3ea;
          --dim:#8b98a5; --gold:#e3b341; --green:#3fb950; --red:#f85149; --blue:#58a6ff; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:14px/1.45 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
  header { padding:16px 24px 0; display:flex; align-items:baseline; gap:18px; flex-wrap:wrap; }
  h1 { margin:0; font-size:20px; }
  .tabs { display:flex; gap:2px; margin-left:auto; }
  .tab { background:transparent; color:var(--dim); border:1px solid transparent;
         border-bottom:none; border-radius:6px 6px 0 0; padding:7px 16px;
         font-size:13.5px; cursor:pointer; }
  .tab.active { background:var(--panel); color:var(--text); border-color:var(--line); }
  .pollbar { display:flex; align-items:center; gap:14px; flex-wrap:wrap;
             margin:10px 24px 0; padding:9px 14px; background:var(--panel);
             border:1px solid var(--line); border-radius:8px; font-size:12.5px;
             color:var(--dim); }
  .pollbar b { color:var(--text); font-weight:600; }
  .pollbar .ok { color:var(--green); } .pollbar .err { color:var(--red); }
  .pollbar button { background:#1c3326; color:var(--green); border:1px solid #2a4434;
                    border-radius:6px; padding:5px 14px; font-size:12.5px; font-weight:600;
                    cursor:pointer; }
  .pollbar button:hover { border-color:var(--green); }
  .pollbar button:disabled { opacity:.55; cursor:default; }
  .spin { display:inline-block; animation:spin 1s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .controls { display:flex; gap:10px; flex-wrap:wrap; padding:12px 24px 14px; align-items:center; }
  .controls input, .controls select, .controls button, .controls label.chk {
    background:var(--panel); color:var(--text); border:1px solid var(--line);
    border-radius:6px; padding:6px 10px; font-size:13px; }
  .controls label.chk { display:flex; gap:6px; align-items:center; cursor:pointer; }
  .sortgrp { display:flex; gap:6px; align-items:center; margin-left:auto; }
  .sortgrp label { color:var(--dim); font-size:12px; }
  .sortgrp button { cursor:pointer; }
  table { border-collapse:collapse; width:100%; }
  thead th { position:sticky; top:0; background:var(--panel); color:var(--dim);
             text-align:left; font-size:11.5px; text-transform:uppercase; letter-spacing:.04em;
             padding:8px 10px; border-bottom:1px solid var(--line); cursor:pointer;
             user-select:none; white-space:nowrap; z-index:1; }
  thead th:hover { color:var(--text); }
  tbody td { padding:7px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }
  tbody tr:hover { background:#202a35; }
  tbody tr.isnew td { background:rgba(63,185,80,.045); }
  .name { font-weight:600; }
  .star { color:var(--gold); }
  .chip { display:inline-block; min-width:42px; text-align:center; padding:2px 7px;
          border-radius:10px; font-weight:600; font-size:12.5px; }
  .badge { display:inline-block; margin-left:7px; padding:1px 7px; border-radius:9px;
           font-size:10.5px; font-weight:700; letter-spacing:.05em; vertical-align:1px; }
  .badge.new { background:#1c3326; color:var(--green); border:1px solid #2a4434; }
  .badge.drop { background:#33201c; color:var(--red); border:1px solid #4a2b25; }
  .badge.dup { background:#222b35; color:var(--dim); border:1px solid var(--line); }
  .links a { color:var(--blue); text-decoration:none; margin-right:10px; }
  .links a:hover { text-decoration:underline; }
  .dim { color:var(--dim); }
  .empty { padding:60px 24px; text-align:center; color:var(--dim); }
  .count { padding:0 24px 4px; color:var(--dim); font-size:12.5px; }
  .count b { color:var(--text); }
  footer { padding:14px 24px 26px; color:var(--dim); font-size:12px; }
  footer code { color:var(--text); }
</style>
</head>
<body>
<header>
  <h1 id="title">📡 Fresh Listings</h1>
  <div class="tabs">
    <button class="tab active" id="tab-fresh" onclick="switchTab('fresh')">📡 Fresh listings</button>
    <button class="tab" id="tab-arena" onclick="switchTab('arena')">🥊 Arena rankings</button>
  </div>
</header>

<div class="pollbar" id="pollbar">loading poll status…</div>

<!-- FRESH VIEW -->
<div id="view-fresh">
  <div class="controls">
    <select id="f-window" onchange="renderFresh()">
      <option value="1">New today</option>
      <option value="3" selected>Last 3 days</option>
      <option value="7">Last 7 days</option>
      <option value="14">Last 14 days</option>
      <option value="30">Last 30 days</option>
    </select>
    <select id="f-minscore" onchange="renderFresh()">
      <option value="">Any score</option>
      <option value="450">≥ 450</option>
      <option value="550" selected>≥ 550 (decent)</option>
      <option value="650">≥ 650 (recommended)</option>
    </select>
    <select id="f-district" onchange="renderFresh()"><option value="">All districts</option></select>
    <select id="f-beds" onchange="renderFresh()">
      <option value="">Any beds</option>
      <option value="1">1BR</option><option value="2">2BR</option>
      <option value="3">3BR</option><option value="4">4BR+</option>
    </select>
    <input id="f-maxprice" type="number" placeholder="Max price $" oninput="renderFresh()">
    <input id="f-q" placeholder="Search condo…" oninput="renderFresh()">
    <label class="chk"><input type="checkbox" id="f-drops" onchange="renderFresh()">⬇ drops only</label>
    <span class="sortgrp">
      <label for="f-sortsel">Sort</label>
      <select id="f-sortsel" onchange="fSetSort(this.value)">
        <option value="score_1000" selected>Score</option>
        <option value="livability">Livability</option>
        <option value="agent_rating">Agent eval</option>
        <option value="days_old">Newest</option>
        <option value="price">Price</option>
        <option value="psf">PSF</option>
        <option value="drop_pct">Price drop</option>
        <option value="project_name">Project</option>
        <option value="district">District</option>
      </select>
      <button id="f-dirbtn" onclick="fToggleDir()" title="flip sort direction">▼</button>
    </span>
  </div>
  <div class="count"><b id="f-shown">0</b> fresh listings shown ·
    <span id="f-tiers"></span></div>
  <table>
    <thead><tr>
      <th onclick="fSortBy('score_1000')" data-key="score_1000" title="MMR money score — backtest-calibrated forward-return signals">Score</th>
      <th onclick="fSortBy('livability')" data-key="livability" title="own-stay heuristic (baths/space/MRT/floor/facing) — separate axis, never part of MMR">Liv</th>
      <th onclick="fSortBy('agent_rating')" data-key="agent_rating">Agent eval</th>
      <th onclick="fSortBy('project_name')" data-key="project_name">Condo</th>
      <th onclick="fSortBy('beds')" data-key="beds">Type</th>
      <th onclick="fSortBy('price')" data-key="price">Price</th>
      <th onclick="fSortBy('psf')" data-key="psf">PSF</th>
      <th onclick="fSortBy('sqft')" data-key="sqft">Sqft</th>
      <th onclick="fSortBy('district')" data-key="district">District</th>
      <th onclick="fSortBy('days_old')" data-key="days_old">First seen</th>
      <th onclick="fSortBy('built_year')" data-key="built_year">Built</th>
      <th>Tenure</th>
      <th>MRT</th>
      <th>Links</th>
    </tr></thead>
    <tbody id="f-rows"></tbody>
  </table>
  <div class="empty" id="f-empty" style="display:none">
    Nothing fresh matches these filters.<br>
    Widen the window / lower the score floor — or hit <b>SCAN NOW</b> above to poll PropertyGuru.
  </div>
</div>

<!-- ARENA VIEW -->
<div id="view-arena" style="display:none">
  <div class="controls">
    <select id="a-bracket" onchange="renderArena()" title="Weight class — like-for-like fights"></select>
    <input id="a-q" placeholder="Search condo…" oninput="renderArena()">
    <select id="a-district" onchange="renderArena()"><option value="">All districts</option></select>
    <input id="a-maxprice" type="number" placeholder="Max price $" oninput="renderArena()">
    <select id="a-frontier" onchange="renderArena()">
      <option value="">All contenders</option>
      <option value="1">Pareto frontier only</option>
    </select>
    <span class="sortgrp">
      <label for="a-sortsel">Sort</label>
      <select id="a-sortsel" onchange="aSetSort(this.value)">
        <option value="rank">Rank</option>
        <option value="project_name">Project</option>
        <option value="beds">BR / type</option>
        <option value="score_1000">MMR/1000</option>
        <option value="elo">Elo</option>
        <option value="win_rate_pct">Win rate</option>
        <option value="price">Price</option>
        <option value="psf">PSF</option>
        <option value="district">District</option>
        <option value="agent_rating">Agent eval</option>
        <option value="built_year">Built</option>
      </select>
      <button id="a-dirbtn" onclick="aToggleDir()" title="flip sort direction">▲</button>
    </span>
  </div>
  <div class="count"><span id="a-shown">0</span> shown · run <span id="a-rundate" class="dim"></span> ·
    ⭐ = Pareto frontier · referee verdict lives in <code>output/arena_latest.md</code></div>
  <table>
    <thead><tr>
      <th onclick="aSortBy('rank')" data-key="rank" data-label="#">#</th>
      <th onclick="aSortBy('project_name')" data-key="project_name" data-label="Condo">Condo</th>
      <th onclick="aSortBy('beds')" data-key="beds" data-label="Type">Type</th>
      <th onclick="aSortBy('elo')" data-key="elo" data-label="Elo">Elo</th>
      <th onclick="aSortBy('win_rate_pct')" data-key="win_rate_pct" data-label="Record">Record</th>
      <th onclick="aSortBy('score_1000')" data-key="score_1000" data-label="MMR/1000">MMR/1000</th>
      <th onclick="aSortBy('price')" data-key="price" data-label="Price">Price</th>
      <th onclick="aSortBy('psf')" data-key="psf" data-label="PSF">PSF</th>
      <th onclick="aSortBy('district')" data-key="district" data-label="District">District</th>
      <th onclick="aSortBy('agent_rating')" data-key="agent_rating" data-label="Agent eval">Agent eval</th>
      <th onclick="aSortBy('built_year')" data-key="built_year" data-label="Built">Built</th>
      <th>MRT</th>
      <th>Links</th>
    </tr></thead>
    <tbody id="a-rows"></tbody>
  </table>
  <div class="empty" id="a-empty" style="display:none">
    No arena results yet. Run <code>python invest.py --fight</code> first, then refresh.
  </div>
</div>

<footer>
  Data: data/listings_db.json (poller) + data/arena_results.csv ·
  APIs: <code>/api/fresh</code> · <code>/api/rankings</code> · <code>/api/poll-status</code> ·
  <code>POST /poll</code> · cron alternative: <code>python poller.py</code> ·
  system dashboard: <code>python dashboard.py</code> → :8643
</footer>

<script>
let FRESH = __FRESH_JSON__;
let ARENA = __ARENA_JSON__;
let POLL = __POLL_JSON__;

const esc = s => { const d = document.createElement("div"); d.textContent = s ?? ""; return d.innerHTML; };
const fmtPrice = p => p ? "$" + (p/1e6).toFixed(2) + "M" : "-";
const fmtPsf = p => p ? "$" + Math.round(p).toLocaleString() : "-";
const scoreColor = s => s >= 650 ? "background:#1c3326;color:#3fb950" :
                        s >= 450 ? "background:#332d1c;color:#e3b341" :
                                   "background:#33201c;color:#f85149";
const livColor = s => s >= 62 ? "background:#16314a;color:#58a6ff" :
                      s >= 42 ? "background:#222b35;color:#8b98a5" :
                                "background:#33201c;color:#f85149";
const livChip = (s, why) => s == null
  ? '<span class="chip" style="background:#222b35;color:#8b98a5" title="no livability signals in this listing\\u2019s data">·</span>'
  : `<span class="chip" style="${livColor(s)}" title="livability (own-stay heuristic, separate from the money score): ${esc(why)}">${s}</span>`;
const scoreChip = s => s == null
  ? '<span class="chip" style="background:#222b35;color:#8b98a5" title="not scored yet — poller scores new listings; run --score-db for the backlog">·</span>'
  : `<span class="chip" style="${scoreColor(s)}">${s}</span>`;
const agoStr = s => s == null ? "?" :
  s < 90 ? Math.round(s) + "s ago" :
  s < 5400 ? Math.round(s/60) + "m ago" :
  s < 129600 ? (s/3600).toFixed(1).replace(/\\.0$/, "") + "h ago" :
  Math.round(s/86400) + "d ago";
const inStr = s => s < 90 ? Math.round(s) + "s" :
  s < 5400 ? Math.round(s/60) + "m" : (s/3600).toFixed(1).replace(/\\.0$/, "") + "h";

/* ---------------- tabs ---------------- */
function switchTab(which) {
  for (const t of ["fresh", "arena"]) {
    document.getElementById("view-" + t).style.display = t === which ? "" : "none";
    document.getElementById("tab-" + t).classList.toggle("active", t === which);
  }
  document.getElementById("title").textContent =
    which === "fresh" ? "📡 Fresh Listings" : "🥊 Condo Arena Rankings";
  location.hash = which;
  if (which === "arena") renderArena(); else renderFresh();
}

/* ---------------- poll bar ---------------- */
function renderPoll() {
  const el = document.getElementById("pollbar");
  const last = POLL.last || {};
  let bits = [];
  if (POLL.running) {
    bits.push(`<span class="spin">⟳</span> <b>polling PropertyGuru…</b> ` +
              `<span class="dim">started ${agoStr(POLL.running_for_s)}</span>`);
  } else if (last.last_run) {
    const ok = last.ok;
    bits.push(`last poll <b>${esc(last.last_run)}</b> <span class="dim">(${agoStr(POLL.last_age_s)})</span>` +
      (ok ? ` <span class="ok">ok</span>` : ` <span class="err" title="${esc(last.error || "")}">FAILED — ${esc(last.error || "?")}</span>`));
    if (ok) bits.push(`<span><b class="ok">+${last.added ?? 0}</b> new · ` +
      `<b>${last.price_changes ?? 0}</b> price changes · <b>${last.scored ?? 0}</b> scored</span>`);
    bits.push(`<span class="dim">D${(last.districts || []).join(",")} · ${(last.beds || []).join(",")}BR · ${last.duration_s ?? "?"}s</span>`);
  } else {
    bits.push(`<b>never polled</b> — hit SCAN NOW to pull the latest listings`);
  }
  bits.push(POLL.auto
    ? `<span class="dim">auto-poll every ${Math.round(POLL.interval_mins/60*10)/10}h` +
      (POLL.next_in_s != null ? `, next in ${inStr(POLL.next_in_s)}` : "") + `</span>`
    : `<span class="dim">auto-poll OFF (--no-poll)</span>`);
  bits.push(`<button id="scanbtn" ${POLL.running ? "disabled" : ""} onclick="scanNow()">` +
            (POLL.running ? "POLLING…" : "SCAN NOW") + `</button>`);
  el.innerHTML = bits.join(" · ");
}

async function scanNow() {
  const r = await fetch("/poll", { method: "POST" });
  if (!r.ok && r.status !== 409) { alert(await r.text()); return; }
  await refreshPollStatus();
}

let _wasRunning = false;
async function refreshPollStatus() {
  try { POLL = await (await fetch("/api/poll-status")).json(); } catch (e) { return; }
  if (_wasRunning && !POLL.running) {  // a poll just finished → refresh data
    try { FRESH = await (await fetch("/api/fresh")).json(); renderFresh(); } catch (e) {}
  }
  _wasRunning = POLL.running;
  renderPoll();
}
setInterval(refreshPollStatus, 10000);

/* ---------------- fresh view ---------------- */
let fSortKey = "score_1000", fSortAsc = false;
const F_ASC = new Set(["project_name", "district", "price", "psf", "days_old", "drop_pct"]);

function initFreshFilters() {
  const sel = document.getElementById("f-district");
  const cur = sel.value;
  const ds = [...new Set(FRESH.rows.map(r => r.district).filter(Boolean))].sort();
  sel.innerHTML = '<option value="">All districts</option>' +
    ds.map(d => `<option value="${esc(d)}">${esc(d)}</option>`).join("");
  sel.value = cur;
}

function renderFresh() {
  initFreshFilters();
  const win = parseInt(document.getElementById("f-window").value);
  const ms = parseInt(document.getElementById("f-minscore").value) || null;
  const d = document.getElementById("f-district").value;
  const b = document.getElementById("f-beds").value;
  const mp = parseFloat(document.getElementById("f-maxprice").value);
  const q = document.getElementById("f-q").value.toLowerCase();
  const drops = document.getElementById("f-drops").checked;

  let rows = FRESH.rows.filter(r =>
    r.days_old < win &&
    (!ms || (r.score_1000 != null && r.score_1000 >= ms)) &&
    (!d || r.district === d) &&
    (!b || (b === "4" ? r.beds >= 4 : r.beds === parseInt(b))) &&
    (!mp || (r.price && r.price <= mp)) &&
    (!q || r.project_name.toLowerCase().includes(q)) &&
    (!drops || r.price_trend === "dropped"));

  rows.sort((a, c) => {
    let x = a[fSortKey], y = c[fSortKey];
    if (x == null) return 1; if (y == null) return -1;
    if (typeof x === "string") { x = x.toLowerCase(); y = String(y).toLowerCase(); }
    return (x < y ? -1 : x > y ? 1 : 0) * (fSortAsc ? 1 : -1);
  });

  document.getElementById("f-sortsel").value = fSortKey;
  document.getElementById("f-dirbtn").textContent = fSortAsc ? "▲" : "▼";
  const n650 = rows.filter(r => (r.score_1000 ?? 0) >= 650).length;
  const nNew = rows.filter(r => r.days_old === 0).length;
  document.getElementById("f-shown").textContent = rows.length;
  // Render cap: thousands of rows make innerHTML crawl (cold-start DBs have
  // everything "fresh"); the table stays snappy and the note says what's cut.
  const CAP = 800;
  const cut = rows.length > CAP ? rows.length - CAP : 0;
  if (cut) rows = rows.slice(0, CAP);
  document.getElementById("f-tiers").innerHTML =
    `<b style="color:var(--green)">${n650}</b> at 650+ (recommended tier) · ` +
    `<b>${nNew}</b> first seen today · data ${esc(FRESH.generated_at)}` +
    (cut ? ` · <b style="color:var(--gold)">showing first ${CAP}, ${cut} more</b> — narrow the filters` : "");

  document.getElementById("f-rows").innerHTML = rows.map(r => `
    <tr${r.days_old === 0 ? ' class="isnew"' : ""}>
      <td>${scoreChip(r.score_1000)}</td>
      <td>${livChip(r.livability, r.liv_why)}</td>
      <td>${agentBadge(r.agent_rating, r.agent_eval_date)}</td>
      <td class="name">${esc(r.project_name)}${r.days_old === 0 ? '<span class="badge new">NEW</span>' : ""}${r.price_trend === "dropped" ? `<span class="badge drop" title="down from its peak ask">⬇ ${r.drop_pct ?? ""}%</span>` : ""}${(r.dup_count || 1) > 1 ? `<span class="badge dup" title="same unit listed by ${r.dup_count} agents">×${r.dup_count}</span>` : ""}</td>
      <td>${r.beds ? r.beds + "BR" : "?"}${r.baths ? '<span class="dim">/' + r.baths + 'ba</span>' : ""}</td>
      <td>${fmtPrice(r.price)}</td>
      <td>${fmtPsf(r.psf)}</td>
      <td class="dim">${r.sqft ? Math.round(r.sqft).toLocaleString() : "-"}</td>
      <td>${esc(r.district)}</td>
      <td class="dim" title="${esc(r.first_seen)}">${r.days_old === 0 ? "today" : r.days_old + "d ago"}</td>
      <td class="dim">${r.built_year ?? "-"}</td>
      <td class="dim">${esc(r.tenure || "-")}</td>
      <td class="dim">${esc(r.mrt_info || "-")}</td>
      <td class="links">
        ${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">Listing ↗</a>` : ""}
        <a href="${esc(r.maps_url)}" target="_blank" rel="noopener">Maps 📍</a>
      </td>
    </tr>`).join("");
  document.getElementById("f-empty").style.display = rows.length ? "none" : "";
}
function fSortBy(k) { if (fSortKey === k) fSortAsc = !fSortAsc; else { fSortKey = k; fSortAsc = F_ASC.has(k); } renderFresh(); }
function fSetSort(k) { fSortKey = k; fSortAsc = F_ASC.has(k); renderFresh(); }
function fToggleDir() { fSortAsc = !fSortAsc; renderFresh(); }

/* ---------------- arena view ---------------- */
let aSortKey = "rank", aSortAsc = true;
const A_ASC = new Set(["rank", "project_name", "beds", "district", "price", "psf"]);
const RATING_ORD = { "strong buy": 4, "buy": 3, "neutral": 2, "avoid": 1 };
const aSortVal = r => aSortKey === "agent_rating"
  ? (RATING_ORD[(r.agent_rating || "").toLowerCase()] ?? 0) || null
  : r[aSortKey];

function agentBadge(rating, date) {
  if (!rating) return '<span class="dim">-</span>';
  const r = rating.toLowerCase();
  const color = r.includes("buy") ? "background:#1c3326;color:#3fb950"
              : r.includes("avoid") ? "background:#33201c;color:#f85149"
              : "background:#332d1c;color:#e3b341";
  return `<span class="chip" style="${color}" title="AI evaluation ${esc(date)}">${esc(rating)}</span>`;
}

function initArenaFilters() {
  const order = { "1BR": 1, "2BR": 2, "3BR": 3, "4BR+": 4, "open": 9 };
  const brackets = [...new Set(ARENA.rows.map(r => r.bracket || "open"))]
    .sort((a, b) => (order[a] ?? 5) - (order[b] ?? 5));
  const bsel = document.getElementById("a-bracket");
  if (!bsel.options.length) {
    const def = brackets.includes("2BR") ? "2BR" : brackets[0];
    bsel.innerHTML = brackets.map(b =>
      `<option value="${esc(b)}"${b === def ? " selected" : ""}>${esc(b + (b === "open" ? " division (all types)" : " bracket"))}</option>`).join("");
  }
  const dsel = document.getElementById("a-district");
  if (dsel.options.length <= 1) {
    const ds = [...new Set(ARENA.rows.map(r => r.district).filter(Boolean))].sort();
    dsel.innerHTML = '<option value="">All districts</option>' +
      ds.map(d => `<option value="${esc(d)}">${esc(d)}</option>`).join("");
  }
}

function renderArena() {
  initArenaFilters();
  document.getElementById("a-rundate").textContent = ARENA.run_date || "—";
  if (!ARENA.rows.length) {
    document.getElementById("a-empty").style.display = "";
    document.getElementById("a-rows").innerHTML = "";
    document.getElementById("a-shown").textContent = 0;
    return;
  }
  document.getElementById("a-empty").style.display = "none";
  const br = document.getElementById("a-bracket").value;
  const q = document.getElementById("a-q").value.toLowerCase();
  const d = document.getElementById("a-district").value;
  const mp = parseFloat(document.getElementById("a-maxprice").value);
  const fo = document.getElementById("a-frontier").value;
  let rows = ARENA.rows.filter(r =>
    r.bracket === br &&
    (!q || r.project_name.toLowerCase().includes(q)) &&
    (!d || r.district === d) &&
    (!mp || (r.price && r.price <= mp)) &&
    (!fo || r.on_frontier));
  rows.sort((a, b2) => {
    let x = aSortVal(a), y = aSortVal(b2);
    if (x == null) return 1; if (y == null) return -1;
    if (typeof x === "string") { x = x.toLowerCase(); y = String(y).toLowerCase(); }
    return (x < y ? -1 : x > y ? 1 : 0) * (aSortAsc ? 1 : -1);
  });
  document.getElementById("a-sortsel").value = aSortKey;
  document.getElementById("a-dirbtn").textContent = aSortAsc ? "▲" : "▼";
  document.getElementById("a-rows").innerHTML = rows.map(r => `
    <tr>
      <td class="dim">${r.rank}</td>
      <td class="name">${r.on_frontier ? '<span class="star">⭐</span> ' : ''}${esc(r.project_name)}</td>
      <td>${r.beds ? r.beds + "BR" : "?"}</td>
      <td>${r.elo ?? "-"}</td>
      <td class="dim">${r.record} (${r.win_rate_pct}%)</td>
      <td>${scoreChip(r.score_1000)}</td>
      <td>${fmtPrice(r.price)}</td>
      <td>${fmtPsf(r.psf)}</td>
      <td>${esc(r.district)}</td>
      <td>${agentBadge(r.agent_rating, r.agent_eval_date)}</td>
      <td class="dim">${r.built_year ?? "-"}</td>
      <td class="dim">${esc(r.mrt_info || "-")}</td>
      <td class="links">
        ${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">Listing ↗</a>` : ""}
        <a href="${esc(r.maps_url)}" target="_blank" rel="noopener">Maps 📍</a>
      </td>
    </tr>`).join("");
  document.getElementById("a-shown").textContent = rows.length;
}
function aSortBy(k) { if (aSortKey === k) aSortAsc = !aSortAsc; else { aSortKey = k; aSortAsc = A_ASC.has(k); } renderArena(); }
function aSetSort(k) { aSortKey = k; aSortAsc = A_ASC.has(k); renderArena(); }
function aToggleDir() { aSortAsc = !aSortAsc; renderArena(); }

/* ---------------- boot ---------------- */
renderPoll();
switchTab(location.hash === "#arena" ? "arena" : "fresh");
window.addEventListener("hashchange", () => {
  const want = location.hash === "#arena" ? "arena" : "fresh";
  if (document.getElementById("view-" + want).style.display === "none") switchTab(want);
});
</script>
</body>
</html>"""


def render_index() -> str:
    # "</" must be escaped when embedding JSON in a <script> block — a scraped
    # name containing "</script>" would otherwise close the tag (standard
    # inline-JSON hardening).
    def j(obj):
        return json.dumps(obj).replace("</", "<\\/")
    return (_PAGE
            .replace("__FRESH_JSON__", j(load_fresh()))
            .replace("__ARENA_JSON__", j(load_rankings()))
            .replace("__POLL_JSON__", j(poll_status())))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._send(200, "text/html; charset=utf-8", render_index().encode())
        elif path == "/api/fresh":
            self._send(200, "application/json", json.dumps(load_fresh()).encode())
        elif path == "/api/rankings":
            self._send(200, "application/json", json.dumps(load_rankings()).encode())
        elif path == "/api/poll-status":
            self._send(200, "application/json", json.dumps(poll_status()).encode())
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path == "/poll":
            if trigger_poll():
                self._send(202, "application/json", b'{"started": true}')
            else:
                self._send(409, "text/plain", b"a poll is already running")
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet
        pass


def main():
    parser = argparse.ArgumentParser(description="Local fresh-listings + arena UI")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-poll", action="store_true",
                        help="serve only — never scrape PropertyGuru")
    parser.add_argument("--poll-interval-mins", type=int,
                        default=poller.DEFAULT_INTERVAL_MINS,
                        help="auto-poll interval (default %(default)s)")
    parser.add_argument("--poll-districts", type=str, default=None,
                        help=f"districts to poll (default {poller.DEFAULT_DISTRICTS})")
    parser.add_argument("--poll-beds", type=str, default=None,
                        help=f"bed counts to poll (default {poller.DEFAULT_BEDS})")
    parser.add_argument("--poll-max-pages", type=int, default=poller.DEFAULT_MAX_PAGES)
    parser.add_argument("--poll-no-headless", action="store_true",
                        help="scrape with a visible Chrome window (helps when "
                             "headless polls die on Cloudflare)")
    args = parser.parse_args()

    POLL_CFG["auto"] = not args.no_poll
    POLL_CFG["interval_mins"] = args.poll_interval_mins
    POLL_CFG["max_pages"] = args.poll_max_pages
    POLL_CFG["headless"] = not args.poll_no_headless
    if args.poll_districts:
        POLL_CFG["districts"] = [int(d) for d in args.poll_districts.split(",")]
    if args.poll_beds:
        POLL_CFG["beds"] = [int(b) for b in args.poll_beds.split(",")]

    if POLL_CFG["auto"]:
        threading.Thread(target=_auto_poll_loop, daemon=True).start()

    # Threading: an idle browser preconnect must not block the accept loop
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    poll_note = (f"auto-poll every {POLL_CFG['interval_mins']}min"
                 if POLL_CFG["auto"] else "auto-poll OFF")
    print(f"Property finder UI: {url}  ({poll_note}, Ctrl-C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
