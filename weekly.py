#!/usr/bin/env python3
"""Weekly scan — poll PropertyGuru, algo-grade everything, AI-analyze the few worth it.

Run by hand, once a week. Nothing auto-starts it: the reminder lives in the
personal data store (`projects/property-finder/weekly-scan.md`, surfaced at
session start when it comes due) and the owner drives it from there. This
script closes the loop by stamping that note's `last_run` on success.

The cycle:

  1. **Poll** — `poller.run_poll()` scrapes the newest listings for the configured
     districts/beds, upserts them, and MMR-scores the new / price-changed ones.
  2. **Algo grade** — every new and price-changed listing already carries
     `score_1000` (plus the valuation / livability / overall axes) from step 1.
     That grade is free, so it is applied to ALL of them.
  3. **Gate** — only listings clearing `MIN_SCORE_FOR_AI` (and the sanity /
     cooldown / dedupe rules below) earn an AI run. Everything else is logged
     algo-only. This is the whole point: the agent is the expensive step.
  4. **AI analyze** — for each shortlisted listing, a headless `claude -p` runs
     the repo's `/analyze-listing` flow and lands a Buy/Neutral/Avoid verdict in
     eval memory. Gather is `invest.py --from-db` — the listing is already
     scraped and enriched, so the agent never touches the network for it.
  5. **Digest** — `output/weekly/YYYY-MM-DD.md`: the whole week, honestly (what
     was seen, what was gated out and why, every verdict).
  6. **Store push** — only genuinely good finds (Buy/Strong Buy at >= medium
     confidence) are appended to the personal data store's scan-finds note.
     The bar is deliberately high: an over-fed note gets ignored.

Usage:
    python weekly.py                 # the real thing (headed browser — see below)
    python weekly.py --dry-run       # poll + gate + digest, but spawn no agents
    python weekly.py --force         # re-run even though this week already succeeded
    python weekly.py --no-poll       # skip the scrape; grade what arrived since the last run
    python weekly.py --max-ai 2      # tighter cap for this run

**Headed by default.** PropertyGuru sits behind Cloudflare, which blocks
headless background polls (see CLAUDE.md). A Chrome window opens and reuses the
persistent `chrome-profile` clearance cookie; if a challenge does appear, solve
it once and the rest of the cycle proceeds. `--headless` is available for
machines where that already works. Since a human is present anyway — this is a
hand-run script — that challenge is a prompt, not a failure mode.

State lives in `data/weekly_state.json`. A run that polled successfully marks
the ISO week done, so a second run that week is a no-op; a run whose poll FAILED
does not, so the retry is just running it again.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import eval_memory
import listings_db
import poller

DATA_DIR = os.path.join(BASE, "data")
STATE_FILE = os.path.join(DATA_DIR, "weekly_state.json")
DIGEST_DIR = os.path.join(BASE, "output", "weekly")
RUNS_DIR = os.path.join(BASE, "output", "weekly_runs")

# --------------------------------------------------------------------------- #
# The knobs
#
# Deliberately NOT in config.py: config.py is hashed into `score_version`, so a
# threshold that has nothing to do with scoring would mark the whole scored book
# as a different vintage. These are scan-policy, not scoring calibration.
# --------------------------------------------------------------------------- #

# The AI gate. 650 is the repo's own "recommended tier" (CLAUDE.md) and sits at
# roughly the 88th percentile of the scored book — a listing below it is not
# worth an agent run just for showing up.
MIN_SCORE_FOR_AI = 650

# Hard cap on agent runs per scan. The gate alone is unbounded: a bulk relist or
# a newly-covered district could put 40 listings over 650 in one week.
#
# 8, not the 5 a daily cadence used: a weekly scan sweeps ~7 days of inventory,
# so the same cap would be ~7x stingier per listing seen. Still far below a
# daily run's 35/week — weekly is the cheaper cadence either way.
MAX_AI_RUNS_PER_SCAN = 8

# Pages per (district, beds) to pull. PropertyGuru search is date-desc, so this
# is really "how far back does one scan reach". The poller's default of 2 is
# tuned for 6-hourly polling; a week of listings needs deeper pagination or the
# older half of the week silently never gets seen.
POLL_MAX_PAGES = 5

# Don't re-analyze the same (condo, bedroom count) inside this window — a fresh
# listing of an already-judged unit type rarely changes the call, and eval memory
# already holds the reasoning. A different bed count IS a different call.
RE_EVAL_COOLDOWN_DAYS = 30

# Per-agent wall clock. The analyze-listing flow does real web research; beyond
# this something is stuck and the run is killed so the cycle finishes.
AI_TIMEOUT_S = 1800

# What the headless agent may touch — scoped, mirroring dashboard.py's ANALYZE.
AI_ALLOWED_TOOLS = "Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch,Skill,TodoWrite"

# Store push bar: only these reach the personal data store's scan-finds note.
STORE_PUSH_RATINGS = {"strong buy", "buy"}
STORE_PUSH_CONFIDENCE = {"high", "medium"}
_STORE = os.environ.get(
    "PF_STORE", os.path.expanduser("~/Sideproject/personal-data-store"))
STORE_FINDS_PATH = os.environ.get(
    "PF_STORE_FINDS",
    os.path.join(_STORE, "projects", "home-buying", "scan-finds.md"))
# The note that reminds the owner to run this. A successful scan stamps its
# `last_run:` so the store's session-start reminder clears itself.
STORE_SCAN_NOTE_PATH = os.environ.get(
    "PF_STORE_SCAN_NOTE",
    os.path.join(_STORE, "projects", "property-finder", "weekly-scan.md"))

_RATING_ICON = {"strong buy": "🟢", "buy": "🟢", "neutral": "🟡", "avoid": "🔴"}

# realsmart.sg REALSCORE lookup. The PUBLIC /p/<slug> page carries it and needs
# no login or browser (the /map SPA is the one that does) — so the agent reads it
# with the store's plain fetcher. One page per shortlisted project, <=8 a week:
# realsmart's ToS is personal-use, so this stays a research lookup and must never
# become a per-listing pipeline feed.
WEB_EXTRACT_FETCH = os.path.join(
    _STORE, ".claude", "skills", "web-extract", "scripts", "fetch.py")


def realsmart_url(project_name: str | None) -> str:
    """Best-guess realsmart project URL from a project name.

    Their slugs are lowercase, alphanumeric, hyphen-joined ('JadeScape' ->
    'jadescape', 'The Continuum' -> 'the-continuum'). A guess, not a lookup —
    the agent is told to verify and to leave the fields null rather than invent
    a number if the slug 404s.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (project_name or "").lower()).strip("-")
    return f"https://realsmart.sg/p/{slug}"


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def iso_week(d: datetime) -> str:
    """ISO year-week key, e.g. '2026-W31'. Weeks start Monday."""
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def already_done_this_week(state: dict, week: str) -> bool:
    """True only if this ISO week already got a SUCCESSFUL poll in.

    A failed poll (the usual cause: an unsolved Cloudflare challenge) leaves the
    week open, so re-running is the retry — no flag needed.
    """
    last = state.get("last_run") or {}
    return last.get("week") == week and bool(last.get("poll_ok"))


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #
def _eval_cooldown_index(cooldown_days: int, today: datetime) -> dict:
    """Map (condo slug, beds) -> most recent evaluation date inside the window.

    Beds come from each history entry's `as_of`, so a 2BR verdict never blocks a
    3BR in the same project. Entries without a usable bed count fall back to a
    wildcard key that blocks any bed count for that condo — the conservative
    reading of "we already looked at this place".

    Eval memory is append-only and years old: some legacy entries carry `as_of`
    as a free-text note rather than the {price, beds, …} dict, and a hand-edited
    file can hold anything. Nothing in here may raise — a malformed entry
    degrades to the wildcard, it does not stop the scan.
    """
    index: dict[tuple[str, object], str] = {}
    cutoff = (today - timedelta(days=cooldown_days)).strftime("%Y-%m-%d")
    try:
        slugs = eval_memory.load_index().get("condos", {}).keys()
    except Exception:  # noqa: BLE001 — a broken index must not stop the scan
        return index
    for slug in slugs:
        data = eval_memory.load_condo(slug) or {}
        for entry in data.get("history", []) or []:
            if not isinstance(entry, dict):
                continue
            when = entry.get("evaluated_at") or ""
            if not isinstance(when, str) or when < cutoff:
                continue
            as_of = entry.get("as_of")
            beds = as_of.get("beds") if isinstance(as_of, dict) else None
            key = (slug, beds if beds is not None else "*")
            if when > index.get(key, ""):
                index[key] = when
    return index


