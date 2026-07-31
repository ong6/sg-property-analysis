"""Forward-calibration of SHIPPED scores: mmr_history.csv -> realized URA PSF.

backtest.py / backtest_ext.py validate score *components* against the URA panel.
This harness closes the remaining loop (see docs/RELEASES.md open items): it takes the
actual score_1000 snapshots the system emitted (data/mmr_history.csv, append-only,
one row per listing per scoring run) and asks whether they ranked realized
forward outcomes — per project, measured on the URA resale-PSF series.

Method (per scoring cohort = score_version x snapshot month):
  1. For each distinct (project, district) in the cohort, take the listing-level
     score_1000 median as the project's shipped score at T0.
  2. Baseline PSF  = project's median URA resale PSF in the 12mo window ending T0.
  3. Outcome PSF   = median resale PSF in the trailing 12mo window ending at the
     latest URA data date T1 (resale + sub-sale only, en-bloc excluded upstream).
  4. forward_ret   = annualized (outcome/baseline) over (T1-T0); requires
     T1-T0 >= --min-window years (FLOOR 1.0 — the 12mo baseline window ending
     T0 and the 12mo outcome window ending T1 must not overlap, or the
     "forward return" partially measures the baseline itself).
  5. Report Spearman(score, forward_ret) + score-quintile forward means, and the
     same for the rating tiers (>=650 / 450-650 / <450).

Cohorting (Jun-2026 audit P0-2): cohorts are keyed (score_version, month) from
mmr_history.csv's `score_version` column (stamped by config.score_version()).
Rows tagged "pre-3.10" predate the stamp and BLEND five config versions
(v3.5b-v3.9; same-listing drift median 178 pts, 93% tier-changing) — they form
their own labeled cohort and any read off it gets a loud warning.

REGISTERED HOLDOUT PROTOCOL (Jun-2026 audit P0-1): the v3.10 weights were
locked on 2026-06-12 against URA data through 2026-06. Every URA transaction
dated AFTER 2026-06 is an untouched temporal test set for those weights —
nothing in the panel after that date may be used to re-tune before this
harness has scored it. The first valid read is the v3.10 cohort with a >=1yr
forward window (~mid-2027). If weights change before then, the new
score_version starts its own clock; the old cohort still scores the old
weights honestly.

Name join: history project names are normalized the same way the scorer joins
URA prints (see scoring/full_scorer._pu_normalize) and matched to the URA
panel per district; the join rate + unjoined samples are reported every run
(the raw exact-string join silently matched only ~58% of keys).

Scores started 2026-06 — until mid-2027 this prints "insufficient forward
window" and exits. Re-run quarterly; it needs zero new wiring (mmr_history and
the district CSVs both append automatically).

Usage:
  python calibrate_forward.py                  # all cohorts with enough window
  python calibrate_forward.py --min-window 1.5 # require longer windows
"""

import argparse
import csv
import re
from collections import defaultdict

import numpy as np

import backtest as bt

# Mixed-config legacy tag: rows scored before config.score_version() existed.
PRE_STAMP = "pre-3.10"

# Fingerprints that denote the SAME calibration under different digests.
#
# score_version is CONFIG_VERSION + a hash of config.py. On 2026-07-29 the hash
# INPUT changed — full-line comments and blank lines are now stripped before the
# digest, so that documenting a constant no longer restamps every scored listing
# (commit 0ddcdbe). No weight, threshold or gate moved: the reference listing
# scores 583 / 1514.8 either side of it. But the digest moved anyway, from
# 0e388936 to ba8d8abe, which would split the registered v3.12 cohort — 9,029
# listings snapshotted 2026-07-28, whose first valid forward read is ~2027-08 —
# into two half-size cohorts of identical calibration.
#
# Aliasing them keeps that read at full n. This map is ONLY for digest changes
# proven score-identical; a genuine recalibration must always start its own
# clock, which is the whole point of the fingerprint.
VERSION_ALIASES = {
    "3.12+ba8d8abe": "3.12+0e388936",   # comment-stripping hash change, scores identical
}


def canonical_version(version: str) -> str:
    """Collapse known score-identical fingerprints onto the cohort that owns
    the holdout clock."""
    return VERSION_ALIASES.get(version, version)


