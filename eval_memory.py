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


def staleness_note(date_str: Optional[str]) -> str:
    age = _days_since(date_str)
    if age is None:
        return ""
    if age <= 30:
        return f"evaluated {age}d ago"
    if age <= 120:
        return f"⚠ evaluated {age}d ago — re-verify current price & market"
    return f"⚠ STALE: evaluated {age}d ago — likely out of date, re-verify before relying on it"


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
    with open(INDEX_FILE, "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
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
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                # Stale lock (crashed process) — steal it rather than dropping the save.
                break
            time.sleep(0.1)
    try:
        yield
    finally:
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
        with open(_condo_path(slug), "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
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

    for entry in listings:
        ae = entry.get("agent_evaluation") or {}
        rating = ae.get("rating")
        if not rating:
            continue  # not evaluated by the AI yet — skip

        condo = entry.get("project_name") or entry.get("title")
        if not condo:
            continue

        history_entry = {
            "evaluated_at": today,
            "rating": rating,
            "confidence": ae.get("confidence"),
            "summary": ae.get("summary"),
            "rating_rationale": ae.get("rating_rationale"),
            "red_flags": ae.get("red_flags", []),
            "catalysts": ae.get("catalysts", []),
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
        if target == cand or target in cand or cand in target:
            score = 1.0
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
            if h.get("red_flags"):
                print(f"      red flags: {'; '.join(h['red_flags'])}")


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
