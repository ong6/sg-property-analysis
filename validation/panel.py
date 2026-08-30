#!/usr/bin/env python3
"""The frozen evaluation panel: as-of-T features joined to a forward outcome.

TARGET — `excess_fwd`. A project's forward PSF CAGR minus its DISTRICT's over
the same window. Two reasons it is differenced rather than raw:

  * The whole usable panel (2021-2026) sits in one bull market. A raw forward
    return mostly measures "was it 2022", which every project shares, and any
    algorithm that merely correlates with the calendar scores well on it.
  * It matches the actual decision. The owner is buying SOMEWHERE; the question
    is never "will Singapore go up" but "which project, given that I'm buying".

Size-banding is not a refinement, it is load-bearing. PSF falls with unit size
(hedonic elasticity around -0.19), so an unbanded comparison rewards big units
for being big — exactly the size-mix artifact that explained away all twelve
top-scoring candidates in the 2026-07-29 sweep.

WHAT THIS PANEL CANNOT DO. URA records transactions, not asking prices. The
"this ask is 15% below comps" channel — the one MMR leans on hardest — has no
historical ground truth here and cannot be backtested at any n. It is testable
only prospectively. A harness that claimed otherwise would be manufacturing
comfort, so this one does not offer the option.

Usage:
    python -m validation.panel --split 2024.46 --window 2.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter, defaultdict

import backtest as bt
import backtest_ext as ext

# The two splits the harness is allowed to use, and why the window is 1.0.
#
# MEASURED, not chosen. A split needs a 3-year lookback (build_panel's `old`
# window is (T-3, T-2], which feeds trailing_cagr and momentum) plus `window`
# years of outcome after it. The URA data runs 2021.38-2026.46 — 5.08 years —
# so the legal splits are [2021.38+3, 2026.46-window]:
#
#     window=2.0  ->  [2024.38, 2024.46]   span 0.08y   ONE split, no VAL
#     window=1.5  ->  [2024.38, 2024.96]   span 0.58y   ONE split, no VAL
#     window=1.0  ->  [2024.38, 2025.46]   span 1.08y   two, disjoint outcomes
#
# So a 2-year window admits no train/validation split at all: the panel-level
# overfit check simply does not exist at that horizon, whatever one would
# prefer. A first attempt used 2022.46/2024.46 at window=2.0 and looked fine —
# build_panel tolerates a missing lookback by setting trailing_cagr=None, so the
# early split silently produced 376 rows with no trailing features, and every
# algorithm needing them abstained on all of them. It failed loudly only because
# the runner insists all algorithms score identical rows.
#
# 1.0 is therefore the longest horizon that supports a DEV/VAL split at all, and
# it is a compromise worth stating: a 1-year forward return is noisier than a
# 2-year one and much shorter than the 5-7yr hold actually being underwritten.
# The fix is time, not cleverness — each further year of URA prints buys back
# horizon. Until then, read these as "does the ranking hold up out-of-split",
# never as "this is what a 5-year buyer earns".
DEV_SPLIT = 2024.40      # outcome (2024.40, 2025.40] — tune here freely
VAL_SPLIT = 2025.45      # outcome (2025.45, 2026.45] — disjoint; look rarely
DEFAULT_WINDOW = 1.0
DEFAULT_MIN_TXN = 5

# Below this many prints in a window a median is noise, not a price.
MIN_DISTRICT_TXN = 20

# ---- tenure -----------------------------------------------------------------
#
# URA's Tenure column is close to clean: over the 106,749 loaded txns, 27,707
# say "Freehold", 78,809 match "N yrs lease commencing from YYYY" (N from 60 to
# 999999), and only 233 are the commencement-less "99 years leasehold" form —
# a 99.78% parse rate at the transaction level, and 100% of panel rows in both
# splits carry a parseable MODAL tenure.
#
# Two traps this parser exists to avoid, both live in build_panel's freehold
# flag ('"999" in tenure' as the 999-year test):
#   * "99 yrs lease commencing from 1999" contains "999" — Caribbean at Keppel
#     Bay and Gardenvista, both ~74 years remaining at the DEV split, read as
#     freehold there. Real decay assets, misfiled.
#   * 9xx-year colonial leases (956 from 1928, 929 from 1953) contain no "999"
#     and read as leasehold — but with ~850 years left they trade as freehold.
# 6 of 393 DEV rows (4 of 386 VAL) disagree between that flag and this parser;
# the panel keeps build_panel's `freehold` field untouched and puts the parsed
# truth in `tenure_kind`, which is what the tenure algorithms read.

_TENURE_RE = re.compile(
    r"^(\d+)\s*(?:yrs?|years?)\s+lease\s+commencing\s+from\s+(\d{4})$")
_TERM_ONLY_RE = re.compile(r"^(\d+)\s*(?:yrs?|years?)\s+leasehold$")

# At or above this term a lease is economically freehold: no decay a buyer
# will live to see, no expiry pressure driving en-bloc dynamics.
FH_LEASE_YEARS = 800


def parse_tenure(tenure: str, at: float):
    """(kind, remaining_years, age_years) as of `at`; kind in
    {'freehold', 'leasehold', None}.

    `age` is years since lease COMMENCEMENT — the land grant, which precedes
    completion by the ~3-4 year construction lag. That overstates building age
    roughly uniformly, so it is harmless for ranking, and it is the only age
    URA carries: there is no completion-date column, which is also why
    freehold rows get age None rather than a number.

    The commencement-less "99 years leasehold" form keeps kind='leasehold' but
    remaining=None — the term is known, the clock's start is not, and
    inventing one would put a made-up number in the panel's strongest-evidenced
    feature.
    """
    s = (tenure or "").strip().lower()
    if not s:
        return None, None, None
    if "freehold" in s:
        return "freehold", None, None
    m = _TENURE_RE.match(s)
    if m:
        term, start = int(m.group(1)), int(m.group(2))
        if term >= FH_LEASE_YEARS:
            return "freehold", None, None
        age = at - start
        return "leasehold", term - age, age
    m = _TERM_ONLY_RE.match(s)
    if m:
        if int(m.group(1)) >= FH_LEASE_YEARS:
            return "freehold", None, None
        return "leasehold", None, None
    return None, None, None


def _attach_tenure(rows: list[dict], txns, split: float) -> None:
    """As-of-T tenure features from each project's MODAL pre-T tenure string.

    Modal for the same reason build_panel's flag is: a first-row read keys the
    feature to load order and mislabels mixed/dirty-tenure projects. Strictly
    pre-T txns only — tenure is a fixed attribute so this costs nothing, and
    it keeps the no-look-ahead rule uniform rather than argued per-feature.
    """
    by_proj = defaultdict(Counter)
    for x in txns:
        if x["t"] <= split and x["tenure"]:
            by_proj[(x["project"], x["district"])][x["tenure"]] += 1
    for r in rows:
        c = by_proj.get((r["project"], r["district"]))
        modal = c.most_common(1)[0][0] if c else ""
        kind, remaining, age = parse_tenure(modal, split)
        r["tenure_kind"] = kind
        r["remaining_lease"] = remaining
        r["lease_age"] = age


# ---- exit demand ------------------------------------------------------------
#
# As-of-T features for the Eric Chiew "exit first" criteria (who buys from you,
# why, at what price). URA caveats carry no unit identifier, so true repeat
# sales cannot be reconstructed — instead PSEUDO repeat-sales pairs: two resales
# of the same (project, district, exact sqft, floor tier) at least a year apart,
# paired consecutively in time. Exact sqft + floor tier is close to "same stack,
# same unit type", so the pair's PSF change is what a buyer-then-seller of that
# unit type realized. Consecutive pairing (not all-pairs) keeps one long-held
# group from minting quadratically many pairs and drowning the rest of the
# panel. Measured on the full URA load: 10,555 pairs, 84.0% profitable — the
# base rate every project record must be read against, since 2021-2026 is one
# bull market and an unsmoothed share saturates at 1.0 for half the panel.
PAIR_MIN_GAP = 1.0     # years between the two legs; same-year flips are churn,
                       # not a hold-and-exit record
FAMILY_SQFT = 900      # >=3BR proxy — Chiew's "family stock" line
BOUTIQUE_UNITS = 250   # his worst-combination flag: small AND freehold AND
BOUTIQUE_SQFT = 750    # shoebox-sized median unit

_UNITS_JSON = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data", "project_units.json")


def _pu_norm(name: str) -> str:
    """Mirrors build_project_units._norm_name (kept local, same reason it is
    kept local there: the join must not drag scoring's import graph into the
    harness)."""
    s = (name or "").lower().strip()
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"\s*@\s*", " at ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _load_units() -> dict:
    """norm name -> URA total_units. Static physical attributes, so reading a
    present-day snapshot at a 2024 split is not look-ahead: a project's unit
    count does not change after completion, and a project only appears in the
    panel if it already has pre-T resales."""
    try:
        with open(_UNITS_JSON) as f:
            projects = json.load(f).get("projects", {})
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v.get("total_units") for k, v in projects.items()
            if v.get("total_units")}


def build_pairs(txns) -> list[dict]:
    """Pseudo repeat-sales pairs over the whole transaction load.

    Built once, unfiltered by split — each pair carries both legs' dates, and
    _attach_exit_demand keeps only pairs whose LATER leg is <= T, so the same
    pair list serves every split with no look-ahead. Rows without a parseable
    floor band (older caveats, txns loaded via bt.load_txns which drops the
    column) simply produce no pairs: a pair whose legs might sit 20 floors
    apart is a floor-premium measurement, not an exit record.
    """
    groups = defaultdict(list)
    for x in txns:
        if x["sale_type"] not in ("Resale", "Sub Sale") or not x["sqft"]:
            continue
        tier = ext._floor_tier(x.get("floor"))
        if tier is None:
            continue
        groups[(x["project"], x["district"], x["sqft"], tier)].append(
            (x["t"], x["psf"]))
    pairs = []
    for (proj, dist, _sqft, _tier), legs in groups.items():
        legs.sort()
        for a, b in zip(legs, legs[1:]):
            if b[0] - a[0] >= PAIR_MIN_GAP:
                pairs.append({"project": proj, "district": dist,
                              "t0": a[0], "t1": b[0],
                              "psf0": a[1], "psf1": b[1]})
    return pairs


def _attach_exit_demand(rows: list[dict], txns, split: float,
                        pairs=None, units=None) -> None:
    """As-of-T exit-demand features. Everything here is strictly pre-T.

    pair_profit / pair_loss — matched-pair exit outcomes (later-leg <= T).
    resale_vol_6m           — resales in (T-0.5, T], Chiew's "1-2 transactions
                              means no demand and no exit" liquidity read.
    family_share            — share of pre-T resales at >= FAMILY_SQFT sqft;
                              all pre-T rather than a trailing window because
                              unit mix is a build attribute, not a market state.
    median_sqft, total_units— the boutique-flag inputs; total_units is None
                              for the ~7% of panel projects the URA GIS join
                              misses, and consumers must treat None as "no
                              evidence", never as a number.
    """
    pairs = build_pairs(txns) if pairs is None else pairs
    units = _load_units() if units is None else units

    pair_pl = defaultdict(lambda: [0, 0])
    for p in pairs:
        if p["t1"] <= split:
            pair_pl[(p["project"], p["district"])][
                0 if p["psf1"] > p["psf0"] else 1] += 1

    vol6 = defaultdict(int)
    fam = defaultdict(lambda: [0, 0])
    sqfts = defaultdict(list)
    for x in txns:
        if x["sale_type"] not in ("Resale", "Sub Sale") or x["t"] > split:
            continue
        k = (x["project"], x["district"])
        if x["t"] > split - 0.5:
            vol6[k] += 1
        if x["sqft"]:
            fam[k][0] += 1
            if x["sqft"] >= FAMILY_SQFT:
                fam[k][1] += 1
            sqfts[k].append(x["sqft"])

    for r in rows:
        k = (r["project"], r["district"])
        r["pair_profit"], r["pair_loss"] = pair_pl.get(k, (0, 0))
        r["resale_vol_6m"] = vol6.get(k, 0)
        n, f = fam.get(k, (0, 0))
        r["family_share"] = f / n if n else None
        r["median_sqft"] = bt._median(sqfts.get(k) or [])
        r["total_units"] = units.get(_pu_norm(r["project"]))


def _district_forward(txns, split: float, window: float) -> dict:
    """Each district's realized forward CAGR — the benchmark to beat.

    Deliberately computed over ALL of a district's resale prints rather than
    over the panel's projects: the benchmark is "the district", not "the
    projects that happened to survive the min_txn filter", and conditioning it
    on survivors would let attrition leak into the outcome.
    """
    base, fwd = defaultdict(list), defaultdict(list)
    for x in txns:
        if x["sale_type"] not in ("Resale", "Sub Sale"):
            continue
        d = x["district"]
        if split - 1 < x["t"] <= split:
            base[d].append(x["psf"])
        elif split + window - 1 < x["t"] <= split + window:
            fwd[d].append(x["psf"])

    out = {}
    for d, b in base.items():
        f = fwd.get(d) or []
        if len(b) < MIN_DISTRICT_TXN or len(f) < MIN_DISTRICT_TXN:
            continue
        bm, fm = bt._median(b), bt._median(f)
        if bm and fm:
            out[d] = (fm / bm) ** (1.0 / window) - 1.0
    return out


def build(split: float = VAL_SPLIT, window: float = DEFAULT_WINDOW,
          min_txn: int = DEFAULT_MIN_TXN, txns=None) -> list[dict]:
    """The panel for one split. Every row carries `excess_fwd` or is dropped.

    size_control and split_sample are ON and not optional. Without size_control
    the outcome is contaminated by unit-mix drift; without split_sample the
    features and the outcome share a price base, and that shared estimation
    noise pushes every value-type feature spuriously negative — the artifact
    backtest.py's --split-sample flag exists for.
    """
    # load_with_floor, not load_txns: identical rows (verified — same panel_id
    # either way) but the Floor Level column survives, which build_pairs needs.
    txns = ext.load_with_floor() if txns is None else txns
    rows = bt.build_panel(txns, split, window, min_txn=min_txn,
                          size_control=True, split_sample=True)
    dfwd = _district_forward(txns, split, window)

    out = []
    for r in rows:
        d = dfwd.get(r["district"])
        if d is None or r.get("forward_cagr") is None:
            continue
        out.append({**r, "window": window,
                    "district_forward_cagr": d,
                    "excess_fwd": r["forward_cagr"] - d})
    _attach_tenure(out, txns, split)
    _attach_exit_demand(out, txns, split)
    out.sort(key=lambda r: (r["district"], r["project"]))
    return out


def panel_id(rows: list[dict], split: float, window: float, min_txn: int) -> str:
    """A name that changes whenever the panel's CONTENT changes.

    Two algorithms are only comparable on the identical panel, and "identical"
    has to mean the data, not just the parameters — a re-pulled URA file with
    three more prints is a different panel wearing the same split. The ledger
    stores this so a later reader can tell whether two rows were ever
    comparable.
    """
    payload = json.dumps(
        [[r["project"], r["district"], round(r["excess_fwd"], 10)] for r in rows],
        sort_keys=True).encode()
    return (f"s{split:g}-w{window:g}-m{min_txn}-"
            f"{hashlib.sha1(payload).hexdigest()[:8]}")


def summary(rows: list[dict]) -> dict:
    ex = [r["excess_fwd"] for r in rows]
    return {
        "projects": len(rows),
        "districts": len({r["district"] for r in rows}),
        "excess_fwd_mean": sum(ex) / len(ex) if ex else None,
        "excess_fwd_median": bt._median(ex),
        "split_sample_fallbacks": sum(r.get("ss_fallback") or 0 for r in rows),
        "tenure_freehold": sum(1 for r in rows if r["tenure_kind"] == "freehold"),
        "tenure_leasehold": sum(1 for r in rows if r["tenure_kind"] == "leasehold"),
        "tenure_unparsed": sum(1 for r in rows if r["tenure_kind"] is None),
        "remaining_lease_known": sum(1 for r in rows
                                     if r["remaining_lease"] is not None),
        "pairs_3plus": sum(1 for r in rows
                           if r["pair_profit"] + r["pair_loss"] >= 3),
        "total_units_known": sum(1 for r in rows
                                 if r["total_units"] is not None),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--split", type=float, default=VAL_SPLIT)
    ap.add_argument("--window", type=float, default=DEFAULT_WINDOW)
    ap.add_argument("--min-txn", type=int, default=DEFAULT_MIN_TXN)
    args = ap.parse_args()

    rows = build(args.split, args.window, args.min_txn)
    s = summary(rows)
    print(f"panel {panel_id(rows, args.split, args.window, args.min_txn)}")
    for k, v in s.items():
        print(f"  {k:26} {v if not isinstance(v, float) else f'{v:+.4f}'}")
    if s["split_sample_fallbacks"]:
        print(f"  NOTE: {s['split_sample_fallbacks']} rows fell back to a shared "
              f"price base (too few recent prints to halve)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
