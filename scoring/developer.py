"""Developer attribution + track-record signal (v3.3 Phase 1).

Developer reputation (build quality, defects, delays, delivery) is a real
evaluation factor the rubric already asks the AI to research — but it was never
captured structurally. PropertyGuru only exposes a developer on new-launch pages
(~0% of our resale DB), and URA caveats carry no developer column. So attribution
is an *enrichment* problem: web-researched per project into `developer_cache.json`
(top URA-liquidity projects backfilled; the rest filled lazily as evaluated).

This module is REFERENCE ONLY — it surfaces developer + track record into
factual_data for the AI to judge. It does NOT feed an MMR weight: per the v3.3
backtest discipline, a factor earns a score weight only after it's shown to
predict forward returns (Phase 2, once attribution coverage exists). Notably,
past appreciation itself was found NOT to predict — so a developer's value is
mostly its qualitative record, not its projects' headline CAGR.
"""

import json
import os
import re
from typing import Any, Optional

_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "data", "developer_cache.json")
_cache: Optional[dict] = None

# Legal-entity / SPV noise stripped when grouping developers. Web research stores
# a canonical `developer_group` (e.g. "CDL", "Hoi Hup") precisely so JV special-
# purpose names ("Dairy Farm Walk JV Development Pte Ltd") still group correctly;
# this is a fallback normalizer for when only a raw `developer` string exists.
_DEV_NOISE = re.compile(
    r"\b(pte|ltd|limited|llp|group|holdings?|development[s]?|realty|land|"
    r"property|properties|investments?|enterprises?|co)\b", re.I)


def _norm_project(name: str) -> str:
    """Project key: lowercased, parens/punctuation stripped (joins to URA/listings)."""
    if not name:
        return ""
    s = re.sub(r"\s*\(.*?\)", "", name).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_developer(name: str) -> str:
    """Collapse a raw developer/SPV string toward a comparable group key."""
    if not name:
        return ""
    s = re.sub(r"\s*\(.*?\)", " ", name)
    s = _DEV_NOISE.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def load_cache(force: bool = False) -> dict:
    global _cache
    if _cache is None or force:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE) as f:
                _cache = json.load(f)
        else:
            _cache = {"updated": None, "attribution": {}}
    return _cache


def save_cache(cache: dict) -> None:
    global _cache
    with open(_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    _cache = cache


def get_attribution(project_name: str) -> Optional[dict]:
    """Developer attribution for a project (exact then normalized match)."""
    attr = load_cache().get("attribution", {})
    if not project_name:
        return None
    if project_name in attr:
        return attr[project_name]
    key = _norm_project(project_name)
    for k, v in attr.items():
        if _norm_project(v.get("project_name", k)) == key or _norm_project(k) == key:
            return v
    return None


def _group_key(entry: dict) -> str:
    return _norm_developer(entry.get("developer_group") or entry.get("developer") or "")


def build_track_record(ura_data: dict) -> dict:
    """Aggregate each developer's OTHER projects' URA stats (data we already own).

    Returns {developer_group_key: {label, projects:[{name, district, appreciation_pct,
    txns}], project_count, median_appreciation_pct}}. Appreciation is shown for
    context only (it is a weak forward signal per the v3.3 backtest); the count
    and the project list are what let the AI gauge a developer's footprint.
    """
    attr = load_cache().get("attribution", {})
    from statistics import median
    groups: dict[str, dict] = {}
    for proj_key, entry in attr.items():
        gk = _group_key(entry)
        if not gk:
            continue
        g = groups.setdefault(gk, {
            "label": entry.get("developer_group") or entry.get("developer"),
            "projects": [],
        })
        u = ura_data.get(_norm_project(entry.get("project_name", proj_key))) or \
            ura_data.get(entry.get("project_name", "").lower()) or {}
        appr = u.get("resale_annualized_appreciation")
        if appr is None:
            appr = u.get("annualized_appreciation")
        g["projects"].append({
            "name": entry.get("project_name", proj_key),
            "district": entry.get("district"),
            "appreciation_pct": round(appr, 1) if isinstance(appr, (int, float)) else None,
            "txns": u.get("transaction_count"),
        })
    for g in groups.values():
        apprs = [p["appreciation_pct"] for p in g["projects"] if p["appreciation_pct"] is not None]
        g["project_count"] = len(g["projects"])
        g["median_appreciation_pct"] = round(median(apprs), 1) if apprs else None
    return groups


def developer_reference(project_name: str, ura_data: dict,
                        track_record: Optional[dict] = None) -> Optional[dict]:
    """AI-facing developer block for factual_data, or None if unattributed."""
    a = get_attribution(project_name)
    if not a:
        return None
    tr = track_record if track_record is not None else build_track_record(ura_data)
    g = tr.get(_group_key(a), {})
    others = [p for p in g.get("projects", [])
              if _norm_project(p["name"]) != _norm_project(project_name)]
    ref: dict[str, Any] = {
        "developer": a.get("developer"),
        "developer_group": a.get("developer_group"),
        "confidence": a.get("confidence"),
        "source": a.get("source"),
        "reputation_notes": a.get("notes"),
        "also_built_in_our_data": [
            {"project": p["name"], "district": p["district"],
             "ura_appreciation_pct": p["appreciation_pct"]}
            for p in sorted(others, key=lambda p: -(p["txns"] or 0))[:6]
        ],
        "portfolio_median_appreciation_pct": g.get("median_appreciation_pct"),
        "note": ("Developer reputation is a RESEARCH PROMPT, not a score input. Verify "
                 "build quality / defects / delivery via web search; appreciation of "
                 "their other projects is weak evidence (past CAGR ≠ forward return)."),
    }
    return ref
