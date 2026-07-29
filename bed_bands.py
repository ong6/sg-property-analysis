#!/usr/bin/env python3
"""Per-project bedroom→size bands, built from URA rental contracts.

The problem this exists to catch: a listing's bedroom count comes from the
marketing agent, not from any registry, and agents relabel. A Waterview 926 sqft
unit was listed as "3BR" when Sim Lian's own mix, URA transaction buckets and
URA rental filings all call that size a 2-bedroom — genuine Waterview 3BRs start
at 1,109 sqft and cost $1.78M+. The global sanity table in listings_db allows
850-1800 sqft for a 3BR, so 926 passes it easily. The check has to be
PER PROJECT to have any teeth.

URA rental contracts are the authority available to us: unlike the sale prints,
they carry "No of Bedroom" alongside a floor-area band, filed by the landlord
against the actual unit. So for each project we can learn what a 2/3/4-bedder
there really measures.

    python bed_bands.py --build                 # rebuild from data/ura_rental_D*.csv
    python bed_bands.py --check "Waterview" 3 926
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import statistics
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
BANDS_FILE = os.path.join(DATA_DIR, "bed_bands.json")

# A band needs this many contracts before it can contradict a listing. Below it
# we say "unknown" rather than risk calling a real 3BR a mislabel on 2 filings.
MIN_CONTRACTS = 8
# How far outside the observed band counts as a mismatch. Rental filings bucket
# area into 100 sqft ranges and unit mixes have genuine outliers, so a listing
# has to miss by a clear margin, not by rounding.
TOLERANCE = 0.12

# `check` above is a CONTAINMENT test: does this size fit the band it claims?
# That is necessary but not sufficient, because once TOLERANCE is applied 74% of
# adjacent bed-count bands in this file overlap — so almost any size "fits"
# something and `ok` stops meaning much. Two real listings passed it cleanly:
#
#   Palm Gardens 1,216 sqft "4BR" — the project's 4BRs are 1,350-2,350 sqft on 28
#     contracts; 1,216 sqft is its 3BR product on 160. Advertised bed count wrong.
#   D'Nest 1,259 sqft "3BR" — sits exactly on the 4BR median; its psf looks cheap
#     because the size belongs to a bigger format, not because the unit is cheap.
#
# So `_contest` asks the COMPARATIVE question the containment test cannot: does a
# different bed count explain this size better? It is advisory only — it annotates
# an `ok` verdict and never blocks, because "the size band belongs to a different
# format" is a reason for the analyst to look harder, not grounds to drop a
# listing unseen.
MIN_FIT_RATIO = 2.0


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _sqft_mid(text: str):
    """'1,000 to 1,100' -> 1050.0 ; '990' -> 990.0"""
    nums = [float(n.replace(",", "")) for n in re.findall(r"[\d,]+", text or "")]
    if not nums:
        return None
    return sum(nums[:2]) / len(nums[:2])


def build() -> dict:
    """Learn each project's bedroom→size bands from every rental CSV."""
    samples: dict[tuple[str, int], list[float]] = {}
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "ura_rental_D*.csv"))):
        try:
            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    proj = normalize(row.get("Project Name"))
                    beds = row.get("No of Bedroom")
                    sqft = _sqft_mid(row.get("Floor Area (SQFT)"))
                    if not proj or not sqft or not str(beds).strip().isdigit():
                        continue
                    samples.setdefault((proj, int(beds)), []).append(sqft)
        except OSError:
            continue

    bands: dict[str, dict] = {}
    for (proj, beds), vals in samples.items():
        if len(vals) < 3:
            continue
        vals.sort()
        lo = vals[max(0, int(len(vals) * 0.05))]
        hi = vals[min(len(vals) - 1, int(len(vals) * 0.95))]
        bands.setdefault(proj, {})[str(beds)] = {
            "lo": round(lo), "hi": round(hi),
            "median": round(statistics.median(vals)),
            "contracts": len(vals),
        }

    out = {"source": "URA rental contracts (data/ura_rental_D*.csv)",
           "min_contracts": MIN_CONTRACTS, "projects": bands}
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = BANDS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=0)
    os.replace(tmp, BANDS_FILE)
    return out


_CACHE: dict | None = None


def load() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            with open(BANDS_FILE) as f:
                _CACHE = json.load(f)
        except (OSError, json.JSONDecodeError):
            _CACHE = {"projects": {}}
    return _CACHE


