"""Point-in-time backtest of the scoring algo's predictive features.

We have no historical *score* log (mmr_history started Jun 2026), so the only
ground truth is the URA transaction panel itself (data/ura_district_D*.csv,
~65k resale/new-sale txns, May-2021 -> May-2026). This harness REPLAYS that
panel point-in-time:

  1. Pick a split date T.
  2. For each project, compute the features the algo leans on -- using ONLY
     transactions on/before T (no look-ahead): trailing PSF CAGR, momentum,
     transaction volume, PSF vs district median, freehold flag, new-sale share.
  3. Measure the REALIZED forward resale-PSF appreciation after T.
  4. Ask: do the as-of-T features actually rank forward winners? (Spearman +
     quintile forward-return tables.)

The headline question: trailing appreciation/momentum is ~55-60% of MMR
variance. Does it predict forward returns, or do returns mean-revert (in which
case the algo's biggest weight points the wrong way)?

Index method (v1): per-project median resale PSF over 12-month windows.
Resale + Sub Sale only (New Sale is developer pricing). Composition shift
across unit sizes is a known caveat -- see --size-control for a stratified
robustness variant. Medians + resale-only + min-count filters keep v1 honest
enough for a first read.

Usage:
  python backtest.py                      # default multi-split run + report
  python backtest.py --split 2023.99 --window 2.0
  python backtest.py --min-txn 5 --dump out.csv
"""

import argparse
import csv
import glob
import math
import os
from collections import defaultdict

import numpy as np

_MON = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

DATA_GLOB = os.path.join(os.path.dirname(__file__), "data", "ura_district_D*.csv")


def _parse_date(s: str):
    """'May-26' -> float year 2026.375 (mid-month). None if unparseable."""
    s = (s or "").strip()
    try:
        mon, yr = s.split("-")
        return (2000 + int(yr)) + (_MON[mon] - 0.5) / 12.0
    except Exception:
        return None


def _num(s: str):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None


def load_txns(paths=None):
    """Load resale-relevant URA transactions as dicts."""
    paths = paths or sorted(glob.glob(DATA_GLOB))
    out = []
    for p in paths:
        with open(p) as fh:
            for row in csv.DictReader(fh):
                t = _parse_date(row.get("Sale Date", ""))
                psf = _num(row.get("Unit Price ($ PSF)"))
                if t is None or not psf:
                    continue
                out.append({
                    "project": row["Project Name"].strip().upper(),
                    "district": row.get("Postal District", "").strip(),
                    "t": t,
                    "psf": psf,
                    "sqft": _num(row.get("Area (SQFT)")),
                    "sale_type": row.get("Type of Sale", "").strip(),
                    "tenure": row.get("Tenure", "").strip(),
                    "segment": row.get("Market Segment", "").strip(),
                })
    return out


def _median(xs):
    return float(np.median(xs)) if xs else None


def _win_vals(txns, lo, hi, sqft_band=None):
    """PSF list of resale txns with sale date in (lo, hi], time-ordered."""
    vals = [(x["t"], x["psf"]) for x in txns
            if lo < x["t"] <= hi and x["sale_type"] in ("Resale", "Sub Sale")
            and not (sqft_band and x["sqft"] and not (sqft_band[0] <= x["sqft"] < sqft_band[1]))]
    vals.sort()
    return [p for _, p in vals]


def _win_median(txns, lo, hi, sqft_band=None):
    """Median PSF of resale txns with sale date in (lo, hi]."""
    vals = _win_vals(txns, lo, hi, sqft_band)
    return _median(vals), len(vals)


