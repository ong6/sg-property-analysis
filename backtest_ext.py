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

Jun-2026 audit additions (measurement honesty, no weight changes):
  * Every reported rho / std_beta carries a seeded project-cluster bootstrap
    95% CI (~400 reps); a CI crossing 0 prints UNDETERMINED. The cluster is
    the project, so the ~87.5%-overlapping pooled splits don't masquerade as
    independent rows (effective-n diagnostics print with the pooled panel).
  * PART 4 (P0-1): config composite and the in-sample-optimal OLS are scored
    on the IDENTICAL complete-case rows; the all-rows number is labeled
    separately. The old "config +0.288 beats optimal +0.241" compared
    different row sets — same-rows it is config +0.225 < optimal +0.241.
  * PART 5f (P1-6): MRT distances are computed against the station network
    OPEN at each row's split (mrt_stations.csv `opened` column); the legacy
    2026-network std_beta is re-printed alongside for comparison.
  * PART 5g: rental windows end at each row's own split (was a single 2024.0
    hint — a 3-month forward leak for the 2023.75 split).
  * PART 5h (P2-1): non-overlapping era blocks + independent-window counts +
    the newest independent window's winner, alongside the direction buckets.

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
        with bt._open_csv(p) as fh:
            for row in csv.DictReader(fh):
                t = bt._parse_date(row.get("Sale Date", ""))
                psf = bt._num(row.get("Unit Price ($ PSF)"))
                if t is None or not psf:
                    continue
                tenure = (row.get("Tenure", "") or "").strip()
                out.append({
                    "project": (row.get("Project Name") or "").strip().upper(),
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


BOOT_REPS = 400  # cluster-bootstrap replications (seeded; audit used the same)


def _ols_ci(X, y, names, clusters, reps=BOOT_REPS, seed=42):
    """OLS + cluster bootstrap 95% CI on the standardized betas.

    Resamples whole clusters (projects — optionally districts) with
    replacement, a block bootstrap over the cluster dimension: a project's
    rows from the ~87.5%-overlapping pooled splits move together, so the CI
    does not treat pooled rows as independent (Jun-2026 audit: no standard
    errors anywhere; |std_beta| < 0.1 was being narrated as signal).

    Returns (rows, r2, n): rows are dicts
      {name, coef, std, lo, hi, coef_lo, coef_hi, und}
    lo/hi bound std_beta, coef_lo/coef_hi bound the raw coefficient;
    und=True when the std_beta CI crosses 0 (printed as UNDETERMINED) or when
    the bootstrap failed to converge.
    """
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    base, r2, n = _ols(X, y, names)
    # cluster -> row-index arrays
    keys = {}
    rows_of = []
    for i, c in enumerate(clusters):
        j = keys.setdefault(c, len(rows_of))
        if j == len(rows_of):
            rows_of.append([i])
        else:
            rows_of[j].append(i)
    rows_of = [np.asarray(ix) for ix in rows_of]
    k = len(rows_of)
    rng = np.random.default_rng(seed)
    boots = np.full((reps, len(names)), np.nan)      # std_beta draws
    boots_raw = np.full((reps, len(names)), np.nan)  # raw-coef draws
    for rep in range(reps):
        idx = np.concatenate([rows_of[i] for i in rng.integers(0, k, k)])
        Xb, yb = X[idx], y[idx]
        Xd = np.column_stack([np.ones(len(idx)), Xb])
        try:
            beta, *_ = np.linalg.lstsq(Xd, yb, rcond=None)
        except np.linalg.LinAlgError:
            continue
        sdy = yb.std()
        if not sdy:
            continue
        boots_raw[rep] = beta[1:]
        boots[rep] = [beta[j + 1] * (Xb[:, j].std() / sdy)
                      for j in range(len(names))]
    out = []
    for j, (nm, c, sb) in enumerate(base):
        col = boots[:, j]
        col = col[~np.isnan(col)]
        raw = boots_raw[:, j]
        raw = raw[~np.isnan(raw)]
        if len(col) >= reps * 0.5:
            lo, hi = (float(v) for v in np.percentile(col, [2.5, 97.5]))
            c_lo, c_hi = (float(v) for v in np.percentile(raw, [2.5, 97.5]))
            und = lo <= 0.0 <= hi
        else:
            lo = hi = c_lo = c_hi = None
            und = True
        out.append({"name": nm, "coef": c, "std": sb, "lo": lo, "hi": hi,
                    "coef_lo": c_lo, "coef_hi": c_hi, "und": und})
    return out, r2, n


def _print_ols_ci(rows, indent="    ", name_w=18):
    """Standard inline print: std_beta, 95% CI, UNDETERMINED tag."""
    print(f"{indent}{'term':<{name_w}}{'std_beta':>10}  {'95% CI (cluster boot)':<24}")
    for o in sorted(rows, key=lambda t: -abs(t["std"])):
        tag = "  UNDETERMINED (CI crosses 0)" if o["und"] else ""
        print(f"{indent}{o['name']:<{name_w}}{o['std']:>+10.3f}  "
              f"{bt._fmt_ci(o['lo'], o['hi']):<24}{tag}")


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
    Xb, yb, cb = [], [], []
    for r in rows:
        rd = region_dummies(r)
        Xb.append([r["ft"], math.log(r["sqft"]), 1.0 if r["freehold"] else 0.0] + rd)
        yb.append(math.log(r["psf"]))
        cb.append(r.get("project") or r["district"])
    namesB = ["floor_tier(0-2)", "log_sqft", "freehold", "region_RCR", "region_CCR"]
    coefB, r2B, nB = _ols_ci(Xb, yb, namesB, cb)
    print(f"\n  Model B — all units (n={nB:,}, R2={r2B:.3f}; "
          f"95% CI = project-cluster bootstrap):")
    print(f"    {'term':<18}{'coef(logPSF)':>14}{'~%effect':>12}{'std_beta':>10}"
          f"  {'95% CI':<20}")
    for o in coefB:
        nm, c, sb = o["name"], o["coef"], o["std"]
        pct = (math.exp(c) - 1) * 100 if abs(c) < 1 else c * 100
        unit = "/tier" if "floor" in nm else ("/log-unit" if "log" in nm else "")
        tag = " UNDETERMINED" if o["und"] else ""
        print(f"    {nm:<18}{c:>+14.4f}{pct:>+11.1f}%{sb:>+10.3f}  "
              f"{bt._fmt_ci(o['lo'], o['hi']):<20}{unit}{tag}")

    # Model A — leasehold only, with age
    Xa, ya, ca = [], [], []
    for r in rows:
        if r["freehold"]:
            continue
        cy = _commence_year(r["tenure"])
        if not cy:
            continue
        # float year minus commencement year — int(t) truncated up to ~1yr off
        # every transaction, attenuating the age coefficient that calibrates
        # AGE_PSF_SLOPE_BY_REGION.
        age = max(0.0, r["t"] - cy)
        if age > 60:
            continue
        rd = region_dummies(r)
        Xa.append([r["ft"], math.log(r["sqft"]), age] + rd)
        ya.append(math.log(r["psf"]))
        ca.append(r.get("project") or r["district"])
    if len(Xa) > 200:
        namesA = ["floor_tier(0-2)", "log_sqft", "age_years", "region_RCR", "region_CCR"]
        coefA, r2A, nA = _ols_ci(Xa, ya, namesA, ca)
        print(f"\n  Model A — leasehold only, with age (n={nA:,}, R2={r2A:.3f}; "
              f"95% CI = project-cluster bootstrap):")
        print(f"    {'term':<18}{'coef(logPSF)':>14}{'~%effect':>12}{'std_beta':>10}"
              f"  {'95% CI':<20}")
        for o in coefA:
            nm, c, sb = o["name"], o["coef"], o["std"]
            pct = (math.exp(c) - 1) * 100 if abs(c) < 1 else c * 100
            tag = " UNDETERMINED" if o["und"] else ""
            print(f"    {nm:<18}{c:>+14.4f}{pct:>+11.1f}%{sb:>+10.3f}  "
                  f"{bt._fmt_ci(o['lo'], o['hi']):<20}{tag}")
        # headline: per-year age decay in $/psf at the sample mean PSF
        age_coef = dict((o["name"], o["coef"]) for o in coefA)["age_years"]
        mean_psf = float(np.mean([math.exp(v) for v in ya]))
        print(f"    -> age decay ~= {abs(age_coef)*100:.2f}%/yr  "
              f"(~${abs(age_coef)*mean_psf:.0f}/psf/yr at mean PSF ${mean_psf:.0f}); "
              f"config assumes $40-60/psf/yr")
    print()


# ============================================================================
# PART 1c — PIECEWISE AGE-PSF CURVE (calibrates config.AGE_PSF_DECAY_SEGMENTS)
# ============================================================================
_AGE_SEGS = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 45)]


