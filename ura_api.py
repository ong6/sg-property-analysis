#!/usr/bin/env python3
"""URA data through the official API (via sgprop), no browser.

Replaces the headed-browser downloads in fetch_ura_districts.py and
fetch_ura_rentals.py. sgprop (github.com/ong6/sgprop) syncs every private
residential sale (5 yrs) and rental contract into its own store in ~40 s, then
writes the SAME per-district CSVs the browser produced, so every parser here
(parse_ura_csv, build_rental_cache, bed_bands) reads them unchanged.

Verified 2026-09-24: API transactions matched the eservice CSVs row for row
(D19 9,724/9,724, D20 2,669/2,669). The eservice rental CSV was silently
capped at 10,000 rows per district (D19 has 18,004 in the same window), so
rental medians and bed bands were built on a truncated sample until this.

The key never lives in this (public) repo: sgprop reads URA_ACCESS_KEY from
the environment or ~/.config/sgprop/credentials.

    python ura_api.py                 # sync + export + rebuild all caches
    python ura_api.py --check         # is the API path usable here?
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
ALL_DISTRICTS = list(range(1, 29))
# The browser fetch pulled rental contracts for roughly the last three years;
# keep the same window so rental medians don't shift just from a longer tail.
RENTAL_QUARTERS = 12


def available() -> tuple[bool, str]:
    """(usable, why-not). Usable = sgprop importable AND a URA key configured."""
    try:
        from sgprop import config
    except ImportError:
        return False, "sgprop not installed (pip install -r requirements.txt)"
    try:
        config.credential("URA_ACCESS_KEY")
    except config.MissingCredential as e:
        return False, str(e)
    return True, ""


def refresh(transactions: bool = True, rentals: bool = True, force: bool = False,
            rebuild: bool = True) -> dict:
    """Sync from URA, export district CSVs, and rebuild the derived caches.

    Sync is a no-op when sgprop's store is already newer than URA's last
    publish (Tue/Fri for sales, the 15th for rentals), but the CSV export
    always runs: it is ~2 s and keeps data/ in step with the store.
    """
    from sgprop import export
    from sgprop.store import Store
    from sgprop.sync import sync_rentals, sync_transactions
    from sgprop.ura import UraClient

    store, client = Store(), UraClient()
    out: dict = {}
    if transactions:
        out["transactions"] = sync_transactions(store, client, force=force)
        out["tx_rows"] = sum(
            export.transactions_csv(store, d, DATA_DIR / f"ura_district_D{d:02d}.csv")
            for d in ALL_DISTRICTS)
        if rebuild:
            from fetch_ura_districts import build_cache_from_district_csvs
            out["ura_cache_projects"] = len(build_cache_from_district_csvs(None))
    if rentals:
        out["rentals"] = sync_rentals(store, client, quarters=RENTAL_QUARTERS, force=force)
        out["rent_rows"] = sum(
            export.rentals_csv(store, d, DATA_DIR / f"ura_rental_D{d:02d}.csv")
            for d in ALL_DISTRICTS)
        if rebuild:
            for script in (["build_rental_cache.py"], ["bed_bands.py", "--build"]):
                subprocess.run([sys.executable, str(BASE / script[0]), *script[1:]],
                               cwd=BASE, check=True, stdout=subprocess.DEVNULL)
            out["rebuilt"] = ["rental_cache.json", "bed_bands.json"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore URA's publish schedule")
    ap.add_argument("--no-rentals", action="store_true")
    ap.add_argument("--no-transactions", action="store_true")
    a = ap.parse_args()
    ok, why = available()
    if a.check or not ok:
        print("URA API: ready" if ok else f"URA API unavailable: {why}")
        return 0 if ok else 1
    print(json.dumps(refresh(transactions=not a.no_transactions,
                             rentals=not a.no_rentals, force=a.force), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
