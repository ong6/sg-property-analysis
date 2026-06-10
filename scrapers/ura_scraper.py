"""URA Transaction History Scraper.

Fetches historical transaction data from URA's Property Market Information system.
Uses Playwright automation since the URA site requires form submission with CSRF tokens.

Data available: 60 months of transaction history (5 years)
Source: https://eservice.ura.gov.sg/property-market-information/pmiResidentialTransactionSearch
"""

import csv
import io
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Optional


@dataclass
class URATransaction:
    """A single transaction from URA data."""
    project_name: str
    price: int
    area_sqft: float
    psf: float
    sale_date: str  # "Jan-26", "Dec-25" format
    street_name: str
    sale_type: str  # "New Sale", "Resale", "Sub Sale"
    tenure: str
    district: int
    market_segment: str  # "CCR", "RCR", "OCR"
    floor_level: str
    num_units: int = 1  # >1 = bulk/en-bloc sale (excluded from PSF metrics)

    @property
    def sale_year(self) -> int:
        """Extract year from sale_date (e.g., 'Jan-26' -> 2026)."""
        match = re.search(r'-(\d{2})$', self.sale_date)
        if match:
            year_short = int(match.group(1))
            return 2000 + year_short if year_short < 50 else 1900 + year_short
        return 0


@dataclass
class URATransactionHistory:
    """Transaction history for a project with calculated metrics."""
    project_name: str
    transactions: list[URATransaction]

    # Calculated metrics (all transactions)
    avg_psf_current_year: Optional[float] = None
    avg_psf_1yr_ago: Optional[float] = None
    avg_psf_3yr_ago: Optional[float] = None
    avg_psf_5yr_ago: Optional[float] = None
    appreciation_1yr: Optional[float] = None  # Percentage
    appreciation_3yr: Optional[float] = None
    appreciation_5yr: Optional[float] = None
    annualized_appreciation: Optional[float] = None
    transaction_count: int = 0

    # NEW: Resale-only metrics (excludes "New Sale" transactions)
    # These give a more accurate picture of genuine market appreciation
    # by filtering out developer-to-market price transitions.
    resale_annualized_appreciation: Optional[float] = None
    resale_avg_psf_current: Optional[float] = None
    resale_avg_psf_oldest: Optional[float] = None
    resale_transaction_count: int = 0
    new_sale_count: int = 0
    new_sale_proportion: float = 0.0  # Fraction of all transactions that are "New Sale"
    has_new_launch_bias: bool = False  # True if significant new sale proportion detected

    # v2.2: Momentum and data quality fields
    # Momentum: compares annualized rates across different time horizons.
    # Accelerating appreciation (1yr > 3yr > 5yr) = positive momentum.
    # Decelerating (1yr < 3yr < 5yr) = negative momentum.
    appreciation_momentum: Optional[float] = None  # -1.0 (decelerating) to +1.0 (accelerating)
    data_coverage: str = "none"  # "5yr", "3yr", "1yr", or "none" — longest reliable period

    def calculate_metrics(self):
        """Calculate appreciation metrics from transactions.

        Computes both overall metrics and resale-only metrics.
        The resale-only metrics filter out "New Sale" transactions to avoid
        the new-launch appreciation bias where developer pricing artificially
        inflates apparent CAGR.
        """
        if not self.transactions:
            return

        current_year = datetime.now().year
        self.transaction_count = len(self.transactions)

        # Separate transactions by sale type
        resale_txns = [t for t in self.transactions if t.sale_type in ("Resale", "Sub Sale")]
        new_sale_txns = [t for t in self.transactions if t.sale_type == "New Sale"]
        self.new_sale_count = len(new_sale_txns)
        self.resale_transaction_count = len(resale_txns)
        self.new_sale_proportion = (
            self.new_sale_count / self.transaction_count
            if self.transaction_count > 0 else 0.0
        )
        try:
            from config import NEW_SALE_PROPORTION_THRESHOLD
        except ImportError:
            NEW_SALE_PROPORTION_THRESHOLD = 0.20
        self.has_new_launch_bias = self.new_sale_proportion >= NEW_SALE_PROPORTION_THRESHOLD

        # --- Overall metrics (all transaction types) ---
        self._calculate_yearly_metrics(self.transactions, current_year)

        # --- Momentum and data coverage ---
        self._calculate_momentum()

        # --- Resale-only metrics ---
        self._calculate_resale_metrics(resale_txns, current_year)

    def _calculate_yearly_metrics(self, transactions: list, current_year: int):
        """Calculate year-based appreciation metrics from a set of transactions.

        Uses the MEDIAN PSF per year (robust to penthouse/ground-floor and
        fat-finger outliers — a single outlier in a sparse year used to swing
        the CAGR endpoint). Bulk/en-bloc rows and unparseable dates are
        excluded from the series.
        """
        if not transactions:
            return

        # Group transactions by year
        by_year: dict[int, list[float]] = {}
        for txn in transactions:
            year = txn.sale_year
            if year <= 0:
                continue  # unparseable date — don't let year 0 anchor the series
            if getattr(txn, "num_units", 1) > 1:
                continue  # en-bloc/bulk sale PSF is not market PSF
            if year not in by_year:
                by_year[year] = []
            by_year[year].append(txn.psf)

        # Median PSF by year (robust to outliers)
        avg_by_year = {
            year: median(psfs) for year, psfs in by_year.items() if psfs
        }

        # Current year or most recent
        # Track which year was actually selected to avoid overlap with "1yr ago"
        selected_current_year = None
        for year in range(current_year, current_year - 3, -1):
            if year in avg_by_year:
                self.avg_psf_current_year = avg_by_year[year]
                selected_current_year = year
                break

        # 1 year ago — must be a DIFFERENT year than what was selected as "current"
        for year in range(current_year - 1, current_year - 3, -1):
            if year in avg_by_year and year != selected_current_year:
                self.avg_psf_1yr_ago = avg_by_year[year]
                break

        # 3 years ago
        for year in range(current_year - 3, current_year - 5, -1):
            if year in avg_by_year:
                self.avg_psf_3yr_ago = avg_by_year[year]
                break

        # 5 years ago
        selected_5yr_year = None
        for year in range(current_year - 5, current_year - 7, -1):
            if year in avg_by_year:
                self.avg_psf_5yr_ago = avg_by_year[year]
                selected_5yr_year = year
                break

        # Calculate appreciation rates
        if self.avg_psf_current_year and self.avg_psf_1yr_ago:
            self.appreciation_1yr = (
                (self.avg_psf_current_year / self.avg_psf_1yr_ago) - 1
            ) * 100

        if self.avg_psf_current_year and self.avg_psf_3yr_ago:
            self.appreciation_3yr = (
                (self.avg_psf_current_year / self.avg_psf_3yr_ago) - 1
            ) * 100

        if self.avg_psf_current_year and self.avg_psf_5yr_ago:
            self.appreciation_5yr = (
                (self.avg_psf_current_year / self.avg_psf_5yr_ago) - 1
            ) * 100
            # Annualized appreciation (CAGR) over the ACTUAL span between the
            # selected endpoint years. Both endpoints have fallback windows, so
            # the real span can be 3-6 years; dividing by a hardcoded 5 skewed
            # the per-year rate whenever a fallback year was used.
            span = (
                (selected_current_year - selected_5yr_year)
                if selected_current_year and selected_5yr_year
                else 5
            )
            span = max(1, span)
            self.annualized_appreciation = (
                ((self.avg_psf_current_year / self.avg_psf_5yr_ago) ** (1 / span)) - 1
            ) * 100

    def _calculate_momentum(self):
        """Calculate appreciation momentum and data coverage.

        Momentum compares short-term vs long-term appreciation rates:
        - Positive momentum: recent appreciation > long-term average (accelerating)
        - Negative momentum: recent appreciation < long-term average (decelerating)
        - Range: -1.0 to +1.0

        Data coverage tracks the longest reliable time period available.
        """
        # Determine data coverage
        if self.annualized_appreciation is not None:
            self.data_coverage = "5yr"
        elif self.appreciation_3yr is not None:
            self.data_coverage = "3yr"
        elif self.appreciation_1yr is not None:
            self.data_coverage = "1yr"
        else:
            self.data_coverage = "none"

        # Calculate momentum by comparing annualized rates at different horizons
        # Convert all to annualized rates for fair comparison
        rates = {}
        if self.appreciation_1yr is not None:
            rates["1yr"] = self.appreciation_1yr  # Already annualized (1 year)
        if self.appreciation_3yr is not None:
            rates["3yr"] = self.appreciation_3yr / 3  # Rough annualization
        if self.annualized_appreciation is not None:
            rates["5yr"] = self.annualized_appreciation  # Already CAGR

        if len(rates) < 2:
            self.appreciation_momentum = None
            return

        # Compare short-term to long-term
        # If we have 1yr and 5yr, momentum = (1yr_annual - 5yr_annual) normalized
        if "1yr" in rates and "5yr" in rates:
            diff = rates["1yr"] - rates["5yr"]
        elif "1yr" in rates and "3yr" in rates:
            diff = rates["1yr"] - rates["3yr"]
        elif "3yr" in rates and "5yr" in rates:
            diff = rates["3yr"] - rates["5yr"]
        else:
            self.appreciation_momentum = None
            return

        # Normalize to -1.0 to +1.0 range
        # A 5% difference in annualized rates is extreme (maps to ±1.0)
        self.appreciation_momentum = max(-1.0, min(1.0, diff / 5.0))

    def _calculate_resale_metrics(self, resale_txns: list, current_year: int):
        """Calculate appreciation from resale-only transactions.

        By excluding "New Sale" (developer) transactions, this gives a more
        accurate picture of genuine market-driven appreciation, free from
        the artificial developer-to-market price transition effect.
        """
        if len(resale_txns) < 5:
            # Need at least 5 resale transactions for statistically meaningful CAGR
            return

        # Group resale transactions by year (same robustness rules as overall:
        # median PSF, skip year-0 dates and bulk/en-bloc rows)
        by_year: dict[int, list[float]] = {}
        for txn in resale_txns:
            year = txn.sale_year
            if year <= 0 or getattr(txn, "num_units", 1) > 1:
                continue
            if year not in by_year:
                by_year[year] = []
            by_year[year].append(txn.psf)

        avg_by_year = {
            year: median(psfs) for year, psfs in by_year.items() if psfs
        }

        if len(avg_by_year) < 2:
            return

        # Get current and oldest resale PSF
        sorted_years = sorted(avg_by_year.keys())
        oldest_year = sorted_years[0]
        newest_year = sorted_years[-1]

        self.resale_avg_psf_current = avg_by_year[newest_year]
        self.resale_avg_psf_oldest = avg_by_year[oldest_year]

        year_span = newest_year - oldest_year
        if year_span >= 1 and self.resale_avg_psf_oldest > 0:
            # CAGR using resale-only data
            self.resale_annualized_appreciation = (
                ((self.resale_avg_psf_current / self.resale_avg_psf_oldest) ** (1 / year_span)) - 1
            ) * 100

    def size_band_metrics(self, recency_years: int = 2,
                          current_year: Optional[int] = None) -> dict:
        """Median psf per size band, for like-for-like (similar-size) comparison.

        The pooled project median mixes a 1BR and a penthouse, so it can't tell
        whether a small unit's high psf is a premium or just its size. We split
        the recent (non-bulk) transactions into disjoint sqft bands and report a
        median psf per band, plus a pooled `recent_median_psf` on the SAME time
        window so a thin band can fall back without a stale-price jump.

        Returns {"window_years", "recent_median_psf", "bands": {band: {
        "median_psf", "txn_count"}}}. Bands with no transactions are omitted.
        """
        import config
        cy = current_year or datetime.now().year
        cutoff = cy - max(0, recency_years - 1)

        def _clean(txns):
            return [t for t in txns
                    if getattr(t, "num_units", 1) <= 1 and t.psf and t.psf > 0
                    and t.area_sqft and t.area_sqft > 0]

        recent = _clean([t for t in self.transactions if t.sale_year >= cutoff])
        # If the recency window is barren (sparse project), use the full history
        # rather than report nothing — staleness is the lesser evil here.
        if len(recent) < config.MIN_BAND_TXNS:
            recent = _clean(self.transactions)

        if not recent:
            return {"window_years": recency_years, "recent_median_psf": None, "bands": {}}

        bands: dict[str, list[float]] = {}
        for t in recent:
            key = config.size_band_key(t.area_sqft)
            if key:
                bands.setdefault(key, []).append(t.psf)

        return {
            "window_years": recency_years,
            "recent_median_psf": round(median([t.psf for t in recent]), 1),
            "bands": {
                k: {"median_psf": round(median(v), 1), "txn_count": len(v)}
                for k, v in bands.items()
            },
        }