def build_panel(txns, split, window, min_txn=5, size_control=False, split_sample=False):
    """Per-project as-of-`split` features + realized forward return.

    Windows are 12-month, center-to-center spans give clean annualized rates:
      old    (T-3, T-2]      center T-2.5
      mid    (T-2, T-1]      center T-1.5
      recent (T-1, T]        center T-0.5   <- best estimate of "price at T"
      fwd    (T+W-1, T+W]    center T+W-0.5

      trailing_cagr = (recent/old)^(1/2) - 1
      momentum      = (recent/mid - 1) - trailing_cagr        # short minus long horizon
      forward_cagr  = (fwd/recent)^(1/W) - 1                   # the OUTCOME
    """
    by_proj = defaultdict(list)
    for x in txns:
        by_proj[(x["project"], x["district"])].append(x)

    # district recent-window median (for the value/psf-vs-district feature)
    district_psf = defaultdict(list)
    for x in txns:
        if split - 1 < x["t"] <= split and x["sale_type"] in ("Resale", "Sub Sale"):
            district_psf[x["district"]].append(x["psf"])
    district_med = {d: _median(v) for d, v in district_psf.items() if v}

    rows = []
    for (proj, dist), ts in by_proj.items():
        band = None
        if size_control:
            # dominant resale sqft band before T -> compare like-for-like sizes
            sq = [x["sqft"] for x in ts if x["sqft"] and x["t"] <= split
                  and x["sale_type"] in ("Resale", "Sub Sale")]
            if sq:
                m = float(np.median(sq))
                band = (m * 0.75, m * 1.25)

        old, n_old = _win_median(ts, split - 3, split - 2, band)
        mid, n_mid = _win_median(ts, split - 2, split - 1, band)
        fwd, n_fwd = _win_median(ts, split + window - 1, split + window, band)

        # "Price at T" from the recent window. In split-sample mode, estimate it
        # from two disjoint halves: recent_feat feeds the FEATURES, recent_out is
        # the OUTCOME base. This removes the shared-estimation-noise artifact that
        # spuriously pushes trailing_cagr / psf_vs_dist correlations negative.
        rec_vals = _win_vals(ts, split - 1, split, band)
        n_rec = len(rec_vals)
        if n_rec < min_txn or not fwd or n_fwd < min_txn:
            continue
        if split_sample and n_rec >= 2 * min_txn:
            a, b = rec_vals[0::2], rec_vals[1::2]   # interleaved halves (balanced in time)
            recent_feat, recent_out = _median(a), _median(b)
        else:
            recent_feat = recent_out = _median(rec_vals)
        recent = recent_feat

        forward_cagr = (fwd / recent_out) ** (1.0 / window) - 1.0

        trailing_cagr = None
        if old and n_old >= min_txn:
            trailing_cagr = (recent / old) ** (1.0 / 2.0) - 1.0
        momentum = None
        if mid and n_mid >= min_txn and trailing_cagr is not None:
            momentum = (recent / mid - 1.0) - trailing_cagr

        # trailing 2yr resale volume (liquidity)
        vol = sum(1 for x in ts if split - 2 < x["t"] <= split
                  and x["sale_type"] in ("Resale", "Sub Sale"))
        # new-sale share in trailing 2yr (new-launch-bias / youth proxy)
        recent_all = [x for x in ts if split - 2 < x["t"] <= split]
        ns = sum(1 for x in recent_all if x["sale_type"] == "New Sale")
        ns_share = ns / len(recent_all) if recent_all else None

        dmed = district_med.get(dist)
        psf_vs_dist = (recent / dmed - 1.0) if dmed else None
        tenure = (ts[0]["tenure"] or "").lower()
        freehold = 1 if ("freehold" in tenure or "999" in tenure) else 0

        rows.append({
            "project": proj, "district": dist,
            "trailing_cagr": trailing_cagr,
            "momentum": momentum,
            "psf_level": recent,
            "psf_vs_dist": psf_vs_dist,
            "txn_vol": vol,
            "new_sale_share": ns_share,
            "freehold": freehold,
            "forward_cagr": forward_cagr,
            "n_recent": n_rec, "n_fwd": n_fwd,
        })
    return rows


# ---- analysis helpers -------------------------------------------------------

def _spearman(x, y):
    """Spearman rho via Pearson on ranks. Returns (rho, n)."""
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None
             and not (isinstance(a, float) and math.isnan(a))]
    n = len(pairs)
    if n < 8:
        return None, n
    xs = np.array([p[0] for p in pairs], float)
    ys = np.array([p[1] for p in pairs], float)
    rx = xs.argsort().argsort().astype(float)
    ry = ys.argsort().argsort().astype(float)
    if rx.std() == 0 or ry.std() == 0:
        return None, n
    rho = float(np.corrcoef(rx, ry)[0, 1])
    return rho, n


