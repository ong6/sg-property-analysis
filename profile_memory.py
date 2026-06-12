"""Git-tracked condo PROFILE memory — the stable physical facts of a development.

Distinct from `evaluations/` (price-dependent judgements that go stale): a
development's stacks, facings, layouts and site plan are PHYSICAL facts that
rarely change, so they are researched ONCE (web-searched and screened by a Claude
instance) and reused. This makes "perfect info" about a unit's stack/facing/view
available at evaluation time instead of re-derived every run.

Layout (all under profiles/):
  profiles/<slug>.json  — one development: `development` meta + `stacks[]` + `layouts[]`
  profiles/index.json   — slug -> {condo, district, stack_count, confidence, ...}

Source of truth is the per-condo files; index.json is rebuilt from them. Every
profile carries `sources` (citations) and a `confidence`. "unknown" is a valid
value for any field — NEVER invent a stack or a facing; an absent stack is far
better than a fabricated one. Physical facts don't expire the way prices do, so
freshness here is gentle (a re-research prompt, not a staleness warning).

Schema (see PROFILE_TEMPLATE):
  {
    "condo", "slug", "district",
    "researched_at", "revision", "confidence": "high|medium|low",
    "sources": [{"url", "type": "site_plan|brochure|stackedhomes|forum|listing", "note"}],
    "development": {"total_units", "blocks", "max_floor", "tenure",
                    "orientation_notes", "known_issues": [...]},
    "stacks":  [{"stack", "beds", "sqft", "facing", "view",
                 "desirability": "high|mid|low", "flags": [...], "notes"}],
    "layouts": [{"type": "2BR", "sqft": [..], "efficiency", "notes"}]
  }
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Optional

# Reuse the proven slug/normalise helpers from the evaluation store.
from eval_memory import slugify, _norm  # noqa: F401

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "profiles")
INDEX_FILE = os.path.join(PROFILE_DIR, "index.json")

DISCLAIMER = (
    "ℹ Condo profiles are researched physical facts (stacks/facings/layouts), not "
    "price judgements. Trust the `confidence` and check `sources`; an absent stack "
    "means 'not yet researched', never 'doesn't exist'."
)

PROFILE_TEMPLATE: dict = {
    "condo": None, "slug": None, "district": None,
    "researched_at": None, "revision": 1, "confidence": "low",
    "sources": [], "development": {}, "stacks": [], "layouts": [],
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


def freshness_note(date_str: Optional[str]) -> str:
    """Gentle re-research hint — physical facts age slowly (unlike prices)."""
    age = _days_since(date_str)
    if age is None:
        return ""
    if age <= 365:
        return f"researched {age}d ago"
    return f"researched {age}d ago — re-research if the development changed (en-bloc, AEI)"


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def _profile_path(slug: str) -> str:
    return os.path.join(PROFILE_DIR, f"{slug}.json")


def _ensure_dir() -> None:
    os.makedirs(PROFILE_DIR, exist_ok=True)


def load_profile(slug: str) -> Optional[dict]:
    path = _profile_path(slug)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


@contextmanager
def _slug_lock(slug: str, timeout: float = 10.0):
    """Cross-process lock for one profile file (concurrent sessions safe)."""
    _ensure_dir()
    lock_path = _profile_path(slug) + ".lock"
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
                # Steal the stale lock and take it ourselves rather than drop
                # the write (proceeding unlocked would also wrongly unlink the
                # other process's lock on exit).
                try:
                    os.unlink(lock_path)
                    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(fd)
                    acquired = True
                except OSError:
                    pass  # raced another stealer — proceed rather than drop the write
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


def normalize_profile(profile: dict) -> dict:
    """Fill defaults, derive per-stack match keys, and keep the shape sane.

    Does NOT invent data — only sets structural defaults and computes the
    size-band / floor-tier used by the stack matcher from values already given.
    """
    import config
    p = {**PROFILE_TEMPLATE, **(profile or {})}
    p["condo"] = (p.get("condo") or "").strip() or None
    p["slug"] = p.get("slug") or (slugify(p["condo"]) if p["condo"] else None)
    p["researched_at"] = p.get("researched_at") or _today()
    p.setdefault("development", {})
    p["sources"] = p.get("sources") or []
    p["layouts"] = p.get("layouts") or []

    norm_stacks = []
    for s in p.get("stacks") or []:
        s = dict(s)
        if s.get("sqft"):
            s["_size_band"] = config.size_band_key(s["sqft"])
        if s.get("floor_range") or s.get("floors"):
            s["_floor_tier"] = config.normalize_floor_tier(s.get("floor_range") or s.get("floors"))
        norm_stacks.append(s)
    p["stacks"] = norm_stacks
    return p


def save_profile(profile: dict) -> str:
    """Validate, normalise and write a development profile. Returns the slug.

    Overwrites (facts, not an append-only history) but bumps `revision` and
    preserves the earliest `first_researched_at`, under a per-condo lock.
    """
    p = normalize_profile(profile)
    if not p.get("condo") or not p.get("slug"):
        raise ValueError("profile needs a 'condo' name")
    slug = p["slug"]
    with _slug_lock(slug):
        prior = load_profile(slug)
        if prior:
            p["revision"] = (prior.get("revision") or 1) + 1
            p["first_researched_at"] = prior.get("first_researched_at") or prior.get("researched_at")
        else:
            p["first_researched_at"] = p["researched_at"]
        _ensure_dir()
        # Atomic write: a crash mid-dump must not leave a truncated profile
        # (load_profile would return None and the research would be lost).
        path = _profile_path(slug)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(p, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    rebuild_index()
    return slug


# --------------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------------- #
def rebuild_index() -> dict:
    _ensure_dir()
    index: dict[str, Any] = {"updated_at": _today(), "condos": {}}
    for fname in os.listdir(PROFILE_DIR):
        if not fname.endswith(".json") or fname == "index.json":
            continue
        slug = fname[:-5]
        data = load_profile(slug)
        if not data:
            continue
        index["condos"][slug] = {
            "condo": data.get("condo"),
            "district": data.get("district"),
            "confidence": data.get("confidence"),
            "stack_count": len(data.get("stacks") or []),
            "layout_count": len(data.get("layouts") or []),
            "researched_at": data.get("researched_at"),
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


# Strict-mode fuzzy floor: below this, two different developments routinely
# collide ("Kovan Residences" vs "Avant Residences" scores 0.88).
_STRICT_RATIO = 0.9


def _norm_district(d) -> Optional[str]:
    """Canonical district label ('D15') from 'D15'/'15'/15; None if unparseable."""
    if d in (None, ""):
        return None
    s = str(d).strip().upper().lstrip("D").strip()
    if not s.isdigit():
        return None
    return f"D{int(s):02d}"


def find_profile(name: str, threshold: float = 0.6, *, strict: bool = True,
                 district=None) -> Optional[dict]:
    """Find a profile by condo name (best match, or None).

    strict=True (the default — used by the AUTOMATED listing→profile join in
    scoring/raw_output): only an exact-normalized match, a containment match,
    or a near-exact fuzzy ratio (>= _STRICT_RATIO) qualifies, and when both
    the caller and the candidate carry a district they must agree (pass
    `district=` when known). A wrong profile silently joins another condo's
    stacks/facings/known_issues into factual_data AND suppresses the
    `not_researched` signal — strictly worse than no match (Jun-2026 audit:
    "Kovan Residences" fuzzy-attached Avant Residences at 0.88; 100+ DB
    projects would mis-match at the old 0.6 floor).

    strict=False (the human CLI `--profile` lookup): the original loose fuzzy
    behavior — best SequenceMatcher match >= `threshold`.
    """
    target = _norm(name)
    if not target:
        return None
    want_district = _norm_district(district)
    best, best_score = None, 0.0
    for slug, meta in load_index().get("condos", {}).items():
        if strict and want_district:
            cand_district = _norm_district(meta.get("district"))
            if cand_district and cand_district != want_district:
                continue  # both sides carry a district and they disagree
        cand = _norm(meta.get("condo") or slug)
        if target == cand:
            score = 1.0
        elif target in cand or cand in target:
            # Containment is strong but not exact ("the myst" is inside
            # "the myst at cashew") — must never outrank a true exact match,
            # since this join silently attaches stacks/facings to listings.
            score = 0.95
        else:
            score = SequenceMatcher(None, target, cand).ratio()
        if score > best_score:
            best, best_score = slug, score
    min_score = _STRICT_RATIO if strict else threshold
    if best and best_score >= min_score:
        return load_profile(best)
    return None


# --------------------------------------------------------------------------- #
# The join — match a listing to a stack/layout
# --------------------------------------------------------------------------- #
_FACING_TOKENS = (
    # compound directions FIRST — "north" must not swallow "northeast"
    ("northeast", "NE"), ("northwest", "NW"), ("southeast", "SE"), ("southwest", "SW"),
    ("north", "N"), ("south", "S"), ("east", "E"), ("west", "W"),
)
_FACING_ABBREVS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


def _facing_dir(raw) -> Optional[str]:
    """Canonical 8-point compass token for a messy facing string, or None.

    Whole-direction matching: 'north' → N, 'North-East'/'NE' → NE. A 'north'
    listing therefore no longer ties to NW/NE stacks on the shared first letter.
    """
    if not raw:
        return None
    import re
    compact = re.sub(r"[^a-z]", "", str(raw).lower())  # "north-east fac." → "northeastfac"
    if not compact:
        return None
    if compact.upper() in _FACING_ABBREVS:
        return compact.upper()
    for word, tok in _FACING_TOKENS:
        if compact.startswith(word):
            return tok
    return None


def _stack_floor_tier(stack: dict) -> Optional[str]:
    """The stack's floor tier ('low'/'mid'/'high') from its declared range."""
    tier = stack.get("_floor_tier")  # derived at save time by normalize_profile
    if tier:
        return tier
    fr = stack.get("floor_range") or stack.get("floors")
    if not fr:
        return None
    import config
    return config.normalize_floor_tier(fr)