def _pu_normalize(name: str) -> str:
    """Conservative name normalization for the URA join — apostrophe variants,
    '@' vs ' at ', punctuation/whitespace.

    REPLICA of the canonical copy in scoring/full_scorer._pu_normalize (kept
    inline so this harness never imports the scorer stack; keep the two in
    sync). The old raw exact-upper join matched only ~58% of keys, silently.
    """
    s = (name or "").lower().strip()
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"\s*@\s*", " at ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


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
            version = canonical_version(
                (r.get("score_version") or "").strip() or PRE_STAMP)
            rows.append({
                "t": t,
                # cohort = (score_version, month): scores from different
                # configs are never pooled into one calibration read
                "cohort": (version, r["scored_at"][:7]),
                "version": version,
                "project": r["project_name"].strip().upper(),
                "district": d,
                "score": score,
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-window", type=float, default=1.0,
                    help="min years between scoring and outcome (floor 1.0: the "
                         "12mo baseline and 12mo outcome windows must not overlap)")
    ap.add_argument("--min-txn", type=int, default=5)
    args = ap.parse_args()
    if args.min_window < 1.0:
        print(f"--min-window {args.min_window} raised to the 1.0yr floor: the 12mo "
              f"baseline window (ending T0) and the 12mo outcome window (ending T1) "
              f"would overlap, making the 'forward return' partly measure the "
              f"baseline itself.")
        args.min_window = 1.0

    txns = bt.load_txns()
    t_latest = max(x["t"] for x in txns)
    by_proj = defaultdict(list)
    for x in txns:
        by_proj[(x["project"], x["district"])].append(x)
    # normalized-name index per district (same normalization as the scorer's
    # URA joins); first writer wins on the rare collision
    ura_norm = {}
    for proj, dist in by_proj:
        ura_norm.setdefault((_pu_normalize(proj), dist), (proj, dist))

    def _join(key):
        """history (PROJECT, district) -> URA panel key, exact then normalized."""
        if key in by_proj:
            return key
        return ura_norm.get((_pu_normalize(key[0]), key[1]))

    hist = load_history()
    if not hist:
        print("mmr_history.csv has no usable rows")
        return
    print(f"Loaded {len(hist):,} score snapshots; URA data through {t_latest:.2f}")

    # ---- join-rate report (every run, even when all cohorts are skipped) ----
    all_keys = {(r["project"], r["district"]) for r in hist}
    unjoined = sorted({k[0] for k in all_keys if _join(k) is None})
    joined_n = len(all_keys) - len(unjoined)
    print(f"URA name join: {joined_n}/{len(all_keys)} distinct (project, district) "
          f"keys matched ({joined_n / len(all_keys):.0%}) "
          f"[normalized join, see _pu_normalize]")
    if unjoined:
        print(f"  unjoined sample ({len(unjoined)} names): "
              f"{', '.join(unjoined[:8])}{' …' if len(unjoined) > 8 else ''}")
    print()

    cohorts = defaultdict(list)
    for r in hist:
        cohorts[r["cohort"]].append(r)

    any_run = False
    for cohort in sorted(cohorts):
        version, month = cohort
        label = f"{month} [{version}]"
        rows = cohorts[cohort]
        if version == PRE_STAMP:
            print(f"cohort {label}: WARNING — rows predate the score_version "
                  f"stamp and BLEND five config versions (v3.5b-v3.9; "
                  f"same-listing drift median 178 pts). Any rank read off this "
                  f"cohort mixes models — labeled legacy, not a calibration of "
                  f"any one config.")
        t0 = float(np.median([r["t"] for r in rows]))
        window = t_latest - t0
        if window < args.min_window:
            print(f"cohort {label}: forward window {window*12:.1f}mo "
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
        n_unjoined = 0
        for key, ss in proj_scores.items():
            jkey = _join(key)
            ts = by_proj.get(jkey) if jkey else None
            if not ts:
                n_unjoined += 1
                continue
            t0p = proj_t0[key]
            base, n0 = bt._win_median(ts, t0p - 1.0, t0p)
            out, n1 = bt._win_median(ts, t_latest - 1.0, t_latest)
            if not base or not out or n0 < args.min_txn or n1 < args.min_txn:
                continue
            scores.append(float(np.median(ss)))
            rets.append((out / base) ** (1.0 / (t_latest - t0p)) - 1.0)

        rho, n = bt._spearman(scores, rets)
        print(f"\n== cohort {label}  (T0={t0:.2f}, window {window:.2f}yr, "
              f"{n} projects matched, {n_unjoined} name-join misses) ==")
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