def _age_spline(age):
    return [max(0.0, min(age, hi) - lo) for lo, hi in _AGE_SEGS]


def age_curve(txns, asof, lookback=2.0):
    """log(PSF) ~ floor + log_sqft + age-spline + DISTRICT FE, leasehold resale.

    Measures the SHAPE of the vintage/age discount that
    config.AGE_PSF_DECAY_SEGMENTS encodes (v3.7): ~3%/yr to age 10 (the $50/psf
    folk rule lives here), a 10-15 plateau, a second ~2.6%/yr leg at 15-20,
    then slow drift. District FE matter: regions/districts differ in both PSF
    level and age mix, and region-only controls leak that into the age coefs.
    Update config when these move materially.
    """
    print("=" * 78)
    print(f"PART 1c — PIECEWISE AGE-PSF CURVE  (leasehold resale, last {lookback:.0f}yr, district FE)")
    print("  calibrates config.AGE_PSF_DECAY_SEGMENTS (relative_value age normalization)")
    print("=" * 78)
    rows = []
    for x in txns:
        if not (asof - lookback < x["t"] <= asof):
            continue
        if x["sale_type"] not in ("Resale", "Sub Sale") or x["freehold"]:
            continue
        if not x["psf"] or not x["sqft"] or x["sqft"] <= 0:
            continue
        cy = _commence_year(x["tenure"])
        ft = _floor_tier(x.get("floor"))
        if not cy or ft is None:
            continue
        age = max(0.0, x["t"] - cy - 3)  # lease-start → TOP offset, as in the pipeline
        if age > 45:
            continue
        rows.append((x, age, ft))
    if len(rows) < 1000:
        print(f"  too few rows ({len(rows)})\n")
        return
    districts = sorted({x["district"] for x, *_ in rows})
    didx = {d: i for i, d in enumerate(districts[1:])}
    X, y, cl = [], [], []
    for x, age, ft in rows:
        dd = [0.0] * len(didx)
        if x["district"] in didx:
            dd[didx[x["district"]]] = 1.0
        X.append([ft, math.log(x["sqft"])] + _age_spline(age) + dd)
        y.append(math.log(x["psf"]))
        cl.append(x.get("project") or x["district"])
    names = (["floor", "log_sqft"] + [f"age{lo}-{hi}" for lo, hi in _AGE_SEGS]
             + [f"D{d}" for d in districts[1:]])
    coef, r2, n = _ols_ci(X, y, names, cl)
    mean_psf = float(np.mean([math.exp(v) for v in y]))
    print(f"\n  n={n:,}, R2={r2:.3f}, mean PSF ${mean_psf:.0f}  "
          f"(95% CI = project-cluster bootstrap on the %/yr coef)")
    print(f"    {'segment':<10}{'%/yr':>8}{'$/psf/yr':>10}  {'95% CI(%/yr)':<22} config")
    try:
        from config import AGE_PSF_DECAY_SEGMENTS as cfg_segs
    except ImportError:
        cfg_segs = []
    for o in coef:
        nm, c = o["name"], o["coef"]
        if not nm.startswith("age"):
            continue
        lo = float(nm[3:].split("-")[0])
        cfg = next((r for a, b, r in cfg_segs if a <= lo < b), None)
        cfg_s = f"{cfg*100:+.1f}%/yr" if cfg is not None else "—"
        ci_s = (f"[{o['coef_lo']*100:+.2f},{o['coef_hi']*100:+.2f}]"
                if o["coef_lo"] is not None else "[ n/a ]")
        tag = " UNDETERMINED" if o["und"] else ""
        print(f"    {nm:<10}{c*100:>+8.2f}{c*mean_psf:>+10.0f}  {ci_s:<22} {cfg_s}{tag}")
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
    print("  95% CI = project-cluster bootstrap (pooled rows overlap across splits;")
    print("  the CI treats each project as ONE independent draw, not 3 rows)")
    print("=" * 78)
    fwd = [r["forward_cagr"] for r in rows]
    cl = [(r["project"], r["district"]) for r in rows]
    feats = ["trailing_cagr", "momentum", "psf_vs_dist", "txn_vol",
             "new_sale_share", "freehold", "log_psf"]
    print(f"  {'feature':<16}{'rho':>9}{'n':>7}  {'95% CI':<18}  interpretation")
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
        x = [r.get(f) for r in rows]
        rho, n = bt._spearman(x, fwd)
        lo, hi, _k = bt._cluster_boot_rho(x, fwd, cl)
        und = " UNDETERMINED" if (lo is None or lo <= 0 <= hi) else ""
        print(f"  {f:<16}{(f'{rho:+.3f}' if rho is not None else 'n/a'):>9}{n:>7}  "
              f"{bt._fmt_ci(lo, hi):<18}  {notes[f]}{und}")
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
    cl = [(r["project"], r["district"]) for r in good]
    coef, r2, n = _ols_ci(X, y, feats, cl)
    print(f"\n  n={n:,}  model R2={r2:.3f}  "
          f"(95% CI = project-cluster bootstrap, {BOOT_REPS} reps)")
    print(f"    {'feature':<16}{'std_beta':>10}{'raw_coef':>14}  {'95% CI(std_beta)':<20}")
    for o in sorted(coef, key=lambda t: -abs(t["std"])):
        tag = "  UNDETERMINED (CI crosses 0)" if o["und"] else ""
        print(f"    {o['name']:<16}{o['std']:>+10.3f}{o['coef']:>+14.5f}  "
              f"{bt._fmt_ci(o['lo'], o['hi']):<20}{tag}")
    print("  (rank by |std_beta|: the features that actually move forward returns;")
    print("   an UNDETERMINED tag means the cluster CI crosses 0 — don't narrate it)\n")