def district_floor_factors(histories: list, recency_years: int = 2,
                           current_year: Optional[int] = None) -> dict:
    """Size-normalized floor-tier PSF multipliers for a set of projects (a district).

    Floor is a real price driver, but the raw floor-PSF gap in a project is badly
    confounded with size — ground/low-floor units are often large PES units that
    trade at a low psf for SIZE reasons, not floor. We isolate the floor effect by
    dividing each transaction's psf by its own project's SAME-SIZE-band median
    (over the same recent window the band medians use, so time-decay cancels too),
    then taking the median ratio per floor tier. Result ~1.0 = no premium; e.g.
    high-rise districts show high>1.0>low, mid-rise districts ~flat.

    Returns {"low", "mid", "high", "txn_count", "basis"} — tiers with < 20
    comparable transactions are omitted (scorer treats a missing tier as 1.0).
    District-level (not per-project) for robustness; the listing coverage is thin,
    so a stable district curve beats noisy per-project floor premia.
    """
    import config
    from collections import defaultdict
    cy = current_year or datetime.now().year
    cutoff = cy - max(0, recency_years - 1)
    tier_ratios: dict[str, list[float]] = defaultdict(list)

    for h in histories:
        def _clean(txns):
            return [t for t in txns if getattr(t, "num_units", 1) <= 1
                    and t.psf and t.psf > 0 and t.area_sqft and t.area_sqft > 0]
        recent = _clean([t for t in h.transactions if t.sale_year >= cutoff])
        if len(recent) < config.MIN_BAND_TXNS:
            recent = _clean(h.transactions)
        if not recent:
            continue
        band_psfs: dict[str, list[float]] = defaultdict(list)
        for t in recent:
            k = config.size_band_key(t.area_sqft)
            if k:
                band_psfs[k].append(t.psf)
        band_median = {k: median(v) for k, v in band_psfs.items()}
        for t in recent:
            tier = config.normalize_floor_tier(t.floor_level)
            key = config.size_band_key(t.area_sqft)
            med = band_median.get(key)
            if tier and med and med > 0:
                tier_ratios[tier].append(t.psf / med)

    out: dict = {"basis": "district",
                 "txn_count": sum(len(v) for v in tier_ratios.values())}
    for tier in ("low", "mid", "high"):
        vals = tier_ratios.get(tier, [])
        if len(vals) >= 20:
            out[tier] = round(median(vals), 4)
    return out


