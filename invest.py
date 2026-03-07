#!/usr/bin/env python3
"""Unified property investment analysis CLI with URA integration.

AI-friendly interface for the complete investment analysis flow:
1. Auto-discover best districts (v2.2)
2. Scrape listings from PropertyGuru
3. Automatically use URA appreciation data (5 years historical)
4. Score properties with v2.2 scoring (includes future potential)
5. Generate investment reports

SCORING SYSTEM v2.2:
- Rental Yield: 15 pts (reduced - yields low at $2M+)
- Capital Appreciation: 30 pts (bias-adjusted URA rates)
- Future Potential: 20 pts (NEW - MRT, govt zones)
- Liquidity: 25 pts
- Cost Efficiency: 10 pts
- Red Flags: -10 pts max

Usage:
    # AUTOMATIC MODE (NEW - recommended)
    python invest.py --auto --beds 2,3 --top 10

    # Discovery only (see district rankings)
    python invest.py --discover-districts

    # Manual district selection
    python invest.py --districts 14 --beds 2,3

    # Multi-district analysis
    python invest.py --districts 3,5,14,15 --min-price 2000000 --max-price 2500000

    # Score existing listings file with URA data
    python invest.py --input output/listings.json --top 20

    # Fetch URA data by district (fastest - all projects in one go)
    python invest.py --fetch-ura-districts 3,5,14,15

    # Build URA cache from downloaded CSV files
    python invest.py --build-ura-cache data/ura_*.csv

    # List all districts for AI reference
    python invest.py --list-districts
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from glob import glob as file_glob

# Project imports
from scoring.full_scorer import score_listings, score_and_filter
from scoring.models import ScoredListing
from scoring.district_scorer import DistrictScorer
from scoring.raw_output import save_raw_analysis, load_reviewed_analysis, apply_review_to_listings
from utils.markdown import save_report, generate_csv_export, generate_report
from utils.geo import normalize_district
from config import SCORE_TIER1_MIN, SCORE_TIER2_MIN
try:
    from config import DATA_FRESHNESS_THRESHOLDS_DAYS
except ImportError:
    DATA_FRESHNESS_THRESHOLDS_DAYS = {}


# Paths
DATA_DIR = Path(__file__).parent / "data"
URA_CACHE_FILE = DATA_DIR / "ura_cache.json"
OUTPUT_DIR = Path(__file__).parent / "output"


def next_run_dir() -> Path:
    """Create and return the next sequential run directory (output/run_001, run_002, ...).

    Scans existing run_NNN directories to find the next number.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    existing = sorted(OUTPUT_DIR.glob("run_*"))
    max_num = 0
    for d in existing:
        try:
            num = int(d.name.split("_", 1)[1])
            max_num = max(max_num, num)
        except (IndexError, ValueError):
            continue
    run_dir = OUTPUT_DIR / f"run_{max_num + 1:03d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


# All Singapore districts with descriptions for AI context
DISTRICTS = {
    1: "Raffles Place, Marina Bay (CCR)",
    2: "Tanjong Pagar, Chinatown (CCR)",
    3: "Queenstown, Alexandra (RCR) ★",
    4: "Harbourfront, Telok Blangah (RCR)",
    5: "Clementi, West Coast (RCR) ★",
    6: "City Hall, Clarke Quay (CCR)",
    7: "Beach Road, Bugis (CCR)",
    8: "Farrer Park, Serangoon Rd (RCR)",
    9: "Orchard, River Valley (CCR)",
    10: "Tanglin, Holland (CCR)",
    11: "Newton, Novena (CCR)",
    12: "Toa Payoh, Balestier (RCR)",
    13: "Macpherson, Potong Pasir (RCR)",
    14: "Eunos, Geylang, Paya Lebar (RCR) ★",
    15: "East Coast, Marine Parade (RCR) ★",
    16: "Bedok, Upper East Coast (OCR)",
    17: "Changi, Loyang (OCR)",
    18: "Pasir Ris, Tampines (OCR)",
    19: "Punggol, Sengkang (OCR)",
    20: "Ang Mo Kio, Bishan (OCR)",
    21: "Clementi Park, Upper Bukit Timah (OCR)",
    22: "Boon Lay, Jurong (OCR)",
    23: "Bukit Batok, Bukit Panjang (OCR)",
    25: "Woodlands, Admiralty (OCR)",
    26: "Mandai, Upper Thomson (OCR)",
    27: "Sembawang, Yishun (OCR)",
    28: "Seletar, Yio Chu Kang (OCR)",
}


def load_ura_cache() -> dict:
    """Load URA appreciation data cache.

    Migrates old entries missing v2.2 bias fields by setting defaults.
    """
    if not URA_CACHE_FILE.exists():
        return {}
    with open(URA_CACHE_FILE) as f:
        data = json.load(f)
    projects = data.get("projects", {})

    # Migrate old entries missing v2.2 bias fields
    migrated = 0
    for key, entry in projects.items():
        if "has_new_launch_bias" not in entry:
            entry.setdefault("new_sale_count", 0)
            entry.setdefault("new_sale_proportion", 0.0)
            entry.setdefault("resale_transaction_count", 0)
            entry.setdefault("resale_annualized_appreciation", None)
            entry.setdefault("has_new_launch_bias", False)
            migrated += 1
    if migrated > 0:
        print(f"  URA cache: migrated {migrated} old entries (missing bias fields)", file=sys.stderr)

    return projects


def save_ura_cache(projects: dict):
    """Save URA appreciation data to cache."""
    cache = {
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
        "projects": projects,
    }
    URA_CACHE_FILE.parent.mkdir(exist_ok=True)
    with open(URA_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)
    print(f"URA cache saved: {len(projects)} projects", file=sys.stderr)


def warn_if_stale_data():
    """Warn if key local datasets are stale based on last_updated."""
    if not DATA_FRESHNESS_THRESHOLDS_DAYS:
        return

    warnings = []
    for filename, max_days in DATA_FRESHNESS_THRESHOLDS_DAYS.items():
        path = DATA_DIR / filename
        if not path.exists():
            warnings.append(f"{filename}: missing")
            continue
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            warnings.append(f"{filename}: unreadable")
            continue

        last_updated = data.get("last_updated")
        if not last_updated:
            warnings.append(f"{filename}: missing last_updated")
            continue

        dt = None
        try:
            dt = datetime.fromisoformat(last_updated)
        except ValueError:
            try:
                dt = datetime.strptime(last_updated, "%Y-%m-%d")
            except ValueError:
                warnings.append(f"{filename}: invalid last_updated '{last_updated}'")
                continue

        days_old = (datetime.now() - dt).days
        if days_old > max_days:
            warnings.append(f"{filename}: {days_old} days old (>{max_days})")

    if warnings:
        print("\nData freshness warnings:", file=sys.stderr)
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)