def _zser(rows, f):
    vals = [r.get(f) for r in rows]
    arr = np.array([v if v is not None else np.nan for v in vals], float)
    mu, sd = np.nanmean(arr), np.nanstd(arr)
    return [(0.0 if (v is None or sd == 0) else (v - mu) / sd) for v in vals]


def _config_composite(r):
    """Current-weight MMR-like composite from as-of-T features. Maps the config
    slopes onto the backtest features (same sign conventions MMR uses):
    appreciation +, momentum +, value (psf_vs_dist) -, liquidity +."""
    from config import (MMR_APPRECIATION_SLOPE, MMR_MOMENTUM_WEIGHT,
                        MMR_RELVALUE_SLOPE, MMR_TXN_VOLUME_WEIGHT)
    s = 0.0
    if r.get("trailing_cagr") is not None:
        s += MMR_APPRECIATION_SLOPE * (r["trailing_cagr"] * 100 - 4.0)
    if r.get("momentum") is not None:
        s += MMR_MOMENTUM_WEIGHT * (r["momentum"] * 100)
    if r.get("psf_vs_dist") is not None:
        s += -MMR_RELVALUE_SLOPE * (r["psf_vs_dist"] * 100)
    if r.get("txn_vol") is not None:
        s += MMR_TXN_VOLUME_WEIGHT * math.tanh(r["txn_vol"] / 40.0)
    return s


def part4_composite(rows, split_sample):
    print("=" * 78)
    print("PART 4 — COMPOSITE-SCORE VALIDATION  | does the ASSEMBLED score predict?")
    print("  Jun-2026 audit P0-1: the old print scored the config composite on ALL")
    print("  pooled rows (incl. partial-feature rows) but fit the 'in-sample optimal'")
    print("  OLS on complete-case rows only — the +0.288 vs +0.241 'config beats")
    print("  optimal' read was a sample-composition artifact. Both are now evaluated")
    print("  on the IDENTICAL complete-case row set; the all-rows number is reported")
    print("  separately, labeled, and is NOT comparable to the OLS line.")
    print("=" * 78)
    feats = ["trailing_cagr", "momentum", "psf_vs_dist", "txn_vol",
             "new_sale_share", "freehold", "log_psf"]
    good = [r for r in rows if all(r.get(f) is not None for f in feats)
            and r.get("forward_cagr") is not None]
    if len(good) < 50:
        print(f"  too few complete rows ({len(good)})\n")
        return
    y_good = [r["forward_cagr"] for r in good]
    cl_good = [(r["project"], r["district"]) for r in good]
    n_proj_good = len(set(cl_good))

    # ---- apples-to-apples: BOTH composites on the complete-case rows -------
    cur_good = [_config_composite(r) for r in good]
    rho_cg, n_cg = bt._spearman(cur_good, y_good)
    lo_cg, hi_cg, _ = bt._cluster_boot_rho(cur_good, y_good, cl_good)

    Z = np.column_stack([_zser(good, f) for f in feats])
    yv = np.array(y_good, float)
    Zd = np.column_stack([np.ones(len(good)), Z])
    beta, *_ = np.linalg.lstsq(Zd, yv, rcond=None)
    fit = Zd @ beta
    rho_opt, n_opt = bt._spearman(list(fit), list(yv))
    # CI of the FIXED fitted composite under cluster resampling (the weights
    # are not refit per draw — this bounds the evaluation noise, not the
    # additional optimism of in-sample fitting, which is already disclosed).
    lo_opt, hi_opt, _ = bt._cluster_boot_rho(list(fit), y_good, cl_good)

    print(f"\n  APPLES-TO-APPLES — identical complete-case rows "
          f"(n={len(good)}, {n_proj_good} projects; 95% CI = project-cluster bootstrap):")
    print(f"    (a) CURRENT config weights      forward rho = {rho_cg:+.3f}  "
          f"{bt._fmt_ci(lo_cg, hi_cg)}")
    print(f"    (b) in-sample OPTIMAL reweight  forward rho = {rho_opt:+.3f}  "
          f"{bt._fmt_ci(lo_opt, hi_opt)}  (fit on these same rows — in-sample "
          f"upper bound)")
    gap = rho_opt - rho_cg
    print(f"    honest read: config {'<' if gap > 0 else '>='} in-sample optimal "
          f"(gap {gap:+.3f}); the config does NOT beat the in-sample ceiling — "
          f"the old claim compared different row sets.")

    # ---- all-rows number, separately labeled --------------------------------
    fwd_all = [r["forward_cagr"] for r in rows]
    cl_all = [(r["project"], r["district"]) for r in rows]
    cur_all = [_config_composite(r) for r in rows]
    rho_ca, n_ca = bt._spearman(cur_all, fwd_all)
    lo_ca, hi_ca, k_ca = bt._cluster_boot_rho(cur_all, fwd_all, cl_all)
    print(f"\n  ALL pooled rows incl. partial-feature rows (NOT comparable to (b)):")
    print(f"    config weights on all rows      forward rho = {rho_ca:+.3f}  "
          f"{bt._fmt_ci(lo_ca, hi_ca)}  (n={n_ca}, {k_ca} projects)")

    print("\n      optimal standardized weights (fit on complete-case rows):")
    order = sorted(zip(feats, beta[1:]), key=lambda t: -abs(t[1]))
    for nm, b in order:
        print(f"        {nm:<16}{b:>+.5f}")
    print("\n  Read: (a) is what ships today; (b) is the ceiling an in-sample linear")
    print("  model could reach on these features — evaluated on the same rows. All")
    print("  numbers are in-sample, single-regime (2021-26), overlapping-window")
    print("  pooled; effective n ~= projects, not rows.\n")


# ============================================================================
# PART 5 — UN-TESTED LEVERS (district-level joins) + floor magnitude + age +
#          joint out-of-split re-fit of every joinable MMR component proxy.
# Added v3.5: validates `future` (govt zones / transformation / supply — the
# production future score is EXACTLY these district-level parts for the
# coordinate-less listings that dominate the DB), `buyer_pool`, the age curve,
# and the stored floor_factors magnitude. `yield` and `dev_size` remain
# UNTESTABLE here: no real per-project rental series exists in the repo
# (district median_rental_psf / PSF is just inverse-PSF = region in disguise),
# and total_units is absent from both the URA panel and 6348/6349 listings.
# ============================================================================

import json as _json
import os as _os

