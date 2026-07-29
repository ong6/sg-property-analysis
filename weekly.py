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
import signal
import subprocess
import sys
import time
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

# --------------------------------------------------------------------------- #
# The search mandate — WHAT the owner is actually buying.
#
# Stated 2026-07-28; mirrored in the store's home-buying README. Two buyers,
# two budgets, one shared region constraint. Everything else here is policy
# about HOW to scan; this is the only block that says WHAT to look for, so it
# is the first thing to change when the hunt changes.
#
# OCR (D16-D28) is a hard filter, not a preference. It also happens to be where
# the backtest points: realized forward OCR +3.7%/yr > RCR +3.1 >> CCR +1.9,
# and region is the strongest single forward signal in the model.
# --------------------------------------------------------------------------- #
# OCR is D16-D28, minus four the owner ruled out as "too far and never make
# money" (2026-07-28). Which four is a measured call, not a vibe — from the
# repo's own URA resale prints (data/ura_district_D*.csv, resale-only):
#
#   D24 Lim Chu Kang/Tengah — 860 prints, ALL New Sale, ZERO resales. No resale
#       market exists, so there is no exit liquidity and no evidence anyone has
#       ever resold at a profit. Disqualifying for a resale-profit mandate.
#   D25 Woodlands/Admiralty — 591 resales across just 7 projects, the thinnest
#       book in OCR, and the lowest median psf (~$1,151).
#   D27 Sembawang/Yishun — weakest trailing 3y (+3.0%/yr) of any OCR district.
#   D17 Changi/Loyang — far east, thin (33 projects), low psf (~$1,254), +4.4%.
#
# Honesty about the evidence: trailing CAGR is WEAK grounds on its own — the
# repo's backtest puts its marginal forward power near zero once clustered
# ("story, not signal"). The load-bearing argument here is DEPTH, which does
# survive clustering (buyer-pool std_β +0.16): D24 and D25 are excluded because
# their resale markets are absent or tiny, which is a liquidity fact, not a
# forecast. D27 and D17 are the softer calls, resting more on the trailing
# numbers — revisit them if the search comes up empty.
#
# Kept despite a low trailing print: D16 Bedok (+3.9%) — it is not "far", and
# it is one of the deepest books in OCR (3,309 resales, 70 projects), so exit
# liquidity is excellent. Depth outranks a 3-year window.
OCR_DISTRICTS = [16, 18, 19, 20, 21, 22, 23, 26, 28]
OCR_EXCLUDED = {17: "far east, thin, low psf",
                24: "no resale market at all (Tengah)",
                25: "thinnest book in OCR (7 projects)",
                27: "weakest trailing returns in OCR"}

MANDATES = [
    # label      max_price   beds        who
    ("his",  1_800_000, (3,),    "him — up to ~$1.8M (flexible), 3BR"),
    ("hers", 2_500_000, (3, 4),  "her — ~$2.5M, 3-4BR, whichever returns most"),
]
# Union of the mandates, used for the scrape itself.
SCAN_DISTRICTS = OCR_DISTRICTS
SCAN_BEDS = sorted({b for _, _, beds, _ in MANDATES for b in beds})
SCAN_MAX_PRICE = max(p for _, p, _, _ in MANDATES)
# A little headroom over the top budget: an ask slightly above it is still worth
# seeing (asks negotiate down), whereas anything far above is noise.
SCAN_PRICE_HEADROOM = 1.06


def district_num(district) -> int | None:
    """'D19' / 'd19' / 19 -> 19. None when it can't be read."""
    digits = "".join(c for c in str(district or "") if c.isdigit())
    return int(digits) if digits else None


def in_region(district) -> bool:
    """True if the district is in the mandate's region (OCR).

    Enforced at the GATE, not just in the scrape scope. The scrape scope only
    controls what a fresh poll fetches; the DB also holds thousands of older
    out-of-region listings, and re-gating without this check would happily
    shortlist RCR stock nobody is looking for. Unknown district = not in scope:
    the mandate is a hard filter, so an unreadable district fails closed.
    """
    n = district_num(district)
    return n is not None and n in OCR_DISTRICTS