def _contest(proj: dict, claimed: dict, beds, sqft) -> dict | None:
    """A rival bed count that explains `sqft` better than the claimed one, or None.

    Fires on either of two readings, both requiring the rival to clear
    MIN_CONTRACTS:

      CONTAINMENT FLIP — the rival band contains this size outright and the
        claimed band does not. Strongest signal; it is what catches D'Nest.
      CLOSER FIT — the rival sits at least MIN_FIT_RATIO times nearer AND rests
        on more contracts than the claimed band. Catches Palm Gardens, where
        1,216 sqft misses the 4BR floor by 11% but the 3BR median by 2.8%.

    The width guard is what makes this usable. A band wider than the one it is
    contradicting is not evidence: Regentville's 2BR band spans 950-1,750 sqft
    (contaminated by mixed stacks), so it "contains" nearly anything, and without
    the guard it flips every genuine 1,152 sqft 3BR there — a unit that misses
    its own 3BR ceiling by 2 sqft — into a fake 2BR. Requiring the rival to be
    the TIGHTER band drops that whole class of false positive.
    """
    def width(bd):
        return bd["hi"] - bd["lo"]

    def inside(bd):
        return bd["lo"] <= sqft <= bd["hi"]

    def gap(bd):
        """Fractional distance from the band, 0 when inside it."""
        if inside(bd):
            return 0.0
        return (bd["lo"] - sqft) / sqft if sqft < bd["lo"] else (sqft - bd["hi"]) / sqft

    claimed_gap, best = gap(claimed), None
    for b, bd in proj.items():
        if int(b) == int(beds) or bd["contracts"] < MIN_CONTRACTS:
            continue
        if width(bd) > width(claimed):
            continue                       # a looser band cannot contradict a tighter one
        if inside(bd) and not inside(claimed):
            rule = "containment flip"
        elif gap(bd) * MIN_FIT_RATIO < claimed_gap and bd["contracts"] > claimed["contracts"]:
            rule = "closer fit + deeper evidence"
        else:
            continue
        if best is None or gap(bd) < best["gap"]:
            best = {"looks_like": int(b), "gap": gap(bd), "rule": rule, "band": bd}
    if best is None:
        return None
    return {
        "looks_like": best["looks_like"],
        "rule": best["rule"],
        "band": best["band"],
        "reason": (
            f"{sqft:.0f} sqft fits this project's {best['looks_like']}BR band "
            f"({best['band']['lo']}-{best['band']['hi']} sqft, "
            f"{best['band']['contracts']} contracts) better than the {beds}BR band "
            f"it is listed under ({claimed['lo']}-{claimed['hi']} sqft, "
            f"{claimed['contracts']} contracts) — verify the format before "
            f"trusting any psf discount, which may be size-mix, not value"),
    }


def check(project: str, beds, sqft) -> dict:
    """Does `sqft` look like a `beds`-bedroom unit in THIS project?

    Returns {"verdict": ok|mismatch|unknown, ...}. `unknown` is the honest
    answer for a thin or absent band and must never be treated as a failure —
    most projects have no rental history worth trusting.

    When it says mismatch it also reports `looks_like`: the bedroom count whose
    band this size actually falls in, which is the useful part (a "3BR" that is
    really a 2BR+study fails the buyer's screen outright).
    """
    res = {"verdict": "unknown", "project": project, "beds": beds, "sqft": sqft}
    if not project or not beds or not sqft:
        return res
    proj = load().get("projects", {}).get(normalize(project))
    if not proj:
        return res

    band = proj.get(str(int(beds)))
    if band and band["contracts"] >= MIN_CONTRACTS:
        lo, hi = band["lo"] * (1 - TOLERANCE), band["hi"] * (1 + TOLERANCE)
        if lo <= sqft <= hi:
            ok = {**res, "verdict": "ok", "band": band}
            # `ok` only means "not contradicted by its own band". Ask the
            # comparative question too, and attach the answer without changing
            # the verdict — callers gate on `mismatch`, and a contested size is
            # a thing to check, not a thing to reject.
            if (rival := _contest(proj, band, beds, sqft)):
                ok["contested"] = rival
            return ok
    elif band:
        return {**res, "verdict": "unknown", "band": band, "reason": "thin band"}
    else:
        return {**res, "reason": "no band for this bed count"}

    # Outside its own band. Direction matters, and conflating the two would
    # produce confident nonsense:
    #
    #   UNDERSIZE — smaller than the project's real NBR units, and sitting in a
    #     lower bed count's band. That is the mislabel we care about: a 2BR+study
    #     sold as a 3BR fails the buyer's screen outright.
    #   OVERSIZE — larger than typical. Penthouses, corner stacks and
    #     dual-key units are genuinely bigger; being roomy is not a mislabel, so
    #     this is reported as `oversize`, never as a wrong bed count.
    if sqft > band["hi"] * (1 + TOLERANCE):
        return {**res, "verdict": "oversize", "band": band,
                "reason": (f"{sqft:.0f} sqft is above this project's {beds}BR band "
                           f"({band['lo']}-{band['hi']} sqft) — likely a larger "
                           f"stack or penthouse, not a mislabel")}

    looks_like = None
    for b, bd in sorted(proj.items(), key=lambda kv: int(kv[0]), reverse=True):
        if bd["contracts"] < MIN_CONTRACTS or int(b) >= int(beds):
            continue                       # only a LOWER bed count explains it
        if bd["lo"] * (1 - TOLERANCE) <= sqft <= bd["hi"] * (1 + TOLERANCE):
            looks_like = int(b)
            break
    if looks_like is None:
        # Undersized but matching nothing — odd, but not evidence of a relabel.
        return {**res, "verdict": "undersize", "band": band,
                "reason": (f"{sqft:.0f} sqft is below this project's {beds}BR band "
                           f"({band['lo']}-{band['hi']} sqft) but matches no "
                           f"smaller bed count either")}
    return {**res, "verdict": "mismatch", "band": band, "looks_like": looks_like,
            "reason": (f"{sqft:.0f} sqft is below this project's {beds}BR band "
                       f"({band['lo']}-{band['hi']} sqft, {band['contracts']} "
                       f"contracts) and matches its {looks_like}BR band")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--check", nargs=3, metavar=("PROJECT", "BEDS", "SQFT"))
    args = ap.parse_args()

    if args.build:
        out = build()
        n_bands = sum(len(v) for v in out["projects"].values())
        print(f"Built {n_bands} bedroom bands across {len(out['projects'])} projects "
              f"-> {BANDS_FILE}")
        return 0
    if args.check:
        p, b, s = args.check
        print(json.dumps(check(p, int(b), float(s)), indent=2))
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
