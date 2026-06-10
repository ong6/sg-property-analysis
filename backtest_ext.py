"""Extended point-in-time backtest — coverage for EVERY valuation variable.

backtest.py answered "does feature X rank forward winners?" for 6 features in
isolation (univariate Spearman). This extends that in four directions the
improvement review needs:

  PART 1  HEDONIC PRICE MODEL  — what actually drives PSF *level* right now
          (floor, size, age, tenure, region). Validates whether the MMR's
          "value" comparison controls for the right things. If floor moves PSF
          by X% and MMR compares a high-floor listing to a pooled-floor cohort
          median, MMR mis-reads value by ~X%.

  PART 2  FORWARD UNIVARIATE (extended) — adds psf_level (absolute), unit size,
          and region to the original 6 forward-return features.

  PART 3  FORWARD MULTIVARIATE — standardized OLS of forward return on all
          features at once. Univariate Spearman can't tell whether freehold's
          negative sign is real or just "freehold == old == CCR". This strips
          collinearity and reports each feature's MARGINAL contribution.

  PART 4  COMPOSITE-SCORE VALIDATION — backtest.py validated the *parts*; it
          never validated the *assembled score*. We rebuild an MMR-like composite
          from as-of-T features using the CURRENT config weights, measure its
          forward rank-correlation, then fit the in-sample optimal weights and
          report the gap.

Same honesty rails as backtest.py: resale+subsale only, point-in-time (no
look-ahead), min-count filters, split-sample option to kill the shared-
estimation mean-reversion artifact. Single regime (2021-26) — read as
direction + magnitude, not gospel.

Usage:
  python backtest_ext.py                 # all four parts, pooled over default splits
  python backtest_ext.py --split-sample  # mean-reversion-artifact-corrected
"""

import argparse
import csv
import glob
import math
import re
from collections import defaultdict

import numpy as np

import backtest as bt  # reuse load_txns / build_panel / _spearman

_CCR = {1, 2, 6, 7, 9, 10, 11}
_RCR = {3, 4, 5, 8, 12, 13, 14, 15}


def _region_of(district):
    try:
        d = int(str(district).replace("D", "").strip() or 0)
    except ValueError:
        return "OCR"
    if d in _CCR:
        return "CCR"
    if d in _RCR:
        return "RCR"
    return "OCR"


def _floor_tier(s):
    """URA floor band -> ordinal 0 (low) / 1 (mid) / 2 (high). None if absent."""
    s = (s or "").strip()
    if not s or s == "-":
        return None
    if s.lower().startswith("b"):
        return 0
    m = re.search(r"\d+", s)
    if not m:
        return None
    lo = int(m.group())
    if lo <= 5:
        return 0
    if lo <= 12:
        return 1
    return 2


def _commence_year(tenure):
    m = re.search(r"commencing from (\d{4})", tenure or "")
    return int(m.group(1)) if m else None


def load_with_floor():
    """Like bt.load_txns but keeps Floor Level + parsed freehold (for the hedonic)."""
    out = []
    for p in sorted(glob.glob(bt.DATA_GLOB)):
        with open(p) as fh:
            for row in csv.DictReader(fh):
                t = bt._parse_date(row.get("Sale Date", ""))
                psf = bt._num(row.get("Unit Price ($ PSF)"))
                if t is None or not psf:
                    continue
                tenure = (row.get("Tenure", "") or "").strip()
                out.append({
                    "district": row.get("Postal District", "").strip(),
                    "t": t,
                    "psf": psf,
                    "sqft": bt._num(row.get("Area (SQFT)")),
                    "sale_type": row.get("Type of Sale", "").strip(),
                    "tenure": tenure,
                    "freehold": ("freehold" in tenure.lower() or "999" in tenure),
                    "floor": row.get("Floor Level", ""),
                })
    return out