def parse_ura_csv(csv_content: str) -> list[URATransaction]:
    """Parse URA CSV content into transaction objects."""
    transactions = []

    reader = csv.DictReader(io.StringIO(csv_content))
    for row in reader:
        try:
            # Parse price (remove commas)
            price_str = row.get('Transacted Price ($)', '0').replace(',', '')
            price = int(float(price_str)) if price_str else 0

            # Parse area
            area_str = row.get('Area (SQFT)', '0').replace(',', '')
            area = float(area_str) if area_str else 0

            # Parse PSF
            psf_str = row.get('Unit Price ($ PSF)', '0').replace(',', '')
            psf = float(psf_str) if psf_str else 0

            # Parse district
            district_str = row.get('Postal District', '0')
            district = int(district_str) if district_str.isdigit() else 0

            # Parse number of units — >1 means a bulk/en-bloc transaction whose
            # PSF carries a collective-sale premium and must not enter market
            # PSF series (column name varies across URA export versions)
            units_str = (row.get('Number of Units') or row.get('No. of Units') or '1').replace(',', '')
            try:
                num_units = int(float(units_str)) if units_str else 1
            except ValueError:
                num_units = 1

            txn = URATransaction(
                project_name=row.get('Project Name', ''),
                price=price,
                area_sqft=area,
                psf=psf,
                sale_date=row.get('Sale Date', ''),
                street_name=row.get('Street Name', ''),
                sale_type=row.get('Type of Sale', ''),
                tenure=row.get('Tenure', ''),
                district=district,
                market_segment=row.get('Market Segment', ''),
                floor_level=row.get('Floor Level', ''),
                num_units=max(1, num_units),
            )

            if txn.price > 0 and txn.psf > 0:
                transactions.append(txn)

        except (ValueError, KeyError):
            continue

    return transactions