def build_ura_cache_from_csv(csv_patterns: list[str]) -> dict:
    """Build URA cache from downloaded CSV files (supports glob patterns)."""
    from scrapers.ura_scraper import load_ura_csv

    cache = load_ura_cache()

    # Expand glob patterns
    csv_files = []
    for pattern in csv_patterns:
        expanded = file_glob(pattern)
        if expanded:
            csv_files.extend(expanded)
        elif os.path.exists(pattern):
            csv_files.append(pattern)
        else:
            print(f"Warning: No files match: {pattern}", file=sys.stderr)

    if not csv_files:
        print("No CSV files found", file=sys.stderr)
        return cache

    print(f"\nProcessing {len(csv_files)} CSV file(s)...", file=sys.stderr)

    for csv_path in csv_files:
        print(f"  {csv_path}", file=sys.stderr)
        try:
            history = load_ura_csv(csv_path)

            if history.transactions:
                project_key = history.project_name.lower()
                cache_entry = {
                    "project_name": history.project_name,
                    "transaction_count": history.transaction_count,
                    "avg_psf_current": history.avg_psf_current_year,
                    "avg_psf_5yr_ago": history.avg_psf_5yr_ago,
                    "appreciation_1yr": history.appreciation_1yr,
                    "appreciation_3yr": history.appreciation_3yr,
                    "appreciation_5yr": history.appreciation_5yr,
                    "annualized_appreciation": history.annualized_appreciation,
                    "source": "ura_5yr_cagr" if history.annualized_appreciation else "ura_data",
                    "updated": datetime.now().strftime("%Y-%m-%d"),
                    # New launch bias detection fields
                    "new_sale_count": history.new_sale_count,
                    "new_sale_proportion": round(history.new_sale_proportion, 3),
                    "resale_transaction_count": history.resale_transaction_count,
                    "resale_annualized_appreciation": history.resale_annualized_appreciation,
                    "has_new_launch_bias": history.has_new_launch_bias,
                    # v2.2: Momentum and data quality
                    "appreciation_momentum": (
                        round(history.appreciation_momentum, 3)
                        if history.appreciation_momentum is not None else None
                    ),
                    "data_coverage": history.data_coverage,
                }
                cache[project_key] = cache_entry

                # Log with new-launch bias info
                bias_tag = " [NEW LAUNCH BIAS]" if history.has_new_launch_bias else ""
                if history.annualized_appreciation:
                    resale_info = ""
                    if history.resale_annualized_appreciation is not None:
                        resale_info = f", resale-only: {history.resale_annualized_appreciation:+.1f}%/yr"
                    print(f"    -> {history.project_name}: {history.annualized_appreciation:+.1f}%/yr "
                          f"({history.transaction_count} txns, "
                          f"{history.new_sale_count} new sale){resale_info}{bias_tag}",
                          file=sys.stderr)
                else:
                    print(f"    -> {history.project_name}: insufficient data for CAGR", file=sys.stderr)
        except Exception as e:
            print(f"    -> Error: {e}", file=sys.stderr)

    save_ura_cache(cache)
    return cache


def scrape_listings(
    districts: list[int],
    min_price: int,
    max_price: int,
    beds: list[int],
    max_pages: int = 5,
    headless: bool = True,
    enrich_top: int = 0,
) -> list[dict]:
    """Scrape listings from PropertyGuru with per-district scraping."""
    from scrapers.browser import BrowserManager
    from scrapers.propertyguru import PropertyGuruScraper
    from models import SearchParams

    params = SearchParams(
        property_type="C",  # Condo
        listing_type="sale",
        min_price=min_price,
        max_price=max_price,
        beds=beds,
        districts=districts,
    )

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"SCRAPING PROPERTYGURU (per-district mode)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"  Districts: {districts} ({len(districts)} districts)", file=sys.stderr)
    print(f"  Price: ${min_price:,} - ${max_price:,}", file=sys.stderr)
    print(f"  Beds: {beds}", file=sys.stderr)
    print(f"  Max pages per district: {max_pages}", file=sys.stderr)
    if enrich_top > 0:
        print(f"  Enrich top: {enrich_top} listings", file=sys.stderr)

    with BrowserManager(headless=headless) as context:
        scraper = PropertyGuruScraper(context)
        listings, stats = scraper.scrape_multi_district(params, max_pages=max_pages)

        # Enrich top listings if requested
        if enrich_top > 0 and listings:
            scraper.enrich_listings(listings, top_n=enrich_top)
            stats = scraper.stats  # refresh stats after enrichment

    # Convert Listing objects to dicts
    result = [l.to_dict() if hasattr(l, 'to_dict') else l for l in listings]

    # Print scraping stats
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"SCRAPING STATS", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(stats.summary(), file=sys.stderr)
    print(f"  Final result: {len(result)} listings", file=sys.stderr)

    return result