def _quintiles(rows, feat, outcome="forward_cagr", q=5):
    vals = [(r[feat], r[outcome]) for r in rows
            if r.get(feat) is not None and r.get(outcome) is not None]
    if len(vals) < q * 3:
        return None
    vals.sort(key=lambda v: v[0])
    n = len(vals)
    out = []
    for i in range(q):
        chunk = vals[i * n // q:(i + 1) * n // q]
        fv = [c[0] for c in chunk]
        ov = [c[1] for c in chunk]
        out.append({
            "q": i + 1, "n": len(chunk),
            "feat_lo": min(fv), "feat_hi": max(fv),
            "fwd_mean": float(np.mean(ov)), "fwd_median": float(np.median(ov)),
        })
    return out


def _fmt_pct(x):
    return f"{x*100:+.1f}%" if x is not None else "  n/a"


def run_report(splits, window, min_txn, size_control, split_sample=False, dump=None):
    txns = load_txns()
    print(f"Loaded {len(txns):,} URA txns "
          f"({sum(1 for t in txns if t['sale_type'] in ('Resale','Sub Sale')):,} resale).")
    print(f"Window={window}yr forward, min_txn/window={min_txn}, "
          f"size_control={size_control}, split_sample={split_sample}\n")

    features = ["trailing_cagr", "momentum", "psf_vs_dist", "txn_vol",
                "new_sale_share", "freehold"]

    all_corr = defaultdict(list)
    last_rows = None
    for split in splits:
        rows = build_panel(txns, split, window, min_txn, size_control, split_sample)
        last_rows = rows
        if len(rows) < 20:
            print(f"== T={split:.2f}: only {len(rows)} projects in-sample, skipping ==\n")
            continue
        fwd = [r["forward_cagr"] for r in rows]
        print(f"== Split T={split:.2f}  (forward {split:.2f}->{split+window:.2f}) "
              f"| {len(rows)} projects ==")
        print(f"   forward annual resale-PSF return: "
              f"mean {_fmt_pct(float(np.mean(fwd)))}  "
              f"median {_fmt_pct(float(np.median(fwd)))}  "
              f"sd {np.std(fwd)*100:.1f}pp")
        print(f"   {'feature':<16}{'spearman_rho':>14}{'n':>7}")
        for f in features:
            rho, n = _spearman([r[f] for r in rows], fwd)
            if rho is not None:
                all_corr[f].append(rho)
            print(f"   {f:<16}{(f'{rho:+.3f}' if rho is not None else 'n/a'):>14}{n:>7}")
        # headline quintile table: the algo's biggest bet
        for f in ("trailing_cagr", "momentum"):
            qt = _quintiles(rows, f)
            if qt:
                print(f"\n   {f} quintiles -> forward return:")
                for b in qt:
                    print(f"     Q{b['q']} n={b['n']:>3}  "
                          f"{f} [{_fmt_pct(b['feat_lo'])},{_fmt_pct(b['feat_hi'])}]"
                          f"   fwd mean {_fmt_pct(b['fwd_mean'])} "
                          f"median {_fmt_pct(b['fwd_median'])}")
        print()

    if len(splits) > 1 and all_corr:
        print("== Stability across splits (mean Spearman rho) ==")
        for f in features:
            cs = all_corr.get(f, [])
            if cs:
                print(f"   {f:<16}{np.mean(cs):+.3f}   "
                      f"(per-split: {', '.join(f'{c:+.2f}' for c in cs)})")
        print()

    if dump and last_rows:
        keys = list(last_rows[0].keys())
        with open(dump, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(last_rows)
        print(f"Dumped {len(last_rows)} rows -> {dump}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", type=float, action="append",
                    help="split year(s), e.g. 2023.99. Repeatable. Default: walk-forward set.")
    ap.add_argument("--window", type=float, default=2.0, help="forward window (years)")
    ap.add_argument("--min-txn", type=int, default=5, help="min resale txns per window")
    ap.add_argument("--size-control", action="store_true",
                    help="stratify PSF to project's dominant sqft band (composition-bias check)")
    ap.add_argument("--split-sample", action="store_true",
                    help="estimate price-at-T from disjoint txn halves (removes mean-reversion artifact)")
    ap.add_argument("--dump", help="write last-split per-project rows to CSV")
    args = ap.parse_args()

    splits = args.split or [2023.75, 2024.0, 2024.25]
    run_report(splits, args.window, args.min_txn, args.size_control,
               args.split_sample, args.dump)


if __name__ == "__main__":
    main()
