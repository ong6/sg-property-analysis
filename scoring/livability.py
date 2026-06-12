"""Livability score — own-stay desirability heuristic, SEPARATE from MMR.

MMR is the money score: every weight in it is backtest-anchored against URA
forward returns. Livability factors (bath count, floor, facing, space) CANNOT
be backtested — URA transactions carry none of those fields — so they must
never leak into MMR. This module scores them as an explicitly heuristic,
display-only second axis: 0–100, 50 = neutral, missing data is neutral
(same philosophy as MMR — absence of data is not a defect).

Components (each 0 when the field is missing):
  baths      — 2b2ba lives better than 2b1ba; beds-1 baths is the SG baseline
  space      — sqft per bedroom vs typical for the bed count
  mrt        — walk distance parsed from the listing's mrt_info text
  floor      — high floor +, low/ground − (only present on enriched listings)
  facing     — N/S avoids direct sun; W catches afternoon sun (enriched only)
  newness    — modern stack (facilities/layouts) +, >30yr maintenance drag −

The `why` string ships with the score so the UI can show its work — a
heuristic that can't explain itself shouldn't be trusted at all.
"""

from __future__ import annotations

import re

# Typical usable sqft for a comfortable unit at each bed count (SG condo norms)
_EXPECTED_SQFT = {0: 450, 1: 500, 2: 720, 3: 1000, 4: 1300, 5: 1600}

_FLOOR_POS = ("high", "penthouse", "top")
_FLOOR_NEG = ("low", "ground")


def _mrt_meters(mrt_info: str | None) -> int | None:
    """Parse '5 min (410 m) from ...' / '(1.2 km)' into meters."""
    if not mrt_info:
        return None
    m = re.search(r"\(([\d.]+)\s*(m|km)\)", mrt_info)
    if not m:
        return None
    val = float(m.group(1))
    return int(val * 1000) if m.group(2) == "km" else int(val)


def score_livability(rec: dict) -> dict:
    """Score a listing record (listings_db shape). Returns
    {"score": int 0-100, "components": {...}, "signals": int, "why": str}."""
    c: dict[str, int] = {}

    beds = rec.get("beds")
    baths = rec.get("baths")
    if beds and baths and beds >= 2:
        if baths >= beds:
            c["baths"] = 10          # 2b2ba / 3b3ba — no morning queue
        elif baths == beds - 1:
            c["baths"] = 0           # the SG baseline (2b1ba, 3b2ba)
        else:
            c["baths"] = -8

    sqft = rec.get("sqft")
    if beds is not None and sqft:
        ratio = sqft / _EXPECTED_SQFT.get(beds, 1300)
        if ratio >= 1.15:
            c["space"] = 8           # the own-stay flip: oversized is a feature
        elif ratio >= 0.95:
            c["space"] = 3
        elif ratio < 0.78:
            c["space"] = -8          # shoebox layout
        else:
            c["space"] = 0

    meters = _mrt_meters(rec.get("mrt_info"))
    if meters is not None:
        if meters <= 400:
            c["mrt"] = 8
        elif meters <= 800:
            c["mrt"] = 4
        elif meters > 1500:
            c["mrt"] = -4
        else:
            c["mrt"] = 0

    floor = (rec.get("floor_level") or "").lower()
    if floor:
        if any(k in floor for k in _FLOOR_POS):
            c["floor"] = 6
        elif any(k in floor for k in _FLOOR_NEG):
            c["floor"] = -8          # harder to live with AND a thinner exit pool

    facing = (rec.get("facing") or "").lower()
    if facing:
        if "north" in facing or "south" in facing:
            c["facing"] = 4          # N/S: no direct sun into the unit
        elif "west" in facing:
            c["facing"] = -4         # full afternoon sun
        else:
            c["facing"] = 0

    built = rec.get("built_year")
    if built:
        try:
            from datetime import datetime
            age = datetime.now().year - int(built)
            if 0 <= age <= 8:
                c["newness"] = 4
            elif age > 30:
                c["newness"] = -4
            else:
                c["newness"] = 0
        except (TypeError, ValueError):
            pass

    score = max(0, min(100, 50 + sum(c.values())))
    why = " · ".join(f"{k} {v:+d}" for k, v in c.items() if v) or "no signals"
    return {"score": score, "components": c, "signals": len(c), "why": why}