def scrape_condo_listings(
    condo_name: str,
    min_price: int,
    max_price: int,
    beds: list[int],
    max_pages: int = 5,
    headless: bool = True,
    enrich_top: int = 0,
    fuzzy: bool = True,
    max_results: int = 80,
) -> list[dict]:
    """Scrape listings by condo name (fuzzy search via freetext)."""
    from scrapers.browser import BrowserManager
    from scrapers.propertyguru import PropertyGuruScraper
    from models import SearchParams
    from difflib import SequenceMatcher
    import re

    def _norm(text: str) -> str:
        text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
        return " ".join(text.split())

    def _score(title: str, project: str) -> float:
        target = _norm(condo_name)
        cand = _norm(project or title)
        if not target or not cand:
            return 0.0
        if target == cand:
            return 1.0
        return SequenceMatcher(None, target, cand).ratio()

    params = SearchParams(
        property_type="C",
        listing_type="sale",
        min_price=min_price,
        max_price=max_price,
        beds=beds,
        freetext=condo_name,
    )

    print(f"\n{'='*60}", file=sys.stderr)
    print("SCRAPING PROPERTYGURU (condo-name mode)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"  Condo: {condo_name}", file=sys.stderr)
    print(f"  Price: ${min_price:,} - ${max_price:,}", file=sys.stderr)
    print(f"  Beds: {beds}", file=sys.stderr)
    print(f"  Max pages: {max_pages}", file=sys.stderr)
    print(f"  Fuzzy matching: {'ON' if fuzzy else 'OFF'}", file=sys.stderr)
    if enrich_top > 0:
        print(f"  Enrich top: {enrich_top} listings", file=sys.stderr)

    with BrowserManager(headless=headless) as context:
        scraper = PropertyGuruScraper(context)
        listings = scraper.scrape(params, max_pages=max_pages)
        if enrich_top > 0 and listings:
            scraper.enrich_listings(listings, top_n=enrich_top)

    result = [l.to_dict() if hasattr(l, 'to_dict') else l for l in listings]

    def _passes(item: dict) -> bool:
        title = item.get("title", "")
        project = item.get("project_name", "")
        score = _score(title, project)
        item["condo_match_score"] = round(score, 3)
        if not fuzzy:
            return score >= 1.0
        return score >= 0.65

    filtered = [item for item in result if _passes(item)]
    filtered.sort(key=lambda x: x.get("condo_match_score", 0), reverse=True)
    if max_results and len(filtered) > max_results:
        filtered = filtered[:max_results]

    print(f"\n{'='*60}", file=sys.stderr)
    print("SCRAPING STATS", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"  Listings fetched: {len(result)}", file=sys.stderr)
    print(f"  Listings matched: {len(filtered)}", file=sys.stderr)

    return filtered


def deduplicate_by_project(scored: list[ScoredListing]) -> list[ScoredListing]:
    """Group listings from the same condo into one entry.

    Keeps the highest-scoring listing as the primary and appends
    other listing URLs to its additional_urls field.
    Listings are assumed to be sorted by total_score descending.
    """
    seen: dict[str, ScoredListing] = {}  # project_key -> primary listing
    result = []

    for listing in scored:
        key = (listing.project_name or listing.title or "").strip().lower()
        if not key:
            result.append(listing)
            continue

        if key not in seen:
            seen[key] = listing
            result.append(listing)
        else:
            # Append this listing's URL to the primary entry
            primary = seen[key]
            if listing.url and listing.url not in primary.additional_urls:
                primary.additional_urls.append(listing.url)

    return result


def group_condo_by_unit_type(scored: list[ScoredListing]) -> list[ScoredListing]:
    """Group condo listings by (project_name, beds) for per-unit-type analysis.

    Instead of collapsing all units into one entry, this keeps one
    representative per bedroom type with full unit variant data.
    The median-priced listing is chosen as representative (within the
    same condo, scores are similar — price is the differentiator).
    """
    from collections import defaultdict
    import statistics

    groups: dict[tuple[str, int], list[ScoredListing]] = defaultdict(list)

    for listing in scored:
        key_name = (listing.project_name or listing.title or "").strip().lower()
        key_beds = listing.beds or 0
        groups[(key_name, key_beds)].append(listing)

    result = []
    for (proj_key, beds), members in groups.items():
        if not proj_key:
            result.extend(members)
            continue

        # Sort by price to pick median
        members.sort(key=lambda s: s.price)
        median_idx = len(members) // 2
        representative = members[median_idx]

        # Build unit_variants list
        variants = []
        for m in members:
            variant = {
                "price": m.price,
                "psf": m.psf,
                "sqft": m.sqft,
                "floor_level": m.floor_level,
                "facing": m.facing,
                "url": m.url,
            }
            variants.append(variant)

        representative.unit_variants = variants

        # Build unit_summary
        prices = [m.price for m in members]
        psfs = [m.psf for m in members if m.psf]
        floors = [m.floor_level for m in members if m.floor_level]
        representative.unit_summary = {
            "count": len(members),
            "price_range": [min(prices), max(prices)] if prices else [],
            "psf_range": [min(psfs), max(psfs)] if psfs else [],
            "floors": sorted(set(floors)) if floors else [],
        }

        # Collect additional URLs (all URLs except the representative's)
        representative.additional_urls = [
            m.url for m in members if m.url and m.url != representative.url
        ]

        result.append(representative)

    # Sort by (beds, -total_score)
    result.sort(key=lambda s: (s.beds or 0, -s.total_score))
    return result


def print_results(scored: list[ScoredListing], top_n: int = 10, verbose: bool = False):
    """Print analysis results to console (v2.2 format)."""
    print(f"\n{'='*70}")
    print("INVESTMENT ANALYSIS RESULTS (v2.2)")
    print(f"{'='*70}")

    # Calculate stats
    scores = [s.total_score for s in scored]
    avg_score = sum(scores) / len(scores) if scores else 0
    min_score = min(scores) if scores else 0
    max_score = max(scores) if scores else 0

    # Count URA data usage
    ura_count = sum(1 for s in scored if s.has_ura_data)

    # Count tiers with new thresholds
    tier1 = [s for s in scored if s.final_tier == 1]
    tier2 = [s for s in scored if s.final_tier == 2]
    tier3 = [s for s in scored if s.final_tier == 3]

    print(f"  Total Analyzed: {len(scored)}")
    print(f"  Score Range: {min_score:.1f} - {max_score:.1f} (avg: {avg_score:.1f})")
    print(
        f"  Tier 1 (>= {SCORE_TIER1_MIN}): {len(tier1)} | "
        f"Tier 2 ({SCORE_TIER2_MIN}-{SCORE_TIER1_MIN - 1}): {len(tier2)} | "
        f"Tier 3 (< {SCORE_TIER2_MIN}): {len(tier3)}"
    )
    print(f"  Real Transaction Data: {ura_count}/{len(scored)} properties")
    print()

    # Top listings
    show_n = min(top_n, len(scored))
    print(f"TOP {show_n} PROPERTIES:")
    print("-" * 70)

    for i, s in enumerate(scored[:show_n], 1):
        apr_pct = s.appreciation_rate * 100
        ura_tag = "[URA]" if s.has_ura_data else "     "
        roi_str = f"{s.roi_5yr.annualized_roi:.1f}%" if s.roi_5yr else "-"

        # Truncate title
        title = s.title[:28] + ".." if len(s.title) > 30 else s.title

        # Future potential indicator
        future_str = ""
        if s.future_score_details:
            if s.future_score_details.nearest_future_mrt:
                future_str = f"MRT:{s.future_score_details.nearest_future_mrt[:8]}"
            elif s.future_score_details.govt_zones:
                future_str = s.future_score_details.govt_zones[0][:12]

        extra = f" (+{len(s.additional_urls)} more)" if s.additional_urls else ""
        print(f"#{i:2d} [{s.total_score:4.1f}]{ura_tag} {title:<30} ${s.price/1e6:.2f}M  "
              f"{apr_pct:+.0f}%/yr  {future_str}{extra}")

    if verbose:
        print(f"\n\n{'='*70}")
        print("DETAILED ANALYSIS (v2.2 Scoring)")
        print(f"{'='*70}")

        for i, s in enumerate(scored[:min(5, top_n)], 1):
            print(f"\n--- #{i} {s.title} ---")
            print(f"  Price: ${s.price:,} | PSF: ${s.psf:,.0f}" if s.psf else f"  Price: ${s.price:,}")
            district_label = normalize_district(s.district or "")
            print(f"  District: {district_label} | Beds: {s.beds} | Sqft: {s.sqft:,.0f}" if s.sqft else "")
            print(f"  Tenure: {s.tenure} | Built: {s.built_year}")
            if s.nearest_mrt:
                print(f"  MRT: {s.nearest_mrt} ({s.mrt_distance_m}m)")
            print()

            # v2.2 Score breakdown
            print(f"  TOTAL SCORE: {s.total_score:.1f}/100")
            print(f"    Rental Yield:      {s.rental_yield_score:5.1f}/15")
            print(f"    Appreciation:      {s.capital_appreciation_score:5.1f}/30")
            print(f"    Future Potential:  {s.future_potential_score:5.1f}/20")
            print(f"    Liquidity:         {s.liquidity_score:5.1f}/25")
            print(f"    Cost Efficiency:   {s.cost_efficiency_score:5.1f}/10")
            if s.red_flag_deductions > 0:
                print(f"    Red Flags:        -{s.red_flag_deductions:5.1f}")

            # Future details
            if s.future_score_details:
                fd = s.future_score_details
                print(f"\n  Future Catalysts:")
                if fd.nearest_future_mrt:
                    print(f"    MRT: {fd.nearest_future_mrt} ({fd.future_mrt_distance_m}m) - {fd.future_mrt_line}")
                if fd.govt_zones:
                    print(f"    Govt Zones: {', '.join(fd.govt_zones)}")

            print()
            print(f"  Rental: ${s.estimated_monthly_rent:,.0f}/mo = {s.estimated_gross_yield:.2f}% yield")

            apr_pct = s.appreciation_rate * 100
            print(f"  Appreciation: {apr_pct:+.1f}%/yr ({s.appreciation_source})")

            if s.roi_5yr:
                print(f"\n  ROI Projections:")
                print(f"    5yr: Exit ${s.roi_5yr.estimated_exit_price:,} -> {s.roi_5yr.annualized_roi:.2f}%/yr")
                if s.roi_7yr:
                    print(f"    7yr: Exit ${s.roi_7yr.estimated_exit_price:,} -> {s.roi_7yr.annualized_roi:.2f}%/yr")

            print(f"\n  URL: {s.url}")
            if s.additional_urls:
                for extra_url in s.additional_urls:
                    print(f"  URL: {extra_url}")


def discover_districts(top_n: int = 5, exclude_ccr: bool = True, region: str = None) -> list[int]:
    """Use DistrictScorer to find top investment districts.

    Args:
        top_n: Number of districts to return
        exclude_ccr: Exclude CCR (high entry cost) by default
        region: Optional filter for specific region

    Returns:
        List of district numbers
    """
    scorer = DistrictScorer()
    return scorer.get_top_districts(top_n=top_n, exclude_ccr=exclude_ccr, region_filter=region)


def print_district_discovery():
    """Print district discovery/ranking results."""
    scorer = DistrictScorer()
    scores = scorer.score_all_districts()

    print("\n" + "=" * 80)
    print("AI-RECOMMENDED DISTRICTS FOR INVESTMENT (v2.2)")
    print("=" * 80)
    print("\nScoring: 30% Historical + 20% Liquidity + 25% Future Infra + 20% Govt Priority + 5% Supply")
    print()

    # Print by region
    for region in ["RCR", "OCR", "CCR"]:
        region_scores = [s for s in scores if s.region == region][:5]
        if not region_scores:
            continue

        region_label = {
            "RCR": "REST OF CENTRAL REGION (Best Value)",
            "OCR": "OUTSIDE CENTRAL REGION (Growth Potential)",
            "CCR": "CORE CENTRAL REGION (Premium)"
        }[region]

        print(f"\n{region_label}:")
        print("-" * 80)
        print(f"{'Rank':<5}{'District':<35}{'Score':<8}{'Key Catalysts'}")
        print("-" * 80)

        for i, s in enumerate(region_scores, 1):
            district_str = f"D{s.district:02d} - {s.name}"[:33]
            catalysts = ", ".join(s.key_catalysts[:3]) if s.key_catalysts else "-"
            print(f"{i:<5}{district_str:<35}{s.total_score:<8.1f}{catalysts}")

    print("\n" + "=" * 80)
    print("Tip: Use --auto to automatically select top districts and scrape listings")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Property investment analyzer with URA data integration (v2.2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
SCORING SYSTEM v2.2 (no URA bias):
  Rental Yield: 15 pts | Capital Appreciation: 30 pts | Future Potential: 20 pts
  Liquidity: 25 pts | Cost Efficiency: 10 pts | Red Flags: -10 pts

Examples:
  # AUTOMATIC MODE (recommended) - discovers best districts automatically
  python invest.py --auto --beds 2,3 --top 10

  # Discovery only - see AI district rankings
  python invest.py --discover-districts

  # Manual district selection
  python invest.py --districts 3,5,14,15 --beds 2,3

  # Analyze OCR districts
  python invest.py --districts 16,18,19 --min-price 1500000 --max-price 2000000

  # Fetch URA data by district (fastest way to get all transaction data)
  python invest.py --fetch-ura-districts 3,5,14,15
  python invest.py --fetch-ura-districts all

  # Build URA cache from downloaded CSVs
  python invest.py --build-ura-cache data/ura_*.csv

  # Score existing listings with URA data
  python invest.py --input output/listings_*.json --top 20
        """
    )

    # NEW: Auto mode
    parser.add_argument("--auto", action="store_true",
                       help="Automatic mode: discover top districts, scrape, score (recommended)")
    parser.add_argument("--discover-districts", action="store_true",
                       help="Show AI district rankings without scraping")
    parser.add_argument("--auto-districts", type=int, default=5,
                       help="Number of districts for auto mode (default: 5)")
    parser.add_argument("--include-ccr", action="store_true",
                       help="Include CCR districts in auto mode (default: exclude)")

    # Scraping options
    parser.add_argument("--districts", "-d", type=str,
                       help="Comma-separated district numbers (e.g., 3,5,14,15)")
    parser.add_argument("--condo", type=str,
                       help="Condo/project name to search for (fuzzy matching by default)")
    parser.add_argument("--condo-max-pages", type=int, default=5,
                       help="Max pages to scrape for condo search (default: 5)")
    parser.add_argument("--condo-max-results", type=int, default=80,
                       help="Max listings to keep for condo search (default: 80)")
    parser.add_argument("--condo-fuzzy", dest="condo_fuzzy", action="store_true", default=True,
                       help="Enable fuzzy condo-name matching (default: enabled)")
    parser.add_argument("--condo-exact", dest="condo_fuzzy", action="store_false",
                       help="Exact match only (disables fuzzy matching)")
    parser.add_argument("--min-price", type=int, default=2000000,
                       help="Minimum price (default: 2000000)")
    parser.add_argument("--max-price", type=int, default=3000000,
                       help="Maximum price (default: 3000000)")
    parser.add_argument("--beds", "-b", type=str, default="2,3",
                       help="Bedroom counts (default: 2,3)")
    parser.add_argument("--max-pages", type=int, default=5,
                       help="Max pages to scrape per district (default: 5)")
    parser.add_argument("--enrich-top", type=int, default=0,
                       help="Enrich top N listings by visiting detail pages (default: 0 = disabled)")

    # Input/output options
    parser.add_argument("--input", "-i", type=str,
                       help="Use existing listings JSON file (supports glob)")
    parser.add_argument("--output", "-o", type=str,
                       help="Output path for markdown report (without extension)")
    parser.add_argument("--csv", type=str,
                       help="Output path for CSV export")
    parser.add_argument("--json", type=str,
                       help="Output path for scored JSON")
    parser.add_argument("--raw", type=str, metavar="PATH",
                       help="Output raw analysis JSON for AI agent review")
    parser.add_argument("--from-review", type=str, metavar="PATH",
                       help="Generate final report from AI-reviewed analysis JSON")

    # URA data options
    parser.add_argument("--build-ura-cache", nargs="+", metavar="CSV",
                       help="Build URA cache from CSV files (supports glob patterns)")
    parser.add_argument("--fetch-ura-districts", type=str, metavar="DISTRICTS",
                       help="Fetch URA data by postal district (e.g., '3,5,14,15' or 'all')")

    # Display options
    parser.add_argument("--top", "-n", type=int, default=10,
                       help="Number of top properties to show (default: 10)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Show detailed score breakdowns")
    parser.add_argument("--no-headless", action="store_true",
                       help="Show browser window (default: headless)")
    parser.add_argument("--list-districts", action="store_true",
                       help="List all districts and exit")

    args = parser.parse_args()
    headless = not args.no_headless

    # Handle --discover-districts (NEW)
    if args.discover_districts:
        print_district_discovery()
        return

    # Handle --list-districts
    if args.list_districts:
        print("\nSingapore Districts for Property Investment:\n")
        print("RCR Prime (★ = best for investment):")
        for d in [3, 5, 14, 15]:
            print(f"  D{d:02d}: {DISTRICTS[d]}")
        print("\nRCR Other:")
        for d in [4, 8, 12, 13]:
            print(f"  D{d:02d}: {DISTRICTS[d]}")
        print("\nOCR (Higher yield, more affordable):")
        for d in [16, 18, 19, 20]:
            print(f"  D{d:02d}: {DISTRICTS[d]}")
        print("\nCCR (Premium, lower yield):")
        for d in [1, 2, 6, 7, 9, 10, 11]:
            print(f"  D{d:02d}: {DISTRICTS[d]}")
        print("\nTip: Use --discover-districts to see AI-ranked districts")
        return

    # Handle --from-review (standalone mode: generate report from reviewed JSON)
    if args.from_review:
        print(f"\nLoading reviewed analysis: {args.from_review}", file=sys.stderr)
        report_level, reviewed_listings = load_reviewed_analysis(args.from_review)
        print(f"  Loaded {len(reviewed_listings)} listings", file=sys.stderr)

        # Reconstruct ScoredListing objects from reviewed data
        scored = []
        for entry in reviewed_listings:
            listing = ScoredListing(
                id=str(entry.get("rank", "")),
                title=entry.get("title", ""),
                price=entry.get("price", 0),
                url=entry.get("url", ""),
                district=entry.get("district"),
                beds=entry.get("beds"),
                sqft=entry.get("sqft"),
                psf=entry.get("psf"),
                tenure=entry.get("tenure"),
                built_year=entry.get("built_year"),
                project_name=entry.get("project_name"),
            )

            # Restore algo scores from breakdown
            ab = entry.get("algo_breakdown", {})
            listing.rental_yield_score = ab.get("rental_yield", {}).get("score", 0)
            listing.capital_appreciation_score = ab.get("capital_appreciation", {}).get("score", 0)
            listing.future_potential_score = ab.get("future_potential", {}).get("score", 0)
            listing.liquidity_score = ab.get("liquidity", {}).get("score", 0)
            listing.cost_efficiency_score = ab.get("cost_efficiency", {}).get("score", 0)
            listing.red_flag_deductions = ab.get("red_flags", {}).get("deductions", 0)

            # Restore remaining lease and MRT
            listing.remaining_lease = entry.get("remaining_lease")
            mrt_str = entry.get("nearest_mrt")
            if mrt_str:
                # Parse "Station Name (123m)" format
                import re
                m = re.match(r"(.+?)\s*\((\d+)m\)", mrt_str)
                if m:
                    listing.nearest_mrt = m.group(1)
                    listing.mrt_distance_m = int(m.group(2))
                else:
                    listing.nearest_mrt = mrt_str

            # Restore rental & appreciation
            rental_info = ab.get("rental_yield", {})
            listing.estimated_monthly_rent = rental_info.get("monthly_rent_est") or 0
            listing.estimated_gross_yield = rental_info.get("gross_yield_pct") or 0
            listing.rent_source = rental_info.get("rent_source") or ""

            cap_info = ab.get("capital_appreciation", {})
            listing.appreciation_rate = (cap_info.get("rate_pct") or 2) / 100
            listing.appreciation_source = cap_info.get("source") or "default"
            if entry.get("roi_sensitivity"):
                listing.roi_sensitivity = entry.get("roi_sensitivity")

            # Restore ROI projections
            from scoring.models import ROIResult
            roi_data = entry.get("roi_projections", {})
            for period_key, attr in [("5yr", "roi_5yr"), ("6yr", "roi_6yr"), ("7yr", "roi_7yr")]:
                roi_entry = roi_data.get(period_key)
                if roi_entry:
                    roi = ROIResult(
                        hold_years=int(period_key[0]),
                        purchase_price=listing.price,
                        estimated_exit_price=roi_entry.get("exit_price", 0),
                        total_rental_income=0,
                        total_holding_costs=0,
                        total_upfront_costs=listing.price,
                        total_exit_costs=0,
                        gross_rental_yield=0,
                        net_rental_yield=0,
                        capital_gain=roi_entry.get("exit_price", 0) - listing.price,
                        total_return=roi_entry.get("total_return", 0),
                        roi_percent=roi_entry.get("roi_pct", 0),
                        annualized_roi=roi_entry.get("annualized_roi", 0),
                    )
                    setattr(listing, attr, roi)

            # Apply agent fields
            listing.agent_summary = entry.get("agent_summary")
            listing.agent_red_flags = entry.get("agent_red_flags", [])
            listing.agent_catalysts = entry.get("agent_catalysts", [])
            listing.agent_score_adjustment = entry.get("agent_score_adjustment", 0)
            listing.agent_adjustment_reason = entry.get("agent_adjustment_reason")
            listing.agent_rental_assessment = entry.get("agent_rental_assessment")
            listing.agent_appreciation_assessment = entry.get("agent_appreciation_assessment")
            listing.agent_stack_notes = entry.get("agent_stack_notes")
            listing.agent_confidence = entry.get("agent_confidence")
            listing.agent_appreciation_rate_pct = entry.get("agent_appreciation_rate_pct")
            listing.agent_appreciation_source = entry.get("agent_appreciation_source")

            # Apply AI appreciation override (if provided)
            if listing.agent_appreciation_rate_pct is not None:
                try:
                    agent_rate_pct = float(listing.agent_appreciation_rate_pct)
                except (TypeError, ValueError):
                    agent_rate_pct = None
                if agent_rate_pct is not None:
                    listing.appreciation_rate = agent_rate_pct / 100
                    listing.appreciation_source = "agent_override"

                    # Recompute capital appreciation score using stored components
                    cap_components = cap_info.get("components", {})
                    if cap_components:
                        from scoring.full_scorer import FullScorer
                        rate_points = FullScorer.score_appreciation_rate_points(
                            agent_rate_pct,
                            listing.appreciation_source,
                        )
                        other_points = (
                            (cap_components.get("momentum_points") or 0)
                            + (cap_components.get("psf_vs_median_points") or 0)
                            + (cap_components.get("tenure_points") or 0)
                            + (cap_components.get("property_age_points") or 0)
                        )
                        listing.capital_appreciation_score = rate_points + other_points
                    else:
                        from scoring.full_scorer import FullScorer
                        rate_points = FullScorer.score_appreciation_rate_points(
                            agent_rate_pct,
                            listing.appreciation_source,
                        )
                        prev_rate_pct = cap_info.get("rate_pct")
                        prev_source = cap_info.get("source") or listing.appreciation_source
                        try:
                            prev_rate_points = (
                                FullScorer.score_appreciation_rate_points(float(prev_rate_pct), prev_source)
                                if prev_rate_pct is not None
                                else None
                            )
                        except (TypeError, ValueError):
                            prev_rate_points = None
                        total_cap = cap_info.get("score", listing.capital_appreciation_score)
                        if prev_rate_points is not None:
                            listing.capital_appreciation_score = total_cap - prev_rate_points + rate_points
                        else:
                            listing.capital_appreciation_score = rate_points

                    # Recompute ROI projections
                    from scoring.roi import ROICalculator
                    roi_calc = ROICalculator()
                    listing_dict = {
                        "price": listing.price,
                        "sqft": listing.sqft,
                        "district": listing.district,
                    }
                    roi_results = roi_calc.calculate_multiple_periods(
                        listing_dict,
                        periods=[5, 6, 7],
                        monthly_rent=listing.estimated_monthly_rent if listing.estimated_monthly_rent > 0 else None,
                        appreciation_rate=listing.appreciation_rate,
                    )
                    listing.roi_5yr = roi_results.get(5)
                    listing.roi_6yr = roi_results.get(6)
                    listing.roi_7yr = roi_results.get(7)
                    sensitivity = roi_calc.calculate_sensitivity(
                        listing_dict,
                        periods=[5, 7],
                        monthly_rent=listing.estimated_monthly_rent if listing.estimated_monthly_rent > 0 else None,
                        appreciation_rate=listing.appreciation_rate,
                    )
                    try:
                        from config import (
                            ROI_SENSITIVITY_RENT_DELTA_PCT,
                            ROI_SENSITIVITY_APPRECIATION_DELTA_PCT,
                        )
                    except ImportError:
                        ROI_SENSITIVITY_RENT_DELTA_PCT = 0.10
                        ROI_SENSITIVITY_APPRECIATION_DELTA_PCT = 0.015

                    def _roi_summary(roi):
                        return {
                            "exit_price": roi.estimated_exit_price,
                            "total_return": round(roi.total_return, 2),
                            "roi_pct": round(roi.roi_percent, 2),
                            "annualized_roi": round(roi.annualized_roi, 2),
                        }

                    listing.roi_sensitivity = {
                        "assumptions": {
                            "rent_delta_pct": ROI_SENSITIVITY_RENT_DELTA_PCT,
                            "appreciation_delta_pct": ROI_SENSITIVITY_APPRECIATION_DELTA_PCT,
                        },
                        "5yr": {
                            "downside": _roi_summary(sensitivity["downside"][5]),
                            "upside": _roi_summary(sensitivity["upside"][5]),
                        },
                        "7yr": {
                            "downside": _roi_summary(sensitivity["downside"][7]),
                            "upside": _roi_summary(sensitivity["upside"][7]),
                        },
                    }

            scored.append(listing)

        # Re-sort by total_score (includes agent adjustments)
        scored.sort(key=lambda s: s.total_score, reverse=True)

        # Deduplicate: group listings from the same condo
        pre_dedup = len(scored)
        scored = deduplicate_by_project(scored)
        if len(scored) < pre_dedup:
            print(f"  Deduplicated: {pre_dedup} -> {len(scored)} unique condos", file=sys.stderr)

        # Count agent-reviewed listings
        reviewed_count = sum(1 for s in scored if s.agent_summary is not None)
        adjusted_count = sum(1 for s in scored if s.agent_score_adjustment != 0)
        print(f"  Agent-reviewed: {reviewed_count}/{len(scored)}", file=sys.stderr)
        print(f"  Score adjustments: {adjusted_count}", file=sys.stderr)

        # Generate output
        output_base = args.output or str(Path(args.from_review).with_suffix(""))
        Path(output_base).parent.mkdir(parents=True, exist_ok=True)
        report_path = f"{output_base}_report.md"
        save_report(scored, report_path, report_level=report_level)
        print(f"\nFinal report saved: {report_path}")

        # Also save re-ranked JSON
        json_path = f"{output_base}_final.json"
        json_data = {
            "analyzed_at": datetime.now().isoformat(),
            "total": len(scored),
            "agent_reviewed": reviewed_count,
            "report_level": report_level,
            "listings": [s.to_dict() for s in scored],
        }
        with open(json_path, "w") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        print(f"Final JSON saved: {json_path}")

        # Print results
        print_results(scored, args.top, args.verbose)
        return


    # Handle --build-ura-cache
    if args.build_ura_cache:
        print("\nBuilding URA cache from CSV files...")
        build_ura_cache_from_csv(args.build_ura_cache)
        return

    # Handle --fetch-ura-districts
    if args.fetch_ura_districts:
        from fetch_ura_districts import main as fetch_districts_main
        import sys as _sys
        # Build argv for fetch_ura_districts
        fetch_args = []
        if args.fetch_ura_districts.lower() != "all":
            fetch_args.extend(["--districts", args.fetch_ura_districts])
        # Pass through and let fetch_ura_districts handle it
        _sys.argv = ["fetch_ura_districts.py"] + fetch_args
        fetch_districts_main()
        return

    run_dir = None

    # Handle --condo mode (scrape by condo name)
    if args.condo:
        beds = [int(b.strip()) for b in args.beds.split(",")]
        # Default enrich_top=20 in condo mode to get floor_level/facing
        condo_enrich = args.enrich_top if args.enrich_top > 0 else 20
        listings = scrape_condo_listings(
            condo_name=args.condo,
            min_price=args.min_price,
            max_price=args.max_price,
            beds=beds,
            max_pages=args.condo_max_pages,
            headless=headless,
            enrich_top=condo_enrich,
            fuzzy=args.condo_fuzzy,
            max_results=args.condo_max_results,
        )

        if not listings:
            print("No listings found", file=sys.stderr)
            return

        run_dir = next_run_dir()
        safe_condo = "_".join(args.condo.lower().split())
        listings_file = run_dir / f"listings_{safe_condo}.json"
        with open(listings_file, "w") as f:
            json.dump(listings, f, indent=2, ensure_ascii=False)
        print(f"Run directory: {run_dir}", file=sys.stderr)
        print(f"Saved: {listings_file}", file=sys.stderr)

    # Handle --auto mode (NEW)
    elif args.auto:
        print("\n" + "=" * 60, file=sys.stderr)
        print("AUTOMATIC DISTRICT DISCOVERY (v2.2)", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

        districts = discover_districts(
            top_n=args.auto_districts,
            exclude_ccr=not args.include_ccr,
        )

        print(f"  AI-selected districts: {districts}", file=sys.stderr)
        print(f"  Criteria: 50% historical + 50% future potential", file=sys.stderr)

        # Continue with scraping using discovered districts
        beds = [int(b.strip()) for b in args.beds.split(",")]

        listings = scrape_listings(
            districts=districts,
            min_price=args.min_price,
            max_price=args.max_price,
            beds=beds,
            max_pages=args.max_pages,
            headless=headless,
            enrich_top=args.enrich_top,
        )

        if not listings:
            print("No listings found", file=sys.stderr)
            return

        # Save scraped listings into run directory
        run_dir = next_run_dir()
        district_str = "_".join(str(d) for d in districts)
        listings_file = run_dir / f"listings_D{district_str}.json"
        with open(listings_file, "w") as f:
            json.dump(listings, f, indent=2, ensure_ascii=False)
        print(f"Run directory: {run_dir}", file=sys.stderr)
        print(f"Saved: {listings_file}", file=sys.stderr)

    # Get listings (manual mode)
    elif args.input:
        listings = []
        # Load from file (support glob patterns)
        input_files = file_glob(args.input) if '*' in args.input else [args.input]
        if not input_files:
            print(f"No files match: {args.input}", file=sys.stderr)
            sys.exit(1)

        for input_file in input_files:
            print(f"Loading: {input_file}...", file=sys.stderr)
            with open(input_file) as f:
                data = json.load(f)
            file_listings = data if isinstance(data, list) else data.get("listings", [])
            listings.extend(file_listings)

        print(f"Total loaded: {len(listings)} listings", file=sys.stderr)

    elif args.districts:
        # Scrape new listings (manual district selection)
        districts = [int(d.strip()) for d in args.districts.split(",")]
        beds = [int(b.strip()) for b in args.beds.split(",")]

        listings = scrape_listings(
            districts=districts,
            min_price=args.min_price,
            max_price=args.max_price,
            beds=beds,
            max_pages=args.max_pages,
            headless=headless,
            enrich_top=args.enrich_top,
        )

        if not listings:
            print("No listings found", file=sys.stderr)
            return

        # Save scraped listings into run directory
        run_dir = next_run_dir()
        district_str = "_".join(str(d) for d in districts)
        listings_file = run_dir / f"listings_D{district_str}.json"
        with open(listings_file, "w") as f:
            json.dump(listings, f, indent=2, ensure_ascii=False)
        print(f"Run directory: {run_dir}", file=sys.stderr)
        print(f"Saved: {listings_file}", file=sys.stderr)

    else:
        # No mode specified
        parser.print_help()
        print("\n\nError: Must specify --auto, --condo, --districts, or --input", file=sys.stderr)
        return


    # Load URA cache
    ura_data = load_ura_cache()
    if ura_data:
        print(f"\nURA cache loaded: {len(ura_data)} projects", file=sys.stderr)

    # Check which projects need URA data
    unique_projects = set()
    for listing in listings:
        project = listing.get("title") or listing.get("project_name", "")
        if project:
            unique_projects.add(project)

    cached_count = sum(1 for p in unique_projects if p.lower() in ura_data)
    stale_count = 0
    for p in unique_projects:
        pk = p.lower()
        if pk in ura_data:
            updated = ura_data[pk].get("updated", "")
            if updated:
                try:
                    days_old = (datetime.now() - datetime.strptime(updated, "%Y-%m-%d")).days
                    if days_old > 7:
                        stale_count += 1
                except ValueError:
                    stale_count += 1

    missing = len(unique_projects) - cached_count
    print(f"\n  Projects: {len(unique_projects)} unique", file=sys.stderr)
    print(f"  URA cached: {cached_count} ({stale_count} stale >7 days)", file=sys.stderr)
    print(f"  Missing URA data: {missing} projects", file=sys.stderr)
    if missing > 0 or stale_count > 0:
        uncached = [p for p in unique_projects if p.lower() not in ura_data]
        if uncached:
            print(f"  Projects without data: {', '.join(sorted(uncached)[:10])}", file=sys.stderr)
            if len(uncached) > 10:
                print(f"    ... and {len(uncached) - 10} more", file=sys.stderr)
        print(f"\n  ** Fetch URA data by district (fastest - gets ALL projects): **", file=sys.stderr)
        print(f"  **   python fetch_ura_districts.py --districts 3,5,14,15 **", file=sys.stderr)
        print(f"  **   python invest.py --fetch-ura-districts all **", file=sys.stderr)
        print(f"  ** Or build cache from existing CSVs: **", file=sys.stderr)
        print(f"  **   python invest.py --build-ura-cache data/ura_*.csv **", file=sys.stderr)

    # Data freshness warning (best-effort)
    warn_if_stale_data()

    # Score listings with pre-filtering (skip full scoring on obvious rejects)
    print(f"\nScoring {len(listings)} listings...", file=sys.stderr)
    result = score_and_filter(listings, min_quick_score=40, ura_data=ura_data)
    scored = result["scored"]
    rejected = result["rejected"]
    if rejected:
        print(f"  Pre-filtered: {len(rejected)} low-scoring listings skipped", file=sys.stderr)

    if not scored:
        print("No scoreable listings", file=sys.stderr)
        return

    # Deduplicate / group listings
    pre_dedup = len(scored)
    if args.condo:
        scored = group_condo_by_unit_type(scored)
        if len(scored) < pre_dedup:
            print(f"  Grouped by unit type: {pre_dedup} -> {len(scored)} unit types", file=sys.stderr)
    else:
        scored = deduplicate_by_project(scored)
        if len(scored) < pre_dedup:
            print(f"  Deduplicated: {pre_dedup} -> {len(scored)} unique condos", file=sys.stderr)

    # Generate outputs — always produce report + raw JSON into run directory
    if run_dir:
        # Always save markdown report
        report_path = str(run_dir / "report.md")
        report_title = f"Condo Analysis: {args.condo}" if args.condo else "Property Investment Analysis"
        save_report(scored, report_path, title=report_title)
        print(f"\nReport saved: {report_path}")

        # Always save scored JSON
        json_data = {
            "analyzed_at": datetime.now().isoformat(),
            "total": len(scored),
            "ura_data_used": sum(1 for s in scored if "ura" in s.appreciation_source.lower()),
            "listings": [s.to_dict() for s in scored],
        }
        json_path = str(run_dir / "scored.json")
        with open(json_path, "w") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        print(f"JSON saved: {json_path}")

        # Always save raw analysis for agent review
        run_config = {
            "price_range": [args.min_price, args.max_price],
            "beds": [int(b.strip()) for b in args.beds.split(",")],
        }
        if args.condo:
            run_config["condo"] = args.condo
            run_config["condo_fuzzy"] = args.condo_fuzzy
            run_config["condo_max_pages"] = args.condo_max_pages
            run_config["condo_max_results"] = args.condo_max_results
        if args.districts:
            run_config["districts"] = [f"D{d.strip()}" for d in args.districts.split(",")]
        elif args.auto:
            run_config["districts"] = [f"D{d}" for d in districts]

        raw_path = save_raw_analysis(scored, str(run_dir / "raw_analysis.json"), ura_data, run_config)
        print(f"Raw analysis saved: {raw_path}")

        # CSV if requested
        if args.csv:
            csv_content = generate_csv_export(scored)
            csv_path = str(run_dir / "export.csv")
            with open(csv_path, "w") as f:
                f.write(csv_content)
            print(f"CSV saved: {csv_path}")

        print(f"\n  All outputs in: {run_dir}")
        print(f"  -> {len(scored)} listings scored")
        print(f"  -> To do agent review: fill agent_* fields in {raw_path}")
        print(f"  -> Then: python invest.py --from-review {raw_path} --output {run_dir / 'final'}")

    else:
        # --input mode (no run_dir) — use explicit output flags
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            report_path = f"{args.output}.md"
            save_report(scored, report_path)
            print(f"\nReport saved: {report_path}")

        if args.csv:
            csv_content = generate_csv_export(scored)
            with open(args.csv, "w") as f:
                f.write(csv_content)
            print(f"CSV saved: {args.csv}")

        if args.json:
            json_data = {
                "analyzed_at": datetime.now().isoformat(),
                "total": len(scored),
                "ura_data_used": sum(1 for s in scored if "ura" in s.appreciation_source.lower()),
                "listings": [s.to_dict() for s in scored],
            }
            with open(args.json, "w") as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)
            print(f"JSON saved: {args.json}")

        if args.raw:
            run_config = {
                "price_range": [args.min_price, args.max_price],
                "beds": [int(b.strip()) for b in args.beds.split(",")],
            }
            if args.districts:
                run_config["districts"] = [f"D{d.strip()}" for d in args.districts.split(",")]
            raw_path = save_raw_analysis(scored, args.raw, ura_data, run_config)
            print(f"\nRaw analysis saved: {raw_path}")

    # Print results
    print_results(scored, args.top, args.verbose)


if __name__ == "__main__":
    main()
