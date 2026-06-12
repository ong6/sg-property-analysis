"""Livability score — own-stay desirability heuristic, SEPARATE from MMR.

MMR is the money score: every weight in it is backtest-anchored against URA
forward returns. Livability factors (bath count, floor, facing, space) CANNOT
be backtested — URA transactions carry none of those fields — so they must
never leak into MMR. This module scores them as an explicitly heuristic,
display-only second axis: 0–100, 50 = neutral, missing data is neutral
(same philosophy as MMR — absence of data is not a defect).

Components (absent when the field is missing). The TYPICAL band of every
present field scores 0 — a fully-typical listing scores the same 50 as a
sparse one, so sorting by livability ranks quality, never data completeness
(pre-recenter, present-and-typical space+MRT alone earned +7):
  baths      −8 under-bathed / 0 beds−1 (SG baseline) / +10 baths ≥ beds
  space      −8 (<0.78× expected) / −3 (0.78–0.95×) / 0 (0.95–1.15× typical)
             / +8 (≥1.15× — the own-stay oversized flip)
  mrt        +6 (≤400m, genuine doorstep plus) / 0 (≤1km, the norm)
             / −4 (1–1.5km) / −8 (>1.5km)
  floor      +6 high/penthouse / 0 mid (explicit) / −8 low/ground (enriched only)
  facing     +4 N/S (no direct sun) / 0 other / −4 W afternoon sun (enriched only)
  newness    +4 (≤8yr modern stack) / 0 / −4 (>30yr maintenance drag)

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
    try:
        val = float(m.group(1))
    except ValueError:  # "[\d.]+" also matches dotted garbage like "1.2.3"
        return None
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
            c["space"] = 0           # typical — present-and-typical is not a plus
        elif ratio < 0.78:
            c["space"] = -8          # shoebox layout
        else:
            c["space"] = -3          # snug

    meters = _mrt_meters(rec.get("mrt_info"))
    if meters is not None:
        if meters <= 400:
            c["mrt"] = 6             # genuine doorstep premium
        elif meters <= 1000:
            c["mrt"] = 0             # the SG norm — not a plus, just present
        elif meters <= 1500:
            c["mrt"] = -4
        else:
            c["mrt"] = -8

    floor = (rec.get("floor_level") or "").lower()
    if floor:
        if any(k in floor for k in _FLOOR_POS):
            c["floor"] = 6
        elif any(k in floor for k in _FLOOR_NEG):
            c["floor"] = -8          # harder to live with AND a thinner exit pool
        else:
            c["floor"] = 0           # mid floor — known and neutral, not absent

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
    # All-zero components ≠ no components: post-recentering, a fully-typical
    # listing legitimately scores 50 with every signal present.
    why = (" · ".join(f"{k} {v:+d}" for k, v in c.items() if v)
           or ("all typical" if c else "no signals"))
    return {"score": score, "components": c, "signals": len(c), "why": why}
