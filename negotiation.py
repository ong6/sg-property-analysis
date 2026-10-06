#!/usr/bin/env python3
"""How far under its ask a listing is likely to close, measured from our own data.

The problem this exists to fix: every value read in the system compares an
ASKING price against URA prints, and URA prints are TRANSACTION prices, already
negotiated. So an ask sitting exactly on its project's recent prints looks
"at market" when the price we would actually pay is a little under it, and the
agent rated asks, not deals. A flat rule ("you can always shave $50 psf") is the
wrong fix, because the room depends on how padded the ask is:

    ask vs the unit's own same-size prints   typical close under ask   (Oct 2026, n=333)
    at market (-3% to +2%)                   ~1.2%
    +2% to +6% over                          ~2.1%
    +6% to +10% over                         ~4.0%
    more than 10% over                       ~5.5%

The headline average (3.1%, ~$52 psf over 610 sales) holds only because many
asks are padded; the listings the scan picks are usually already at or under
their prints, where the room is ~1%.

Calibration: every URA resale print since the listings DB began (Jun 2026) is
matched to a PropertyGuru listing in the same project, within 1.5% of its size,
that was listed before the sale and vanished around it (last seen from 75 days
before the sale month to 14 days after it, and not seen since). Matches whose
candidate asks disagree by more than 2% are dropped as ambiguous. Each match's
"ask vs own prints" is computed AS OF the sale (the project's resale prints
within ±7% sqft over the prior 24 months, ≥3 of them, the same window the
scorer's tight comps use), so the sale itself never leaks into its benchmark.

Known noise, accepted: `last_seen` only moves when a listing is scraped, so a
listing that drifted past the polled pages can look "gone" and be matched to a
different same-size unit's sale. That widens the distribution; it does not
shift its centre much.

Estimating a live listing: take the K calibration sales whose ask premium sat
closest to this listing's `psf_premium_vs_ura_median_pct`, and read the
discount distribution straight off them: the median is the expected close, the
75th percentile is a strong negotiation, and the share at or beyond a given
cut is how often a cut that deep happens. No premium (no own prints) falls back
to every matched sale, labelled as such.

    python negotiation.py --build        # recalibrate from the DB + URA prints
    python negotiation.py --show         # print the current calibration
    python negotiation.py --ask 1800000 --sqft 1346 --premium -4.2 --max-buy 1750000
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
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
CALIBRATION_FILE = os.path.join(DATA_DIR, "nego_calibration.json")
LISTINGS_FILE = os.path.join(DATA_DIR, "listings_db.json")

# Matching a vanished listing to a URA print (see the module docstring).
SIZE_TOL = 0.015            # listing sqft within 1.5% of the print's area
GONE_BEFORE_DAYS = 75       # last seen no earlier than this before the sale month
GONE_AFTER_DAYS = 14        # ...and no later than this after it
STILL_LIVE_DAYS = 10        # seen this recently by the latest poll = still for sale
AMBIGUOUS_SPREAD = 1.02     # candidate asks further apart than this: drop the print
MAX_ABS_DISCOUNT = 0.15     # beyond ±15% the match is a different unit, not a deal
# The "ask vs own prints" benchmark, mirroring the scorer's tight comps.
COMP_SIZE_TOL = 0.07
COMP_MONTHS = 24
COMP_MIN_PRINTS = 3
# Sales nearest in ask premium used for one estimate.
K_NEIGHBOURS = 80
# Refresh cadence when weekly.py checks the file.
MAX_AGE_DAYS = 30

_MONTHS = {m: i for i, m in
           enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}

_cache: dict | None = None


def _norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def _num(s: str) -> float:
    return float((s or "0").replace(",", ""))


def _load_resale_prints() -> dict[str, list[tuple[int, float, float, float]]]:
    """project -> [(month_index, sqft, psf, price)] for every URA resale print."""
    prints: dict[str, list] = {}
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "ura_district_D*.csv"))):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                if r.get("Type of Sale") != "Resale":
                    continue
                try:
                    mon, yy = r["Sale Date"].split("-")
                    mi = (2000 + int(yy)) * 12 + _MONTHS[mon]
                    sqft = _num(r["Area (SQFT)"])
                    psf = _num(r["Unit Price ($ PSF)"])
                    price = _num(r["Transacted Price ($)"])
                except (KeyError, ValueError):
                    continue
                if sqft > 0 and price > 0:
                    prints.setdefault(_norm(r["Project Name"]), []).append(
                        (mi, sqft, psf, price))
    return prints


def _month_span(mi: int) -> tuple[date, date]:
    y, m = divmod(mi - 1, 12)
    start = date(y, m + 1, 1)
    nxt = date(y + (m + 1) // 12, (m + 1) % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def _premium_as_of(project_prints: list, mi: int, sqft: float, ask: float) -> float | None:
    """Ask psf vs the median of the project's same-size resale prints in the
    24 months BEFORE the sale month, in percent. None without enough prints."""
    comps = [psf for (m, a, psf, _p) in project_prints
             if mi - COMP_MONTHS <= m < mi and abs(a - sqft) / sqft <= COMP_SIZE_TOL]
    if len(comps) < COMP_MIN_PRINTS:
        return None
    return (ask / sqft / statistics.median(comps) - 1) * 100


def match_sales(listings: dict, prints: dict) -> list[dict]:
    """Pair URA resale prints with the listing that most plausibly sold."""
    def d(s: str) -> date:
        return date.fromisoformat(str(s)[:10])

    by_project: dict[str, list] = {}
    for x in listings.values():
        if (x.get("sqft") and x.get("price") and x.get("project_name")
                and x.get("first_seen") and x.get("last_seen")):
            by_project.setdefault(_norm(x["project_name"]), []).append(x)
    if not by_project:
        return []
    last_poll = max(d(x["last_seen"]) for xs in by_project.values() for x in xs)
    first_listing = min(d(x["first_seen"]) for xs in by_project.values() for x in xs)
    first_mi = first_listing.year * 12 + first_listing.month

    rows = []
    for proj, ps in prints.items():
        cands_all = by_project.get(proj)
        if not cands_all:
            continue
        for (mi, sqft, _psf, price) in ps:
            if mi < first_mi:
                continue
            ms, me = _month_span(mi)
            cands = []
            for x in cands_all:
                if abs(x["sqft"] - sqft) / sqft > SIZE_TOL:
                    continue
                fs, ls = d(x["first_seen"]), d(x["last_seen"])
                if fs > me or ls < ms - timedelta(days=GONE_BEFORE_DAYS) \
                        or ls > me + timedelta(days=GONE_AFTER_DAYS):
                    continue
                if (last_poll - ls).days < STILL_LIVE_DAYS:
                    continue
                cands.append(x)
            if not cands:
                continue
            asks = [float(x["price"]) for x in cands]
            if max(asks) / min(asks) > AMBIGUOUS_SPREAD:
                continue
            ask = statistics.median(asks)
            disc = 1 - price / ask
            if abs(disc) > MAX_ABS_DISCOUNT:
                continue
            prem = _premium_as_of(ps, mi, sqft, ask)
            y, m = divmod(mi - 1, 12)
            rows.append({
                "project": proj, "sale_month": f"{y}-{m + 1:02d}", "sqft": sqft,
                "ask": ask, "price": price,
                "discount_pct": round(disc * 100, 2),
                "discount_psf": round((ask - price) / sqft, 1),
                "premium_pct": None if prem is None else round(prem, 2),
            })
    return rows


def _q(values: list[float], q: float) -> float:
    v = sorted(values)
    if not v:
        return 0.0
    pos = q * (len(v) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def _bucket_table(pairs: list[list[float]]) -> list[dict]:
    out = []
    for lo, hi, label in ((-100, -3, "below -3%"), (-3, 2, "-3% to +2%"),
                          (2, 6, "+2% to +6%"), (6, 10, "+6% to +10%"),
                          (10, 100, "above +10%")):
        d = [disc for prem, disc in pairs if lo <= prem < hi]
        if d:
            out.append({"ask_vs_prints": label, "n": len(d),
                        "median_pct": round(_q(d, .5), 1), "p75_pct": round(_q(d, .75), 1)})
    return out


def build(listings_path: str = LISTINGS_FILE) -> dict:
    """Recalibrate from the listings DB and the URA print CSVs; writes the JSON."""
    with open(listings_path) as f:
        listings = json.load(f)["listings"]
    rows = match_sales(listings, _load_resale_prints())
    if not rows:
        raise RuntimeError("no listing could be matched to a URA resale print")
    pairs = [[r["premium_pct"], r["discount_pct"]] for r in rows if r["premium_pct"] is not None]
    discs = [r["discount_pct"] for r in rows]
    psfs = [r["discount_psf"] for r in rows]
    months = sorted(r["sale_month"] for r in rows)
    cal = {
        "built_at": datetime.now().strftime("%Y-%m-%d"),
        "n_matched": len(rows),
        "n_with_premium": len(pairs),
        "sales_from": months[0], "sales_to": months[-1],
        "overall": {"median_pct": round(_q(discs, .5), 1), "p25_pct": round(_q(discs, .25), 1),
                    "p75_pct": round(_q(discs, .75), 1),
                    "median_psf": round(_q(psfs, .5)),
                    "share_above_ask_pct": round(100 * sum(x < 0 for x in discs) / len(discs))},
        "by_ask_premium": _bucket_table(pairs),
        "pairs": sorted(pairs),
        "discounts": sorted(discs),
    }
    tmp = CALIBRATION_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cal, f, indent=1)
    os.replace(tmp, CALIBRATION_FILE)
    global _cache
    _cache = cal
    return cal


def load() -> dict | None:
    """The current calibration, or None when it has never been built."""
    global _cache
    if _cache is None and os.path.exists(CALIBRATION_FILE):
        try:
            with open(CALIBRATION_FILE) as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = None
    return _cache


def age_days() -> float | None:
    cal = load()
    if not cal or not cal.get("built_at"):
        return None
    return (datetime.now() - datetime.strptime(cal["built_at"], "%Y-%m-%d")).days


def _neighbour_discounts(cal: dict, premium_pct: float | None) -> tuple[list[float], str]:
    pairs = cal.get("pairs") or []
    if premium_pct is None or len(pairs) < 20:
        return list(cal.get("discounts") or []), "all matched sales (no own-print benchmark)"
    k = min(K_NEIGHBOURS, len(pairs))
    near = sorted(pairs, key=lambda p: abs(p[0] - premium_pct))[:k]
    lo, hi = min(p[0] for p in near), max(p[0] for p in near)
    return [p[1] for p in near], f"{k} sales whose ask sat {lo:+.0f}% to {hi:+.0f}% vs own prints"


def estimate(ask: float | None, sqft: float | None, premium_pct: float | None,
             cal: dict | None = None) -> dict | None:
    """Expected and strong-negotiation close for one ask. None without a calibration."""
    cal = cal or load()
    if not cal or not ask:
        return None
    discs, basis = _neighbour_discounts(cal, premium_pct)
    if not discs:
        return None
    d50 = max(0.0, _q(discs, .5))
    d75 = max(d50, _q(discs, .75))

    def close(d):
        return int(round(ask * (1 - d / 100), -3))

    out = {
        "ask_vs_own_prints_pct": None if premium_pct is None else round(premium_pct, 1),
        "expected_discount_pct": round(d50, 1),
        "expected_close_price": close(d50),
        "strong_discount_pct": round(d75, 1),
        "strong_close_price": close(d75),
        "basis": basis,
        "calibration": f"{cal.get('n_matched')} matched sales, "
                       f"{cal.get('sales_from')} to {cal.get('sales_to')}, built {cal.get('built_at')}",
    }
    if sqft:
        out["expected_discount_psf"] = round(ask * d50 / 100 / sqft)
        out["strong_discount_psf"] = round(ask * d75 / 100 / sqft)
    return out


# A walk-away price is "typical" when at least half of comparable sales closed
# that far under ask, and "hard" when at least a quarter did.
TYPICAL_SHARE = 0.5
HARD_SHARE = 0.25
TIER_LABELS = {
    "at_ask": "Buy at the ask",
    "typical": "Buy at a typical negotiation",
    "hard": "Buy only with a hard negotiation",
    "out_of_reach": "Buy price out of negotiating reach",
}


def assess(ask: float | None, sqft: float | None, premium_pct: float | None,
           max_buy_price: float | None, cal: dict | None = None) -> dict | None:
    """Translate an agent's walk-away price into how reachable it is."""
    est = estimate(ask, sqft, premium_pct, cal)
    if est is None or not max_buy_price:
        return est
    cal = cal or load()
    discs, _ = _neighbour_discounts(cal, premium_pct)
    cut = (1 - float(max_buy_price) / ask) * 100
    share = sum(d >= cut - 1e-9 for d in discs) / len(discs)
    if cut <= 0:
        tier = "at_ask"
    elif share >= TYPICAL_SHARE:
        tier = "typical"
    elif share >= HARD_SHARE:
        tier = "hard"
    else:
        tier = "out_of_reach"
    est.update({
        "max_buy_price": int(max_buy_price),
        "cut_needed_pct": round(cut, 1),
        "cut_needed_psf": round(ask * cut / 100 / sqft) if sqft else None,
        "share_of_sales_this_deep_pct": round(share * 100),
        "tier": tier,
        "tier_label": TIER_LABELS[tier],
    })
    return est


