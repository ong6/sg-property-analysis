from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import urlencode


@dataclass
class SearchParams:
    property_type: str = "C"  # C = Condo
    listing_type: str = "sale"
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    beds: Optional[list[int]] = None
    districts: Optional[list[int]] = None
    sort: str = "date"
    order: str = "desc"
    freetext: Optional[str] = None

    def to_url_path(self, page: int = 1) -> str:
        """Build URL path using PropertyGuru's path-based format.

        Format: /apartment-condo-for-sale/with-{N}-bedrooms[/{page}]
        Query params appended separately for price and districts.
        """
        # Property type path segment
        type_slugs = {
            "C": "apartment-condo",
            "N": "apartment-condo",
            "L": "hdb",
            "H": "landed",
        }
        type_slug = type_slugs.get(self.property_type, "property")
        path = f"/{type_slug}-for-{self.listing_type}"

        # Bedrooms path segment (only for single bed count)
        if self.beds and len(self.beds) == 1:
            path += f"/with-{self.beds[0]}-bedrooms"

        # Page number path segment
        if page > 1:
            path += f"/{page}"

        return path

    def to_query_string(self, page: int = 1) -> str:
        """Build query string for filters that use query params."""
        pairs: list[tuple[str, str]] = []
        if self.min_price is not None:
            pairs.append(("minPrice", str(self.min_price)))
        if self.max_price is not None:
            pairs.append(("maxPrice", str(self.max_price)))
        if self.districts:
            for d in self.districts:
                pairs.append(("districtCode", f"D{d:02d}"))
        # Multiple bed counts go as query params (path only supports single)
        if self.beds and len(self.beds) > 1:
            for b in self.beds:
                pairs.append(("bedrooms", str(b)))
        # Always emitted, even at the defaults. Date-desc is not a preference
        # here, it is a load-bearing assumption: the poller treats page depth as
        # "how far back one scan reaches" (weekly.POLL_MAX_PAGES), and
        # listings_db.sweep_staleness only dares call a listing missing when it
        # falls inside the recency window a date-desc scrape demonstrably
        # covered. Both are nonsense under a "Recommended"-style ordering.
        # Omitting the params left that ordering to PropertyGuru's default —
        # correct today by luck, and silently rotting the day the default moves.
        pairs.append(("sort", self.sort))
        pairs.append(("order", self.order))
        if self.freetext:
            pairs.append(("freetext", self.freetext))
        return urlencode(pairs) if pairs else ""


