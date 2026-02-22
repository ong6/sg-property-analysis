"""Transaction history scraper for PropertyGuru.

Fetches historical transaction data for properties to calculate
actual appreciation rates instead of using hardcoded assumptions.

LIMITATIONS:
    - The PropertyGuru API returns only the ~100 most recent transactions
      with no pagination support. For popular condos with high transaction
      volume, this covers only ~5 weeks of data - insufficient for
      calculating reliable multi-year appreciation rates (CAGR).
    - For historical analysis (5-year CAGR), prefer URA data instead:
        python fetch_ura_districts.py --districts 3,5,14,15
        python invest.py --build-ura-cache data/ura_district_*.csv
    - This scraper remains useful as a supplementary source for very
      recent transaction data not yet reflected in URA's system.

API Endpoint discovered:
  GET /api/consumer/property-transactions/transactions
  Query params:
    - isNew: true
    - transactionType: sales
    - propertyTypeGroup: N
    - propertyTypeCode: CONDO
    - propertyId: {projectId}
    - streetName: {street}
    - districtCode: {district}
    - projectName: {name}
    - locale: en
    - region: sg
"""

import json
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from statistics import mean, median
from typing import Optional
from urllib.parse import urlencode, quote

COOKIE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies.json")
UA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "user_agent.txt")
BASE_URL = "https://www.propertyguru.com.sg"
API_ENDPOINT = "/api/consumer/property-transactions/transactions"


@dataclass
class Transaction:
    """A single property transaction record."""
    date: datetime
    price: int
    psf: float
    sqft: float
    floor_level: str
    bedrooms: Optional[int]
    lease_years: Optional[int]
    property_type: str = "CONDO"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat() if self.date else None
        return d


@dataclass
class TransactionHistory:
    """Transaction history for a property/condo."""
    project_name: str
    district: str
    transactions: list[Transaction] = field(default_factory=list)

    # Calculated metrics
    avg_psf_1yr: Optional[float] = None
    avg_psf_3yr: Optional[float] = None
    avg_psf_5yr: Optional[float] = None
    psf_trend_1yr: Optional[float] = None  # % change
    psf_trend_3yr: Optional[float] = None
    psf_trend_5yr: Optional[float] = None
    annualized_appreciation: Optional[float] = None
    transaction_volume_1yr: int = 0

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "district": self.district,
            "transaction_count": len(self.transactions),
            "avg_psf_1yr": self.avg_psf_1yr,
            "avg_psf_3yr": self.avg_psf_3yr,
            "avg_psf_5yr": self.avg_psf_5yr,
            "psf_trend_1yr": self.psf_trend_1yr,
            "psf_trend_3yr": self.psf_trend_3yr,
            "psf_trend_5yr": self.psf_trend_5yr,
            "annualized_appreciation": self.annualized_appreciation,
            "transaction_volume_1yr": self.transaction_volume_1yr,
        }


