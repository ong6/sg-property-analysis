"""Rental listing scraper for PropertyGuru.

Extends the existing scraping infrastructure to fetch rental data
for yield estimation.
"""

import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from statistics import median
from typing import Optional

COOKIE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies.json")
UA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "user_agent.txt")
BASE_URL = "https://www.propertyguru.com.sg"


@dataclass
class RentalListing:
    """Rental listing data."""
    id: str
    condo_name: str
    monthly_rent: int
    sqft: float
    rent_psf: float  # monthly rent / sqft
    beds: int
    district: str
    address: Optional[str] = None
    url: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


class RentalScraper:
    """
    Scraper for rental listings from PropertyGuru.

    Uses the same cookie/session mechanism as the main scraper.
    """

    def __init__(self):
        self.cookies: dict = {}
        self.user_agent: str = ""
        self.session = None

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

    def _build_rental_url(
        self,
        condo_name: Optional[str] = None,
        district: Optional[str] = None,
        beds: Optional[int] = None,
        page: int = 1,
    ) -> str:
        """Build rental search URL."""
        # Path-based URL for rentals
        path = "/apartment-condo-for-rent"
        if beds:
            path += f"/with-{beds}-bedrooms"
        if page > 1:
            path += f"/{page}"

        # Query params
        pairs = []
        if condo_name:
            pairs.append(("freetext", condo_name))
        if district:
            d = district.upper()
            if not d.startswith("D"):
                d = f"D{int(d):02d}"
            pairs.append(("districtCode", d))

        url = f"{BASE_URL}{path}"
        if pairs:
            from urllib.parse import urlencode
            url += f"?{urlencode(pairs)}"

        return url

    def _fetch_page(self, url: str) -> Optional[dict]:
        """Fetch and parse a page."""
        self._load_credentials()
        session = self._get_session()

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": BASE_URL,
        }

        resp = session.get(url, headers=headers, timeout=30)

        if resp.status_code != 200:
            print(f"HTTP {resp.status_code} for {url[:80]}", file=sys.stderr)
            return None

        if "Just a moment" in resp.text[:1000]:
            print("Cloudflare challenge - cookies expired", file=sys.stderr)
            return None

        # Extract __NEXT_DATA__
        match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            resp.text,
            re.DOTALL,
        )
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        return None

    def _parse_rental_listings(self, next_data: dict) -> list[RentalListing]:
        """Parse rental listings from __NEXT_DATA__."""
        listings = []

        try:
            props = next_data["props"]["pageProps"]
            page_data = props.get("pageData", props)
            inner_data = page_data.get("data", {})
            listings_data = (
                inner_data.get("listingsData", [])
                or page_data.get("listingsData", [])
                or []
            )
        except (KeyError, TypeError):
            return []

        for item in listings_data:
            inner = item.get("listingData", item) if isinstance(item, dict) else item
            rental = self._parse_single_rental(inner)
            if rental:
                listings.append(rental)

        return listings

    def _parse_single_rental(self, item: dict) -> Optional[RentalListing]:
        """Parse a single rental listing."""
        try:
            listing_id = item.get("id") or item.get("listingId")
            if not listing_id:
                return None

            # Price (monthly rent)
            price_raw = item.get("price", 0)
            if isinstance(price_raw, dict):
                monthly_rent = price_raw.get("value") or 0
            else:
                monthly_rent = price_raw or 0

            if not monthly_rent:
                return None

            # Area
            sqft = item.get("floorArea") or item.get("size")
            if not sqft:
                return None
            sqft = float(sqft)

            # Calculate rent PSF
            rent_psf = monthly_rent / sqft if sqft > 0 else 0

            # Condo name
            condo_name = item.get("localizedTitle") or item.get("projectName") or ""

            # District
            additional = item.get("additionalData", {}) or {}
            district = additional.get("districtCode", "")

            # Beds
            beds = item.get("bedrooms") or item.get("beds") or 0

            # URL
            url = item.get("url", "")
            if url and not url.startswith("http"):
                url = f"{BASE_URL}{url}"

            return RentalListing(
                id=str(listing_id),
                condo_name=condo_name,
                monthly_rent=int(monthly_rent),
                sqft=sqft,
                rent_psf=round(rent_psf, 2),
                beds=int(beds) if beds else 0,
                district=district,
                address=item.get("fullAddress"),
                url=url,
            )
        except Exception:
            return None

    def scrape_by_condo(
        self,
        condo_name: str,
        max_pages: int = 3,
        delay: float = 1.5,
    ) -> list[RentalListing]:
        """
        Scrape rental listings for a specific condo.

        Args:
            condo_name: Name of the condo to search
            max_pages: Maximum pages to scrape
            delay: Delay between requests

        Returns:
            List of RentalListing objects
        """
        all_listings = []

        for page in range(1, max_pages + 1):
            url = self._build_rental_url(condo_name=condo_name, page=page)
            data = self._fetch_page(url)

            if not data:
                break

            listings = self._parse_rental_listings(data)
            if not listings:
                break

            all_listings.extend(listings)

            if page < max_pages:
                time.sleep(delay)

        return all_listings

    def scrape_by_district(
        self,
        district: str,
        beds: Optional[int] = None,
        max_pages: int = 5,
        delay: float = 1.5,
    ) -> list[RentalListing]:
        """
        Scrape rental listings for a district.

        Args:
            district: District code (e.g., "D05" or "5")
            beds: Optional bedroom filter
            max_pages: Maximum pages to scrape
            delay: Delay between requests

        Returns:
            List of RentalListing objects
        """
        all_listings = []

        for page in range(1, max_pages + 1):
            url = self._build_rental_url(district=district, beds=beds, page=page)
            data = self._fetch_page(url)

            if not data:
                break

            listings = self._parse_rental_listings(data)
            if not listings:
                break

            all_listings.extend(listings)

            if page < max_pages:
                time.sleep(delay)

        return all_listings

    def calculate_median_rent_psf(self, listings: list[RentalListing]) -> float:
        """Calculate median rent per square foot from listings."""
        if not listings:
            return 0.0
        psf_values = [l.rent_psf for l in listings if l.rent_psf > 0]
        return median(psf_values) if psf_values else 0.0

    def get_condo_rental_data(
        self,
        condo_names: list[str],
        delay: float = 2.0,
    ) -> dict[str, float]:
        """
        Get rental data for multiple condos.

        Args:
            condo_names: List of condo names to search
            delay: Delay between searches

        Returns:
            Dict mapping condo_name (lowercase) -> median rent_psf
        """
        rental_data = {}

        for name in condo_names:
            listings = self.scrape_by_condo(name, max_pages=2)
            if listings:
                median_psf = self.calculate_median_rent_psf(listings)
                rental_data[name.lower()] = round(median_psf, 2)
                print(
                    f"  {name}: {len(listings)} rentals, median ${median_psf:.2f}/sqft",
                    file=sys.stderr,
                )
            time.sleep(delay)

        return rental_data

    def get_district_rental_medians(
        self,
        districts: list[str],
        delay: float = 2.0,
    ) -> dict[str, float]:
        """
        Get rental medians for multiple districts.

        Args:
            districts: List of district codes
            delay: Delay between searches

        Returns:
            Dict mapping district (D##) -> median rent_psf
        """
        medians = {}

        for district in districts:
            listings = self.scrape_by_district(district, max_pages=3)
            if listings:
                median_psf = self.calculate_median_rent_psf(listings)
                d = district.upper()
                if not d.startswith("D"):
                    d = f"D{int(d):02d}"
                medians[d] = round(median_psf, 2)
                print(
                    f"  {d}: {len(listings)} rentals, median ${median_psf:.2f}/sqft",
                    file=sys.stderr,
                )
            time.sleep(delay)

        return medians


