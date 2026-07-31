#!/usr/bin/env python3
"""Rank the algorithms on a panel, and write the result somewhere auditable.

    python -m validation.run                    # DEV
    python -m validation.run --split val        # VAL — logged, look rarely
    python -m validation.run --both             # both + the degradation ratio

Every algorithm is scored on the SAME rows. That is not tidiness: the Jun-2026
audit caught a "config beats optimal" read that was pure sample composition —
one scorer evaluated on all rows, the other on complete cases only. Algorithms
abstain on rows they cannot score, so the comparison runs on the intersection
and reports what that cost.
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime

import config
from validation import algos, metrics, panel

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(BASE, "data", "validation_results.csv")

FIELDS = ["run_date", "panel_id", "split", "window", "algo", "is_baseline", "n",
          "rho", "rho_lo", "rho_hi", "significant", "decile_lift",
          "monotone_frac", "hit_rate", "hit_p", "base_rate", "score_version"]


def _common_rows(rows, names):
    """Rows every named algorithm can score."""
    keys = None
    for name in names:
        kept, _ = algos.score_panel(name, rows)
        k = {(r["project"], r["district"]) for r in kept}
        keys = k if keys is None else (keys & k)
    return [r for r in rows if (r["project"], r["district"]) in (keys or set())]


def evaluate_all(rows, names=None, reps=400):
    names = names or list(algos.ALGOS)
    common = _common_rows(rows, names)
    out = {}
    for name in names:
        kept, scores = algos.score_panel(name, common)
        out[name] = metrics.evaluate(kept, scores, reps=reps)
        out[name]["is_baseline"] = name in algos.BASELINES
    return common, out


def _fmt(v, spec="+.3f"):
    return "—" if v is None else format(v, spec)


def render(results: dict, pid: str, dropped: int, total: int) -> str:
    L = [f"panel {pid} · {total - dropped}/{total} rows scoreable by every algorithm"]
    if dropped:
        L.append(f"  ({dropped} dropped so all algorithms are compared on identical rows)")
    L.append("")
    L.append(f"{'algorithm':21} {'rho':>7} {'95% CI':>18} {'sig':>4} "
             f"{'dec.lift':>9} {'mono':>5} {'hit@25':>7} {'p':>6}")
    L.append("-" * 83)
    for name, m in results.items():
        d, h = m.get("decile") or {}, m.get("hit") or {}
        tag = "  (baseline)" if m["is_baseline"] else ""
        L.append(
            f"{name:21} {_fmt(m['rho']):>7} "
            f"{'[' + _fmt(m['rho_lo']) + ',' + _fmt(m['rho_hi']) + ']':>18} "
            f"{'yes' if m['rho_significant'] else 'no':>4} "
            f"{_fmt(d.get('lift'), '+.4f'):>9} "
            f"{_fmt(d.get('monotone_frac'), '.2f'):>5} "
            f"{_fmt(h.get('hit_rate'), '.2f'):>7} "
            f"{_fmt(h.get('p_value'), '.3f'):>6}{tag}")
    base = results.get("psf_vs_dist", {}).get("rho")
    L.append("")
    L.append("The bar is psf_vs_dist, not zero: an assembled model that cannot beat")
    L.append("single-feature cheapness has added complexity and nothing else.")
    if base is not None:
        for name, m in results.items():
            if not m["is_baseline"] and m.get("rho") is not None:
                verdict = "beats" if m["rho"] > base else "does NOT beat"
                L.append(f"  {name}: rho {m['rho']:+.3f} {verdict} psf_vs_dist ({base:+.3f})")
    return "\n".join(L)


def append_ledger(results: dict, pid: str, split: float, window: float):
    """Append-only, one row per (run, panel, algorithm).

    Sibling of mmr_history.csv and arena_results.csv: results are supposed to be
    graphable across months, and a harness whose numbers are only ever printed
    to a terminal cannot show whether the model got better or the panel did.
    """
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    new = not os.path.exists(LEDGER)
    with open(LEDGER, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for name, m in results.items():
            d, h = m.get("decile") or {}, m.get("hit") or {}
            w.writerow({
                "run_date": datetime.now().strftime("%Y-%m-%d"),
                "panel_id": pid, "split": f"{split:g}", "window": f"{window:g}",
                "algo": name, "is_baseline": int(m["is_baseline"]), "n": m["n"],
                "rho": m["rho"], "rho_lo": m["rho_lo"], "rho_hi": m["rho_hi"],
                "significant": int(bool(m["rho_significant"])),
                "decile_lift": d.get("lift"), "monotone_frac": d.get("monotone_frac"),
                "hit_rate": h.get("hit_rate"), "hit_p": h.get("p_value"),
                "base_rate": h.get("base_rate"),
                "score_version": config.score_version(),
            })


def run_split(split: float, window: float, min_txn: int, reps: int,
              write: bool = True) -> dict:
    rows = panel.build(split, window, min_txn)
    pid = panel.panel_id(rows, split, window, min_txn)
    common, results = evaluate_all(rows, reps=reps)
    print(render(results, pid, len(rows) - len(common), len(rows)))
    if write:
        append_ledger(results, pid, split, window)
        print(f"\nledger -> {os.path.relpath(LEDGER, BASE)}")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--split", choices=["dev", "val"], default="dev")
    ap.add_argument("--both", action="store_true")
    ap.add_argument("--window", type=float, default=panel.DEFAULT_WINDOW)
    ap.add_argument("--min-txn", type=int, default=panel.DEFAULT_MIN_TXN)
    ap.add_argument("--reps", type=int, default=400)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    if args.both:
        print("=== DEV (tune here) ===")
        dev = run_split(panel.DEV_SPLIT, args.window, args.min_txn, args.reps,
                        not args.no_write)
        print("\n=== VAL (look rarely) ===")
        val = run_split(panel.VAL_SPLIT, args.window, args.min_txn, args.reps,
                        not args.no_write)
        print("\n=== degradation (VAL rho / DEV rho; <0.6 flags overfit) ===")
        for name in dev:
            deg = metrics.degradation(dev[name], val[name])
            if deg is None:
                print(f"  {name:21} — (DEV rho too near zero to divide by)")
            else:
                print(f"  {name:21} {deg['ratio']:+.2f}"
                      f"{'   OVERFIT' if deg['overfit_flag'] else ''}")
    else:
        run_split(panel.DEV_SPLIT if args.split == "dev" else panel.VAL_SPLIT,
                  args.window, args.min_txn, args.reps, not args.no_write)

    print("\nBoth splits sit inside the 2021-2026 bull market. Passing VAL means")
    print("'not overfit to noise', never 'works' — only the prospective clock in")
    print("calibrate_forward.py tests that, first honest read ~2027-08.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