_DATA_DIR = _os.path.join(_os.path.dirname(__file__), "data")


def _load_json(name):
    with open(_os.path.join(_DATA_DIR, name)) as f:
        return _json.load(f)


def _district_future_signals(asof):
    """Per-district, point-in-time future-potential sub-scores, mirroring
    scoring/future_scorer.py for a coordinate-less listing (mrt part = 0):
      zone (0-6, max score_points of zones containing the district),
      transformation (0-4, counts of future MRT lines + govt zones),
      supply (0-4 from the static label).
    A line counts as 'future' at T if completion_year > T - 2 (the production
    RECENT_OPERATIONAL_YEARS window, applied point-in-time)."""
    infra = _load_json("future_infrastructure.json").get("mrt_lines", {})
    zones = _load_json("government_zones.json").get("development_zones", {})
    profiles = _load_json("district_profiles.json").get("districts", {})

    def _line_is_future(code):
        try:
            cy = int(str(infra.get(code, {}).get("completion", ""))[:4])
        except (ValueError, TypeError):
            return True  # unknown completion -> keep (mirrors production)
        return cy > asof - 2

    out = {}
    for dnum_s, prof in profiles.items():
        dnum = int(dnum_s)
        zone_pts = 0.0
        nz = 0
        for z in zones.values():
            if dnum in z.get("districts", []):
                nz += 1
                zone_pts = max(zone_pts, float(z.get("score_points", 0)))
        zone_pts = min(zone_pts, 6.0)
        fut_lines = [c for c in prof.get("future_mrt", []) if _line_is_future(c)]
        transf = (2 if len(fut_lines) >= 2 else (1 if len(fut_lines) == 1 else 0)) \
            + (2 if nz >= 2 else (1 if nz == 1 else 0))
        transf = min(transf, 4)
        supply = {"very_high": 4, "high": 3, "medium": 2, "low": 1}.get(
            prof.get("supply_constraint", "medium"), 2)
        fp = zone_pts + transf + supply  # production future_potential (mrt=0)
        out[dnum] = {
            "future_potential": fp,
            "future_mmr_comp": max(0.0, fp - 4.0) * 0.7,  # the actual MMR component
            "buyer_pool": {"very_deep": 7.0, "deep": 4.5, "moderate": 1.5,
                           "shallow": -3.0}.get(prof.get("buyer_pool_depth"), 0.0),
        }
    return out


def _dnum(district):
    try:
        return int(str(district).replace("D", "").strip())
    except ValueError:
        return None


def part5_levers(rows, txns, asof):
    print("=" * 78)
    print("PART 5 — UN-TESTED LEVERS  | district-level point-in-time joins")
    print("  `future` here = the EXACT production future score for a coordinate-less")
    print("  listing (zone+transformation+supply; mrt sub-score requires lat/lng the")
    print("  listings DB does not have). buyer_pool = the MMR point values.")
    print("=" * 78)
    fwd = [r["forward_cagr"] for r in rows]

    # join district signals (as-of the earliest split is fine: zone/supply static,
    # line gating barely moves across the 0.5yr split spread)
    sig = _district_future_signals(asof)
    fut, fmc, bp = [], [], []
    for r in rows:
        s = sig.get(_dnum(r["district"]))
        fut.append(s["future_potential"] if s else None)
        fmc.append(s["future_mmr_comp"] if s else None)
        bp.append(s["buyer_pool"] if s else None)

    cl = [(r["project"], r["district"]) for r in rows]
    rho_f, n_f = bt._spearman(fut, fwd)
    rho_b, n_b = bt._spearman(bp, fwd)
    lo_f, hi_f, _ = bt._cluster_boot_rho(fut, fwd, cl)
    lo_b, hi_b, _ = bt._cluster_boot_rho(bp, fwd, cl)
    print(f"\n  univariate Spearman vs forward {2.0:.0f}yr return "
          f"(95% CI = project-cluster bootstrap):")
    print(f"    future_potential (0-14)   rho {rho_f:+.3f}  {bt._fmt_ci(lo_f, hi_f)}  n={n_f}")
    print(f"    buyer_pool pts (-3..7)    rho {rho_b:+.3f}  {bt._fmt_ci(lo_b, hi_b)}  n={n_b}")

    # within-region read (region is the dominant confounder at district level)
    for reg in ("RCR", "OCR"):
        sub = [i for i, r in enumerate(rows) if r["region"] == reg]
        rho_fr, n_fr = bt._spearman([fut[i] for i in sub], [fwd[i] for i in sub])
        rho_br, n_br = bt._spearman([bp[i] for i in sub], [fwd[i] for i in sub])
        print(f"    within {reg}: future rho {f'{rho_fr:+.3f}' if rho_fr is not None else ' n/a'} (n={n_fr})"
              f"   buyer_pool rho {f'{rho_br:+.3f}' if rho_br is not None else ' n/a'} (n={n_br})")

    # multivariate: does future/buyer_pool survive region + value + liquidity?
    feats = ["psf_vs_dist", "txn_vol", "freehold"]
    good = [i for i, r in enumerate(rows)
            if all(r.get(f) is not None for f in feats)
            and fut[i] is not None and bp[i] is not None]
    X = [[rows[i][f] for f in feats]
         + [fut[i], bp[i],
            1.0 if rows[i]["region"] == "RCR" else 0.0,
            1.0 if rows[i]["region"] == "CCR" else 0.0]
         for i in good]
    names = feats + ["future_potential", "buyer_pool", "region_RCR", "region_CCR"]
    coef, r2, n = _ols_ci(X, [fwd[i] for i in good], names,
                          [cl[i] for i in good])
    print(f"\n  multivariate (n={n}, R2={r2:.3f}) — std_beta (survives collinearity):")
    _print_ols_ci(coef)
    print("  CAVEAT: ~28 districts -> as many distinct future/buyer_pool values; treat")
    print("  as a coarse screen, not a per-listing validation.\n")


