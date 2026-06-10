import json
import re
import time
import logging
import random
import hashlib
from copy import deepcopy
from patchright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeout

from config import (
    PROPERTYGURU_BASE_URL,
    PAGE_LOAD_TIMEOUT,
    NEXT_DATA_WAIT_TIMEOUT,
    DOM_CONTENT_WAIT_TIMEOUT,
    CLOUDFLARE_WAIT_TIMEOUT,
    NEXT_DATA_SELECTOR,
    LISTING_CARD_SELECTORS,
    REQUEST_DELAY_SECONDS,
    REQUEST_DELAY_JITTER,
    MAX_RETRIES,
    RETRY_BACKOFF_SECONDS,
    LISTINGS_PER_PAGE,
    MAX_PAGES_LIMIT,
    DETAIL_PAGE_DELAY,
)
from models import SearchParams, Listing, ScrapedPage, ScrapeStats
from scrapers.browser import wait_for_cloudflare
from utils.geo import normalize_district

logger = logging.getLogger("property-finder")


class CloudflareBlockedError(Exception):
    """Raised when Cloudflare challenge cannot be bypassed."""


class PageParseError(Exception):
    """Raised when the page content cannot be parsed."""


class ProjectPageNeedsNameFallback(Exception):
    """Raised when a pasted URL is a project/condo page rather than a listing
    or results page. Carries the extracted project name so the caller can
    delegate to the condo-name search flow."""

    def __init__(self, project_name: str | None):
        super().__init__(project_name or "")
        self.project_name = project_name


def classify_pg_url(url: str) -> tuple[str, str | None]:
    """Classify a PropertyGuru URL.

    Returns (kind, project_name) where kind is one of:
      - "detail"  : a single listing detail page  (/listing/...)
      - "results" : a search/results/list page    (.../property-for-sale?...)
      - "project" : a project/condo directory page (best-effort name fallback)
    """
    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(url)
    path = parsed.path.lower()
    qkeys = {k.lower() for k in parse_qs(parsed.query)}

    # 1. Single listing detail page wins first.
    if "/listing/" in path:
        return "detail", None

    # 2. Project / condo-directory page -> condo-name fallback.
    if any(seg in path for seg in ("/project/", "/new-project/", "/condo-directory/")):
        return "project", _project_name_from_url(url)

    # 3. Results / search page.
    results_markers = (
        "-for-sale", "-for-rent", "/property-for-sale", "/property-for-rent",
        "/property-search", "/listing/",
    )
    search_qkeys = {
        "freetext", "districtcode", "listingtype", "propertytypegroup",
        "propertytypecode", "minprice", "maxprice", "beds", "bedrooms", "search",
    }
    if any(m in path for m in results_markers) or (search_qkeys & qkeys):
        return "results", None

    # 4. Unknown -> safest is a best-effort condo-name search from the slug.
    return "project", _project_name_from_url(url)


def _project_name_from_url(url: str) -> str | None:
    """Extract a human-readable project name from a PG URL slug.

    e.g. '/project/the-continuum-98765' -> 'the continuum'
    """
    from urllib.parse import urlparse

    path = urlparse(url).path.rstrip("/")
    if not path:
        return None
    slug = path.split("/")[-1]
    slug = re.sub(r"-?\d{4,}$", "", slug)           # strip trailing numeric id
    name = slug.replace("-", " ").strip()
    return name or None


