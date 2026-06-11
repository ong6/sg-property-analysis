"""Forward-calibration of SHIPPED scores: mmr_history.csv -> realized URA PSF.

backtest.py / backtest_ext.py validate score *components* against the URA panel.
This harness closes the remaining loop (IMPROVEMENT_PLAN gap #3): it takes the
actual score_1000 snapshots the system emitted (data/mmr_history.csv, append-only,
one row per listing per scoring run) and asks whether they ranked realized
forward outcomes — per project, measured on the URA resale-PSF series.

Method (per scoring cohort = snapshot month):
  1. For each distinct (project, district) in the cohort, take the listing-level
     score_1000 median as the project's shipped score at T0.
  2. Baseline PSF  = project's median URA resale PSF in the 12mo window ending T0.
  3. Outcome PSF   = median resale PSF in the trailing 12mo window ending at the
     latest URA data date T1 (resale + sub-sale only, en-bloc excluded upstream).
  4. forward_ret   = annualized (outcome/baseline) over (T1-T0); requires
     T1-T0 >= --min-window years (default 0.75) else the cohort is SKIPPED.
  5. Report Spearman(score, forward_ret) + score-quintile forward means, and the
     same for the rating tiers (>=650 / 450-650 / <450).

Scores started 2026-06 — until mid-2027 this prints "insufficient forward
window" and exits. Re-run quarterly; it needs zero new wiring (mmr_history and
the district CSVs both append automatically).

Usage:
  python calibrate_forward.py                  # all cohorts with enough window
  python calibrate_forward.py --min-window 0.5 # accept shorter windows (noisier)
"""

import argparse
import csv
from collections import defaultdict

import numpy as np

import backtest as bt


def load_history(path="data/mmr_history.csv"):
    rows = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            try:
                score = float(r["score_1000"])
            except (KeyError, ValueError):
                continue
            d = (r.get("district") or "").replace("D", "").lstrip("0") or None
            if not r.get("project_name") or not d:
                continue
            # scored_at "2026-06-07" -> float year
            try:
                y, m, dd = r["scored_at"].split("-")
                t = int(y) + (int(m) - 1 + (int(dd) - 0.5) / 30.4) / 12.0
            except (KeyError, ValueError):
                continue
            rows.append({
                "t": t,
                "cohort": r["scored_at"][:7],
                "project": r["project_name"].strip().upper(),
                "district": d,
                "score": score,
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-window", type=float, default=0.75,
                    help="min years between scoring and outcome (default 0.75)")
    ap.add_argument("--min-txn", type=int, default=5)
    args = ap.parse_args()

    txns = bt.load_txns()
    t_latest = max(x["t"] for x in txns)
    by_proj = defaultdict(list)
    for x in txns:
        by_proj[(x["project"], x["district"])].append(x)

    hist = load_history()
    if not hist:
        print("mmr_history.csv has no usable rows")
        return
    print(f"Loaded {len(hist):,} score snapshots; URA data through {t_latest:.2f}\n")

    cohorts = defaultdict(list)
    for r in hist:
        cohorts[r["cohort"]].append(r)

    any_run = False
    for cohort in sorted(cohorts):
        rows = cohorts[cohort]
        t0 = float(np.median([r["t"] for r in rows]))
        window = t_latest - t0
        if window < args.min_window:
            print(f"cohort {cohort}: forward window {window*12:.1f}mo "
                  f"< {args.min_window*12:.0f}mo — insufficient, skipped "
                  f"(re-run after {'%.2f' % (t0 + args.min_window)})")
            continue
        any_run = True

        # project-level shipped score (median across that cohort's listings)
        proj_scores = defaultdict(list)
        proj_t0 = {}
        for r in rows:
            key = (r["project"], r["district"])
            proj_scores[key].append(r["score"])
            # earliest scoring date for THIS project — the baseline window must
            # end at it, not at the cohort median, or projects scored early in
            # the cohort get post-score transactions in their baseline
            # (lookahead).
            proj_t0[key] = min(proj_t0.get(key, r["t"]), r["t"])

        scores, rets = [], []
        for key, ss in proj_scores.items():
            ts = by_proj.get(key)
            if not ts:
                continue
            t0p = proj_t0[key]
            base, n0 = bt._win_median(ts, t0p - 1.0, t0p)
            out, n1 = bt._win_median(ts, t_latest - 1.0, t_latest)
            if not base or not out or n0 < args.min_txn or n1 < args.min_txn:
                continue
            scores.append(float(np.median(ss)))
            rets.append((out / base) ** (1.0 / (t_latest - t0p)) - 1.0)

        rho, n = bt._spearman(scores, rets)
        print(f"\n== cohort {cohort}  (T0={t0:.2f}, window {window:.2f}yr, "
              f"{n} projects matched) ==")
        if rho is None:
            print("   too few matched projects")
            continue
        print(f"   Spearman(score_1000, realized fwd return) = {rho:+.3f}")
        pairs = sorted(zip(scores, rets))
        q = 5
        print("   score quintiles -> realized annualized return:")
        for i in range(q):
            chunk = pairs[i * len(pairs) // q:(i + 1) * len(pairs) // q]
            if not chunk:
                continue
            s = [c[0] for c in chunk]
            f = [c[1] for c in chunk]
            print(f"     Q{i+1} score[{min(s):4.0f},{max(s):4.0f}]  "
                  f"fwd {np.mean(f)*100:+.2f}%/yr  n={len(chunk)}")
        # tier read (the copy the rubric ships: 650+ recommended / <450 below)
        for name, lo, hi in (("score>=650", 650, 9999), ("450-650", 450, 650),
                             ("score<450", -1, 450)):
            f = [r_ for s_, r_ in zip(scores, rets) if lo <= s_ < hi]
            if f:
                print(f"   tier {name:<11} fwd {np.mean(f)*100:+.2f}%/yr  n={len(f)}")

    if not any_run:
        print("\nNo cohort has aged enough yet — nothing to calibrate. "
              "The harness is wired; re-run quarterly.")


if __name__ == "__main__":
    main()