def part5_floor(asof, lookback=2.0):
    """Within-project floor premium (project fixed effects) vs the stored
    ura_cache floor_factors (which are all district-basis and ~1.4% low->high)."""
    print("=" * 78)
    print("PART 5b — FLOOR-FACTOR MAGNITUDE  | within-project (FE) vs stored cache")
    print("=" * 78)
    # load_with_floor drops project names, so read the CSVs keeping them
    by_proj = defaultdict(list)
    for pth in sorted(glob.glob(bt.DATA_GLOB)):
        with bt._open_csv(pth) as fh:
            for row in csv.DictReader(fh):
                t = bt._parse_date(row.get("Sale Date", ""))
                psf = bt._num(row.get("Unit Price ($ PSF)"))
                if t is None or not psf:
                    continue
                if not (asof - lookback < t <= asof):
                    continue
                if row.get("Type of Sale", "").strip() not in ("Resale", "Sub Sale"):
                    continue
                ft = _floor_tier(row.get("Floor Level", ""))
                if ft is None:
                    continue
                by_proj[row["Project Name"].strip().upper()].append((ft, math.log(psf)))
    # project-demeaned regression of log psf on tier (= project FE)
    xs, ys = [], []
    used = 0
    for proj, obs in by_proj.items():
        tiers = {t for t, _ in obs}
        if len(obs) < 6 or len(tiers) < 2:
            continue
        used += 1
        mt = sum(t for t, _ in obs) / len(obs)
        mp = sum(p for _, p in obs) / len(obs)
        for t, p in obs:
            xs.append(t - mt)
            ys.append(p - mp)
    if len(xs) > 200:
        xs_a, ys_a = np.array(xs), np.array(ys)
        beta = float((xs_a * ys_a).sum() / (xs_a * xs_a).sum())
        print(f"\n  within-project premium: {(math.exp(beta)-1)*100:+.2f}%/tier "
              f"(low->high {(math.exp(2*beta)-1)*100:+.2f}%)  "
              f"[{used} projects, {len(xs):,} txns]")
    # stored cache factors for comparison
    cache = _load_json("ura_cache.json").get("projects", {})
    spreads = [ff["high"] / ff["low"] - 1 for ff in
               (p.get("floor_factors") or {} for p in cache.values())
               if ff.get("high") and ff.get("low")]
    bases = {}
    for p in cache.values():
        b = (p.get("floor_factors") or {}).get("basis")
        if b:
            bases[b] = bases.get(b, 0) + 1
    if spreads:
        print(f"  stored ura_cache factors: basis={bases}, median low->high spread "
              f"{float(np.median(spreads))*100:+.2f}%  (compare to the FE estimate above)\n")