def _with_page_segment(url: str, page_num: int) -> str:
    """Insert PropertyGuru's '/{n}' pagination segment before the query string.

    e.g. '/property-for-sale?foo=bar' + page 2 -> '/property-for-sale/2?foo=bar'.
    If a trailing '/{n}' segment already exists, it is replaced.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    path = re.sub(r"/\d+$", "", parts.path.rstrip("/"))
    new_path = f"{path}/{page_num}"
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


# ---------------------------------------------------------------------------
# JavaScript to extract listing data from the rendered DOM
# ---------------------------------------------------------------------------
DOM_EXTRACT_JS = """
() => {
    const results = [];

    // PropertyGuru 2024+ uses listing-card-v2 with da-id attributes
    let cards = document.querySelectorAll('.listing-card-v2.card');

    // Fallback: try legacy selectors
    if (!cards.length) {
        cards = document.querySelectorAll('[data-listing-id]');
    }
    if (!cards.length) {
        const selectors = [
            'div[class*="listing-card"]',
            'div[class*="ListingCard"]',
            'article[class*="listing"]',
        ];
        for (const sel of selectors) {
            cards = document.querySelectorAll(sel);
            if (cards.length) break;
        }
    }

    for (const card of cards) {
        try {
            const listing = {};

            // URL — extract from gallery link or any listing link
            const linkEl = card.querySelector('a[href*="/listing/"]');
            listing.url = linkEl?.href || '';

            // ID — extract from URL (e.g., "for-sale-cairnhill-nine-500023705" -> "500023705")
            if (listing.url) {
                const idMatch = listing.url.match(/(\\d{6,})$/);
                listing.id = idMatch ? idMatch[1] : '';
            } else {
                listing.id = card.getAttribute('data-listing-id') || '';
            }

            // Title — property name from da-id selector
            const titleEl = card.querySelector('[da-id="listing-card-v2-title"]');
            listing.title = titleEl?.textContent?.trim() || '';

            // Headline/description
            const headlineEl = card.querySelector('[da-id="listing-card-v2-headline"]');
            listing.headline = headlineEl?.textContent?.trim() || '';

            // Price
            const priceEl = card.querySelector('[da-id="listing-card-v2-price"]');
            const priceText = priceEl?.textContent?.trim() || '';
            // Handle "$1.23M" / "$950K" abbreviations — the old integer-only
            // match read "$1.23M" as 1.
            const priceMatch = priceText.replace(/,/g, '').match(/(\\d+(?:\\.\\d+)?)\\s*([mMkK])?/);
            let priceVal = 0;
            if (priceMatch) {
                priceVal = parseFloat(priceMatch[1]);
                const suffix = (priceMatch[2] || '').toLowerCase();
                if (suffix === 'm') priceVal *= 1000000;
                else if (suffix === 'k') priceVal *= 1000;
            }
            listing.price = Math.round(priceVal);
            listing.price_text = priceText;

            // PSF
            const psfEl = card.querySelector('[da-id="listing-card-v2-psf"]');
            listing.psf_text = psfEl?.textContent?.trim() || '';

            // Address — prefer da-id selector, fallback to class
            const addrEl = card.querySelector('[da-id="listing-card-v2-address"]')
                || card.querySelector('[class*="address"]');
            listing.address = addrEl?.textContent?.trim() || '';

            // Project name (same as title for DOM extraction)
            listing.project_name = listing.title;

            // District — try extracting from URL query params
            try {
                const urlParams = new URLSearchParams(window.location.search);
                const dc = urlParams.getAll('districtCode');
                if (dc.length === 1) listing.district = dc[0];
            } catch(e) {}

            // Lat/lng from data attributes on card or nested elements
            const latAttr = card.getAttribute('data-lat') || card.querySelector('[data-lat]')?.getAttribute('data-lat');
            const lngAttr = card.getAttribute('data-lng') || card.querySelector('[data-lng]')?.getAttribute('data-lng');
            if (latAttr) listing.latitude = parseFloat(latAttr);
            if (lngAttr) listing.longitude = parseFloat(lngAttr);

            // Beds
            const bedsEl = card.querySelector('[da-id="listing-card-v2-bedrooms"]');
            if (bedsEl) {
                const m = bedsEl.textContent.match(/(\\d+)/);
                if (m) listing.beds = parseInt(m[1]);
            }

            // Baths
            const bathsEl = card.querySelector('[da-id="listing-card-v2-bathrooms"]');
            if (bathsEl) {
                const m = bathsEl.textContent.match(/(\\d+)/);
                if (m) listing.baths = parseInt(m[1]);
            }

            // Floor area (sqft)
            const areaEl = card.querySelector('[da-id="listing-card-v2-area"]');
            if (areaEl) {
                const m = areaEl.textContent.replace(/,/g, '').match(/(\\d+)/);
                if (m) listing.sqft = parseInt(m[1]);
            }

            // Property type
            const typeEl = card.querySelector('[da-id="listing-card-v2-unit-type"]');
            listing.property_type = typeEl?.textContent?.trim() || null;

            // Tenure
            const tenureEl = card.querySelector('[da-id="listing-card-v2-tenure"]');
            listing.tenure = tenureEl?.textContent?.trim() || null;

            // Built year
            const builtEl = card.querySelector('[da-id="listing-card-v2-build-year"]');
            if (builtEl) {
                const m = builtEl.textContent.match(/(\\d{4})/);
                if (m) listing.built_year = parseInt(m[1]);
            }

            // MRT info
            const mrtEl = card.querySelector('[da-id="listing-card-v2-mrt"]');
            listing.mrt_info = mrtEl?.textContent?.trim() || null;

            // Agent name
            const agentEl = card.querySelector('[da-id="listing-card-v2-agent-name"]');
            listing.listing_agent = agentEl?.textContent?.trim() || null;

            // Listing date/recency
            const recencyEl = card.querySelector('[da-id="listing-card-v2-recency"]');
            listing.listing_date = recencyEl?.textContent?.trim() || null;

            // Image — first image in gallery
            const imgEl = card.querySelector('.gallery img[src*="pgimgs"], img[src*="listing"]');
            listing.image_url = imgEl?.src || null;

            // Floor level — check for badge text like "High Floor", "Low Floor"
            const allBadges = card.querySelectorAll('[class*="badge"], [class*="Badge"], [class*="tag"], [class*="Tag"]');
            for (const badge of allBadges) {
                const bt = badge.textContent.trim().toLowerCase();
                if (bt.includes('high floor') || bt.includes('mid floor') || bt.includes('low floor')
                    || bt.includes('penthouse') || bt.includes('ground')) {
                    listing.floor_level = badge.textContent.trim();
                    break;
                }
            }

            // Collect tags from badges
            const tags = [];
            if (listing.property_type) tags.push(listing.property_type);
            if (listing.tenure) tags.push(listing.tenure);
            if (builtEl) tags.push(builtEl.textContent.trim());
            listing.tags = tags;

            // Only add if we have an ID and title (property name)
            if (listing.id && listing.title) {
                results.push(listing);
            }
        } catch(e) {
            // skip this card
        }
    }
    return results;
}
"""

# JavaScript to extract pagination info from the DOM
PAGINATION_EXTRACT_JS = """
() => {
    // Try to find total result count
    let totalResults = 0;
    let totalPages = 1;

    // Look for result count text like "1,234 Properties"
    const countEls = document.querySelectorAll('[class*="result-count"], [class*="ResultCount"], [class*="total"], h1, [class*="search-title"]');
    for (const el of countEls) {
        const text = el.textContent.replace(/,/g, '');
        const m = text.match(/(\\d+)\\s*(?:propert|listing|result|home|condo)/i);
        if (m) {
            totalResults = parseInt(m[1]);
            break;
        }
    }

    // Look for pagination
    const paginationLinks = document.querySelectorAll('[class*="pagination"] a, nav[aria-label*="pagination"] a, [class*="Pagination"] a');
    for (const link of paginationLinks) {
        const text = link.textContent.trim();
        const num = parseInt(text);
        if (!isNaN(num) && num > totalPages) {
            totalPages = num;
        }
    }

    // Fallback: if we found total results, estimate pages
    if (totalResults > 0 && totalPages <= 1) {
        totalPages = Math.ceil(totalResults / 20);
    }

    return { totalResults, totalPages };
}
"""

# JavaScript to find JSON data in any script tag
SCRIPT_JSON_EXTRACT_JS = """
() => {
    const results = [];
    const scripts = document.querySelectorAll('script[type="application/json"], script[type="application/ld+json"], script#__NEXT_DATA__');
    for (const script of scripts) {
        try {
            const data = JSON.parse(script.textContent);
            results.push({ type: script.type || script.id, data });
        } catch(e) {}
    }

    // Also look for window.__NEXT_DATA__ or similar global variables
    const globals = ['__NEXT_DATA__', '__INITIAL_STATE__', '__PRELOADED_STATE__', '__APP_DATA__'];
    for (const name of globals) {
        try {
            const data = window[name];
            if (data && typeof data === 'object') {
                results.push({ type: 'window.' + name, data });
            }
        } catch(e) {}
    }

    return results;
}
"""


class PropertyGuruScraper:
    def __init__(self, context: BrowserContext):
        self._context = context
        self._page: Page | None = None
        self._intercepted_data: list[dict] = []
        self._seen_ids: set[str] = set()
        self._stats = ScrapeStats()
        self._interception_page: Page | None = None

    @property
    def stats(self) -> ScrapeStats:
        return self._stats

    def scrape(self, params: SearchParams, max_pages: int = 5) -> list[Listing]:
        """Scrape listings for given params with smart pagination and dedup.

        After page 1, reads total_pages from the response and auto-extends
        up to min(total_pages, MAX_PAGES_LIMIT). Stops early if a page
        returns fewer than LISTINGS_PER_PAGE listings or 0 new (deduped).
        """
        all_listings: list[Listing] = []
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

        # Set up API response interception
        self._setup_interception()

        effective_max = max_pages

        for page_num in range(1, MAX_PAGES_LIMIT + 1):
            if page_num > effective_max:
                break

            logger.info("Scraping page %d / %d", page_num, effective_max)

            scraped = self._scrape_single_page(params, page_num)
            if not scraped or not scraped.listings:
                logger.info("No listings on page %d, stopping", page_num)
                break

            self._stats.pages_fetched += 1

            # Dedup against seen IDs
            new_listings = []
            for listing in scraped.listings:
                if listing.id in self._seen_ids:
                    self._stats.duplicates_skipped += 1
                    continue
                self._seen_ids.add(listing.id)
                new_listings.append(listing)

            if not new_listings:
                logger.info("Page %d: 0 new listings after dedup, stopping", page_num)
                break

            all_listings.extend(new_listings)
            logger.info(
                "Page %d: %d new listings (%d dupes skipped, total: %d / %d)",
                page_num, len(new_listings),
                len(scraped.listings) - len(new_listings),
                len(all_listings), scraped.total_results,
            )

            # Smart pagination: after page 1, auto-extend if more pages available
            if page_num == 1 and scraped.total_pages > effective_max:
                old_max = effective_max
                effective_max = min(scraped.total_pages, MAX_PAGES_LIMIT)
                logger.info(
                    "Smart pagination: %d total pages detected, extending %d -> %d",
                    scraped.total_pages, old_max, effective_max,
                )

            # Stop if fewer than a full page (definitive last page)
            if len(scraped.listings) < LISTINGS_PER_PAGE:
                logger.info("Partial page (%d < %d), reached last page",
                            len(scraped.listings), LISTINGS_PER_PAGE)
                break

            # Stop if we've reached the last page
            if page_num >= scraped.total_pages:
                if scraped.total_pages <= 1 and len(scraped.listings) >= LISTINGS_PER_PAGE:
                    logger.info("Got full page (%d listings) but total_pages=%d, continuing",
                                len(scraped.listings), scraped.total_pages)
                else:
                    logger.info("Reached last page (%d)", scraped.total_pages)
                    break

            if page_num < effective_max:
                _sleep_with_jitter(REQUEST_DELAY_SECONDS, REQUEST_DELAY_JITTER)

        self._propagate_coordinates(all_listings)
        logger.info("Scraping complete: %d listings collected", len(all_listings))
        return all_listings

    def scrape_multi_district(
        self, params: SearchParams, max_pages: int = 5
    ) -> tuple[list[Listing], ScrapeStats]:
        """Scrape each district independently, then merge with dedup.

        Each district gets its own page quota. Returns combined listings
        and aggregated stats.
        """
        districts = params.districts or []
        if len(districts) <= 1:
            # Single district or none — just use regular scrape
            listings = self.scrape(params, max_pages=max_pages)
            if districts:
                self._stats.per_district_counts[districts[0]] = len(listings)
            return listings, self._stats

        all_listings: list[Listing] = []
        combined_stats = ScrapeStats()

        for i, district in enumerate(districts):
            logger.info(
                "=== District D%02d (%d/%d) ===", district, i + 1, len(districts)
            )

            # Create per-district params
            district_params = deepcopy(params)
            district_params.districts = [district]

            # Reset per-district state
            self._stats = ScrapeStats()
            self._seen_ids = set()

            district_listings = self.scrape(district_params, max_pages=max_pages)
            self._stats.per_district_counts[district] = len(district_listings)

            all_listings.extend(district_listings)
            combined_stats.merge(self._stats)

            logger.info(
                "District D%02d: %d listings (running total: %d)",
                district, len(district_listings), len(all_listings),
            )

            # Delay between districts (not after the last one)
            if i < len(districts) - 1:
                _sleep_with_jitter(REQUEST_DELAY_SECONDS, REQUEST_DELAY_JITTER)

        self._stats = combined_stats
        logger.info(
            "Multi-district scraping complete: %d total listings, %d duplicates skipped",
            len(all_listings), combined_stats.duplicates_skipped,
        )
        return all_listings, combined_stats

    def scrape_from_url(self, url: str, max_pages: int = 5) -> list[Listing]:
        """Scrape listings from an arbitrary PropertyGuru URL.

        Classifies the URL and routes:
          - detail  -> parse the single listing into ONE Listing
          - results -> page through using the given URL as page 1
          - project -> raise ProjectPageNeedsNameFallback(name) so the caller
                       can delegate to the condo-name search.
        """
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._setup_interception()

        kind, project_name = classify_pg_url(url)
        logger.info("URL classified as '%s': %s", kind, url)

        if kind == "project":
            raise ProjectPageNeedsNameFallback(project_name)

        if kind == "detail":
            listing = self._scrape_detail_as_listing(url)
            if listing:
                self._propagate_coordinates([listing])
                return [listing]
            # Detail extraction failed — fall back to treating it as a results page.
            logger.warning("Detail extraction failed; retrying as a results page")

        # results (or detail fallback): page through, reusing scrape()'s loop body.
        all_listings: list[Listing] = []
        effective_max = max_pages

        for page_num in range(1, MAX_PAGES_LIMIT + 1):
            if page_num > effective_max:
                break

            page_url = url if page_num == 1 else _with_page_segment(url, page_num)
            scraped = self._scrape_url_page(page_url, page_num)
            if not scraped or not scraped.listings:
                break

            self._stats.pages_fetched += 1

            new_listings = []
            for listing in scraped.listings:
                if listing.id in self._seen_ids:
                    self._stats.duplicates_skipped += 1
                    continue
                self._seen_ids.add(listing.id)
                new_listings.append(listing)

            if not new_listings:
                logger.info("Page %d: 0 new listings after dedup, stopping", page_num)
                break

            all_listings.extend(new_listings)

            if page_num == 1 and scraped.total_pages > effective_max:
                effective_max = min(scraped.total_pages, MAX_PAGES_LIMIT)

            if len(scraped.listings) < LISTINGS_PER_PAGE:
                break
            if page_num >= scraped.total_pages and scraped.total_pages >= 1:
                if not (scraped.total_pages <= 1 and len(scraped.listings) >= LISTINGS_PER_PAGE):
                    break

            if page_num < effective_max:
                _sleep_with_jitter(REQUEST_DELAY_SECONDS, REQUEST_DELAY_JITTER)

        self._propagate_coordinates(all_listings)
        logger.info("URL scrape complete: %d listings collected", len(all_listings))
        return all_listings

    def _scrape_url_page(self, url: str, page_num: int) -> ScrapedPage | None:
        """Navigate to a concrete URL and extract listings (mirrors
        _scrape_single_page but takes a ready-made URL)."""
        self._intercepted_data = []

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._page.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")

                cf_resolved = wait_for_cloudflare(self._page, timeout_ms=CLOUDFLARE_WAIT_TIMEOUT)
                if not cf_resolved:
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
                        continue
                    raise CloudflareBlockedError(
                        f"Cloudflare challenge unresolved after {MAX_RETRIES} attempts"
                    )

                result = self._extract_page_data(page_num)
                if result and result.listings:
                    return result

                logger.warning("No listings extracted on attempt %d/%d", attempt, MAX_RETRIES)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])

            except PlaywrightTimeout:
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
                else:
                    raise CloudflareBlockedError(f"Could not load {url} after {MAX_RETRIES} attempts")
            except json.JSONDecodeError as e:
                raise PageParseError(f"Failed to parse JSON: {e}")

        return None

    def _scrape_detail_as_listing(self, url: str) -> Listing | None:
        """Navigate to a single listing detail page and parse it into one Listing."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._page.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
                if not wait_for_cloudflare(self._page, timeout_ms=CLOUDFLARE_WAIT_TIMEOUT):
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
                        continue
                    return None
                break
            except PlaywrightTimeout:
                if attempt >= MAX_RETRIES:
                    return None
                time.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])

        raw = None
        try:
            raw = self._page.evaluate("""
                () => {
                    const el = document.querySelector('script#__NEXT_DATA__');
                    if (el) return JSON.parse(el.textContent);
                    if (window.__NEXT_DATA__) return window.__NEXT_DATA__;
                    return null;
                }
            """)
        except Exception as e:
            logger.debug("Detail __NEXT_DATA__ read failed: %s", e)

        if isinstance(raw, dict):
            props = raw.get("props", {}).get("pageProps", {})
            page_data = props.get("pageData", props)
            candidate = (
                (page_data.get("data", {}) or {}).get("listingData")
                or props.get("listingData")
                or props.get("data")
            )
            # Sometimes wrapped one level deeper as {"listingData": {...}}
            if isinstance(candidate, dict) and isinstance(candidate.get("listingData"), dict):
                candidate = candidate["listingData"]
            if isinstance(candidate, dict):
                listing = self._parse_single_listing(candidate)
                if listing:
                    return listing
            # Fallback: recursively hunt for a listing object anywhere in the blob.
            sp = self._search_for_listings(raw, page_num=1)
            if sp and sp.listings:
                return sp.listings[0]

        # Last resort: run the full multi-strategy page extraction.
        result = self._extract_page_data(1)
        if result and result.listings:
            return result.listings[0]
        return None

    def enrich_listings(self, listings: list[Listing], top_n: int = 10) -> int:
        """Visit detail pages for top listings to fill missing fields.

        Enriches lat/lng, facing, floor_level, furnishing, total_units, developer.
        Returns count of successfully enriched listings.
        """
        if top_n <= 0:
            return 0

        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        enriched = 0

        # Only enrich listings missing key fields
        candidates = [
            l for l in listings[:top_n]
            if not l.latitude or not l.facing or not l.total_units
        ]
        if not candidates:
            logger.info("No listings need enrichment")
            return 0

        logger.info("Enriching %d listings from detail pages...", len(candidates))

        for i, listing in enumerate(candidates):
            if not listing.url:
                continue

            url = listing.url
            if not url.startswith("http"):
                url = f"{PROPERTYGURU_BASE_URL}{url}"

            logger.info("  Enriching %d/%d: %s", i + 1, len(candidates), listing.title[:40])

            try:
                self._page.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
                wait_for_cloudflare(self._page, timeout_ms=CLOUDFLARE_WAIT_TIMEOUT)

                detail_data = self._extract_detail_page()
                if detail_data:
                    changed = self._apply_enrichment(listing, detail_data)
                    if changed:
                        enriched += 1
                        logger.debug("  Enriched: %s", ", ".join(changed))

            except (PlaywrightTimeout, Exception) as e:
                logger.warning("  Failed to enrich %s: %s", listing.id, e)

            if i < len(candidates) - 1:
                _sleep_with_jitter(DETAIL_PAGE_DELAY, REQUEST_DELAY_JITTER)

        self._stats.enriched_count = enriched
        logger.info("Enrichment complete: %d/%d listings enriched", enriched, len(candidates))
        return enriched

    def _extract_detail_page(self) -> dict | None:
        """Extract enrichment data from a listing detail page."""
        data = {}

        # Try __NEXT_DATA__ first
        try:
            raw = self._page.evaluate("""
                () => {
                    const el = document.querySelector('script#__NEXT_DATA__');
                    if (el) return JSON.parse(el.textContent);
                    if (window.__NEXT_DATA__) return window.__NEXT_DATA__;
                    return null;
                }
            """)
            if raw and isinstance(raw, dict):
                props = raw.get("props", {}).get("pageProps", {})
                listing_data = props.get("listingData", props.get("data", {}))
                if isinstance(listing_data, dict):
                    data.update(self._extract_detail_fields(listing_data))
        except Exception as e:
            logger.debug("Detail __NEXT_DATA__ failed: %s", e)

        # Try ld+json structured data
        try:
            ld_json = self._page.evaluate("""
                () => {
                    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                    for (const s of scripts) {
                        try {
                            const d = JSON.parse(s.textContent);
                            if (d['@type'] === 'Product' || d['@type'] === 'Residence'
                                || d['@type'] === 'Apartment' || d.geo) return d;
                        } catch(e) {}
                    }
                    return null;
                }
            """)
            if ld_json and isinstance(ld_json, dict):
                geo = ld_json.get("geo", {})
                if geo:
                    if "latitude" not in data:
                        data["latitude"] = _safe_float(geo.get("latitude"))
                    if "longitude" not in data:
                        data["longitude"] = _safe_float(geo.get("longitude"))
        except Exception as e:
            logger.debug("Detail ld+json failed: %s", e)

        return data if data else None

    def _extract_detail_fields(self, data: dict) -> dict:
        """Pull enrichment fields from detail page JSON."""
        fields = {}
        if data.get("latitude"):
            fields["latitude"] = _safe_float(data["latitude"])
        if data.get("longitude"):
            fields["longitude"] = _safe_float(data["longitude"])
        if data.get("facing") or data.get("unitFacing"):
            fields["facing"] = data.get("facing") or data.get("unitFacing")
        if data.get("floorLevel"):
            fields["floor_level"] = data["floorLevel"]
        if data.get("furnishing"):
            fields["furnishing"] = data["furnishing"]
        if data.get("totalUnits"):
            fields["total_units"] = _safe_int(data["totalUnits"])
        if data.get("developerName"):
            fields["developer"] = data["developerName"]
        elif isinstance(data.get("developer"), dict):
            fields["developer"] = data["developer"].get("name")
        project = data.get("project", {})
        if isinstance(project, dict):
            if not fields.get("total_units") and project.get("totalUnits"):
                fields["total_units"] = _safe_int(project["totalUnits"])
            if not fields.get("developer") and project.get("developerName"):
                fields["developer"] = project["developerName"]
        return fields

    def _apply_enrichment(self, listing: Listing, data: dict) -> list[str]:
        """Apply enrichment data to a listing. Returns list of changed field names."""
        changed = []
        for field_name in ["latitude", "longitude", "facing", "floor_level",
                           "furnishing", "total_units", "developer"]:
            new_val = data.get(field_name)
            if new_val is not None and not getattr(listing, field_name, None):
                setattr(listing, field_name, new_val)
                changed.append(field_name)
        return changed

    def _setup_interception(self):
        """Intercept API responses that may contain listing data."""
        self._intercepted_data = []

        if not self._page:
            return

        # Avoid stacking multiple handlers on the same page
        if self._interception_page is self._page:
            return

        def handle_response(response):
            try:
                url = response.url
                # Look for API responses that likely contain listing data
                if any(kw in url for kw in ["listing", "search", "property", "api"]):
                    if "application/json" in (response.headers.get("content-type") or ""):
                        body = response.json()
                        if isinstance(body, dict):
                            self._intercepted_data.append({
                                "url": url,
                                "data": body,
                            })
                            logger.debug("Intercepted API response: %s", url[:100])
            except Exception:
                pass

        try:
            self._page.on("response", handle_response)
            self._interception_page = self._page
        except Exception as e:
            logger.debug("Could not set up response interception: %s", e)

    def _build_url(self, params: SearchParams, page: int) -> str:
        path = params.to_url_path(page)
        qs = params.to_query_string(page)
        url = f"{PROPERTYGURU_BASE_URL}{path}"
        if qs:
            url += f"?{qs}"
        return url

    def _scrape_single_page(self, params: SearchParams, page_num: int) -> ScrapedPage | None:
        url = self._build_url(params, page_num)
        logger.debug("URL: %s", url)

        # Clear intercepted data for this page
        self._intercepted_data = []

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._page.goto(url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")

                # Handle Cloudflare
                cf_resolved = wait_for_cloudflare(self._page, timeout_ms=CLOUDFLARE_WAIT_TIMEOUT)
                if not cf_resolved:
                    logger.warning("Cloudflare challenge unresolved (attempt %d/%d)", attempt, MAX_RETRIES)
                    if attempt < MAX_RETRIES:
                        backoff = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                        logger.info("Retrying in %ds...", backoff)
                        time.sleep(backoff)
                        continue
                    raise CloudflareBlockedError(
                        f"Cloudflare challenge unresolved after {MAX_RETRIES} attempts on page {page_num}"
                    )

                # Multi-strategy extraction
                result = self._extract_page_data(page_num)
                if result and result.listings:
                    return result

                # If no listings found, might be a timeout/loading issue
                logger.warning("No listings extracted on attempt %d/%d", attempt, MAX_RETRIES)
                if attempt < MAX_RETRIES:
                    backoff = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                    logger.info("Retrying in %ds...", backoff)
                    time.sleep(backoff)

            except PlaywrightTimeout:
                backoff = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                if attempt < MAX_RETRIES:
                    logger.warning(
                        "Timeout on page %d (attempt %d/%d), retrying in %ds",
                        page_num, attempt, MAX_RETRIES, backoff,
                    )
                    time.sleep(backoff)
                else:
                    logger.error("Failed to load page %d after %d attempts", page_num, MAX_RETRIES)
                    raise CloudflareBlockedError(
                        f"Could not load page after {MAX_RETRIES} attempts on page {page_num}"
                    )

            except json.JSONDecodeError as e:
                raise PageParseError(f"Failed to parse JSON: {e}")

        return None

    def _extract_page_data(self, page_num: int) -> ScrapedPage | None:
        """Try multiple strategies to extract listing data from the page."""

        # Strategy 1: __NEXT_DATA__ (legacy, fast check)
        result = self._try_next_data(page_num)
        if result and result.listings:
            logger.info("Extracted %d listings via __NEXT_DATA__", len(result.listings))
            self._record_page_stats(result, "__NEXT_DATA__")
            return result

        # Strategy 2: JSON in script tags or window globals
        result = self._try_script_json(page_num)
        if result and result.listings:
            logger.info("Extracted %d listings via script JSON", len(result.listings))
            self._record_page_stats(result, "script_json")
            return result

        # Strategy 3: Intercepted API responses
        result = self._try_intercepted_api(page_num)
        if result and result.listings:
            logger.info("Extracted %d listings via intercepted API", len(result.listings))
            self._record_page_stats(result, "intercepted_api")
            return result

        # Strategy 4: DOM extraction (wait for cards to render)
        result = self._try_dom_extraction(page_num)
        if result and result.listings:
            logger.info("Extracted %d listings via DOM", len(result.listings))
            self._record_page_stats(result, "dom")
            return result

        return None

    def _record_page_stats(self, result: ScrapedPage, strategy: str):
        """Record per-page stats from a successful extraction."""
        n = len(result.listings)
        self._stats.total_cards += n
        self._stats.parsed_ok += n
        self._stats.strategy_counts[strategy] = self._stats.strategy_counts.get(strategy, 0) + 1
        # Track missing fields
        key_fields = ["district", "latitude", "longitude", "facing", "tenure",
                       "built_year", "psf", "sqft"]
        for listing in result.listings:
            for fname in key_fields:
                if getattr(listing, fname, None) is None:
                    self._stats.fields_missing[fname] += 1

    def _try_next_data(self, page_num: int) -> ScrapedPage | None:
        """Strategy 1: Extract from __NEXT_DATA__ script tag."""
        try:
            # Script tags are hidden elements — use state="attached" not "visible"
            self._page.wait_for_selector(NEXT_DATA_SELECTOR, state="attached", timeout=NEXT_DATA_WAIT_TIMEOUT)
            raw = self._page.locator(NEXT_DATA_SELECTOR).text_content()
            next_data = json.loads(raw)

            return self._parse_next_data(next_data, page_num)
        except (PlaywrightTimeout, json.JSONDecodeError, Exception) as e:
            logger.debug("__NEXT_DATA__ not available: %s", e)
            return None

    def _try_script_json(self, page_num: int) -> ScrapedPage | None:
        """Strategy 2: Look for listing data in script tags or window globals."""
        try:
            script_data = self._page.evaluate(SCRIPT_JSON_EXTRACT_JS)
            if not script_data:
                return None

            for entry in script_data:
                data = entry.get("data", {})

                # Try parsing as __NEXT_DATA__ format
                if isinstance(data, dict) and "props" in data:
                    result = self._parse_next_data(data, page_num)
                    if result and result.listings:
                        return result

                # Try finding listing arrays in the data
                result = self._search_for_listings(data, page_num)
                if result and result.listings:
                    return result

        except Exception as e:
            logger.debug("Script JSON extraction failed: %s", e)
        return None

    def _try_intercepted_api(self, page_num: int) -> ScrapedPage | None:
        """Strategy 3: Use intercepted API responses."""
        # Wait a bit for any pending API calls to complete
        time.sleep(2)

        for entry in self._intercepted_data:
            data = entry.get("data", {})

            result = self._search_for_listings(data, page_num)
            if result and result.listings:
                return result

        return None

    def _try_dom_extraction(self, page_num: int) -> ScrapedPage | None:
        """Strategy 4: Extract listing data from rendered DOM elements."""
        # Wait for listing cards to appear
        card_found = False
        for selector in LISTING_CARD_SELECTORS:
            try:
                self._page.wait_for_selector(selector, timeout=DOM_CONTENT_WAIT_TIMEOUT)
                count = self._page.locator(selector).count()
                if count > 0:
                    logger.debug("Found %d elements matching '%s'", count, selector)
                    card_found = True
                    break
            except PlaywrightTimeout:
                continue

        if not card_found:
            # Last resort: wait for page to settle and try anyway
            logger.debug("No known listing card selectors matched, trying DOM extract anyway")
            time.sleep(3)

        try:
            dom_listings = self._page.evaluate(DOM_EXTRACT_JS)
            if not dom_listings:
                logger.debug("DOM extraction returned empty results")
                return None

            # Get pagination info from DOM
            try:
                pagination = self._page.evaluate(PAGINATION_EXTRACT_JS)
            except Exception:
                pagination = {"totalResults": 0, "totalPages": 1}

            listings = []
            for item in dom_listings:
                listing = self._parse_dom_listing(item)
                if listing:
                    listings.append(listing)
                else:
                    self._stats.parse_failures += 1

            if listings:
                return ScrapedPage(
                    page_number=page_num,
                    total_results=pagination.get("totalResults", len(listings)),
                    total_pages=pagination.get("totalPages", 1),
                    listings=listings,
                )

        except Exception as e:
            logger.warning("DOM extraction failed: %s", e)

        return None

    def _search_for_listings(self, data: dict, page_num: int, _depth: int = 0) -> ScrapedPage | None:
        """Recursively search a JSON structure for listing arrays."""
        if not isinstance(data, dict) or _depth > 8:
            return None

        # Look for common listing data keys
        for key in ["listingsData", "listings", "results", "data", "searchResults",
                     "properties", "items", "hits"]:
            val = data.get(key)
            if isinstance(val, list) and len(val) > 0:
                # Check if items look like listings (have id/title/price)
                sample = val[0]
                if isinstance(sample, dict) and any(
                    k in sample for k in ["id", "title", "price", "url", "listingId"]
                ):
                    logger.debug("Found listing array in key '%s' (%d items)", key, len(val))

                    # Look for pagination in same dict
                    pagination = data.get("pagination", {})
                    total_results = (
                        pagination.get("totalResultCount")
                        or pagination.get("total")
                        or data.get("totalResults")
                        or data.get("total")
                        or len(val)
                    )
                    total_pages = (
                        pagination.get("totalPages")
                        or data.get("totalPages")
                        or 1
                    )

                    listings = []
                    for item in val:
                        listing = self._parse_single_listing(item)
                        if listing:
                            listings.append(listing)
                        else:
                            self._stats.parse_failures += 1

                    if listings:
                        return ScrapedPage(
                            page_number=page_num,
                            total_results=total_results,
                            total_pages=total_pages,
                            listings=listings,
                        )

            # Recurse into nested dicts
            if isinstance(val, dict):
                result = self._search_for_listings(val, page_num, _depth + 1)
                if result and result.listings:
                    return result
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        result = self._search_for_listings(item, page_num, _depth + 1)
                        if result and result.listings:
                            return result

        return None

    # -----------------------------------------------------------------------
    # Parsing helpers
    # -----------------------------------------------------------------------

    def _parse_next_data(self, data: dict, page_num: int) -> ScrapedPage:
        try:
            props = data["props"]["pageProps"]
            page_data = props.get("pageData", props)
            inner_data = page_data.get("data", {})

            listings_data = (
                inner_data.get("listingsData", [])
                or page_data.get("listingsData", [])
            )

            # New format: pagination is at paginationData
            pagination = (
                inner_data.get("paginationData", {})
                or inner_data.get("pagination", {})
                or page_data.get("pagination", {})
            )

            total_results = (
                page_data.get("resultCount")
                or pagination.get("totalResultCount")
                or 0
            )
            total_pages = pagination.get("totalPages", 1)

        except (KeyError, TypeError) as e:
            raise PageParseError(f"Unexpected __NEXT_DATA__ structure: {e}")

        listings = []
        for item in listings_data:
            # New format: listing data is nested under listingData key
            inner = item.get("listingData", item) if isinstance(item, dict) else item
            listing = self._parse_single_listing(inner)
            if listing:
                listings.append(listing)
            else:
                self._stats.parse_failures += 1

        return ScrapedPage(
            page_number=page_num,
            total_results=total_results,
            total_pages=total_pages,
            listings=listings,
        )

    def _parse_dom_listing(self, item: dict) -> Listing | None:
        """Parse a listing extracted from DOM elements."""
        try:
            listing_id = str(item.get("id", ""))
            title = item.get("title", "")
            if not listing_id and not title:
                return None

            # Generate ID from URL if missing
            if not listing_id and item.get("url"):
                m = re.search(r'(\d{6,})', item["url"])
                if m:
                    listing_id = m.group(1)

            if not listing_id:
                listing_id = _stable_listing_id(
                    url=item.get("url", ""),
                    title=title,
                    address=item.get("address", ""),
                    price=item.get("price", 0),
                    sqft=item.get("sqft", 0),
                )

            # Parse PSF from text like "S$ 1,769 psf" or "S$ 2,489.91 psf"
            psf = None
            psf_text = item.get("psf_text", "")
            if psf_text:
                m = re.search(r'[\d]+\.?\d*', psf_text.replace(",", ""))
                if m:
                    psf = _safe_float(m.group(0))

            # Parse tags for tenure/property type
            tags = item.get("tags", [])

            # Use headline as description if available
            description = item.get("headline")

            # Parse listing date from recency text like "Listed on Feb 05, 2026 (1m ago)"
            listing_date = item.get("listing_date")
            if listing_date and "Listed on" in listing_date:
                # Extract just the date part
                m = re.search(r'Listed on ([A-Za-z]+ \d+, \d+)', listing_date)
                if m:
                    listing_date = m.group(1)

            return Listing(
                id=listing_id,
                title=title,
                price=item.get("price", 0),
                url=item.get("url", ""),
                address=item.get("address"),
                district=normalize_district(item.get("district", "")) or item.get("district"),
                beds=_safe_int(item.get("beds")),
                baths=_safe_int(item.get("baths")),
                sqft=_safe_float(item.get("sqft")),
                psf=psf,
                property_type=item.get("property_type"),
                tenure=item.get("tenure"),
                floor_level=item.get("floor_level"),
                built_year=_safe_int(item.get("built_year")),
                latitude=_safe_float(item.get("latitude")),
                longitude=_safe_float(item.get("longitude")),
                project_name=item.get("project_name"),
                mrt_info=item.get("mrt_info"),
                description=description,
                listing_date=listing_date,
                listing_agent=item.get("listing_agent"),
                image_url=item.get("image_url"),
                tags=tags if tags else [],
            )
        except Exception as e:
            logger.warning("Failed to parse DOM listing: %s", e)
            return None

    def _parse_single_listing(self, item: dict) -> Listing | None:
        """Parse a listing from JSON data (API or __NEXT_DATA__)."""
        try:
            listing_id = str(item.get("id") or item.get("listingId") or "")
            title = item.get("localizedTitle") or item.get("title") or item.get("name") or ""
            if not listing_id and not title:
                logger.debug("Skipping listing with missing id/title: %s", item.get("id"))
                return None

            # Price — new format has {value, pretty, currency}. For range-only
            # prices use the midpoint: taking max biased range listings up
            # (while the DOM fallback grabbed the first/min figure — the two
            # strategies disagreed on the same listing).
            price_raw = item.get("price", {})
            if isinstance(price_raw, dict):
                price = price_raw.get("value") or price_raw.get("amount")
                if not price:
                    p_min, p_max = price_raw.get("min"), price_raw.get("max")
                    if p_min and p_max:
                        price = (p_min + p_max) / 2
                    else:
                        price = p_min or p_max or 0
            else:
                price = price_raw or 0

            # URL — new format already has full URL
            url = item.get("url") or item.get("listingUrl") or ""
            if url and not url.startswith("http"):
                url = f"{PROPERTYGURU_BASE_URL}{url}"

            # Address — new format uses fullAddress + additionalData
            address = item.get("fullAddress") or ""
            additional = item.get("additionalData", {}) or {}

            # Legacy address format fallback
            if not address:
                address_raw = item.get("address", {})
                if isinstance(address_raw, dict):
                    address = address_raw.get("streetAddress") or address_raw.get("full") or address_raw.get("street", "")
                elif isinstance(address_raw, str):
                    address = address_raw

            # District info from additionalData (new format)
            district = additional.get("districtCode") or ""
            district_name = additional.get("districtText") or ""
            region = additional.get("regionText") or ""

            # Legacy district fallback
            if not district:
                address_raw = item.get("address", {})
                if isinstance(address_raw, dict):
                    district = address_raw.get("district") or address_raw.get("districtCode", "")
                    district_name = district_name or address_raw.get("districtName") or address_raw.get("areaName", "")
                    region = region or address_raw.get("region") or address_raw.get("regionName", "")

            # Lat/lng
            latitude = _safe_float(item.get("latitude") or item.get("lat"))
            longitude = _safe_float(item.get("longitude") or item.get("lng"))

            # Rooms — new format has bedrooms/bathrooms at top level
            beds = _safe_int(item.get("bedrooms") or item.get("beds") or item.get("bedroom"))
            baths = _safe_int(item.get("bathrooms") or item.get("baths") or item.get("bathroom"))

            # Area — new format has floorArea as number, area.localeStringValue as string.
            # NOTE: landArea is NOT a fallback for strata floor area — it inflated
            # area (understating PSF) for any listing that hit that path.
            sqft = _safe_float(item.get("floorArea") or item.get("size"))
            floor_area_sqm = _safe_float(item.get("floorAreaSqm") or item.get("floorAreaMetric"))
            land_area_sqft = _safe_float(item.get("landAreaSqft"))

            # Unit sanity: if floorArea was actually delivered in sqm (ratio to
            # the sqm field ~1 instead of ~10.76), convert — a silent sqm/sqft
            # mixup overstates PSF ~10.8x.
            if sqft and floor_area_sqm and floor_area_sqm > 0:
                if sqft / floor_area_sqm < 2.0:
                    sqft = floor_area_sqm * 10.7639
            elif not sqft and floor_area_sqm:
                sqft = floor_area_sqm * 10.7639

            # PSF — new format has pricePerArea.localeStringValue as string
            psf = _safe_float(item.get("psf") or item.get("pricePerSqft"))
            if psf is None:
                psf_text = item.get("psfText") or ""
                if not psf_text:
                    ppa = item.get("pricePerArea", {})
                    if isinstance(ppa, dict):
                        psf_text = ppa.get("localeStringValue", "")
                if psf_text:
                    m = re.search(r'[\d,]+\.?\d*', psf_text.replace(",", ""))
                    if m:
                        psf = _safe_float(m.group(0))

            # Project name — new format uses localizedTitle
            project_name = item.get("localizedTitle") or item.get("projectName")
            project = item.get("project", {})
            if isinstance(project, dict):
                project_name = project_name or project.get("name")

            # Developer — only use if it's actually a developer listing
            developer = None
            if item.get("isDeveloperListing"):
                developer_raw = item.get("developer")
                if isinstance(developer_raw, dict):
                    developer = developer_raw.get("name") or developer_raw.get("developerName")
                elif isinstance(developer_raw, str):
                    developer = developer_raw
            if not developer:
                developer = item.get("developerName")

            total_units = _safe_int(item.get("totalUnits"))
            if isinstance(project, dict):
                total_units = total_units or _safe_int(project.get("totalUnits"))

            # Dates — new format has postedOn.text / postedOn.unix
            posted = item.get("postedOn", {})
            if isinstance(posted, dict):
                listing_date = posted.get("text") or item.get("listingDate")
            else:
                listing_date = item.get("listingDate") or item.get("postedDate") or item.get("datePosted")

            # MRT info — new format has mrt.nearbyText
            mrt_info = _extract_mrt_info(item)

            # Description
            description = item.get("description") or item.get("shortDescription")
            if description and len(description) > 500:
                description = description[:500] + "..."

            # Facing
            facing = item.get("facing") or item.get("unitFacing")

            # Tenure — prefer human-readable from badges, fallback to additionalData code
            tenure = item.get("tenure") or item.get("tenureType")

            # Property type and built year from badges/listingFeatures
            property_type = item.get("propertyType") or item.get("propertyTypeName")
            built_year = _safe_int(item.get("builtYear") or item.get("completionYear"))

            # Parse badges for tenure/type/built_year (new format)
            tags = []
            badges = item.get("badges", [])
            if isinstance(badges, list):
                for badge in badges:
                    if isinstance(badge, dict):
                        text = badge.get("text", "")
                        name = badge.get("name", "")
                        if text:
                            tags.append(text)
                        if name == "tenure" and text and not tenure:
                            tenure = text
                        elif name == "unit_type" and text and not property_type:
                            property_type = text
                        elif name == "launch" and text and not built_year:
                            m = re.search(r'\d{4}', text)
                            if m:
                                built_year = int(m.group(0))
                    elif isinstance(badge, str):
                        tags.append(badge)

            # Fallback tenure from additionalData code
            if not tenure:
                tenure = additional.get("tenure")

            # Also check listingFeatures for data
            features = item.get("listingFeatures", [])
            if isinstance(features, list):
                for feat in features:
                    if isinstance(feat, dict):
                        text = feat.get("text", "")
                        auto_id = feat.get("dataAutomationId", "")
                        if "tenure" in auto_id and text and not tenure:
                            tenure = text
                        elif "unit-type" in auto_id and text and not property_type:
                            property_type = text
                        elif "build-year" in auto_id and text and not built_year:
                            m = re.search(r'\d{4}', text)
                            if m:
                                built_year = int(m.group(0))

            # Image — new format uses thumbnail
            image_url = item.get("thumbnail") or None
            if not image_url:
                images = item.get("images", []) or item.get("medias", []) or item.get("photos", [])
                if images and isinstance(images, list):
                    first = images[0]
                    if isinstance(first, dict):
                        image_url = first.get("url") or first.get("src")
                    elif isinstance(first, str):
                        image_url = first

            # Agent
            agent_name = _extract_agent_name(item)
            agent_phone = _extract_agent_phone(item)

            return Listing(
                id=listing_id,
                title=title,
                price=int(price),
                url=url,
                address=address or None,
                district=normalize_district(district or "") or (district or None),
                district_name=district_name or None,
                region=region or None,
                beds=beds,
                baths=baths,
                sqft=sqft,
                psf=psf,
                property_type=property_type,
                tenure=tenure,
                floor_level=item.get("floorLevel"),
                furnishing=item.get("furnishing"),
                facing=facing,
                built_year=built_year,
                top_year=_safe_int(item.get("topYear")),
                latitude=latitude,
                longitude=longitude,
                project_name=project_name,
                developer=developer,
                total_units=total_units,
                listing_date=listing_date,
                mrt_info=mrt_info,
                description=description,
                listing_agent=agent_name,
                agent_phone=agent_phone,
                image_url=image_url,
                floor_area_sqm=floor_area_sqm,
                land_area_sqft=land_area_sqft,
                tags=tags,
            )

        except Exception as e:
            logger.warning("Failed to parse listing: %s", e)
            return None

    def _propagate_coordinates(self, listings: list[Listing]) -> None:
        """Fill missing lat/lng using other listings from the same project."""
        by_project: dict[str, tuple[float, float]] = {}

        for listing in listings:
            key = (listing.project_name or listing.title or "").strip().lower()
            if not key:
                continue
            if listing.latitude is not None and listing.longitude is not None:
                by_project[key] = (listing.latitude, listing.longitude)

        if not by_project:
            return

        for listing in listings:
            if listing.latitude is not None and listing.longitude is not None:
                continue
            key = (listing.project_name or listing.title or "").strip().lower()
            if not key:
                continue
            coords = by_project.get(key)
            if coords:
                listing.latitude, listing.longitude = coords


def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _extract_agent_name(item: dict) -> str | None:
    agent = item.get("agent") or item.get("agentInfo") or {}
    if isinstance(agent, dict):
        return agent.get("name") or agent.get("agentName")
    return None


def _extract_agent_phone(item: dict) -> str | None:
    agent = item.get("agent") or item.get("agentInfo") or {}
    if isinstance(agent, dict):
        return agent.get("phone") or agent.get("mobile") or agent.get("contactNumber")
    return None


def _extract_mrt_info(item: dict) -> str | None:
    """Extract MRT proximity info from various possible field locations."""
    mrt = item.get("mrt") or item.get("nearestMrt")
    if mrt:
        if isinstance(mrt, dict):
            # New format: mrt.nearbyText = "6 min (540 m) from EW7 Eunos MRT Station"
            nearby = mrt.get("nearbyText")
            if nearby:
                return nearby
            # Legacy format
            name = mrt.get("name") or mrt.get("stationName", "")
            dist = mrt.get("distance") or mrt.get("distanceInMeters", "")
            walking = mrt.get("walkingTime") or mrt.get("walkingTimeInMins", "")
            parts = [name]
            if dist:
                parts.append(f"{dist}m")
            if walking:
                parts.append(f"{walking} min walk")
            return " - ".join(p for p in parts if p)
        return str(mrt)

    transport = item.get("transportInfo") or item.get("nearbyTransport", [])
    if isinstance(transport, list) and transport:
        first = transport[0]
        if isinstance(first, dict):
            return first.get("description") or first.get("name")
        return str(first)

    return None


def _stable_listing_id(url: str, title: str, address: str, price: int, sqft: float) -> str:
    """Generate a stable hash ID when listing id is missing."""
    raw = f"{url}|{title}|{address}|{price}|{sqft}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _sleep_with_jitter(base_seconds: float, jitter: float = 0.3) -> None:
    if base_seconds <= 0:
        return
    jitter = max(0.0, min(0.9, jitter))
    factor = random.uniform(1 - jitter, 1 + jitter)
    time.sleep(base_seconds * factor)