def _ols(X, y, names):
    """Plain OLS with intercept. X already includes any dummies (no intercept col).
    Returns list of (name, coef, std_coef) where std_coef = coef * sd(x)/sd(y)."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, k = X.shape
    Xd = np.column_stack([np.ones(n), X])
    beta, *_ = np.linalg.lstsq(Xd, y, rcond=None)
    yhat = Xd @ beta
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot else float("nan")
    sdy = y.std()
    out = []
    for i, nm in enumerate(names):
        col = X[:, i]
        std = beta[i + 1] * (col.std() / sdy) if sdy else float("nan")
        out.append((nm, beta[i + 1], std))
    return out, r2, n


# ============================================================================
# PART 1 — HEDONIC PRICE MODEL
# ============================================================================
def hedonic(txns, asof, lookback=2.0):
    """log(PSF) ~ floor + log(sqft) + age + freehold + region, recent resale txns.

    Two models: (A) leasehold-only including age, (B) all units with a freehold
    dummy (no age — freehold age isn't derivable from the tenure string).
    """
    print("=" * 78)
    print(f"PART 1 — HEDONIC PRICE MODEL  (resale+subsale, last {lookback:.0f}yr to T={asof})")
    print("  What drives PSF *level*? Coeffs are on log(PSF): a coef of 0.10 on a")
    print("  0/1 dummy ~= +10% PSF. This is what 'value' must control for.")
    print("=" * 78)

    rows = []
    for x in txns:
        if not (asof - lookback < x["t"] <= asof):
            continue
        if x["sale_type"] not in ("Resale", "Sub Sale"):
            continue
        if not x["psf"] or not x["sqft"] or x["sqft"] <= 0:
            continue
        ft = _floor_tier(x.get("floor"))
        if ft is None:
            continue
        rows.append(x | {"ft": ft})
    if len(rows) < 200:
        print(f"  too few rows ({len(rows)})\n")
        return

    def region_dummies(r):
        reg = _region_of(r["district"])
        return [1.0 if reg == "RCR" else 0.0, 1.0 if reg == "CCR" else 0.0]

    # Model B — all units, freehold dummy
    Xb, yb = [], []
    for r in rows:
        rd = region_dummies(r)
        Xb.append([r["ft"], math.log(r["sqft"]), 1.0 if r["freehold"] else 0.0] + rd)
        yb.append(math.log(r["psf"]))
    namesB = ["floor_tier(0-2)", "log_sqft", "freehold", "region_RCR", "region_CCR"]
    coefB, r2B, nB = _ols(Xb, yb, namesB)
    print(f"\n  Model B — all units (n={nB:,}, R2={r2B:.3f}):")
    print(f"    {'term':<18}{'coef(logPSF)':>14}{'~%effect':>12}{'std_beta':>10}")
    for nm, c, sb in coefB:
        pct = (math.exp(c) - 1) * 100 if abs(c) < 1 else c * 100
        unit = "/tier" if "floor" in nm else ("/log-unit" if "log" in nm else "")
        print(f"    {nm:<18}{c:>+14.4f}{pct:>+11.1f}%{sb:>+10.3f}  {unit}")

    # Model A — leasehold only, with age
    Xa, ya = [], []
    for r in rows:
        if r["freehold"]:
            continue
        cy = _commence_year(r["tenure"])
        if not cy:
            continue
        age = max(0, int(r["t"]) - cy)
        if age > 60:
            continue
        rd = region_dummies(r)
        Xa.append([r["ft"], math.log(r["sqft"]), float(age)] + rd)
        ya.append(math.log(r["psf"]))
    if len(Xa) > 200:
        namesA = ["floor_tier(0-2)", "log_sqft", "age_years", "region_RCR", "region_CCR"]
        coefA, r2A, nA = _ols(Xa, ya, namesA)
        print(f"\n  Model A — leasehold only, with age (n={nA:,}, R2={r2A:.3f}):")
        print(f"    {'term':<18}{'coef(logPSF)':>14}{'~%effect':>12}{'std_beta':>10}")
        for nm, c, sb in coefA:
            pct = (math.exp(c) - 1) * 100 if abs(c) < 1 else c * 100
            print(f"    {nm:<18}{c:>+14.4f}{pct:>+11.1f}%{sb:>+10.3f}")
        # headline: per-year age decay in $/psf at the sample mean PSF
        age_coef = dict((n, c) for n, c, _ in coefA)["age_years"]
        mean_psf = float(np.mean([math.exp(v) for v in ya]))
        print(f"    -> age decay ~= {abs(age_coef)*100:.2f}%/yr  "
              f"(~${abs(age_coef)*mean_psf:.0f}/psf/yr at mean PSF ${mean_psf:.0f}); "
              f"config assumes $40-60/psf/yr")
    print()


# ============================================================================
# PARTS 2-4 — FORWARD RETURNS (pooled panel over splits)
# ============================================================================
def pooled_panel(txns, splits, window, min_txn, split_sample):
    rows = []
    for s in splits:
        for r in bt.build_panel(txns, s, window, min_txn, False, split_sample):
            r = dict(r)
            r["region"] = _region_of(r["district"])
            r["log_psf"] = math.log(r["psf_level"]) if r.get("psf_level") else None
            rows.append(r)
    return rows


def part2_univariate(rows):
    print("=" * 78)
    print("PART 2 — FORWARD UNIVARIATE (extended)  | pooled, Spearman rho vs forward 2yr return")
    print("=" * 78)
    fwd = [r["forward_cagr"] for r in rows]
    feats = ["trailing_cagr", "momentum", "psf_vs_dist", "txn_vol",
             "new_sale_share", "freehold", "log_psf"]
    print(f"  {'feature':<16}{'rho':>9}{'n':>7}   interpretation")
    notes = {
        "trailing_cagr": "past appreciation",
        "momentum": "accel vs trend",
        "psf_vs_dist": "cheap-vs-district (NEG = cheap wins)",
        "txn_vol": "liquidity",
        "new_sale_share": "new-launch share (NEG = newer underperforms fwd)",
        "freehold": "tenure (sign?)",
        "log_psf": "absolute price level (NEG = cheap wins)",
    }
    for f in feats:
        rho, n = bt._spearman([r.get(f) for r in rows], fwd)
        print(f"  {f:<16}{(f'{rho:+.3f}' if rho is not None else 'n/a'):>9}{n:>7}   {notes[f]}")
    # region as categorical -> mean forward by region
    print("\n  forward return by region:")
    by = defaultdict(list)
    for r in rows:
        by[r["region"]].append(r["forward_cagr"])
    for reg in ("CCR", "RCR", "OCR"):
        v = by.get(reg, [])
        if v:
            print(f"    {reg}: mean {np.mean(v)*100:+.1f}%  median {np.median(v)*100:+.1f}%  n={len(v)}")
    print()


def part3_multivariate(rows):
    print("=" * 78)
    print("PART 3 — FORWARD MULTIVARIATE (standardized OLS)  | marginal contribution")
    print("  std_beta = SD change in forward return per 1-SD of the feature,")
    print("  holding the others fixed. This is what survives collinearity.")
    print("=" * 78)
    feats = ["trailing_cagr", "momentum", "psf_vs_dist", "txn_vol",
             "new_sale_share", "freehold", "log_psf"]
    good = [r for r in rows if all(r.get(f) is not None for f in feats)
            and r.get("forward_cagr") is not None]
    if len(good) < 50:
        print(f"  too few complete rows ({len(good)})\n")
        return
    X = [[r[f] for f in feats] for r in good]
    y = [r["forward_cagr"] for r in good]
    coef, r2, n = _ols(X, y, feats)
    print(f"\n  n={n:,}  model R2={r2:.3f}")
    print(f"    {'feature':<16}{'std_beta':>10}{'raw_coef':>14}")
    for nm, c, sb in sorted(coef, key=lambda t: -abs(t[2])):
        print(f"    {nm:<16}{sb:>+10.3f}{c:>+14.5f}")
    print("  (rank by |std_beta|: the features that actually move forward returns)\n")


def _zser(rows, f):
    vals = [r.get(f) for r in rows]
    arr = np.array([v if v is not None else np.nan for v in vals], float)
    mu, sd = np.nanmean(arr), np.nanstd(arr)
    return [(0.0 if (v is None or sd == 0) else (v - mu) / sd) for v in vals]


def part4_composite(rows, split_sample):
    print("=" * 78)
    print("PART 4 — COMPOSITE-SCORE VALIDATION  | does the ASSEMBLED score predict?")
    print("=" * 78)
    from config import (MMR_APPRECIATION_SLOPE, MMR_MOMENTUM_WEIGHT,
                        MMR_RELVALUE_SLOPE)
    fwd = [r["forward_cagr"] for r in rows]

    # (a) current-weight MMR-like composite from as-of-T features.
    # Map the config slopes onto the backtest features (same sign conventions
    # MMR uses): appreciation +, momentum +, value (psf_vs_dist) -, liquidity +.
    cur = []
    for r in rows:
        s = 0.0
        if r.get("trailing_cagr") is not None:
            s += MMR_APPRECIATION_SLOPE * (r["trailing_cagr"] * 100 - 4.0)
        if r.get("momentum") is not None:
            s += MMR_MOMENTUM_WEIGHT * (r["momentum"] * 100)
        if r.get("psf_vs_dist") is not None:
            s += -MMR_RELVALUE_SLOPE * (r["psf_vs_dist"] * 100)
        if r.get("txn_vol") is not None:
            s += 10.0 * math.tanh(r["txn_vol"] / 40.0)
        cur.append(s)
    rho_cur, n_cur = bt._spearman(cur, fwd)
    print(f"\n  (a) CURRENT config weights  -> forward rho = "
          f"{rho_cur:+.3f}  (n={n_cur})" if rho_cur is not None else "  n/a")

    # (b) in-sample optimal linear reweighting on standardized features.
    feats = ["trailing_cagr", "momentum", "psf_vs_dist", "txn_vol",
             "new_sale_share", "freehold", "log_psf"]
    good = [r for r in rows if all(r.get(f) is not None for f in feats)
            and r.get("forward_cagr") is not None]
    Z = np.column_stack([_zser(good, f) for f in feats])
    yv = np.array([r["forward_cagr"] for r in good], float)
    beta, *_ = np.linalg.lstsq(np.column_stack([np.ones(len(good)), Z]), yv, rcond=None)
    fit = np.column_stack([np.ones(len(good)), Z]) @ beta
    rho_opt, n_opt = bt._spearman(list(fit), list(yv))
    print(f"  (b) in-sample OPTIMAL reweight -> forward rho = {rho_opt:+.3f}  (n={n_opt})")
    print("      optimal standardized weights:")
    order = sorted(zip(feats, beta[1:]), key=lambda t: -abs(t[1]))
    for nm, b in order:
        print(f"        {nm:<16}{b:>+.5f}")
    print("\n  Read: (a) is what ships today; (b) is the ceiling an in-sample linear")
    print("  model could reach on these features. A small gap = weights are ~right;")
    print("  a big gap = the composite is leaving signal on the table.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split-sample", action="store_true")
    ap.add_argument("--window", type=float, default=2.0)
    ap.add_argument("--min-txn", type=int, default=5)
    args = ap.parse_args()

    txns = bt.load_txns()
    print(f"\nLoaded {len(txns):,} URA txns "
          f"({sum(1 for t in txns if t['sale_type'] in ('Resale','Sub Sale')):,} resale). "
          f"split_sample={args.split_sample}\n")

    # latest date in panel -> hedonic as-of (uses a floor-aware reload)
    asof = max(t["t"] for t in txns)
    hedonic(load_with_floor(), asof)

    splits = [2023.75, 2024.0, 2024.25]
    rows = pooled_panel(txns, splits, args.window, args.min_txn, args.split_sample)
    print(f"Pooled forward panel: {len(rows)} project-split rows "
          f"over splits {splits}, window={args.window}yr\n")
    part2_univariate(rows)
    part3_multivariate(rows)
    part4_composite(rows, args.split_sample)


if __name__ == "__main__":
    main()