def _blocked_by_cooldown(index: dict, slug: str, beds) -> str | None:
    """Return the blocking evaluation date, or None if this unit type is open."""
    return index.get((slug, beds)) or index.get((slug, "*"))


# PropertyGuru's payload does not always carry a project name; when it doesn't,
# the scraper falls back to the listing TITLE — which is the agent's marketing
# headline ("Cheapest💎D03💎Best Value💎Freehold💎Duplex Penthouse💎"). ~15% of
# distinct names in the DB are these. They must never reach an agent: the URA
# comps, realsmart lookup, eval-memory slug and cooldown key are ALL keyed on
# project name, so the run researches nothing and permanently pollutes
# git-tracked memory with a junk slug.
_MARKETING_RX = re.compile(
    r"\b(cheapest|cheap|best\s*value|best\s*buy|must\s*sell|don'?t\s*miss|urgent|"
    r"rare(\s*find)?|below\s*valuation|undervalued?|steal|doorstep|key\s*collection|"
    r"walk(ing)?\s*to|near\s*mrt|min(s)?\s*to|\d\s*km|1km|rental\s*yield|high\s*floor|"
    r"brand\s*new|freehold|renovated|unblocked|bedder|bdrm|psf|amenities|"
    r"price[d]?\s*to\s*sell|for\s*sale|value[\s.-]*buy|super\s|top\s*soon)\b",
    re.I)