def match_stack(listing: dict, profile: dict) -> Optional[dict]:
    """Best-effort match of a listing to a stack (or layout) in the profile.

    sqft is the primary key — each layout has a characteristic floor area; beds
    narrows it. When the sqft match is ambiguous, two tiebreaks run in order:
    facing (whole compass direction — 'north' matches N but not NE/NW), then
    floor (the listing's floor_level tier vs each stack's declared floor range,
    when both are known). Ambiguous matches are returned at LOW confidence
    — informative for the agent, never authoritative. Returns None when there is
    no usable match (so the caller simply omits stack info).
    """
    if not profile:
        return None
    sqft = listing.get("sqft")
    beds = listing.get("beds")
    facing_dir = _facing_dir(listing.get("facing"))
    if not sqft:
        return None

    def _filter_beds(items):
        if beds is None:
            return items
        bm = [x for x in items if x.get("beds") in (None, beds)]
        return bm or items

    def _sqft_dist(x):
        return abs(x["sqft"] - sqft) / sqft

    # Prefer stacks; fall back to layouts (sqft list) when stacks aren't researched.
    stacks = [s for s in (profile.get("stacks") or []) if s.get("sqft")]
    pool, kind = (_filter_beds(stacks), "stack") if stacks else ([], None)

    if not pool:
        for lay in profile.get("layouts") or []:
            for sf in (lay.get("sqft") or []):
                pool.append({"sqft": sf, "beds": _beds_from_type(lay.get("type")),
                             "layout_type": lay.get("type"), "notes": lay.get("notes"),
                             "efficiency": lay.get("efficiency")})
        pool, kind = _filter_beds(pool), "layout"

    pool = [x for x in pool if x.get("sqft")]
    if not pool:
        return None

    pool.sort(key=_sqft_dist)
    best = pool[0]
    d = _sqft_dist(best)
    if d > 0.08:  # >8% sqft gap — not confidently the same layout
        return None

    close = [x for x in pool if _sqft_dist(x) <= 0.03]
    tiebreaks = []
    if facing_dir and len(close) > 1:
        fm = [x for x in close if _facing_dir(x.get("facing")) == facing_dir]
        if len(fm) == 1:
            best, close = fm[0], fm
            tiebreaks.append("facing")
    if len(close) > 1:
        # Floor tiebreak: only when the listing's floor and the stacks' floor
        # ranges are both known, and exactly one candidate's tier contains it.
        listing_tier = None
        if listing.get("floor_level") is not None:
            import config
            listing_tier = config.normalize_floor_tier(listing.get("floor_level"))
        if listing_tier:
            fm = [x for x in close if _stack_floor_tier(x) == listing_tier]
            if len(fm) == 1:
                best, close = fm[0], fm
                tiebreaks.append("floor")

    if d <= 0.02 and len(close) <= 1:
        conf = "high"
    elif d <= 0.05 and len(close) <= 2:
        conf = "medium"
    else:
        conf = "low"

    out = {
        "matched": kind,
        "confidence": conf,
        "basis": f"sqft Δ{d*100:.1f}%" + "".join(f" + {t}" for t in tiebreaks),
        "stack": best.get("stack"),
        "layout_type": best.get("layout_type"),
        "beds": best.get("beds"),
        "sqft": best.get("sqft"),
        "facing": best.get("facing"),
        "view": best.get("view"),
        "desirability": best.get("desirability"),
        "flags": best.get("flags") or [],
        "notes": best.get("notes"),
        "ambiguous_with": [x.get("stack") for x in close if x is not best and x.get("stack")],
    }
    return {k: v for k, v in out.items() if v not in (None, [], "")} or None