class TransactionScraper:
    """
    Scraper for property transaction history from PropertyGuru API.

    Uses the same cookie/session mechanism as the main scraper.
    """

    def __init__(self):
        self.cookies: dict = {}
        self.user_agent: str = ""
        self.session = None
        self._cache: dict[str, TransactionHistory] = {}

    def _load_credentials(self):
        """Load cookies and user agent from warmup files (cached after first load)."""
        if self.cookies:
            return

        if not os.path.exists(COOKIE_FILE):
            raise FileNotFoundError(
                "No cookies.json found. Run: python warmup.py"
            )

        with open(COOKIE_FILE) as f:
            cookies = json.load(f)
        self.cookies = {c["name"]: c["value"] for c in cookies}

        if os.path.exists(UA_FILE):
            with open(UA_FILE) as f:
                self.user_agent = f.read().strip()
        else:
            self.user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    def _get_session(self):
        """Get or create HTTP session."""
        if self.session is None:
            try:
                from curl_cffi import requests
            except ImportError:
                import subprocess
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "curl_cffi"],
                    stdout=subprocess.DEVNULL,
                )
                from curl_cffi import requests

            self.session = requests.Session(impersonate="chrome")
            for name, value in self.cookies.items():
                self.session.cookies.set(name, value, domain=".propertyguru.com.sg")

        return self.session

    def _build_api_url(
        self,
        project_name: str,
        district: Optional[str] = None,
        street_name: Optional[str] = None,
        property_id: Optional[str] = None,
    ) -> str:
        """Build transaction API URL."""
        params = {
            "isNew": "true",
            "transactionType": "sales",
            "propertyTypeGroup": "N",
            "propertyTypeCode": "CONDO",
            "projectName": project_name,
            "locale": "en",
            "region": "sg",
        }

        if district:
            # Normalize district code
            d = district.upper()
            if not d.startswith("D"):
                d = f"D{int(d):02d}"
            params["districtCode"] = d

        if street_name:
            params["streetName"] = street_name

        if property_id:
            params["propertyId"] = property_id

        return f"{BASE_URL}{API_ENDPOINT}?{urlencode(params)}"

    def _fetch_transactions(self, url: str) -> Optional[dict]:
        """Fetch transaction data from API."""
        self._load_credentials()
        session = self._get_session()

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{BASE_URL}/",
            "X-Requested-With": "XMLHttpRequest",
        }

        try:
            resp = session.get(url, headers=headers, timeout=30)

            if resp.status_code != 200:
                print(f"HTTP {resp.status_code} for transactions API", file=sys.stderr)
                return None

            return resp.json()
        except Exception as e:
            print(f"Error fetching transactions: {e}", file=sys.stderr)
            return None

    def _parse_transaction(self, item: dict) -> Optional[Transaction]:
        """Parse a single transaction from API response."""
        try:
            # Parse date (milliseconds timestamp)
            date_ms = item.get("date")
            if date_ms:
                date = datetime.fromtimestamp(date_ms / 1000)
            else:
                return None

            # Parse price
            price_data = item.get("price", {})
            price = price_data.get("amount", 0)
            psf = price_data.get("psfValue", 0)

            if not price or not psf:
                return None

            # Parse size
            size_data = item.get("size", {})
            sqft = size_data.get("area", 0)

            # Other fields
            floor_level = item.get("floorLevel", "")
            bedrooms = item.get("bedroom")

            # Parse lease from string like "99-year lease (91 years left)"
            lease_str = item.get("lease", "")
            lease_years = None
            if lease_str:
                import re
                lease_match = re.search(r"(\d+)-year lease", lease_str)
                if lease_match:
                    lease_years = int(lease_match.group(1))

            return Transaction(
                date=date,
                price=int(price),
                psf=float(psf),
                sqft=float(sqft) if sqft else 0,
                floor_level=floor_level or "",
                bedrooms=int(bedrooms) if bedrooms else None,
                lease_years=lease_years,
            )
        except Exception as e:
            return None

    def _calculate_metrics(self, history: TransactionHistory) -> None:
        """Calculate appreciation metrics from transaction history.

        v2.2: Improved CAGR calculation to reduce new-launch bias.
        Instead of comparing single oldest vs newest transaction (which
        can be dominated by developer-price outliers), uses average PSF
        of oldest and newest year-cohorts for more stable estimation.
        """
        if not history.transactions:
            return

        now = datetime.now()

        # Group transactions by time period
        txns_1yr = [t for t in history.transactions
                    if (now - t.date).days <= 365]
        txns_3yr = [t for t in history.transactions
                    if (now - t.date).days <= 365 * 3]
        txns_5yr = [t for t in history.transactions
                    if (now - t.date).days <= 365 * 5]
        txns_old = [t for t in history.transactions
                    if (now - t.date).days > 365 * 3]

        # Calculate average PSF for each period
        if txns_1yr:
            history.avg_psf_1yr = mean([t.psf for t in txns_1yr])
            history.transaction_volume_1yr = len(txns_1yr)
        if txns_3yr:
            history.avg_psf_3yr = mean([t.psf for t in txns_3yr])
        if txns_5yr:
            history.avg_psf_5yr = mean([t.psf for t in txns_5yr])

        # Calculate PSF trends (% change)
        # Compare recent (last 1yr) vs older (2-3 years ago)
        if txns_1yr and txns_old:
            recent_psf = mean([t.psf for t in txns_1yr])

            # 1 year trend: compare to 1-2 years ago
            txns_1_2yr = [t for t in history.transactions
                         if 365 < (now - t.date).days <= 365 * 2]
            if txns_1_2yr:
                old_psf = mean([t.psf for t in txns_1_2yr])
                history.psf_trend_1yr = ((recent_psf / old_psf) - 1) * 100

            # 3 year trend: compare to 2-4 years ago
            txns_2_4yr = [t for t in history.transactions
                         if 365 * 2 < (now - t.date).days <= 365 * 4]
            if txns_2_4yr and txns_3yr:
                old_psf = mean([t.psf for t in txns_2_4yr])
                history.psf_trend_3yr = ((recent_psf / old_psf) - 1) * 100

            # 5 year trend: compare to 4-6 years ago
            txns_4_6yr = [t for t in history.transactions
                         if 365 * 4 < (now - t.date).days <= 365 * 6]
            if txns_4_6yr and txns_5yr:
                old_psf = mean([t.psf for t in txns_4_6yr])
                history.psf_trend_5yr = ((recent_psf / old_psf) - 1) * 100

        # Calculate annualized appreciation
        # v2.2: Use year-cohort averages instead of single oldest/newest txn
        # to reduce noise from developer-price outliers
        if len(history.transactions) >= 2:
            sorted_txns = sorted(history.transactions, key=lambda t: t.date)

            # Group into oldest cohort (first 20% or min 2) and newest cohort
            cohort_size = max(2, len(sorted_txns) // 5)
            oldest_cohort = sorted_txns[:cohort_size]
            newest_cohort = sorted_txns[-cohort_size:]

            oldest_avg_psf = mean([t.psf for t in oldest_cohort])
            newest_avg_psf = mean([t.psf for t in newest_cohort])
            oldest_avg_date = mean([t.date.timestamp() for t in oldest_cohort])
            newest_avg_date = mean([t.date.timestamp() for t in newest_cohort])

            years_diff = (newest_avg_date - oldest_avg_date) / (365.25 * 86400)
            if years_diff >= 1 and oldest_avg_psf > 0:
                # Compound annual growth rate (CAGR)
                total_return = newest_avg_psf / oldest_avg_psf
                history.annualized_appreciation = (
                    (total_return ** (1 / years_diff)) - 1
                ) * 100

    def get_transaction_history(
        self,
        project_name: str,
        district: Optional[str] = None,
        street_name: Optional[str] = None,
        use_cache: bool = True,
    ) -> TransactionHistory:
        """
        Get transaction history for a property/condo.

        Args:
            project_name: Name of the condo/project
            district: District code (e.g., "D05")
            street_name: Street name for more specific matching
            use_cache: Whether to use cached results

        Returns:
            TransactionHistory with transactions and calculated metrics
        """
        cache_key = f"{project_name}_{district or ''}_{street_name or ''}".lower()

        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        history = TransactionHistory(
            project_name=project_name,
            district=district or "",
        )

        url = self._build_api_url(
            project_name=project_name,
            district=district,
            street_name=street_name,
        )

        data = self._fetch_transactions(url)

        if data and "items" in data:
            for item in data["items"]:
                txn = self._parse_transaction(item)
                if txn:
                    history.transactions.append(txn)

        # Calculate metrics
        self._calculate_metrics(history)

        # Cache result
        self._cache[cache_key] = history

        return history

    def get_appreciation_rate(
        self,
        project_name: str,
        district: Optional[str] = None,
        default_rate: float = 2.0,
        ura_cache: Optional[dict] = None,
    ) -> tuple[float, str]:
        """
        Get estimated annual appreciation rate for a property.

        Args:
            project_name: Name of the condo/project
            district: District code
            default_rate: Default rate if no data available
            ura_cache: Optional URA cache dict for fallback (project_key -> data)

        Returns:
            Tuple of (appreciation_rate, source)
            source is one of: "project_history", "ura_cache", "project_trend_3yr", "default"

        Note:
            PropertyGuru API only returns ~100 most recent transactions.
            For reliable CAGR, URA data (5 years) is strongly preferred.
            If ura_cache is provided and contains data for this project,
            it will be used when PropertyGuru data is insufficient.
        """
        history = self.get_transaction_history(project_name, district)

        # Priority 1: Project-specific annualized appreciation (if enough data)
        if history.annualized_appreciation is not None and len(history.transactions) >= 5:
            # Cap between -5% and 10% for sanity
            rate = max(-5.0, min(10.0, history.annualized_appreciation))
            return (rate, "project_history")

        # Priority 2: URA cache fallback (5 years of data, much more reliable)
        if ura_cache:
            ura_entry = ura_cache.get(project_name.lower(), {})
            ura_apr = ura_entry.get("annualized_appreciation")
            if ura_apr is not None:
                rate = max(-5.0, min(10.0, ura_apr))
                return (rate, "ura_cache")

        # Priority 3: Use 3-year trend if available
        if history.psf_trend_3yr is not None:
            annual_rate = history.psf_trend_3yr / 3  # Convert to annual
            rate = max(-5.0, min(10.0, annual_rate))
            return (rate, "project_trend_3yr")

        # Priority 4: District average (would need to aggregate)
        # For now, return default
        return (default_rate, "default")

    def get_batch_appreciation(
        self,
        listings: list[dict],
        delay: float = 0.5,
    ) -> dict[str, dict]:
        """
        Get appreciation data for multiple listings.

        Args:
            listings: List of listing dicts with "title" and "district" keys
            delay: Delay between API calls

        Returns:
            Dict mapping project_name -> appreciation data
        """
        results = {}
        seen_projects = set()

        for listing in listings:
            project = listing.get("title") or listing.get("project_name", "")
            district = listing.get("district", "")

            if not project:
                continue

            # Dedupe by project
            key = f"{project}_{district}".lower()
            if key in seen_projects:
                continue
            seen_projects.add(key)

            history = self.get_transaction_history(project, district)

            results[key] = {
                "project_name": project,
                "district": district,
                "transaction_count": len(history.transactions),
                "annualized_appreciation": history.annualized_appreciation,
                "avg_psf_1yr": history.avg_psf_1yr,
                "avg_psf_3yr": history.avg_psf_3yr,
                "psf_trend_1yr": history.psf_trend_1yr,
                "psf_trend_3yr": history.psf_trend_3yr,
                "transaction_volume_1yr": history.transaction_volume_1yr,
            }

            print(
                f"  {project}: {len(history.transactions)} txns, "
                f"appreciation: {history.annualized_appreciation:.1f}%/yr"
                if history.annualized_appreciation else f"  {project}: no data",
                file=sys.stderr,
            )

            if delay > 0:
                time.sleep(delay)

        return results


def get_project_appreciation(
    project_name: str,
    district: Optional[str] = None,
) -> tuple[float, str]:
    """
    Convenience function to get appreciation rate for a project.

    Returns:
        Tuple of (annual_appreciation_percent, source)
    """
    scraper = TransactionScraper()
    return scraper.get_appreciation_rate(project_name, district)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch transaction history")
    parser.add_argument("project", type=str, help="Project/condo name")
    parser.add_argument("--district", type=str, help="District code (e.g., D05)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    scraper = TransactionScraper()
    history = scraper.get_transaction_history(args.project, args.district)

    if args.json:
        output = history.to_dict()
        output["transactions"] = [t.to_dict() for t in history.transactions[:20]]
        print(json.dumps(output, indent=2))
    else:
        print(f"\nTransaction History for '{args.project}'")
        print(f"District: {history.district or 'N/A'}")
        print(f"Total Transactions: {len(history.transactions)}")
        print(f"Volume (1yr): {history.transaction_volume_1yr}")
        print()

        if history.avg_psf_1yr:
            print(f"Avg PSF (1yr): ${history.avg_psf_1yr:,.0f}")
        if history.avg_psf_3yr:
            print(f"Avg PSF (3yr): ${history.avg_psf_3yr:,.0f}")
        if history.avg_psf_5yr:
            print(f"Avg PSF (5yr): ${history.avg_psf_5yr:,.0f}")
        print()

        if history.psf_trend_1yr is not None:
            print(f"PSF Trend (1yr): {history.psf_trend_1yr:+.1f}%")
        if history.psf_trend_3yr is not None:
            print(f"PSF Trend (3yr): {history.psf_trend_3yr:+.1f}%")
        if history.annualized_appreciation is not None:
            print(f"Annualized Appreciation: {history.annualized_appreciation:+.1f}%/yr")
        print()

        print("Recent Transactions:")
        for txn in sorted(history.transactions, key=lambda t: t.date, reverse=True)[:10]:
            print(f"  {txn.date.strftime('%Y-%m-%d')}: ${txn.price:,} "
                  f"(${txn.psf:,.0f}/sqft) - {txn.sqft:.0f}sqft")