_EMOJI_RX = re.compile("[\U0001F000-\U0001FAFF☀-➿️⭐]")


def looks_like_marketing_title(name: str, known_projects: set | None = None) -> bool:
    """True when `name` is a listing headline rather than a development name.

    Deliberately two-sided. The heuristics alone would risk demoting a real
    development with a loud name, so an exact match against a known URA project
    RESCUES any name — the government's own project list outranks any guess we
    make here. Conversely a name with no URA match is not condemned on that
    basis alone (genuine new launches have no prints yet); it needs to actually
    read as marketing copy.
    """
    n = (name or "").strip()
    if not n:
        return True
    if known_projects and n.lower() in known_projects:
        return False
    return bool(_EMOJI_RX.search(n)) or "!" in n or len(n) > 45 \
        or bool(_MARKETING_RX.search(n))


def _repriced_since(rec: dict, since: str) -> bool:
    """Did this listing record a price change on/after `since`?

    Only counts as a re-price if there is more than one price_history entry —
    the first is the listing's original ask, not a change.
    """
    hist = rec.get("price_history") or []
    if not isinstance(hist, list) or len(hist) < 2:
        return False
    for entry in hist[1:]:
        if isinstance(entry, dict) and str(entry.get("date") or "") >= since:
            return True
    return False


def _known_projects() -> set:
    """Lowercased URA project names — the rescue list. Never raises."""
    try:
        with open(os.path.join(DATA_DIR, "ura_cache.json")) as f:
            data = json.load(f)
        return {str(k).lower() for k in (data.get("projects") or data).keys()}
    except (OSError, json.JSONDecodeError, AttributeError):
        return set()