_BEDS_WORD = {"studio": 0, "1br": 1, "2br": 2, "3br": 3, "4br": 4, "5br": 5}


def _beds_from_type(t: Optional[str]) -> Optional[int]:
    if not t:
        return None
    s = str(t).lower().replace(" ", "")
    for k, v in _BEDS_WORD.items():
        if k in s:
            return v
    import re
    m = re.search(r"(\d+)\s*(?:br|bed)", s)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# CLI printing
# --------------------------------------------------------------------------- #
def print_profile(name: str) -> None:
    # Loose fuzzy lookup is fine here: a human typed the name and reads the
    # result (the strict default protects the automated listing→profile join).
    profile = find_profile(name, strict=False)
    print(f"\n{'='*70}\nCONDO PROFILE — '{name}'\n{'='*70}")
    print(DISCLAIMER + "\n")
    if not profile:
        print("No profile yet. Research one with the /research-development flow.")
        return
    dev = profile.get("development", {})
    print(f"● {profile.get('condo')} [{profile.get('district') or '?'}] "
          f"— confidence {profile.get('confidence')} · {freshness_note(profile.get('researched_at'))}")
    if dev:
        bits = [f"{k}={v}" for k, v in dev.items() if k != "known_issues" and v not in (None, "")]
        if bits:
            print("  " + " · ".join(bits))
        for issue in dev.get("known_issues") or []:
            print(f"  ⚠ {issue}")
    stacks = profile.get("stacks") or []
    if stacks:
        print(f"\n  Stacks ({len(stacks)}):")
        for s in stacks:
            flags = f"  flags: {', '.join(s['flags'])}" if s.get("flags") else ""
            print(f"    #{s.get('stack','?'):<4} {str(s.get('beds') or '?')}BR "
                  f"{str(s.get('sqft') or '?'):>5}sqft  {str(s.get('facing') or '?'):<3} "
                  f"view={s.get('view') or '?':<8} {s.get('desirability') or '?'}{flags}")
    for lay in profile.get("layouts") or []:
        print(f"  layout {lay.get('type')}: {lay.get('sqft')} ({lay.get('efficiency') or '?'}) "
              f"{lay.get('notes') or ''}")
    if profile.get("sources"):
        print("\n  Sources:")
        for src in profile["sources"]:
            print(f"    - [{src.get('type','?')}] {src.get('url','')} {src.get('note','')}")


def print_index() -> None:
    condos = load_index().get("condos", {})
    print(f"\n{'='*70}\nCONDO PROFILES — {len(condos)} development(s)\n{'='*70}")
    print(DISCLAIMER + "\n")
    if not condos:
        print("No profiles yet.")
        return
    for r in sorted(condos.values(), key=lambda r: r.get("researched_at") or "", reverse=True):
        print(f"  {r.get('condo'):<32} {str(r.get('district') or ''):<5} "
              f"conf={str(r.get('confidence') or '?'):<7} "
              f"{r.get('stack_count')} stacks / {r.get('layout_count')} layouts  "
              f"{freshness_note(r.get('researched_at'))}")


def save_profile_from_file(path: str) -> str:
    """Load a profile JSON (produced by the research flow) and store it."""
    with open(path) as f:
        return save_profile(json.load(f))