@dataclass
class Listing:
    id: str
    title: str
    price: int
    url: str
    address: Optional[str] = None
    district: Optional[str] = None
    district_name: Optional[str] = None
    region: Optional[str] = None
    beds: Optional[int] = None
    baths: Optional[int] = None
    sqft: Optional[float] = None
    psf: Optional[float] = None
    property_type: Optional[str] = None
    tenure: Optional[str] = None
    floor_level: Optional[str] = None
    furnishing: Optional[str] = None
    facing: Optional[str] = None
    built_year: Optional[int] = None
    top_year: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    project_name: Optional[str] = None
    developer: Optional[str] = None
    total_units: Optional[int] = None
    listing_date: Optional[str] = None
    mrt_info: Optional[str] = None
    description: Optional[str] = None
    listing_agent: Optional[str] = None
    agent_phone: Optional[str] = None
    image_url: Optional[str] = None
    floor_area_sqm: Optional[float] = None
    land_area_sqft: Optional[float] = None
    tags: Optional[list[str]] = field(default_factory=list)
    facilities: Optional[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Remove None values for cleaner JSON
        return {k: v for k, v in d.items() if v is not None and v != []}


@dataclass
class ScrapeStats:
    """Statistics collected during a scraping run."""
    total_cards: int = 0
    parsed_ok: int = 0
    parse_failures: int = 0
    duplicates_skipped: int = 0
    pages_fetched: int = 0
    strategy_counts: dict = field(default_factory=dict)  # strategy_name -> count
    fields_missing: Counter = field(default_factory=Counter)  # field_name -> count
    per_district_counts: dict = field(default_factory=dict)  # district -> listing count
    enriched_count: int = 0
    # Coverage bookkeeping. Without these the repo cannot state what fraction of
    # live inventory it holds — it knows what it fetched, never what it missed.
    # `scope_coverage` maps a scope label to {total_results, total_pages,
    # pages_fetched, truncated}; `truncated` marks a scope that stopped on an
    # empty page rather than a known last page, which is what a mid-scan
    # Cloudflare block or parse regression looks like from the outside.
    scope_coverage: dict = field(default_factory=dict)

    def merge(self, other: "ScrapeStats"):
        """Merge another ScrapeStats into this one."""
        self.total_cards += other.total_cards
        self.parsed_ok += other.parsed_ok
        self.parse_failures += other.parse_failures
        self.duplicates_skipped += other.duplicates_skipped
        self.pages_fetched += other.pages_fetched
        for k, v in other.strategy_counts.items():
            self.strategy_counts[k] = self.strategy_counts.get(k, 0) + v
        self.fields_missing.update(other.fields_missing)
        self.per_district_counts.update(other.per_district_counts)
        self.enriched_count += other.enriched_count
        self.scope_coverage.update(other.scope_coverage)

    def coverage_summary(self) -> dict:
        """Reach across every scope this run touched: how much was there, how
        much was fetched, and which scopes ended in the dark."""
        scopes = self.scope_coverage.values()
        available = sum(s.get("total_results") or 0 for s in scopes)
        return {
            "scopes": len(self.scope_coverage),
            "results_available": available,
            "results_seen": sum(s.get("listings_seen") or 0 for s in scopes),
            "pages_available": sum(s.get("total_pages") or 0 for s in scopes),
            "pages_fetched": sum(s.get("pages_fetched") or 0 for s in scopes),
            "truncated_scopes": sorted(k for k, s in self.scope_coverage.items()
                                       if s.get("truncated")),
            "capped_scopes": sorted(k for k, s in self.scope_coverage.items()
                                    if s.get("capped")),
        }

    def summary(self) -> str:
        lines = [
            f"  Pages fetched:      {self.pages_fetched}",
            f"  Cards found:        {self.total_cards}",
            f"  Parsed OK:          {self.parsed_ok}",
            f"  Parse failures:     {self.parse_failures}",
            f"  Duplicates skipped: {self.duplicates_skipped}",
        ]
        if self.per_district_counts:
            lines.append("  Per district:")
            for d, count in sorted(self.per_district_counts.items()):
                lines.append(f"    D{d:02d}: {count} listings")
        if self.strategy_counts:
            lines.append("  Extraction strategies:")
            for s, count in sorted(self.strategy_counts.items(), key=lambda x: -x[1]):
                lines.append(f"    {s}: {count} pages")
        if self.enriched_count:
            lines.append(f"  Enriched listings:  {self.enriched_count}")
        if self.fields_missing:
            top_missing = self.fields_missing.most_common(5)
            lines.append("  Top missing fields:")
            for field_name, count in top_missing:
                lines.append(f"    {field_name}: {count}")
        return "\n".join(lines)


@dataclass
class ScrapedPage:
    page_number: int
    total_results: int
    total_pages: int
    listings: list[Listing] = field(default_factory=list)


# Re-export scoring models for convenience
# These are defined in scoring/models.py for modularity
# but can be imported from here for backward compatibility
try:
    from scoring.models import QuickScore, ScoredListing, ROIResult, CostBreakdown
    __all__ = [
        "SearchParams", "Listing", "ScrapedPage", "ScrapeStats",
        "QuickScore", "ScoredListing", "ROIResult", "CostBreakdown"
    ]
except ImportError:
    # Scoring module not yet available
    __all__ = ["SearchParams", "Listing", "ScrapedPage", "ScrapeStats"]