def select_candidates(
    rows: list[dict],
    db_listings: dict,
    cooldown_index: dict | None = None,
    min_score: int = MIN_SCORE_FOR_AI,
    max_ai: int = MAX_AI_RUNS_PER_SCAN,
    known_projects: set | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split the scan's new/changed listings into (shortlist, rejected).

    Pure over its inputs so the policy is testable without a poll. `rows` are
    poll-state rows (id / project_name / score_1000 / …); each is joined to its
    DB record for the fields the gate needs. Every rejected row carries a
    `gate_reason` — the digest prints them, so the gate is never a black box.

    Order of checks matters: cheap data-quality rules first, then the score,
    then the two rules that need the rest of the batch (in-batch dedupe, cap).
    """
    cooldown_index = {} if cooldown_index is None else cooldown_index
    shortlist: list[dict] = []
    rejected: list[dict] = []

    ranked = sorted(rows, key=lambda r: r.get("score_1000") or 0, reverse=True)
    seen_cohort: set[tuple[str, object]] = set()

    for row in ranked:
        rec = db_listings.get(row["id"]) or {}
        cand = {**row,
                "unit_group": rec.get("unit_group"),
                "ingest_flags": rec.get("ingest_flags") or [],
                "tenure": rec.get("tenure"),
                "sqft": rec.get("sqft"),
                "status": rec.get("status")}
        score = row.get("score_1000")
        name = row.get("project_name") or rec.get("title") or ""
        slug = eval_memory.slugify(name) if name else ""
        cohort = (slug, row.get("beds"))

        if rec.get("status") == "stale":
            cand["gate_reason"] = "listing went stale this cycle"
        elif not (rec.get("price") and rec.get("sqft") and rec.get("psf")):
            cand["gate_reason"] = "incomplete record (price/sqft/psf)"
        elif score is None:
            cand["gate_reason"] = "not scored"
        elif score < min_score:
            cand["gate_reason"] = f"below gate ({score} < {min_score})"
        elif looks_like_marketing_title(name, known_projects):
            # Not a judgement on the unit — we simply don't know which
            # development it is, so there is nothing to research.
            cand["gate_reason"] = "no usable project name (listing headline, not a development)"
        elif (blocked := _blocked_by_cooldown(cooldown_index, slug, row.get("beds"))):
            cand["gate_reason"] = f"same unit type evaluated {blocked} (cooldown)"
        elif cohort in seen_cohort:
            cand["gate_reason"] = "same condo + bed count already shortlisted this scan"
        elif len(shortlist) >= max_ai:
            cand["gate_reason"] = f"over the per-scan AI cap ({max_ai})"
        else:
            cand["slug"] = slug
            seen_cohort.add(cohort)
            shortlist.append(cand)
            continue

        rejected.append(cand)

    return shortlist, rejected


# --------------------------------------------------------------------------- #
# The AI step
# --------------------------------------------------------------------------- #
def _agent_prompt(cand: dict) -> str:
    flags = cand.get("ingest_flags") or []
    flag_note = (
        f"\nThe ingest flags on this record are {flags} — treat any apparent "
        "discount as unverified until you have checked it against the project's "
        "own recent (24mo) prints, per the trust rules in CLAUDE.md."
        if flags else ""
    )
    return (
        f"Analyze this PropertyGuru listing per the /analyze-listing flow: "
        f"{cand.get('url')}\n\n"
        f"It is already scraped, detail-enriched and scored in the listings DB "
        f"(id {cand['id']}, {cand.get('project_name')}, "
        f"score_1000 {cand.get('score_1000')}). For step ② Gather, run\n"
        f"    python invest.py --from-db {cand['id']}\n"
        f"instead of `--url` — it builds the run dir and raw_analysis.json from "
        f"the DB record with no scraping (PropertyGuru is behind Cloudflare and "
        f"this is an unattended run).{flag_note}\n\n"
        f"REQUIRED in step ② Gather — realsmart.sg REALSCORE for this project:\n"
        f"    python3 {WEB_EXTRACT_FETCH} \"{realsmart_url(cand.get('project_name'))}\"\n"
        "Public page, plain fetch, no login. Read off REALSCORE (0-5 "
        "profitability rank), the '% Profitable' badge, avg annualized profit, "
        "and the transaction COUNT behind them, then fill `realscore`, "
        "`realsmart_pct_profitable` and `realsmart_annual_return_pct` in "
        "agent_evaluation. If the slug 404s, try the project name's other "
        "spellings once, then leave the fields null and say so — never guess a "
        "number. Weigh it as downside evidence (has this project ever lost "
        "owners money?), NOT as an appreciation forecast: it is backward-looking "
        "like trailing CAGR, and a perfect score on a handful of transactions "
        "means little. Uncompleted projects legitimately show N.A.\n\n"
        "This is a NON-INTERACTIVE headless run in the weekly scan: do not ask "
        "anything; where the flow would ask about purpose, assume investment "
        "(5-7yr hold) and proceed. Do the real research step — your view first, "
        "algo_reference second. An honest Neutral or Avoid is the expected "
        "outcome most weeks; never manufacture a Buy. Finish the full flow "
        "including --from-review so the verdict is saved to eval memory."
    )


def run_agent(cand: dict, timeout_s: int = AI_TIMEOUT_S) -> dict:
    """Run one headless `claude -p` analyze pass. Never raises."""
    os.makedirs(RUNS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(RUNS_DIR, f"{cand.get('slug') or cand['id']}_{stamp}.log")
    cmd = ["claude", "-p", _agent_prompt(cand), "--allowedTools", AI_ALLOWED_TOOLS]
    result = {"id": cand["id"], "log": os.path.relpath(log_path, BASE)}
    try:
        with open(log_path, "w") as logf:
            proc = subprocess.run(cmd, cwd=BASE, stdin=subprocess.DEVNULL,
                                  stdout=logf, stderr=subprocess.STDOUT,
                                  timeout=timeout_s)
        result["returncode"] = proc.returncode
        result["ok"] = proc.returncode == 0
    except subprocess.TimeoutExpired:
        result.update(ok=False, error=f"timed out after {timeout_s}s")
    except (OSError, ValueError) as e:
        result.update(ok=False, error=f"{type(e).__name__}: {e}")
    return result


def read_verdict(cand: dict, on_or_after: str) -> dict | None:
    """Pull the verdict the agent just wrote back out of eval memory.

    Matches on the listing URL first (the precise join — `save_evaluations_from
    _review` stamps `source.url`), then falls back to the newest entry for that
    condo dated on/after the run. Returns None when the agent saved nothing,
    which is how a failed or hedged run shows up in the digest.
    """
    slug = cand.get("slug") or eval_memory.slugify(cand.get("project_name") or "")
    data = eval_memory.load_condo(slug) or {}
    fresh = [h for h in (data.get("history") or [])
             if isinstance(h, dict) and str(h.get("evaluated_at") or "") >= on_or_after]
    if not fresh:
        return None
    url = cand.get("url")
    for entry in reversed(fresh):
        if url and (entry.get("source") or {}).get("url") == url:
            return entry
    return fresh[-1]


# --------------------------------------------------------------------------- #
# Digest
# --------------------------------------------------------------------------- #
def _money(v) -> str:
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "—"


def _realsmart(v: dict) -> dict:
    """realsmart fields out of a verdict, wherever the agent put them.

    `save_evaluations_from_review` copies agent_evaluation wholesale AND lifts
    some keys to the history entry's top level, so accept either shape.
    """
    ae = v.get("agent_evaluation") or {}
    return {k: (v.get(k) if v.get(k) is not None else ae.get(k))
            for k in ("realscore", "realsmart_pct_profitable",
                      "realsmart_annual_return_pct")}


def _realsmart_line(v: dict) -> str:
    rs = _realsmart(v)
    if rs["realscore"] is None and rs["realsmart_pct_profitable"] is None:
        return ""
    bits = []
    if rs["realscore"] is not None:
        bits.append(f"**REALSCORE {rs['realscore']}**/5")
    if rs["realsmart_pct_profitable"] is not None:
        bits.append(f"{rs['realsmart_pct_profitable']}% of resales profitable")
    if rs["realsmart_annual_return_pct"] is not None:
        bits.append(f"{rs['realsmart_annual_return_pct']}%/yr avg (past 1y)")
    return "realsmart: " + " · ".join(bits)


def _row(c: dict) -> str:
    return (f"| {c.get('score_1000') or '—'} | {c.get('project_name') or '—'} | "
            f"{c.get('beds') or '—'}BR | {_money(c.get('price'))} | "
            f"{_money(c.get('psf'))} psf | {c.get('district') or '—'} |")


def build_digest(day: str, poll_state: dict, shortlist: list[dict],
                 rejected: list[dict], verdicts: dict, min_score: int,
                 max_ai: int, dry_run: bool, pushed: list[dict]) -> str:
    n_new = len(poll_state.get("new_listings") or [])
    n_changed = len(poll_state.get("changed_listings") or [])
    L = [f"# Weekly scan — {day} ({iso_week(datetime.strptime(day, '%Y-%m-%d'))})", ""]

    if poll_state.get("ok"):
        L.append(
            f"**Poll** · D{','.join(str(d) for d in poll_state.get('districts', []))} · "
            f"{','.join(str(b) for b in poll_state.get('beds', []))}BR · "
            f"{poll_state.get('scraped', 0)} scraped → "
            f"**+{poll_state.get('added', 0)} new**, "
            f"{poll_state.get('price_changes', 0)} price changes, "
            f"{poll_state.get('enriched', 0)} detail-enriched "
            f"({poll_state.get('duration_s', 0)}s)")
    elif poll_state.get("skipped"):
        L.append("**Poll** · skipped (`--no-poll`) — graded what was already in the DB")
    else:
        L.append(f"> ⚠️ **Poll FAILED** — `{poll_state.get('error')}`. "
                 "Everything below is from the DB as it stands; the week was NOT "
                 "marked done, so just run it again.")

    L += ["",
          f"**Gate** · `score_1000 >= {min_score}` · max {max_ai} AI runs/scan · "
          f"{RE_EVAL_COOLDOWN_DAYS}d re-eval cooldown",
          f"**Result** · {n_new} new + {n_changed} price-changed → "
          f"**{len(shortlist)} cleared the gate** → "
          f"{'0 analyzed (dry run)' if dry_run else f'{len(verdicts)} analyzed'}",
          ""]

    if verdicts:
        L += ["## AI verdicts", ""]
        for cand in shortlist:
            v = verdicts.get(cand["id"])
            if not v:
                L += [f"### ⚪️ No verdict saved — {cand.get('project_name')} "
                      f"({cand['id']})",
                      "The agent run did not write an evaluation — see its log.", ""]
                continue
            rating = (v.get("rating") or "?").strip()
            icon = _RATING_ICON.get(rating.lower(), "⚪️")
            L.append(f"### {icon} {rating} — {cand.get('project_name')} · "
                     f"{cand.get('beds')}BR {cand.get('sqft') or '?'} sqft · "
                     f"{_money(cand.get('price'))} ({_money(cand.get('psf'))} psf) · "
                     f"{cand.get('district')} · score {cand.get('score_1000')} · "
                     f"{v.get('confidence') or '?'} confidence")
            if (rs := _realsmart_line(v)):
                L += ["", rs]
            if v.get("summary"):
                L += ["", f"> {v['summary']}"]
            if v.get("rating_rationale"):
                L += ["", f"**Why:** {v['rating_rationale']}"]
            for label, key in (("Red flags", "red_flags"), ("Catalysts", "catalysts")):
                items = v.get(key) or []
                if items:
                    L += ["", f"**{label}:**"] + [f"- {i}" for i in items]
            L += ["", f"[Listing]({cand.get('url')}) · "
                      f"eval memory: `evaluations/{cand.get('slug')}.json`", ""]

    if dry_run and shortlist:
        L += ["## Cleared the gate (dry run — no agent spawned)", "",
              "| Score | Project | Beds | Price | PSF | District |",
              "|---|---|---|---|---|---|"] + [_row(c) for c in shortlist] + [""]

    if rejected:
        L += [f"## Algo-only — {len(rejected)} not sent to the agent", "",
              "| Score | Project | Beds | Price | PSF | District | Why |",
              "|---|---|---|---|---|---|---|"]
        L += [_row(c) + f" {c.get('gate_reason', '—')} |" for c in rejected]
        L.append("")

    if pushed:
        L += ["## Pushed to the store", ""]
        L += [f"- **{p['project_name']}** — {p['rating']} "
              f"({p['confidence']} confidence) → `{os.path.basename(STORE_FINDS_PATH)}`"
              for p in pushed]
        L.append("")

    if not shortlist and not rejected:
        L += ["Nothing new or price-changed in scope this week.", ""]

    return "\n".join(L)


def write_digest(day: str, text: str) -> str:
    os.makedirs(DIGEST_DIR, exist_ok=True)
    path = os.path.join(DIGEST_DIR, f"{day}.md")
    with open(path, "w") as f:
        f.write(text)
    return path


# --------------------------------------------------------------------------- #
# Store push — only the genuinely good ones
# --------------------------------------------------------------------------- #
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _insert_rows(existing: str, rows: list[str]) -> str:
    """Insert `rows` directly under the note's first markdown table header.

    Newest-first, and — the reason this isn't a plain append — the store note
    keeps explanatory prose BELOW its table. Appending at EOF would drop table
    rows after that prose and break both. Falls back to appending only if the
    note has no table at all.
    """
    lines = existing.split("\n")
    for i, line in enumerate(lines):
        if _TABLE_SEP.match(line) and i > 0 and lines[i - 1].lstrip().startswith("|"):
            return "\n".join(lines[:i + 1] + rows + lines[i + 1:])
    return existing.rstrip("\n") + "\n" + "\n".join(rows) + "\n"


def push_to_store(day: str, shortlist: list[dict], verdicts: dict,
                  path: str = STORE_FINDS_PATH) -> list[dict]:
    """Append Buy-grade finds to the store's scan-finds note. Returns what was added.

    The bar is intentionally high (Buy/Strong Buy at >= medium confidence) — the
    store note is a signal feed the owner actually reads, not a log. The full
    week, verdicts included, is always in the digest regardless.

    Never creates the file: if the note is missing the store is not where we
    think it is, and silently seeding a stray markdown file elsewhere is worse
    than skipping. Rows already present (same listing URL) are not re-added, so
    re-running a scan is safe.
    """
    good = []
    for cand in shortlist:
        v = verdicts.get(cand["id"])
        if not v:
            continue
        if (v.get("rating") or "").strip().lower() not in STORE_PUSH_RATINGS:
            continue
        if (v.get("confidence") or "").strip().lower() not in STORE_PUSH_CONFIDENCE:
            continue
        good.append((cand, v))
    if not good:
        return []

    if not os.path.exists(path):
        print(f"  ⚠ store note not found at {path} — skipping push "
              f"({len(good)} find(s) are in the digest only)", file=sys.stderr)
        return []

    try:
        with open(path) as f:
            existing = f.read()
    except OSError as e:
        print(f"  ⚠ could not read {path}: {e}", file=sys.stderr)
        return []

    added, lines = [], []
    for cand, v in good:
        url = cand.get("url") or ""
        if url and url in existing:
            continue
        summary = (v.get("summary") or v.get("rating_rationale") or "").strip()
        summary = summary.replace("|", "·").replace("\n", " ")
        if len(summary) > 320:
            summary = summary[:317].rstrip() + "…"
        rs = _realsmart(v)
        real = "—" if rs["realscore"] is None else f"{rs['realscore']}"
        if rs["realsmart_pct_profitable"] is not None:
            real += f" · {rs['realsmart_pct_profitable']}% prof"
        lines.append(
            f"| {day} | [{cand.get('project_name')}]({url}) | "
            f"{cand.get('beds')}BR {cand.get('sqft') or '?'} sqft | "
            f"{_money(cand.get('price'))} ({_money(cand.get('psf'))} psf) · "
            f"{cand.get('district')} | {cand.get('score_1000')} | {real} | "
            f"**{v.get('rating')}** ({v.get('confidence')}) | {summary} |")
        added.append({"project_name": cand.get("project_name"),
                      "rating": v.get("rating"), "confidence": v.get("confidence"),
                      "url": url})
    if not lines:
        return []

    # Keep the note's `updated:` frontmatter honest — the store's convention.
    body = _set_frontmatter(_insert_rows(existing, lines), "updated", day)
    _atomic_write(path, body)
    return added


def _atomic_write(path: str, body: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(body)
    os.replace(tmp, path)


def _set_frontmatter(text: str, key: str, value: str) -> str:
    """Rewrite one existing frontmatter key. Adds nothing, touches nothing else.

    Only rewrites a key that is already there — the store's notes own their own
    shape, and a scan script inventing frontmatter fields in them would be
    overreach.
    """
    if not text.startswith("---\n"):
        return text
    head, sep, rest = text.partition("\n---\n")
    if not sep:
        return text
    head = "\n".join(
        f"{key}: {value}" if ln.startswith(f"{key}:") else ln
        for ln in head.split("\n"))
    return head + sep + rest


def stamp_scan_note(day: str, path: str = STORE_SCAN_NOTE_PATH) -> bool:
    """Stamp `last_run:` on the store's reminder note so the reminder clears.

    This is what makes the store the driver: the note says when the scan is due,
    the scan says when it last ran, and the session-start hook does the
    subtraction. Best-effort — a missing store just means the reminder stays up.
    """
    if not os.path.exists(path):
        print(f"  ⚠ scan note not found at {path} — reminder not stamped",
              file=sys.stderr)
        return False
    try:
        with open(path) as f:
            text = f.read()
        updated = _set_frontmatter(_set_frontmatter(text, "last_run", day),
                                   "updated", day)
        if updated != text:
            _atomic_write(path, updated)
        return True
    except OSError as e:
        print(f"  ⚠ could not stamp {path}: {e}", file=sys.stderr)
        return False


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Weekly PropertyGuru scan + AI triage")
    ap.add_argument("--force", action="store_true",
                    help="run even if this week already completed a successful poll")
    ap.add_argument("--dry-run", action="store_true",
                    help="poll, grade and write the digest, but spawn no agents")
    ap.add_argument("--no-poll", action="store_true",
                    help="skip the scrape — grade what already arrived since the last run")
    ap.add_argument("--headless", action="store_true",
                    help="poll headless (Cloudflare usually blocks this — see module docs)")
    ap.add_argument("--min-score", type=int, default=MIN_SCORE_FOR_AI)
    ap.add_argument("--max-ai", type=int, default=MAX_AI_RUNS_PER_SCAN)
    ap.add_argument("--max-pages", type=int, default=POLL_MAX_PAGES,
                    help=f"pages per district — how far back one scan reaches "
                         f"(default {POLL_MAX_PAGES})")
    ap.add_argument("--districts", type=str, default=None)
    ap.add_argument("--beds", type=str, default=None)
    ap.add_argument("--no-store-push", action="store_true",
                    help="don't append good finds to the personal data store note")
    args = ap.parse_args()

    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    week = iso_week(now)
    state = load_state()

    if already_done_this_week(state, week) and not args.force:
        last = state["last_run"]
        print(f"Weekly scan already ran this week ({week}, "
              f"{last.get('finished_at')}) — digest: {last.get('digest')}. "
              f"Use --force to re-run.")
        return 0

    # 1. Poll
    if args.no_poll:
        poll_state = {"ok": True, "skipped": True,
                      "new_listings": [], "changed_listings": []}
    else:
        print("Polling PropertyGuru…", file=sys.stderr)
        poll_state = poller.run_poll(
            districts=[int(d) for d in args.districts.split(",")] if args.districts else None,
            beds=[int(b) for b in args.beds.split(",")] if args.beds else None,
            max_pages=args.max_pages,
            headless=args.headless,
        )

    rows = list(poll_state.get("new_listings") or []) + \
        list(poll_state.get("changed_listings") or [])

    # --no-poll still has a job to do: grade everything that arrived since the
    # last run (not just today — the whole point of a weekly cadence). That
    # means NEW listings *and* ones that re-priced in the window: a drop INTO
    # the gate is exactly the signal worth catching, and keying only on
    # first_seen would silently discard every one of them.
    if args.no_poll:
        since = (state.get("last_run") or {}).get("date") \
            or (now - timedelta(days=7)).strftime("%Y-%m-%d")
        db = listings_db.load_db()["listings"]
        rows = [{"id": k, "project_name": r.get("project_name") or r.get("title"),
                 "district": r.get("district"), "beds": r.get("beds"),
                 "price": r.get("price"), "psf": r.get("psf"),
                 "score_1000": r.get("score_1000"), "url": r.get("url")}
                for k, r in db.items()
                if (r.get("first_seen") or "") >= since or _repriced_since(r, since)]
        print(f"--no-poll: grading {len(rows)} listing(s) new or re-priced since {since}",
              file=sys.stderr)

    # 2/3. Gate
    db_listings = listings_db.load_db()["listings"]
    cooldown = _eval_cooldown_index(RE_EVAL_COOLDOWN_DAYS, now)
    shortlist, rejected = select_candidates(
        rows, db_listings, cooldown_index=cooldown,
        min_score=args.min_score, max_ai=args.max_ai,
        known_projects=_known_projects())
    print(f"\nGate: {len(rows)} new/changed → {len(shortlist)} for AI analysis "
          f"(>= {args.min_score}, cap {args.max_ai})", file=sys.stderr)

    # 4. AI analyze — sequential on purpose: each run is a full agent doing web
    # research, and serializing keeps cost, logs and any browser use legible.
    verdicts: dict[str, dict] = {}
    runs = []
    if not args.dry_run:
        for i, cand in enumerate(shortlist, 1):
            print(f"  [{i}/{len(shortlist)}] analyzing {cand.get('project_name')} "
                  f"({cand.get('score_1000')})…", file=sys.stderr)
            run = run_agent(cand)
            runs.append(run)
            if not run.get("ok"):
                print(f"      agent run failed: {run.get('error') or run.get('returncode')} "
                      f"— see {run['log']}", file=sys.stderr)
            v = read_verdict(cand, on_or_after=day)
            if v:
                verdicts[cand["id"]] = v
                print(f"      → {v.get('rating')} ({v.get('confidence')})", file=sys.stderr)
            else:
                print("      → no evaluation saved", file=sys.stderr)

    # 6. Store push (before the digest, so the digest can report what was pushed)
    pushed = [] if (args.dry_run or args.no_store_push) \
        else push_to_store(day, shortlist, verdicts)

    # 5. Digest
    digest = build_digest(day, poll_state, shortlist, rejected, verdicts,
                          args.min_score, args.max_ai, args.dry_run, pushed)
    digest_path = write_digest(day, digest)
    print(f"\nDigest: {digest_path}", file=sys.stderr)

    # Clear the store's reminder — only on a real, successful run.
    closed = bool(poll_state.get("ok")) and not args.dry_run
    if closed and not args.no_store_push:
        stamp_scan_note(day)

    state.setdefault("history", []).append({
        "date": day, "week": week, "poll_ok": bool(poll_state.get("ok")),
        # `closed` is the one that matters for the guard: a dry run can have a
        # perfectly good poll and still deliberately leave the week open.
        "closed": closed, "dry_run": args.dry_run,
        "candidates": len(rows), "shortlisted": len(shortlist),
        "analyzed": len(verdicts), "pushed": len(pushed),
    })
    state["history"] = state["history"][-52:]
    state["last_run"] = {
        "date": day, "week": week,
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # A dry run deliberately does not close the week.
        "poll_ok": closed,
        "digest": os.path.relpath(digest_path, BASE),
        "shortlisted": len(shortlist), "analyzed": len(verdicts),
        "pushed": len(pushed), "runs": runs,
    }
    save_state(state)
    return 0 if poll_state.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
