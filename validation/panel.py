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
import re
from collections import Counter, defaultdict

import backtest as bt

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
    txns = bt.load_txns() if txns is None else txns
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
