"""Git-tracked evaluation memory.

Stores the AI's qualitative evaluations of condos so that future analyses can
reference what was concluded before. This is committed to the repo (unlike the
per-run `output/`), so the knowledge persists across sessions and machines.

IMPORTANT — past evaluations may be WRONG or STALE. Prices, supply, interest
rates and government plans all change. A stored rating is a *reference point*,
not ground truth. Always re-verify the current price and conditions before
trusting a prior evaluation, and append a fresh evaluation rather than assuming
the old one still holds.

Layout (all under evaluations/):
  evaluations/<slug>.json  — one condo, append-only `history` of evaluations
  evaluations/index.json   — fast lookup: slug -> {condo, district, latest...}
  evaluations/README.md     — the standing caveat (human-facing)

Source of truth is the per-condo JSON files; index.json is rebuilt from them.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Optional

EVAL_DIR = os.path.join(os.path.dirname(__file__), "evaluations")
INDEX_FILE = os.path.join(EVAL_DIR, "index.json")

DISCLAIMER = (
    "⚠ Past evaluations may be stale or wrong — prices and market conditions "
    "change. Treat the rating as a reference, re-verify the current price, and "
    "append a fresh evaluation."
)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _days_since(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        return (datetime.now() - datetime.strptime(date_str, "%Y-%m-%d")).days
    except ValueError:
        return None


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "unknown"


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


# Evaluations written before this date predate the v3.4.1 prior corrections in
# the flow skills/rubric — their rationales routinely credit trailing CAGR and
# freehold as forward edges, which the backtests measured at ~0. The Buy-low
# divergence audit (2026-06-11) traced every big "agent Buy, score low" gap to
# this cohort, not to a scoring bug.
PRIORS_FIXED_DATE = "2026-06-10"


def staleness_note(date_str: Optional[str]) -> str:
    age = _days_since(date_str)
    if age is None:
        return ""
    if age <= 30:
        note = f"evaluated {age}d ago"
    elif age <= 120:
        note = f"⚠ evaluated {age}d ago — re-verify current price & market"
    else:
        note = f"⚠ STALE: evaluated {age}d ago — likely out of date, re-verify before relying on it"
    if date_str and date_str < PRIORS_FIXED_DATE:
        note += (" · ⚠ pre-v3.4.1 priors: rationale may credit trailing CAGR/"
                 "freehold as forward edges (backtested ≈0) — re-judge under "
                 "current priors before trusting the rating")
    return note


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def _condo_path(slug: str) -> str:
    return os.path.join(EVAL_DIR, f"{slug}.json")


def load_condo(slug: str) -> Optional[dict]:
    path = _condo_path(slug)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _ensure_dir() -> None:
    os.makedirs(EVAL_DIR, exist_ok=True)


def rebuild_index() -> dict:
    """Rebuild evaluations/index.json from the per-condo files."""
    _ensure_dir()
    index: dict[str, Any] = {"updated_at": _today(), "condos": {}}
    for fname in os.listdir(EVAL_DIR):
        if not fname.endswith(".json") or fname == "index.json":
            continue
        slug = fname[:-5]
        data = load_condo(slug)
        if not data or not data.get("history"):
            continue
        latest = data["history"][-1]
        index["condos"][slug] = {
            "condo": data.get("condo"),
            "district": data.get("district"),
            "latest_rating": latest.get("rating"),
            "latest_date": latest.get("evaluated_at"),
            "eval_count": len(data["history"]),
        }
    tmp = INDEX_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    os.replace(tmp, INDEX_FILE)
    return index


def load_index() -> dict:
    if not os.path.exists(INDEX_FILE):
        return {"updated_at": None, "condos": {}}
    try:
        with open(INDEX_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"updated_at": None, "condos": {}}


# --------------------------------------------------------------------------- #
# Saving evaluations from a reviewed analysis JSON
# --------------------------------------------------------------------------- #
def _key_facts(entry: dict) -> dict:
    fd = entry.get("factual_data", {})
    appr = fd.get("appreciation", {})
    rental = fd.get("rental", {})
    return {
        "appreciation_pct": appr.get("annual_rate_pct"),
        "appreciation_source": appr.get("data_source"),
        "gross_yield_pct": rental.get("gross_yield_pct"),
        "nearest_mrt": entry.get("nearest_mrt"),
        "tenure": entry.get("tenure"),
        "remaining_lease": entry.get("remaining_lease"),
    }


@contextmanager
def _slug_lock(slug: str, timeout: float = 10.0):
    """Cross-process lock for one condo file, so concurrent sessions evaluating
    different units of the same condo can't lose each other's appends."""
    lock_path = _condo_path(slug) + ".lock"
    deadline = time.monotonic() + timeout
    acquired = False
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                # Stale lock (crashed process) — steal it and take it ourselves
                # rather than dropping the save. (Previously this proceeded
                # WITHOUT the lock and then unlinked the other process's lock.)
                try:
                    os.unlink(lock_path)
                    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(fd)
                    acquired = True
                except OSError:
                    pass  # raced another stealer — proceed rather than drop the save
                break
            time.sleep(0.1)
    try:
        yield
    finally:
        if acquired:
            try:
                os.unlink(lock_path)
            except OSError:
                pass


def save_evaluation(condo: str, district: Optional[str], history_entry: dict) -> str:
    """Append one evaluation to a condo's append-only history. Returns the slug.

    Load-append-write happens under a per-condo lock — concurrent runs on
    different listings of the same condo each get their entry appended.
    """
    _ensure_dir()
    slug = slugify(condo)
    with _slug_lock(slug):
        data = load_condo(slug) or {"condo": condo, "slug": slug, "district": district, "history": []}
        data["condo"] = condo
        if district:
            data["district"] = district
        data.setdefault("history", []).append(history_entry)
        # Atomic write: a crash mid-dump must not truncate the condo's
        # append-only history (load_condo would return None and the next save
        # would silently start a fresh file).
        path = _condo_path(slug)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    return slug


def save_evaluations_from_review(reviewed_path: str, run_dir: Optional[str] = None) -> int:
    """Extract filled agent_evaluations from a reviewed analysis JSON and store them.

    Only listings whose `agent_evaluation.rating` is filled are saved.
    Returns the number of evaluations saved.
    """
    try:
        with open(reviewed_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  Could not read {reviewed_path}: {e}")
        return 0

    listings = data if isinstance(data, list) else data.get("listings", [])
    today = _today()
    saved = 0

    from scoring.models import verdict_from_rating

    skipped = 0
    for entry in listings:
        ae = entry.get("agent_evaluation") or {}
        rating = ae.get("rating")
        if not rating:
            continue  # not evaluated by the AI yet — skip

        condo = entry.get("project_name") or entry.get("title")
        if not condo:
            continue

        # v3.4 validation: don't commit malformed AI fields to git-tracked memory.
        # verdict_from_rating degrades fuzzily (anything containing buy/sell/hold/
        # neutral still maps), so only truly unrecognizable text (e.g. "asdf", "") is
        # skipped here; confidence is normalized to high/medium/low; list fields are
        # coerced so a stray string can't poison downstream joins/arena.
        if verdict_from_rating(rating) is None:
            print(f"  ⚠ skipping {condo}: unrecognized rating {rating!r} "
                  "(expected Strong Buy / Buy / Neutral / Avoid)")
            skipped += 1
            continue
        conf = ae.get("confidence")
        if conf is not None and str(conf).strip().lower() not in ("high", "medium", "low"):
            print(f"  ⚠ {condo}: confidence {conf!r} not in high/medium/low — storing as None")
            conf = None

        def _as_list(v):
            return [] if v is None else (v if isinstance(v, list) else [v])

        history_entry = {
            "evaluated_at": today,
            "rating": rating,
            "confidence": conf,
            "summary": ae.get("summary"),
            "rating_rationale": ae.get("rating_rationale"),
            "red_flags": _as_list(ae.get("red_flags")),
            "catalysts": _as_list(ae.get("catalysts")),
            "as_of": {
                "price": entry.get("price"),
                "psf": entry.get("psf"),
                "beds": entry.get("beds"),
                "sqft": entry.get("sqft"),
            },
            "key_facts": _key_facts(entry),
            "agent_evaluation": ae,
            "source": {
                "run_dir": run_dir,
                "url": entry.get("url"),
                "review_file": os.path.basename(reviewed_path),
            },
        }
        save_evaluation(condo, entry.get("district"), history_entry)
        saved += 1

    if skipped:
        print(f"  ⚠ {skipped} evaluation(s) skipped (malformed rating)")
    if saved:
        rebuild_index()
    return saved


# --------------------------------------------------------------------------- #
# Recall / listing
# --------------------------------------------------------------------------- #
def recall(name: str, fuzzy: bool = True, threshold: float = 0.6) -> list[dict]:
    """Return stored condo evaluations matching `name`, best match first."""
    index = load_index()
    target = _norm(name)
    matches: list[tuple[float, dict]] = []

    for slug, meta in index.get("condos", {}).items():
        cand = _norm(meta.get("condo") or slug)
        if target == cand:
            score = 1.0
        elif target in cand or cand in target:
            score = 0.95  # containment is strong but must not outrank an exact match
        elif fuzzy:
            score = SequenceMatcher(None, target, cand).ratio()
        else:
            score = 0.0
        if score >= (threshold if fuzzy else 1.0):
            data = load_condo(slug)
            if data:
                matches.append((score, data))

    matches.sort(key=lambda t: t[0], reverse=True)
    return [d for _, d in matches]


def print_recall(name: str) -> None:
    results = recall(name)
    print(f"\n{'='*70}\nEVALUATION MEMORY — recall: '{name}'\n{'='*70}")
    print(DISCLAIMER + "\n")
    if not results:
        print("No prior evaluations found. (This is a fresh evaluation.)")
        return
    for data in results:
        print(f"\n● {data.get('condo')} [{data.get('district') or '?'}] "
              f"— {len(data.get('history', []))} evaluation(s)")
        for h in reversed(data.get("history", [])):
            note = staleness_note(h.get("evaluated_at"))
            as_of = h.get("as_of", {})
            price = as_of.get("price")
            price_str = f"${price/1e6:.2f}M" if price else "$?"
            print(f"  • {h.get('evaluated_at')} — {h.get('rating')} "
                  f"(confidence: {h.get('confidence') or '?'}) @ {price_str} "
                  f"{as_of.get('beds') or '?'}BR  [{note}]")
            if h.get("summary"):
                print(f"      {h['summary']}")
            # Show both sides so the recall doesn't anchor negatively or positively
            if h.get("red_flags"):
                print(f"      red flags: {'; '.join(h['red_flags'])}")
            if h.get("catalysts"):
                print(f"      catalysts: {'; '.join(h['catalysts'])}")


# Rating-frequency guardrails (audit, not quota). The honest-verdict rubric has
# no fixed targets, but a healthy distribution over a broad scraped market
# should roughly land in these bands; outside them = systematic drift worth a
# look (gap #4: 2026-06 audit found Strong Buy 4/621 = dead, Neutral 55% =
# central clustering, before the prior fixes).
RATING_BANDS = {
    "Strong Buy": (0.01, 0.08),
    "Buy": (0.10, 0.35),
    "Neutral": (0.30, 0.60),
    "Avoid": (0.10, 0.40),
}


def print_eval_stats() -> None:
    """Rating/confidence distribution audit — overall and post-priors-fix."""
    from collections import Counter
    cohorts = {"ALL": Counter(), f"≥{PRIORS_FIXED_DATE} (current priors)": Counter()}
    conf = Counter()
    n_total = 0
    for fname in os.listdir(EVAL_DIR):
        if not fname.endswith(".json") or fname == "index.json":
            continue
        data = load_condo(fname[:-5])
        if not data or not data.get("history"):
            continue
        latest = data["history"][-1]
        rating, date = latest.get("rating"), latest.get("evaluated_at") or ""
        if not rating:
            continue
        n_total += 1
        cohorts["ALL"][rating] += 1
        conf[latest.get("confidence") or "?"] += 1
        if date >= PRIORS_FIXED_DATE:
            cohorts[f"≥{PRIORS_FIXED_DATE} (current priors)"][rating] += 1
    print(f"\n{'='*70}\nEVALUATION CALIBRATION — latest rating per condo "
          f"(n={n_total})\n{'='*70}")
    for name, counts in cohorts.items():
        n = sum(counts.values())
        print(f"\n  {name}  (n={n})")
        if not n:
            print("    (no evaluations)")
            continue
        for rating in ("Strong Buy", "Buy", "Neutral", "Avoid"):
            share = counts.get(rating, 0) / n
            lo, hi = RATING_BANDS[rating]
            flag = "" if lo <= share <= hi else ("  ⚠ above band" if share > hi
                                                 else "  ⚠ below band (dead?)")
            print(f"    {rating:<11} {counts.get(rating, 0):>4}  {share:>5.1%}"
                  f"   band {lo:.0%}–{hi:.0%}{flag}")
        other = {k: v for k, v in counts.items()
                 if k not in RATING_BANDS}
        if other:
            print(f"    ⚠ invalid rating labels: {dict(other)}")
    print(f"\n  Confidence mix: " + ", ".join(
        f"{k} {v} ({v/max(1,n_total):.0%})" for k, v in conf.most_common()))
    print("  Bands are drift alarms, not quotas — honest verdicts win; "
          "see docs/evaluation-rubric.md.")


def print_index() -> None:
    index = load_index()
    condos = index.get("condos", {})
    print(f"\n{'='*70}\nEVALUATION MEMORY — {len(condos)} condo(s)\n{'='*70}")
    print(DISCLAIMER + "\n")
    if not condos:
        print("No evaluations stored yet.")
        return
    rows = sorted(condos.values(), key=lambda r: r.get("latest_date") or "", reverse=True)
    for r in rows:
        note = staleness_note(r.get("latest_date"))
        print(f"  {r.get('condo'):<32} {str(r.get('district') or ''):<5} "
              f"{str(r.get('latest_rating') or '?'):<12} {r.get('latest_date') or '?'}  "
              f"({r.get('eval_count')}x) {note}")