def scrape_rental_data(
    condo_names: Optional[list[str]] = None,
    districts: Optional[list[str]] = None,
) -> dict:
    """
    Convenience function to scrape rental data.

    Returns:
        Dict with "condo_medians" and "district_medians"
    """
    scraper = RentalScraper()
    result = {"condo_medians": {}, "district_medians": {}}

    if condo_names:
        result["condo_medians"] = scraper.get_condo_rental_data(condo_names)

    if districts:
        result["district_medians"] = scraper.get_district_rental_medians(districts)

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape rental listings")
    parser.add_argument("--condo", type=str, help="Condo name to search")
    parser.add_argument("--district", type=str, help="District code (e.g., D05)")
    parser.add_argument("--pages", type=int, default=3, help="Max pages")
    args = parser.parse_args()

    scraper = RentalScraper()

    if args.condo:
        listings = scraper.scrape_by_condo(args.condo, args.pages)
        print(f"\nFound {len(listings)} rental listings for '{args.condo}':")
        for l in listings[:10]:
            print(f"  ${l.monthly_rent}/mo - {l.sqft} sqft - ${l.rent_psf}/sqft")
        if listings:
            print(f"\nMedian rent PSF: ${scraper.calculate_median_rent_psf(listings):.2f}")

    elif args.district:
        listings = scraper.scrape_by_district(args.district, max_pages=args.pages)
        print(f"\nFound {len(listings)} rental listings in {args.district}:")
        for l in listings[:10]:
            print(f"  ${l.monthly_rent}/mo - {l.sqft} sqft - ${l.rent_psf}/sqft - {l.condo_name}")
        if listings:
            print(f"\nMedian rent PSF: ${scraper.calculate_median_rent_psf(listings):.2f}")