class URAScraper:
    """
    Scraper for URA Property Market Information.

    Uses Playwright to automate the web interface and download CSV data.
    """

    URA_URL = "https://eservice.ura.gov.sg/property-market-information/pmiResidentialTransactionSearch"

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser = None
        self._page = None
        self._cache: dict[str, URATransactionHistory] = {}

    def _get_browser(self):
        """Get or create Playwright browser."""
        if self._browser is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError:
                print("Installing playwright...", file=sys.stderr)
                import subprocess
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "playwright"],
                    stdout=subprocess.DEVNULL,
                )
                subprocess.check_call(
                    [sys.executable, "-m", "playwright", "install", "chromium"],
                    stdout=subprocess.DEVNULL,
                )
                from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)
            self._page = self._browser.new_page()

        return self._page

    def close(self):
        """Close browser."""
        if self._browser:
            self._browser.close()
            self._playwright.stop()
            self._browser = None
            self._page = None

    def search_project(
        self,
        project_name: str,
        year_from: int = 2021,
        year_to: int = 2026,
        month_from: int = 1,
        month_to: int = 12,
    ) -> Optional[str]:
        """
        Search for transactions and download CSV.

        Returns CSV content as string, or None if failed.
        """
        page = self._get_browser()

        try:
            # Navigate to search page
            page.goto(self.URA_URL, wait_until="networkidle")
            time.sleep(1)

            # Click project selector
            page.click('button:has-text("Project or Location")')
            time.sleep(0.5)

            # Type project name
            page.fill('input[aria-label="Project name"]', project_name)
            time.sleep(1)

            # Wait for and click the checkbox for the project
            project_upper = project_name.upper()
            checkbox = page.locator(f'input[type="checkbox"][value="{project_upper}"]')

            if checkbox.count() == 0:
                # Try partial match
                checkbox = page.locator(f'text="{project_upper}"').first

            if checkbox.count() > 0:
                checkbox.click()
                time.sleep(0.3)
            else:
                print(f"Project '{project_name}' not found", file=sys.stderr)
                return None

            # Click Apply
            page.click('button:has-text("Apply")')
            time.sleep(0.5)

            # Set date range
            page.select_option('select[aria-label="Sale Year From"]', str(year_from))
            page.select_option('select[aria-label="Sale Month From"]', self._month_name(month_from))
            page.select_option('select[aria-label="Sale Year To"]', str(year_to))
            page.select_option('select[aria-label="Sale Month To"]', self._month_name(month_to))

            # Click Search
            page.click('button:has-text("Search")')
            time.sleep(2)

            # Wait for results
            page.wait_for_selector('text="Showing"', timeout=10000)

            # Click Download, then CSV
            page.click('button:has-text("Download")')
            time.sleep(0.5)

            # Set up download handler
            with page.expect_download() as download_info:
                page.click('text="CSV"')

            download = download_info.value

            # Read CSV content
            csv_path = download.path()
            with open(csv_path, 'r', encoding='utf-8') as f:
                csv_content = f.read()

            return csv_content

        except Exception as e:
            print(f"Error searching URA: {e}", file=sys.stderr)
            return None

    def _month_name(self, month: int) -> str:
        """Convert month number to name."""
        names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        return names[month - 1] if 1 <= month <= 12 else 'Jan'

    def get_transaction_history(
        self,
        project_name: str,
        use_cache: bool = True,
    ) -> URATransactionHistory:
        """
        Get transaction history for a project.

        Args:
            project_name: Name of the condo/project
            use_cache: Whether to use cached results

        Returns:
            URATransactionHistory with transactions and metrics
        """
        cache_key = project_name.lower()

        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        history = URATransactionHistory(
            project_name=project_name,
            transactions=[],
        )

        csv_content = self.search_project(project_name)

        if csv_content:
            transactions = parse_ura_csv(csv_content)
            history.transactions = transactions
            history.calculate_metrics()

        self._cache[cache_key] = history
        return history

    def get_appreciation_rate(
        self,
        project_name: str,
        default_rate: float = 2.0,
    ) -> tuple[float, str]:
        """
        Get appreciation rate for a project.

        Args:
            project_name: Name of the condo/project
            default_rate: Default rate if no data

        Returns:
            Tuple of (annual_rate_percent, source)
        """
        history = self.get_transaction_history(project_name)

        if history.annualized_appreciation is not None:
            # Cap between -5% and 15%
            rate = max(-5.0, min(15.0, history.annualized_appreciation))
            return (rate, "ura_5yr_cagr")

        if history.appreciation_3yr is not None:
            annual_rate = history.appreciation_3yr / 3
            rate = max(-5.0, min(15.0, annual_rate))
            return (rate, "ura_3yr_avg")

        if history.appreciation_1yr is not None:
            rate = max(-5.0, min(15.0, history.appreciation_1yr))
            return (rate, "ura_1yr")

        return (default_rate, "default")