def mandate_summary() -> str:
    """'3BR<=$1.8M or 3-4BR<=$2.5M' — derived, never hand-written.

    This string goes into every off-mandate rejection line in the digest. It was
    hardcoded once and immediately went stale when his ceiling moved $1.5M ->
    $1.8M, so every rejection told the reader the wrong budget. Deriving it from
    MANDATES means the explanation cannot disagree with the rule again.
    """
    parts = []
    for _, max_price, beds, _ in MANDATES:
        bed_txt = "-".join(str(b) for b in sorted(beds))
        parts.append(f"{bed_txt}BR<=${max_price / 1e6:.1f}M")
    return " or ".join(parts)


def mandate_for(price, beds, district=None) -> str | None:
    """Which buyer's mandate a listing fits ('his' / 'hers'), or None.

    Cheapest-fitting mandate wins, so a $1.4M 3BR is tagged 'his' rather than
    'hers' — it is the tighter budget's find, and hers has better options at
    that price. When `district` is given it must also be in region; callers
    that only have price/beds can omit it and check `in_region` separately.
    """
    if price is None or beds is None:
        return None
    if district is not None and not in_region(district):
        return None
    for label, max_price, ok_beds, _ in MANDATES:
        if beds in ok_beds and price <= max_price:
            return label
    return None


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


def realsmart_url(project_name: str | None) -> tuple[str, bool]:
    """(url, resolved) for a project's realsmart page.

    Resolved against the cached sitemap slug index (`realsmart.py`), because
    guessing gets it wrong often enough to matter — PropertyGuru writes "West
    Bay Condo" where realsmart has "west-bay-condominium", and the guess 404s.
    Falls back to the naive guess with resolved=False, which the prompt then
    labels as unverified so the agent knows to check the page identity before
    trusting a score.
    """
    try:
        import realsmart
        return realsmart.url_for(project_name or "")
    except Exception:  # noqa: BLE001 — a missing/broken index must not stop a scan
        slug = re.sub(r"[^a-z0-9]+", "-", (project_name or "").lower()).strip("-")
        return f"https://realsmart.sg/p/{slug}", False


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


def _bed_mismatch(project: str, beds, sqft) -> dict | None:
    """A confirmed bedroom relabel for this project, or None.

    Only `mismatch` blocks — the size is below the project's own band for that
    bed count AND lands in a smaller one, which is the 2BR-sold-as-3BR pattern.
    `oversize`, `undersize` and `unknown` all pass: a roomy unit is not a
    mislabel, and most projects have no rental history to judge by. Never
    raises — a missing band file must not stop a scan.
    """
    try:
        import bed_bands
        v = bed_bands.check(project, beds, sqft)
    except Exception:  # noqa: BLE001 — advisory check, never fatal
        return None
    return v if v.get("verdict") == "mismatch" else None


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
    eligible: list[dict] = []

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
        district = row.get("district") or rec.get("district")
        mandate = mandate_for(row.get("price"), row.get("beds"), district)
        cand["mandate"] = mandate

        if rec.get("status") == "stale":
            cand["gate_reason"] = "listing went stale this cycle"
        elif not (rec.get("price") and rec.get("sqft") and rec.get("psf")):
            cand["gate_reason"] = "incomplete record (price/sqft/psf)"
        elif score is None:
            cand["gate_reason"] = "not scored"
        elif score < min_score:
            cand["gate_reason"] = f"below gate ({score} < {min_score})"
        elif not in_region(district):
            cand["gate_reason"] = (
                f"out of region ({district or '?'} is not OCR — mandate is D16-D28)")
        elif mandate is None:
            # Off-mandate: nobody can buy it, so its score is irrelevant.
            cand["gate_reason"] = (
                f"outside the mandate ({row.get('beds')}BR @ "
                f"{_money(row.get('price'))} — need {mandate_summary()})")
        elif looks_like_marketing_title(name, known_projects):
            # Not a judgement on the unit — we simply don't know which
            # development it is, so there is nothing to research.
            cand["gate_reason"] = "no usable project name (listing headline, not a development)"
        elif (bed := _bed_mismatch(name, row.get("beds"), cand.get("sqft"))):
            # The bedroom count comes from the marketing agent, not a registry,
            # and 2BR+study routinely gets listed as 3BR. The mandate screens on
            # bed count, so an unchallenged relabel puts the wrong product in
            # front of the buyer entirely.
            cand["gate_reason"] = (
                f"bed count looks wrong — {bed['reason']}")
        elif (blocked := _blocked_by_cooldown(cooldown_index, slug, row.get("beds"))):
            cand["gate_reason"] = f"same unit type evaluated {blocked} (cooldown)"
        elif cohort in seen_cohort:
            cand["gate_reason"] = "same condo + bed count already shortlisted this scan"
        else:
            cand["slug"] = slug
            seen_cohort.add(cohort)
            eligible.append(cand)     # budget allocated below, not here
            continue

        rejected.append(cand)

    # --- Allocate the AI budget across the buyers -------------------------- #
    # Two passes, because neither extreme is right on its own:
    #   * pure score ranking lets hers (a ~$1M larger budget, so systematically
    #     nicer stock) take every slot and his mandate never gets researched;
    #   * a hard per-buyer cap wastes slots in the common case where only one
    #     buyer has candidates this week.
    # So: give each mandate its fair share first, then hand any slots nobody
    # claimed to the best remaining listings regardless of buyer.
    shortlist, deferred = _allocate_budget(eligible, max_ai)
    rejected.extend(deferred)
    return shortlist, rejected


