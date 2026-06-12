#!/usr/bin/env python3
"""Fetch URA private-residential RENTAL CONTRACTS by postal district (D01-D28).

Sibling of fetch_ura_districts.py, pointed at the rental-contracts search of the
same URA Property Market Information portal. Rental contracts give REAL
per-project rents (monthly gross rent, bedroom count, floor-area band, lease
commencement date) — the missing series that makes the MMR `yield` component
testable (district median_rental_psf / PSF is just inverse-PSF, i.e. the region
effect in disguise; see docs/RELEASES.md v3.5).

Data source: https://eservice.ura.gov.sg/property-market-information/pmiResidentialRentalSearch
Updated monthly on the 15th; covers contracts reported in the last 60 months.

Usage:
    python fetch_ura_rentals.py                       # all 28 districts
    python fetch_ura_rentals.py --districts 15,19     # specific districts
    python fetch_ura_rentals.py --districts 15 --force
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
URA_RENTAL_URL = "https://eservice.ura.gov.sg/property-market-information/pmiResidentialRentalSearch"

ALL_DISTRICTS = list(range(1, 29))


def rental_csv_path(district: int, suffix: str = "") -> Path:
    return DATA_DIR / f"ura_rental_D{district:02d}{suffix}.csv"


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _set_lease_range(page, date_from: str | None, date_to: str | None):
    """Set the lease-commencement range dropdowns ('YYYY-MM' strings).

    The CSV export is capped at 10,000 rows (most-recent first), so a busy
    district's default 60-month window silently loses the older months — a
    point-in-time (as-of-T) rent series needs an explicit historical range."""
    def parts(s):
        y, m = s.split("-")
        return y, _MONTHS[int(m) - 1]

    if date_from:
        y, m = parts(date_from)
        page.get_by_role("combobox", name="Lease Commencement Year From").select_option(label=y)
        page.wait_for_timeout(150)
        page.get_by_role("combobox", name="Lease Commencement Month From").select_option(label=m)
        page.wait_for_timeout(150)
    if date_to:
        y, m = parts(date_to)
        page.get_by_role("combobox", name="Lease Commencement Year To").select_option(label=y)
        page.wait_for_timeout(150)
        page.get_by_role("combobox", name="Lease Commencement Month To").select_option(label=m)
        page.wait_for_timeout(150)


def is_fresh(csv_path: Path, max_age_days: int) -> bool:
    if not csv_path.exists():
        return False
    mtime = datetime.fromtimestamp(csv_path.stat().st_mtime)
    return (datetime.now() - mtime).days <= max_age_days


def fetch_rental_district(page, district: int, suffix: str = "",
                          date_from: str | None = None,
                          date_to: str | None = None) -> tuple[str | None, str | None]:
    """Fetch URA rental-contract CSV for one postal district.

    The rental search uses the same popup UI as the transaction search
    (fetch_ura_districts.fetch_district): "Postal District" tab, district
    checkboxes, compulsory property type, Search, then a downloadCSV link.
    Returns (csv_content, error_message); one of them is None.
    """
    district_str = f"D{district:02d}"
    try:
        page.goto(URA_RENTAL_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        page.locator("button.search-popup-btn").click()
        page.wait_for_timeout(1000)

        postal_tab = page.locator('a[href="#postalDistrict"]')
        if postal_tab.count() == 0:
            postal_tab = page.locator('a:has-text("Postal District")')
        if postal_tab.count() == 0:
            return None, "Could not find 'Postal District' tab in popup"
        postal_tab.first.click()
        page.wait_for_timeout(500)

        cb = page.locator('input[type="checkbox"]').filter(
            has=page.locator(f'text=/{district_str} \\//'),
        )
        if cb.count() == 0:
            container = page.locator(f'text=/{district_str} \\//')
            if container.count() > 0:
                container.first.click()
                page.wait_for_timeout(300)
            else:
                for val in [district_str, f"{district:02d}", str(district)]:
                    cb = page.locator(f'input[type="checkbox"][value="{val}"]')
                    if cb.count() > 0:
                        cb.first.click()
                        page.wait_for_timeout(300)
                        break
                else:
                    return None, f"Could not find checkbox for {district_str}"
        else:
            cb.first.click()
            page.wait_for_timeout(300)

        page.locator('button:has-text("Apply"):visible').click()
        page.wait_for_timeout(500)

        # Compulsory property type for postal-district searches. The rental
        # search labels condos "Non-Landed Housing Development" (the
        # transaction search calls them "Apartments & Condominiums").
        page.locator('select').filter(
            has=page.locator('option:has-text("Non-Landed")')
        ).first.select_option(label="Non-Landed Housing Development")
        page.wait_for_timeout(300)

        _set_lease_range(page, date_from, date_to)

        page.locator('button.btn-primary:has-text("Search")').click()
        page.wait_for_timeout(3000)

        try:
            page.wait_for_selector("text=/Showing \\d+/", timeout=30000)
        except Exception:
            no_results = page.locator('text=/No results|No rental|No contract/i')
            if no_results.count() > 0:
                return None, f"No results for {district_str}"
            return None, f"Timeout waiting for results for {district_str}"

        result_text = page.locator("text=/Showing \\d+/").first.inner_text()
        print(f"  {result_text}", file=sys.stderr)

        csv_path = str(rental_csv_path(district, suffix))
        with page.expect_download(timeout=30000) as dl_info:
            page.evaluate("document.querySelector('a.downloadCSV').scrollIntoView()")
            page.wait_for_timeout(500)
            page.evaluate("document.querySelector('a.downloadCSV').click()")
        dl_info.value.save_as(csv_path)

        with open(csv_path) as f:
            return f.read(), None
    except Exception as e:
        return None, str(e)


def main():
    parser = argparse.ArgumentParser(description="Fetch URA rental contracts by postal district")
    parser.add_argument("--districts", "-d", type=str,
                        help="Comma-separated district numbers (default: all 28)")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-age", type=int, default=30)
    parser.add_argument("--from", dest="date_from", type=str, default=None,
                        help="Lease commencement from, YYYY-MM (export caps at "
                             "10k most-recent rows — set a range for history)")
    parser.add_argument("--to", dest="date_to", type=str, default=None,
                        help="Lease commencement to, YYYY-MM")
    args = parser.parse_args()

    suffix = ""
    if args.date_from or args.date_to:
        suffix = f"_{(args.date_from or 'start').replace('-','')}_{(args.date_to or 'now').replace('-','')}"

    districts = ([int(d.strip()) for d in args.districts.split(",")]
                 if args.districts else ALL_DISTRICTS)
    to_fetch = [d for d in districts
                if args.force or not is_fresh(rental_csv_path(d, suffix), args.max_age)]

    print("\nURA Rental Fetch", file=sys.stderr)
    print(f"  Districts requested: {districts}", file=sys.stderr)
    print(f"  To fetch: {len(to_fetch)}", file=sys.stderr)
    if not to_fetch:
        print("All districts cached and fresh (use --force to re-fetch).", file=sys.stderr)
        return

    from playwright.sync_api import sync_playwright
    DATA_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()
        ok = fail = 0
        for i, district in enumerate(to_fetch, 1):
            print(f"\n[{i}/{len(to_fetch)}] District D{district:02d}", file=sys.stderr)
            csv_content, error = fetch_rental_district(
                page, district, suffix, args.date_from, args.date_to)
            if csv_content:
                n_rows = max(0, csv_content.count("\n") - 1)
                print(f"  -> {n_rows} rental contracts saved to {rental_csv_path(district, suffix)}",
                      file=sys.stderr)
                ok += 1
            else:
                print(f"  -> FAILED: {error}", file=sys.stderr)
                fail += 1
            if i < len(to_fetch):
                time.sleep(2)
        browser.close()

    print(f"\nDone: {ok} fetched, {fail} failed", file=sys.stderr)


if __name__ == "__main__":
    main()