def _print(cal: dict) -> None:
    o = cal["overall"]
    print(f"Calibration built {cal['built_at']}: {cal['n_matched']} matched sales "
          f"({cal['sales_from']} to {cal['sales_to']}), {cal['n_with_premium']} with an "
          f"own-print benchmark")
    print(f"  overall: median {o['median_pct']}% (~${o['median_psf']} psf) under ask, "
          f"p25 {o['p25_pct']}%, p75 {o['p75_pct']}%; {o['share_above_ask_pct']}% sold at or above ask")
    for b in cal["by_ask_premium"]:
        print(f"  ask {b['ask_vs_prints']:>12}: n={b['n']:3d}  median {b['median_pct']:4.1f}%  "
              f"p75 {b['p75_pct']:4.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build", action="store_true", help="recalibrate from the DB and URA prints")
    ap.add_argument("--show", action="store_true", help="print the current calibration")
    ap.add_argument("--ask", type=float)
    ap.add_argument("--sqft", type=float)
    ap.add_argument("--premium", type=float, help="ask vs own prints, percent")
    ap.add_argument("--max-buy", type=float, help="walk-away price to assess")
    args = ap.parse_args()
    if args.build:
        _print(build())
    elif args.show or not args.ask:
        cal = load()
        if not cal:
            print("no calibration yet: python negotiation.py --build", file=sys.stderr)
            return 1
        _print(cal)
    if args.ask:
        print(json.dumps(assess(args.ask, args.sqft, args.premium, args.max_buy), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
