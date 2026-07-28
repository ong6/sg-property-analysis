#!/usr/bin/env python3
"""realsmart.sg slug resolution — turn a project name into its /p/<slug> URL.

Guessing the slug from a PropertyGuru project name is wrong often enough to
matter: PG abbreviates ("West Bay Condo") where realsmart spells it out
("west-bay-condominium"), so the guess 404s and the scan silently loses the
REALSCORE for that project.

realsmart publishes every property URL in its sitemap, and the web-extract
skill's own notes call that out as the intended way to *resolve slugs* (as
opposed to bulk-collecting pages, which their personal-use ToS forbids). So:
fetch the sitemap once, cache the 12.8k slugs locally, and resolve names against
it. That is one sitemap request a month, and zero extra page requests.

    python realsmart.py --refresh              # pull/refresh the slug index
    python realsmart.py "West Bay Condo"       # resolve one name

Used by weekly.py to build the REALSCORE lookup URL it hands each agent.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
SLUG_FILE = os.path.join(DATA_DIR, "realsmart_slugs.json")
SITEMAP_URL = "https://realsmart.sg/sitemap.xml"
STORE = os.environ.get(
    "PF_STORE", os.path.expanduser("~/Sideproject/personal-data-store"))
SITEMAP_TOOL = os.path.join(
    STORE, ".claude", "skills", "web-extract", "scripts", "sitemap.py")

# Only the tail-word abbreviations PropertyGuru actually uses. Kept small and
# explicit: an aggressive synonym table would start matching the wrong project,
# which is worse than returning nothing (the caller reports "not found" and the
# agent leaves the field null rather than inventing a number).
_EXPANSIONS = {
    "condo": "condominium",
    "apt": "apartment",
    "apts": "apartments",
    "res": "residences",
    "residence": "residences",
    "gdns": "gardens",
    "ctr": "centre",
    "hts": "heights",
    "pk": "park",
    "mans": "mansions",
}


def normalize(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace to single hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _variants(name: str) -> list[str]:
    """Plausible slugs for a name, best first (no network)."""
    base = normalize(name)
    if not base:
        return []
    out = [base]
    parts = base.split("-")
    # Expand a trailing abbreviation ("...-condo" -> "...-condominium") and,
    # separately, drop a trailing generic ("...-condo" -> "...").
    if parts[-1] in _EXPANSIONS:
        out.append("-".join(parts[:-1] + [_EXPANSIONS[parts[-1]]]))
    if len(parts) > 1 and parts[-1] in ("condo", "condominium", "apartment", "apartments"):
        out.append("-".join(parts[:-1]))
    if parts[0] == "the" and len(parts) > 1:
        out.append("-".join(parts[1:]))
    else:
        out.append("the-" + base)
    seen, uniq = set(), []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def load_slugs() -> dict:
    """{slug: True} index plus metadata, or {} when not yet fetched."""
    try:
        with open(SLUG_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def refresh_slugs(timeout_s: int = 180) -> int:
    """Fetch the sitemap and cache every /p/<slug>. Returns the count."""
    if not os.path.exists(SITEMAP_TOOL):
        print(f"  ⚠ sitemap tool not found at {SITEMAP_TOOL}", file=sys.stderr)
        return 0
    try:
        proc = subprocess.run(
            [sys.executable, SITEMAP_TOOL, SITEMAP_URL, "--match", "/p/",
             "--limit", "20000"],
            capture_output=True, text=True, timeout=timeout_s)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"  ⚠ sitemap fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 0

    slugs = sorted({m.group(1) for m in
                    re.finditer(r"realsmart\.sg/p/([^\s/?#]+)", proc.stdout)})
    if not slugs:
        print("  ⚠ sitemap returned no /p/ URLs — index left unchanged",
              file=sys.stderr)
        return 0

    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SLUG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"fetched_from": SITEMAP_URL, "count": len(slugs),
                   "slugs": slugs}, f, indent=0)
    os.replace(tmp, SLUG_FILE)
    return len(slugs)


def resolve(project_name: str, index: dict | None = None) -> str | None:
    """Return the real slug for `project_name`, or None if not in the index.

    None is a first-class answer — the caller should say "not found" rather
    than fall back to a guess, because a guessed URL that happens to 200 on a
    DIFFERENT project would attach the wrong REALSCORE to the listing.
    """
    idx = load_slugs() if index is None else index
    known = set(idx.get("slugs") or ())
    if not known:
        return None
    for v in _variants(project_name):
        if v in known:
            return v
    # Last resort: a unique slug that starts with the normalized name. Unique
    # only — an ambiguous prefix means we cannot tell which project it is.
    base = normalize(project_name)
    if len(base) >= 6:
        hits = [s for s in known if s.startswith(base + "-")]
        if len(hits) == 1:
            return hits[0]
    return None


def url_for(project_name: str, index: dict | None = None) -> tuple[str, bool]:
    """(url, resolved). Falls back to the naive guess with resolved=False."""
    slug = resolve(project_name, index)
    if slug:
        return f"https://realsmart.sg/p/{slug}", True
    return f"https://realsmart.sg/p/{normalize(project_name)}", False


def main() -> int:
    if "--refresh" in sys.argv:
        n = refresh_slugs()
        print(f"Cached {n} realsmart property slugs -> {SLUG_FILE}")
        return 0 if n else 1
    names = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not names:
        print(__doc__)
        return 1
    idx = load_slugs()
    if not idx:
        print("No slug index yet — run: python realsmart.py --refresh",
              file=sys.stderr)
        return 1
    for n in names:
        url, ok = url_for(n, idx)
        print(f"{n!r:45s} -> {url}  {'' if ok else '(GUESS — not in sitemap)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