def load_ura_csv(csv_path: str) -> URATransactionHistory:
    """
    Load and parse a pre-downloaded URA CSV file as a SINGLE project history.

    ⚠ Only valid for single-project CSV exports. District-wide exports contain
    many projects — use load_ura_csv_grouped() for those; this function would
    mix cross-project PSF under one arbitrary project name.

    Args:
        csv_path: Path to CSV file

    Returns:
        URATransactionHistory with transactions and metrics
    """
    with open(csv_path, 'r', encoding='utf-8') as f:
        csv_content = f.read()

    transactions = parse_ura_csv(csv_content)

    if not transactions:
        return URATransactionHistory(project_name="Unknown", transactions=[])

    project_names = {t.project_name.strip() for t in transactions if t.project_name.strip()}
    if len(project_names) > 1:
        print(
            f"  ⚠ {csv_path}: {len(project_names)} projects in one CSV — "
            "load_ura_csv() mixes them; use load_ura_csv_grouped()",
            file=sys.stderr,
        )

    # Get project name from first transaction
    project_name = transactions[0].project_name

    history = URATransactionHistory(
        project_name=project_name,
        transactions=transactions,
    )
    history.calculate_metrics()

    return history


def load_ura_csv_grouped(csv_path: str) -> list[URATransactionHistory]:
    """Load a URA CSV and return one history PER PROJECT.

    District CSV exports hold many projects. The old single-history path
    averaged every project's PSF together and cached it under one arbitrary
    name — silently corrupting appreciation for the whole district.
    """
    with open(csv_path, 'r', encoding='utf-8') as f:
        csv_content = f.read()

    transactions = parse_ura_csv(csv_content)

    by_project: dict[str, list[URATransaction]] = {}
    for txn in transactions:
        name = txn.project_name.strip()
        if name:
            by_project.setdefault(name, []).append(txn)

    histories = []
    for name, txns in by_project.items():
        history = URATransactionHistory(project_name=name, transactions=txns)
        history.calculate_metrics()
        histories.append(history)

    histories.sort(key=lambda h: h.transaction_count, reverse=True)
    return histories


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="URA Transaction Scraper")
    parser.add_argument("--project", type=str, help="Project name to search")
    parser.add_argument("--csv", type=str, help="Path to pre-downloaded CSV file")
    parser.add_argument("--headless", action="store_true", default=True)
    args = parser.parse_args()

    if args.csv:
        # Load from existing CSV
        history = load_ura_csv(args.csv)
        print(f"\nProject: {history.project_name}")
        print(f"Transactions: {history.transaction_count}")
        print(f"Current PSF: ${history.avg_psf_current_year:,.0f}" if history.avg_psf_current_year else "")
        print(f"1yr ago PSF: ${history.avg_psf_1yr_ago:,.0f}" if history.avg_psf_1yr_ago else "")
        print(f"3yr ago PSF: ${history.avg_psf_3yr_ago:,.0f}" if history.avg_psf_3yr_ago else "")
        print(f"5yr ago PSF: ${history.avg_psf_5yr_ago:,.0f}" if history.avg_psf_5yr_ago else "")
        print()
        if history.appreciation_1yr:
            print(f"1yr appreciation: {history.appreciation_1yr:+.1f}%")
        if history.appreciation_3yr:
            print(f"3yr appreciation: {history.appreciation_3yr:+.1f}%")
        if history.appreciation_5yr:
            print(f"5yr appreciation: {history.appreciation_5yr:+.1f}%")
        if history.annualized_appreciation:
            print(f"Annualized (CAGR): {history.annualized_appreciation:+.1f}%/yr")

    elif args.project:
        # Live scrape
        scraper = URAScraper(headless=args.headless)
        try:
            rate, source = scraper.get_appreciation_rate(args.project)
            print(f"\n{args.project}: {rate:+.1f}%/yr ({source})")
        finally:
            scraper.close()
    else:
        parser.print_help()