def part5_age(rows, txns):
    """Age-at-T vs forward return (tests the MMR age sweet-spot curve's sign)."""
    print("=" * 78)
    print("PART 5c — PROPERTY AGE vs FORWARD RETURN  | leasehold, age at split")
    print("=" * 78)
    commence = {}
    for x in txns:
        if x["project"] not in commence:
            cy = _commence_year(x["tenure"])
            if cy:
                commence[x["project"]] = cy
    ages, fwds, cls = [], [], []
    for r in rows:
        cy = commence.get(r["project"])
        if cy and r.get("forward_cagr") is not None:
            age = r.get("split", 2024.0) - cy  # rows carry their split now
            if 0 <= age <= 60:
                ages.append(age)
                fwds.append(r["forward_cagr"])
                cls.append((r["project"], r["district"]))
    rho, n = bt._spearman(ages, fwds)
    lo, hi, _ = bt._cluster_boot_rho(ages, fwds, cls)
    print(f"\n  age_at_T vs forward: rho {rho:+.3f}  {bt._fmt_ci(lo, hi)}  (n={n})")
    # quintiles for shape (the MMR curve says 3-7yr is the sweet spot)
    pairs = sorted(zip(ages, fwds))
    q = 5
    print("  age quintiles -> forward mean:")
    for i in range(q):
        chunk = pairs[i * len(pairs) // q:(i + 1) * len(pairs) // q]
        a = [c[0] for c in chunk]
        f = [c[1] for c in chunk]
        print(f"    age [{min(a):4.1f},{max(a):4.1f}]  fwd mean {np.mean(f)*100:+.2f}%  n={len(chunk)}")
    print()


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _parse_opened(s):
    """'2022-11' or '2024' -> float year (mid-month / mid-year). None = blank
    (pre-2021 station, assumed always open)."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        parts = s.split("-")
        y = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 6
        return y + (m - 0.5) / 12.0
    except (ValueError, IndexError):
        return None


def part5f_devsize_mrt(rows):
    """dev_size (total dwelling units) and MRT distance vs forward returns.

    Joins data/project_units.json (URA "No of Dwelling Units" GIS layer:
    per-project unit totals + block-centroid WGS84 coords) and
    data/mrt_stations.csv (operational network). Tests the two remaining
    un-validated MMR components: dev_size (6·tanh((units-150)/300)) and
    mrt (9·tanh((700-d)/600)). Coords are per-PROJECT (not per-listing) —
    fine, since the panel rows are projects.

    Jun-2026 audit P1-6: the station file is the 2026 network — TEL3/4/5-era
    stations had not opened at the 2023.75-2024.25 splits, so "distance to a
    2026 station" leaked future infrastructure into an as-of-T feature and
    inflated the MRT std_beta. Stations now carry an `opened` column and are
    excluded until open at each row's split; the legacy (look-ahead) number is
    re-printed alongside for comparison.
    """
    print("=" * 78)
    print("PART 5f — DEV SIZE + MRT DISTANCE  | project_units.json + mrt_stations.csv")
    print("=" * 78)
    try:
        pu = _load_json("project_units.json")["projects"]
    except FileNotFoundError:
        print("  data/project_units.json missing — run build_project_units.py first\n")
        return
    stations = []
    with open(_os.path.join(_DATA_DIR, "mrt_stations.csv")) as fh:
        for r in csv.DictReader(fh):
            try:
                stations.append((float(r["latitude"]), float(r["longitude"]),
                                 _parse_opened(r.get("opened"))))
            except (KeyError, ValueError):
                continue
    n_dated = sum(1 for *_xy, op in stations if op is not None)

    joined = []
    for r in rows:
        e = pu.get(r["project"].lower())
        if not e or e.get("landed_only"):
            continue
        split = r.get("split", 2024.0)
        d_all = None
        d_asof = None
        for sl, so, op in stations:
            d = _haversine_m(e["lat"], e["lng"], sl, so)
            if d_all is None or d < d_all:
                d_all = d
            if (op is None or op <= split) and (d_asof is None or d < d_asof):
                d_asof = d
        joined.append((r, e, d_asof, d_all))

    fwd = [r["forward_cagr"] for r, *_ in joined]
    cl = [(r["project"], r["district"]) for r, *_ in joined]
    units = [e["total_units"] for _, e, _, _ in joined]
    mrt_d = [d for _, _, d, _ in joined]
    mrt_d_all = [d for _, _, _, d in joined]
    dev_comp = [6.0 * math.tanh((u - 150.0) / 300.0) for u in units]
    mrt_comp = [9.0 * math.tanh((700.0 - d) / 600.0) if d is not None else None
                for d in mrt_d]
    n_moved = sum(1 for a, b in zip(mrt_d, mrt_d_all)
                  if a is not None and b is not None and a - b > 1.0)

    print(f"\n  joined {len(joined)}/{len(rows)} panel rows to project_units")
    print(f"  station file: {len(stations)} stations, {n_dated} dated 2021+ "
          f"(as-of-T gating); {n_moved} rows' nearest station was NOT yet open "
          f"at their split")
    print(f"  (95% CI = project-cluster bootstrap)")
    for nm, x in (("total_units", units), ("dev_size MMR comp", dev_comp),
                  ("mrt_dist_asof_T", mrt_d), ("mrt MMR comp asof_T", mrt_comp),
                  ("mrt_dist_2026net", mrt_d_all)):
        rho, n = bt._spearman(x, fwd)
        lo, hi, _ = bt._cluster_boot_rho(x, fwd, cl)
        print(f"    {nm:<20} rho {f'{rho:+.3f}' if rho is not None else '  n/a'}  "
              f"{bt._fmt_ci(lo, hi)}  (n={n})")

    # multivariate with the usual controls (value, region, liquidity) — run on
    # the SAME row set twice: as-of-T distance (honest) vs 2026-network
    # distance (the legacy look-ahead number, for comparison).
    def _mv(use_asof):
        feats_rows, y, cls = [], [], []
        for (r, e, d_a, d_l) in joined:
            d = d_a if use_asof else d_l
            if r.get("psf_vs_dist") is None or d_a is None or d_l is None:
                continue
            feats_rows.append([
                r["psf_vs_dist"],
                math.tanh((r.get("txn_vol") or 0) / 40.0),
                math.log(max(50, e["total_units"])),
                math.log(max(80.0, d)),
                1.0 if r["region"] == "RCR" else 0.0,
                1.0 if r["region"] == "CCR" else 0.0,
            ])
            y.append(r["forward_cagr"])
            cls.append((r["project"], r["district"]))
        return feats_rows, y, cls

    names = ["psf_vs_dist", "txn_vol", "log_units", "log_mrt_dist",
             "region_RCR", "region_CCR"]
    fr, y, cls = _mv(use_asof=True)
    if len(fr) >= 80:
        coef, r2, n = _ols_ci(fr, y, names, cls)
        print(f"\n  multivariate, as-of-T station network (n={n}, R2={r2:.3f}):")
        _print_ols_ci(coef, name_w=14)
        fr_l, y_l, cls_l = _mv(use_asof=False)
        coef_l, _r2l, _nl = _ols_ci(fr_l, y_l, names, cls_l)
        old = next(o for o in coef_l if o["name"] == "log_mrt_dist")
        new = next(o for o in coef if o["name"] == "log_mrt_dist")
        print(f"\n  MRT std_beta, look-ahead vs honest (same rows/controls):")
        print(f"    2026 network (legacy, leaks unopened stations) "
              f"{old['std']:+.3f}  {bt._fmt_ci(old['lo'], old['hi'])}")
        print(f"    as-of-T network (P1-6 fix)                     "
              f"{new['std']:+.3f}  {bt._fmt_ci(new['lo'], new['hi'])}"
              f"{'  UNDETERMINED' if new['und'] else ''}")
    print()


def _load_rentals(glob_pat="ura_rental_D*_202301_202406.csv"):
    """Load URA rental contracts (as-of slice). Returns rows with project,
    district, t (float year), monthly_rent, sqft_mid, beds."""
    out = []
    for p in sorted(glob.glob(_os.path.join(_DATA_DIR, glob_pat))):
        with bt._open_csv(p) as fh:
            for r in csv.DictReader(fh):
                t = bt._parse_date(r.get("Lease Commencement Date", ""))
                rent = bt._num(r.get("Monthly Rent ($)"))
                band = (r.get("Floor Area (SQFT)") or "").replace(",", "")
                m = re.findall(r"\d+", band)
                sqft_mid = (float(m[0]) + float(m[1])) / 2 if len(m) >= 2 else None
                if t is None or not rent or not sqft_mid:
                    continue
                out.append({
                    "project": (r.get("Project Name") or "").strip().upper(),
                    "district": (r.get("Postal District") or "").strip(),
                    "t": t,
                    "rent": rent,
                    "sqft_mid": sqft_mid,
                    "rent_psf": rent / sqft_mid,
                })
    return out


def part5g_yield(rows):
    """REAL-rent gross yield (as-of-T) vs forward returns — the yield lever.

    Numerator: per-project median rental PSF from actual URA rental contracts
    commencing in (T-1, T] — T is each row's OWN split (Jun-2026 audit: the
    old single split_hint=2024.0 window let rows from the 2023.75 split see
    rental contracts from up to 3 months after their split — a small forward
    leak; now per-split point-in-time). Source:
    data/ura_rental_D*_202301_202406.csv (the point-in-time slice; the default
    60-month export is capped at the 10k most recent rows, so a dedicated
    historical fetch is required).
    Denominator: the panel's as-of-T resale PSF level. This is the first test
    of `yield` with real rents — the district-constant rent estimate used in
    production is mechanically const/PSF (inverse price), which is why it was
    never testable before."""
    print("=" * 78)
    print("PART 5g — REAL-RENT GROSS YIELD vs FORWARD RETURN  | URA rental contracts")
    print("=" * 78)
    rentals = _load_rentals()
    if not rentals:
        print("  no data/ura_rental_D*_202301_202406.csv files — run "
              "fetch_ura_rentals.py --from 2023-01 --to 2024-06 first\n")
        return
    def _dkey(d):
        return str(d).replace("D", "").lstrip("0")

    rent_min = min(r["t"] for r in rentals)
    rent_max = max(r["t"] for r in rentals)
    splits = sorted({r.get("split") for r in rows if r.get("split") is not None})
    rent_psf = {}  # (project, dkey, split) -> median rent psf in (split-1, split]
    for s in splits:
        tmp = defaultdict(list)
        for r in rentals:
            if s - 1 < r["t"] <= s:
                # key on (project, district) — name-only joins collide across districts
                tmp[(r["project"], _dkey(r["district"]))].append(r["rent_psf"])
        for k, v in tmp.items():
            if len(v) >= 3:
                rent_psf[k + (s,)] = float(np.median(v))
        cov = max(0.0, min(s, rent_max) - max(s - 1, rent_min))
        print(f"  split {s}: rent window ({s-1:.2f},{s:.2f}] covered {cov:.0%} by "
              f"rental data ({sum(1 for k in rent_psf if k[2] == s):,} projects >=3 contracts)")
    print(f"  {len(rentals):,} rental contracts loaded "
          f"({rent_min:.2f} -> {rent_max:.2f}); per-row windows end at each row's "
          f"OWN split (no cross-split leak)")

    def _row_yield(r):
        rp = rent_psf.get((r["project"], _dkey(r["district"]), r.get("split")))
        if rp is None or not r.get("psf_level"):
            return None
        return 12.0 * rp / r["psf_level"] * 100  # gross yield %

    fwd, gy, cl = [], [], []
    for r in rows:
        v = _row_yield(r)
        if v is None:
            continue
        fwd.append(r["forward_cagr"])
        gy.append(v)
        cl.append((r["project"], r["district"]))
    rho, n = bt._spearman(gy, fwd)
    print(f"  panel rows matched: {len(gy)}")
    if rho is None:
        print("  too few matched rows\n")
        return
    lo, hi, _ = bt._cluster_boot_rho(gy, fwd, cl)
    print(f"  univariate: gross_yield vs forward rho {rho:+.3f}  "
          f"{bt._fmt_ci(lo, hi)}  (n={n})")

    # multivariate: does yield survive value + region + liquidity?
    feats_rows, y, cls = [], [], []
    for r in rows:
        v = _row_yield(r)
        if v is None or r.get("psf_vs_dist") is None:
            continue
        feats_rows.append([
            v,
            r["psf_vs_dist"],
            math.tanh((r.get("txn_vol") or 0) / 40.0),
            1.0 if r["region"] == "RCR" else 0.0,
            1.0 if r["region"] == "CCR" else 0.0,
        ])
        y.append(r["forward_cagr"])
        cls.append((r["project"], r["district"]))
    if len(feats_rows) >= 80:
        names = ["gross_yield", "psf_vs_dist", "txn_vol", "region_RCR", "region_CCR"]
        coef, r2, n2 = _ols_ci(feats_rows, y, names, cls)
        print(f"\n  multivariate (n={n2}, R2={r2:.3f}) — std_beta:")
        _print_ols_ci(coef, name_w=14)
    print("  NOTE: yield numerator is real rents, denominator is the same as-of-T")
    print("  price as the value features — a positive yield read that survives")
    print("  psf_vs_dist is genuine rental-carry signal, not re-skinned cheapness.\n")


def part5h_regime():
    """Region tilt across PAST cycles — the single-regime caveat, addressed.

    Uses URA's non-landed price index by locality (data.gov.sg
    d_f65e490a8ad430f60a9a3d9df2bff2a0, quarterly 2004->now,
    data/ppi_nonlanded_locality.csv). For every quarter with a 2yr forward
    window, computes each region's forward 2yr return and groups by market
    regime (mean of the three regions' forward returns): does the v3.4/v3.5
    OCR>RCR>CCR forward tilt hold outside the 2021-26 bull run?"""
    print("=" * 78)
    print("PART 5h — REGION TILT ACROSS REGIMES  | URA price index 2004-now")
    print("=" * 78)
    path = _os.path.join(_DATA_DIR, "ppi_nonlanded_locality.csv")
    if not _os.path.exists(path):
        print("  data/ppi_nonlanded_locality.csv missing (data.gov.sg "
              "d_f65e490a8ad430f60a9a3d9df2bff2a0)\n")
        return
    idx = defaultdict(dict)  # quarter_float -> region -> index
    reg_map = {"Core Central Region": "CCR", "Rest of Central Region": "RCR",
               "Outside Central Region": "OCR"}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            try:
                y, q = r["quarter"].split("-Q")
                t = int(y) + (int(q) - 0.5) / 4.0
                idx[t][reg_map[r["market_segment"]]] = float(r["price_index"])
            except (KeyError, ValueError):
                continue
    ts = sorted(idx)
    rows = []
    for t in ts:
        t_fwd = t + 2.0
        if t_fwd not in idx:
            continue
        if not all(g in idx[t] and g in idx[t_fwd] for g in ("CCR", "RCR", "OCR")):
            continue
        fwd = {g: (idx[t_fwd][g] / idx[t][g]) ** 0.5 - 1 for g in ("CCR", "RCR", "OCR")}
        rows.append((t, fwd))
    print(f"\n  {len(rows)} quarters with a 2yr forward window "
          f"({ts[0]:.2f} -> {ts[-1]:.2f})")

    buckets = {"down (mkt fwd < 0)": [], "flat (0..3%/yr)": [], "bull (>3%/yr)": []}
    for t, fwd in rows:
        mkt = sum(fwd.values()) / 3
        key = ("down (mkt fwd < 0)" if mkt < 0
               else "flat (0..3%/yr)" if mkt < 0.03 else "bull (>3%/yr)")
        buckets[key].append(fwd)
    print(f"\n  {'regime':<22}{'n':>4}{'CCR':>8}{'RCR':>8}{'OCR':>8}   OCR-CCR gap")
    for k, v in buckets.items():
        if not v:
            continue
        m = {g: float(np.mean([f[g] for f in v])) * 100 for g in ("CCR", "RCR", "OCR")}
        print(f"  {k:<22}{len(v):>4}{m['CCR']:>+8.2f}{m['RCR']:>+8.2f}{m['OCR']:>+8.2f}"
              f"   {m['OCR']-m['CCR']:+.2f} pp/yr")
    # who wins quarter by quarter
    wins = defaultdict(int)
    for _, fwd in rows:
        wins[max(fwd, key=fwd.get)] += 1
    print(f"\n  quarter-by-quarter forward winner: {dict(wins)}")

    # ---- era blocks (Jun-2026 audit P2-1) -----------------------------------
    # The direction buckets above AGGREGATE across 22 years — they hid that CCR
    # won 20/81 windows (incl. the most recent independent one). Non-overlapping
    # calendar eras + per-era win counts + the count of INDEPENDENT windows
    # (consecutive 2yr-forward windows 1 quarter apart are ~87.5% the same
    # window) keep that visible instead of averaged away.
    eras = [(2004, 2009, "2004-08"), (2009, 2014, "2009-13"),
            (2014, 2018, "2014-17"), (2018, 2021, "2018-20"),
            (2021, 2027, "2021-26")]
    print("\n  era blocks (non-overlapping calendar windows):")
    print(f"  {'era':<9}{'qtrs':>5}{'indep':>6}{'CCR':>8}{'RCR':>8}{'OCR':>8}"
          f"   OCR-CCR   wins (CCR/RCR/OCR)")
    for lo_y, hi_y, label in eras:
        qs = [(t, f) for t, f in rows if lo_y <= t < hi_y]
        if not qs:
            print(f"  {label:<9}{0:>5}     —  (no quarters with a forward window)")
            continue
        m = {g: float(np.mean([f[g] for _, f in qs])) * 100
             for g in ("CCR", "RCR", "OCR")}
        w = defaultdict(int)
        for _, f in qs:
            w[max(f, key=f.get)] += 1
        # independent (non-overlapping) windows: greedy pick, spaced >= 2yr
        n_ind = 0
        last = None
        for t, _f in qs:
            if last is None or t >= last + 2.0:
                n_ind += 1
                last = t
        print(f"  {label:<9}{len(qs):>5}{n_ind:>6}{m['CCR']:>+8.2f}{m['RCR']:>+8.2f}"
              f"{m['OCR']:>+8.2f}   {m['OCR']-m['CCR']:>+7.2f}   "
              f"{w['CCR']}/{w['RCR']}/{w['OCR']}")
    # the most recent INDEPENDENT windows, picked backwards from the newest
    # quarter so the latest read is exactly the latest available window
    ind = []
    last = None
    for t, f in reversed(rows):
        if last is None or t <= last - 2.0:
            ind.append((t, f))
            last = t
    print("\n  most recent independent (non-overlapping) 2yr windows, newest first:")
    for t, f in ind[:5]:
        winner = max(f, key=f.get)
        print(f"    {t:.2f} -> {t+2:.2f}:  CCR {f['CCR']*100:+.2f}  RCR {f['RCR']*100:+.2f}  "
              f"OCR {f['OCR']*100:+.2f}  /yr   winner {winner}")
    ccr_won = [(t, f) for t, f in rows if max(f, key=f.get) == "CCR"]
    if ccr_won:
        t, f = ccr_won[-1]
        print(f"  most recent CCR-won window: {t:.2f} -> {t+2:.2f}  "
              f"(CCR {f['CCR']*100:+.2f} vs RCR {f['RCR']*100:+.2f} / OCR "
              f"{f['OCR']*100:+.2f} /yr) — a 2yr-independent chain anchored on it "
              f"makes CCR the winner of ITS most recent window")
    print("\n  Read: if OCR's edge exists ONLY in the bull bucket, the regional")
    print("  baselines' OCR tilt is regime-bound — keep it compressed. CCR wins in")
    print(f"  {wins.get('CCR', 0)}/{len(rows)} windows are real regimes, not noise —")
    print("  the era table and the newest independent window must stay visible.\n")


def part5_joint_refit(txns, window, min_txn, split_sample, asof):
    """Joint ridge re-fit of every joinable MMR-component proxy, with an
    OUT-OF-SPLIT evaluation (fit on T=2023.75+2024.0, evaluate on T=2024.25).
    This is the honest version of PART 4(b): no shared in-sample fit."""
    print("=" * 78)
    print("PART 5d — JOINT RIDGE RE-FIT (out-of-split)  | all joinable components")
    print("=" * 78)
    sig = _district_future_signals(asof)
    commence = {}
    for x in txns:
        if x["project"] not in commence:
            cy = _commence_year(x["tenure"])
            if cy:
                commence[x["project"]] = cy

    def featurize(split):
        rows = bt.build_panel(txns, split, window, min_txn, False, split_sample)
        out = []
        for r in rows:
            r = dict(r)
            r["region"] = _region_of(r["district"])
            s = sig.get(_dnum(r["district"])) or {}
            cy = commence.get(r["project"])
            age = (split - cy) if cy else None
            out.append({
                "project": r["project"], "district": r["district"],
                "trailing_cagr": r.get("trailing_cagr"),
                "momentum": r.get("momentum"),
                "psf_vs_dist": r.get("psf_vs_dist"),
                "txn_vol": math.tanh((r.get("txn_vol") or 0) / 40.0),
                "new_sale_share": r.get("new_sale_share"),
                "freehold": r.get("freehold"),
                "log_psf": math.log(r["psf_level"]) if r.get("psf_level") else None,
                "future_potential": s.get("future_potential"),
                "buyer_pool": s.get("buyer_pool"),
                "age": age if (age is None or 0 <= age <= 60) else None,
                "region_RCR": 1.0 if r["region"] == "RCR" else 0.0,
                "region_CCR": 1.0 if r["region"] == "CCR" else 0.0,
                "forward_cagr": r["forward_cagr"],
            })
        return out

    feats = ["trailing_cagr", "momentum", "psf_vs_dist", "txn_vol", "new_sale_share",
             "freehold", "log_psf", "future_potential", "buyer_pool", "age",
             "region_RCR", "region_CCR"]
    train = featurize(2023.75) + featurize(2024.0)
    test = featurize(2024.25)
    tr = [r for r in train if all(r.get(f) is not None for f in feats)]
    te = [r for r in test if all(r.get(f) is not None for f in feats)]
    if len(tr) < 80 or len(te) < 40:
        print(f"  too few complete rows (train {len(tr)}, test {len(te)})\n")
        return
    mu = {f: float(np.mean([r[f] for r in tr])) for f in feats}
    sd = {f: float(np.std([r[f] for r in tr])) or 1.0 for f in feats}

    def Z(rows_):
        return np.array([[(r[f] - mu[f]) / sd[f] for f in feats] for r in rows_])

    Ztr, ytr = Z(tr), np.array([r["forward_cagr"] for r in tr])
    Zte, yte = Z(te), np.array([r["forward_cagr"] for r in te])
    cl_te = [(r["project"], r["district"]) for r in te]
    for lam in (1.0, 10.0):
        A = Ztr.T @ Ztr + lam * np.eye(len(feats))
        b = Ztr.T @ (ytr - ytr.mean())
        w = np.linalg.solve(A, b)
        rho_te, n_te = bt._spearman(list(Zte @ w), list(yte))
        lo, hi, _ = bt._cluster_boot_rho(list(Zte @ w), list(yte), cl_te)
        print(f"\n  ridge lam={lam:g}: out-of-split forward rho = {rho_te:+.3f} "
              f"{bt._fmt_ci(lo, hi)} (n={n_te})")
        for f, wi in sorted(zip(feats, w), key=lambda t: -abs(t[1])):
            print(f"    {f:<18}{wi:>+9.5f}")
    # current-config composite on the SAME test split, for an apples comparison
    from config import (MMR_APPRECIATION_SLOPE, MMR_MOMENTUM_WEIGHT,
                        MMR_RELVALUE_SLOPE, MMR_TXN_VOLUME_WEIGHT)
    cur = []
    for r in te:
        s_ = 0.0
        if r.get("trailing_cagr") is not None:
            s_ += MMR_APPRECIATION_SLOPE * (r["trailing_cagr"] * 100 - 4.0)
        if r.get("momentum") is not None:
            s_ += MMR_MOMENTUM_WEIGHT * (r["momentum"] * 100)
        if r.get("psf_vs_dist") is not None:
            s_ += -MMR_RELVALUE_SLOPE * (r["psf_vs_dist"] * 100)
        s_ += MMR_TXN_VOLUME_WEIGHT * r["txn_vol"]
        cur.append(s_)
    rho_cur, n_cur = bt._spearman(cur, list(yte))
    lo_c, hi_c, _ = bt._cluster_boot_rho(cur, list(yte), cl_te)
    print(f"\n  current-config composite on the same test split: rho {rho_cur:+.3f} "
          f"{bt._fmt_ci(lo_c, hi_c)} (n={n_cur})\n")


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
    ftxns = load_with_floor()
    hedonic(ftxns, asof)
    age_curve(ftxns, asof)

    splits = [2023.75, 2024.0, 2024.25]
    rows = pooled_panel(txns, splits, args.window, args.min_txn, args.split_sample)
    print(f"Pooled forward panel: {len(rows)} project-split rows "
          f"over splits {splits}, window={args.window}yr\n")
    bt.effective_n_report(rows, splits, args.window)
    if args.split_sample:
        fb = sum(r.get("ss_fallback", 0) for r in rows)
        print(f"split-sample fallback (shared feature/outcome price base): "
              f"{fb}/{len(rows)} pooled rows ({fb/len(rows):.0%})\n")
    part2_univariate(rows)
    part3_multivariate(rows)
    part4_composite(rows, args.split_sample)
    part5_levers(rows, txns, splits[0])
    part5_floor(asof)
    part5_age(rows, txns)
    part5f_devsize_mrt(rows)
    part5g_yield(rows)
    part5h_regime()
    part5_joint_refit(txns, args.window, args.min_txn, args.split_sample, splits[0])


if __name__ == "__main__":
    main()