def _allocate_budget(eligible: list[dict], max_ai: int) -> tuple[list[dict], list[dict]]:
    """Split `max_ai` slots across mandates fairly, then fill leftovers by score.

    `eligible` must already be score-ordered. Returns (shortlist, deferred);
    deferred candidates carry a gate_reason explaining which cap they hit.
    """
    present = [m for m, *_ in MANDATES if any(c.get("mandate") == m for c in eligible)]
    if not present:
        return [], []
    fair_share = max(1, -(-max_ai // len(present)))    # ceil, so 8/2 -> 4 each

    taken: dict[str, int] = {}
    shortlist, leftovers = [], []
    for cand in eligible:                              # pass 1: fair share
        m = cand.get("mandate")
        if len(shortlist) < max_ai and taken.get(m, 0) < fair_share:
            taken[m] = taken.get(m, 0) + 1
            shortlist.append(cand)
        else:
            leftovers.append(cand)

    deferred = []
    for cand in leftovers:                             # pass 2: unclaimed slots
        if len(shortlist) < max_ai:
            shortlist.append(cand)
        else:
            cand["gate_reason"] = f"over the per-scan AI cap ({max_ai})"
            deferred.append(cand)

    shortlist.sort(key=lambda c: c.get("score_1000") or 0, reverse=True)
    return shortlist, deferred


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
    rs_url, rs_resolved = realsmart_url(cand.get("project_name"))
    rs_note = "" if rs_resolved else (
        "(NOTE: this URL is a GUESS — the project is not in realsmart's sitemap "
        "index, so verify the page is actually this development, or skip it.)\n")
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
        f"    python3 {WEB_EXTRACT_FETCH} \"{rs_url}\"\n"
        f"{rs_note}"
        "Public page, plain fetch, no login. Read off REALSCORE (0-5 "
        "profitability rank), the '% Profitable' badge, avg annualized profit, "
        "and the transaction COUNT behind them, then fill `realscore`, "
        "`realsmart_pct_profitable` and `realsmart_annual_return_pct` in "
        "agent_evaluation. Confirm the page's title really is this development "
        "before reading numbers off it. If it 404s or is a different project, "
        "leave the fields null and say so — never guess a number, and never "
        "attach another project's score to this listing. Weigh it as downside "
        "evidence (has this project ever lost owners money?), NOT as an "
        "appreciation forecast: it is backward-looking like trailing CAGR, and "
        "a perfect score on a handful of transactions means little — always "
        "report the transaction count with it. Uncompleted projects "
        "legitimately show N.A.\n\n"
        "This is a NON-INTERACTIVE headless run in the weekly scan: do not ask "
        "anything; where the flow would ask about purpose, assume investment "
        "(5-7yr hold) and proceed. Do the real research step — your view first, "
        "algo_reference second. An honest Neutral or Avoid is the expected "
        "outcome most weeks; never manufacture a Buy. Finish the full flow "
        "including --from-review so the verdict is saved to eval memory."
    )


def run_agent(cand: dict, timeout_s: int = AI_TIMEOUT_S) -> dict:
    """Run one headless `claude -p` analyze pass. Never raises.

    The deadline is enforced on the WALL CLOCK, not via subprocess.run(timeout=),
    and the child gets its own process group.

    Both details are load-bearing, learned the hard way: on the first real OCR
    scan an agent ran 1h45m against a 30-minute timeout that never fired,
    blocking the queue behind it. subprocess.run's timeout is measured with
    time.monotonic(), which on macOS does NOT advance while the system sleeps —
    so a laptop that naps mid-run pauses the timeout while real time keeps
    passing. Wall clock is what "this has been stuck for an hour" actually
    means. The process group matters because `claude` spawns children; killing
    only the direct child can leave orphans holding the work.
    """
    os.makedirs(RUNS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(RUNS_DIR, f"{cand.get('slug') or cand['id']}_{stamp}.log")
    cmd = ["claude", "-p", _agent_prompt(cand), "--allowedTools", AI_ALLOWED_TOOLS]
    result = {"id": cand["id"], "log": os.path.relpath(log_path, BASE)}
    logf = None
    try:
        logf = open(log_path, "w")
        proc = subprocess.Popen(cmd, cwd=BASE, stdin=subprocess.DEVNULL,
                                stdout=logf, stderr=subprocess.STDOUT,
                                start_new_session=True)
    except (OSError, ValueError) as e:
        if logf:
            logf.close()
        result.update(ok=False, error=f"{type(e).__name__}: {e}")
        return result

    deadline = time.time() + timeout_s
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                result["returncode"] = rc
                result["ok"] = rc == 0
                break
            if time.time() >= deadline:
                _kill_group(proc)
                result.update(ok=False,
                              error=f"timed out after {timeout_s}s (wall clock)")
                break
            time.sleep(2)
    finally:
        logf.close()
    return result


def keep_awake() -> subprocess.Popen | None:
    """Hold off idle sleep for as long as this scan runs (macOS).

    Not a nicety — it is the fix for the failure that wrecked the first real OCR
    scan. On battery the Mac took repeated 'Maintenance Sleep' naps mid-run;
    every sleeping agent lost its connection ("API Error: Connection closed
    mid-response") and 5 of 8 candidates came back with no verdict at all.

    `caffeinate -w <pid>` exits by itself when this process does, so the
    assertion can never outlive the scan and leave the machine awake. Silent
    no-op off macOS, and a failure to caffeinate is not worth aborting a scan
    over — it only makes sleep possible again, which is where we started.
    """
    if sys.platform != "darwin":
        return None
    try:
        return subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except (OSError, ValueError) as e:  # noqa: BLE001 — best effort by design
        print(f"  ⚠ could not hold off sleep ({type(e).__name__}: {e}); "
              "long agent runs may be interrupted", file=sys.stderr)
        return None


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM the child's process group, then SIGKILL what survives."""
    for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


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


_MANDATE_TAG = {"his": "**HIS**", "hers": "**HERS**"}


def _row(c: dict) -> str:
    return (f"| {c.get('score_1000') or '—'} | "
            f"{_MANDATE_TAG.get(c.get('mandate'), '—')} | "
            f"{c.get('project_name') or '—'} | "
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
              "| Score | For | Project | Beds | Price | PSF | District |",
              "|---|---|---|---|---|---|---|"] + [_row(c) for c in shortlist] + [""]

    if rejected:
        L += [f"## Algo-only — {len(rejected)} not sent to the agent", "",
              "| Score | For | Project | Beds | Price | PSF | District | Why |",
              "|---|---|---|---|---|---|---|---|"]
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
    ap.add_argument("--full-book", action="store_true",
                    help="gate over EVERY active in-scope listing, not just this "
                         "cycle's new/re-priced ones — 'what is the best thing on "
                         "the market', rather than 'what is new this week'")
    ap.add_argument("--headless", action="store_true",
                    help="poll headless (Cloudflare usually blocks this — see module docs)")
    ap.add_argument("--min-score", type=int, default=MIN_SCORE_FOR_AI)
    ap.add_argument("--max-ai", type=int, default=MAX_AI_RUNS_PER_SCAN)
    ap.add_argument("--max-pages", type=int, default=POLL_MAX_PAGES,
                    help=f"pages per district — how far back one scan reaches "
                         f"(default {POLL_MAX_PAGES})")
    ap.add_argument("--districts", type=str, default=None,
                    help=f"override the mandate's districts (default OCR {SCAN_DISTRICTS})")
    ap.add_argument("--beds", type=str, default=None,
                    help=f"override the mandate's bed counts (default {SCAN_BEDS})")
    ap.add_argument("--max-price", type=int,
                    default=int(SCAN_MAX_PRICE * SCAN_PRICE_HEADROOM),
                    help="scrape ceiling; defaults to the top mandate budget + headroom")
    ap.add_argument("--no-store-push", action="store_true",
                    help="don't append good finds to the personal data store note")
    args = ap.parse_args()

    keep_awake()

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
        # Reuse the LAST poll's own results rather than re-deriving from the DB
        # — that keeps price-changed listings (which a first_seen scan can't
        # recover, their first_seen is old) and costs PropertyGuru nothing.
        poll_state = poller.load_poll_state() or {}
        if poll_state.get("ok") and poll_state.get("last_run", "")[:10] == day:
            poll_state = {**poll_state, "skipped": True, "reused": True}
            print(f"--no-poll: reusing today's poll "
                  f"({len(poll_state.get('new_listings') or [])} new, "
                  f"{len(poll_state.get('changed_listings') or [])} changed)",
                  file=sys.stderr)
        else:
            poll_state = {"ok": True, "skipped": True,
                          "new_listings": [], "changed_listings": []}
    else:
        print("Polling PropertyGuru…", file=sys.stderr)
        poll_state = poller.run_poll(
            districts=[int(d) for d in args.districts.split(",")] if args.districts
            else SCAN_DISTRICTS,
            beds=[int(b) for b in args.beds.split(",")] if args.beds else SCAN_BEDS,
            max_pages=args.max_pages,
            max_price=args.max_price,
            headless=args.headless,
        )

    rows = list(poll_state.get("new_listings") or []) + \
        list(poll_state.get("changed_listings") or [])

    # --no-poll still has a job to do: grade everything that arrived since the
    # last run (not just today — the whole point of a weekly cadence). That
    # means NEW listings *and* ones that re-priced in the window: a drop INTO
    # the gate is exactly the signal worth catching, and keying only on
    # first_seen would silently discard every one of them.
    # --full-book asks a different question. The delta view ("what arrived this
    # week?") is right for monitoring, but the owner is buying ONE property out
    # of everything currently for sale — and standing inventory is invisible to
    # a delta scan forever. Treasure at Tampines had 103 listings in the DB and
    # could never surface, because none of them were new in any later cycle.
    if args.full_book:
        db = listings_db.load_db()["listings"]
        rows = [{"id": k, "project_name": r.get("project_name") or r.get("title"),
                 "district": r.get("district"), "beds": r.get("beds"),
                 "price": r.get("price"), "psf": r.get("psf"),
                 "score_1000": r.get("score_1000"), "url": r.get("url")}
                for k, r in db.items() if r.get("status") != "stale"]
        print(f"--full-book: gating over {len(rows)} active listing(s) — the whole "
              f"market in scope, not just this cycle's changes", file=sys.stderr)

    elif args.no_poll and not poll_state.get("reused"):
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

            # One retry when the run produced no verdict. Transient API errors
            # ("Connection closed mid-response") land here AFTER the research is
            # done but before the evaluation is written — losing the whole slot
            # to a flaky connection. Slots are the scarce resource, so buying
            # one retry is cheaper than dropping a candidate. Strictly one: a
            # listing that genuinely defeats the flow must not loop.
            if v is None:
                print("      → no evaluation saved; retrying once", file=sys.stderr)
                run = run_agent(cand)
                run["retry"] = True
                runs.append(run)
                v = read_verdict(cand, on_or_after=day)

            if v:
                verdicts[cand["id"]] = v
                print(f"      → {v.get('rating')} ({v.get('confidence')})", file=sys.stderr)
            else:
                print("      → no evaluation saved (after retry)", file=sys.stderr)

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
