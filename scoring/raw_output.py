"""Generate raw analysis JSON for AI agent review.

Outputs a structured intermediate file with algo-computed scores and
empty agent fields for the AI to fill in during the review step.
"""

import json
from datetime import datetime
from typing import Any, Optional

from scoring.models import ScoredListing


def _build_algo_breakdown(listing: ScoredListing) -> dict:
    """Build detailed algo breakdown from a scored listing."""
    breakdown = {}

    # Rental yield
    rental = {
        "score": round(listing.rental_yield_score, 1),
        "max": 15,
        "gross_yield_pct": round(listing.estimated_gross_yield, 2) if listing.estimated_gross_yield else None,
        "monthly_rent_est": round(listing.estimated_monthly_rent) if listing.estimated_monthly_rent else None,
        "rent_source": listing.rent_source or None,
    }
    breakdown["rental_yield"] = rental

    # Capital appreciation
    cap = {
        "score": round(listing.capital_appreciation_score, 1),
        "max": 30,
        "rate_pct": round(listing.appreciation_rate * 100, 2),
        "source": listing.appreciation_source,
    }
    # Add details from score_breakdown if available
    sb_cap = listing.score_breakdown.get("capital_appreciation", {})
    if sb_cap:
        cap["momentum"] = sb_cap.get("momentum_score")
        cap["txn_count"] = sb_cap.get("txn_count")
        cap["confidence"] = sb_cap.get("confidence")
        cap["psf_vs_median_ratio"] = sb_cap.get("psf_vs_median_ratio")
    if listing.appreciation_adjustment != 0:
        cap["raw_rate_pct"] = round(listing.raw_appreciation_rate * 100, 2)
        cap["adjustment_pct"] = round(listing.appreciation_adjustment * 100, 2)
        cap["adjustment_reason"] = listing.adjustment_reason
    breakdown["capital_appreciation"] = cap

    # Future potential
    future = {
        "score": round(listing.future_potential_score, 1),
        "max": 20,
        "future_mrt": None,
        "govt_zones": [],
    }
    if listing.future_score_details:
        fd = listing.future_score_details
        if fd.nearest_future_mrt:
            future["future_mrt"] = {
                "station": fd.nearest_future_mrt,
                "distance_m": fd.future_mrt_distance_m,
                "line": fd.future_mrt_line,
            }
        future["govt_zones"] = fd.govt_zones
    breakdown["future_potential"] = future

    # Liquidity
    breakdown["liquidity"] = {
        "score": round(listing.liquidity_score, 1),
        "max": 25,
    }

    # Cost efficiency
    breakdown["cost_efficiency"] = {
        "score": round(listing.cost_efficiency_score, 1),
        "max": 10,
    }

    # Red flags
    sb_flags = listing.score_breakdown.get("red_flags", {})
    breakdown["red_flags"] = {
        "deductions": round(listing.red_flag_deductions, 1),
        "flags": sb_flags.get("flags", []),
    }

    return breakdown


def _build_roi_projections(listing: ScoredListing) -> dict:
    """Build ROI projection data."""
    projections = {}
    for years, roi in [(5, listing.roi_5yr), (6, listing.roi_6yr), (7, listing.roi_7yr)]:
        if roi:
            projections[f"{years}yr"] = {
                "exit_price": roi.estimated_exit_price,
                "total_return": round(roi.total_return),
                "roi_pct": round(roi.roi_percent, 2),
                "annualized_roi": round(roi.annualized_roi, 2),
            }
    return projections


def _build_context(listing: ScoredListing, ura_data: dict) -> dict:
    """Build extra context for AI consumption."""
    context: dict[str, Any] = {}

    # District info
    district_str = (listing.district or "").upper().replace("D", "").strip()
    try:
        d_num = int(district_str)
    except ValueError:
        d_num = None

    # Region mapping
    ccr = {1, 2, 6, 9, 10, 11}
    rcr = {3, 4, 5, 7, 8, 12, 13, 14, 15, 20}
    if d_num:
        if d_num in ccr:
            context["region"] = "CCR"
        elif d_num in rcr:
            context["region"] = "RCR"
        else:
            context["region"] = "OCR"
    else:
        context["region"] = None

    # District appreciation from score_breakdown
    sb_cap = listing.score_breakdown.get("capital_appreciation", {})
    context["district_median_psf"] = sb_cap.get("district_median_psf")
    context["district_appreciation"] = sb_cap.get("district_appreciation")

    # New launch bias flag
    context["new_launch_bias"] = listing.appreciation_adjustment != 0

    # Nearby projects from URA cache (same district)
    nearby = []
    project_lower = (listing.project_name or listing.title or "").lower()
    for key, entry in ura_data.items():
        if key == project_lower:
            continue
        # Include projects in same district
        entry_name = entry.get("project_name", key)
        # We don't have district on URA entries, so just collect a few
        nearby.append(entry_name)
        if len(nearby) >= 5:
            break
    context["nearby_projects_in_ura"] = nearby

    return context


def _listing_to_raw(
    listing: ScoredListing,
    rank: int,
    ura_data: dict,
) -> dict:
    """Convert a single ScoredListing to the raw analysis format."""
    entry: dict[str, Any] = {
        "rank": rank,
        "algo_score": round(listing.total_score, 1),
        "title": listing.title,
        "project_name": listing.project_name or listing.title,
        "price": listing.price,
        "psf": listing.psf,
        "sqft": listing.sqft,
        "beds": listing.beds,
        "district": listing.district,
        "tenure": listing.tenure,
        "remaining_lease": listing.remaining_lease,
        "built_year": listing.built_year,
        "nearest_mrt": None,
        "url": listing.url,
    }

    # MRT info
    if listing.nearest_mrt:
        dist_str = f" ({listing.mrt_distance_m}m)" if listing.mrt_distance_m else ""
        entry["nearest_mrt"] = f"{listing.nearest_mrt}{dist_str}"

    # Algo data
    entry["algo_breakdown"] = _build_algo_breakdown(listing)
    entry["roi_projections"] = _build_roi_projections(listing)
    entry["context"] = _build_context(listing, ura_data)

    # Additional listing URLs (same condo, different units)
    if listing.additional_urls:
        entry["additional_urls"] = listing.additional_urls

    # Agent fields (empty — to be filled by AI)
    entry["agent_summary"] = None
    entry["agent_red_flags"] = []
    entry["agent_catalysts"] = []
    entry["agent_score_adjustment"] = 0
    entry["agent_adjustment_reason"] = None
    entry["agent_rental_assessment"] = None
    entry["agent_appreciation_assessment"] = None
    entry["agent_stack_notes"] = None
    entry["agent_confidence"] = None

    return entry


def generate_raw_analysis(
    scored: list[ScoredListing],
    ura_data: dict,
    run_config: Optional[dict] = None,
) -> dict:
    """Generate the raw analysis JSON structure for AI agent review.

    Args:
        scored: List of scored listings (sorted by score descending).
        ura_data: URA cache data dict.
        run_config: Optional dict with districts, price_range, beds, etc.

    Returns:
        Complete raw analysis dict ready to be written as JSON.
    """
    result: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "run_config": run_config or {},
        "report_level": {
            "agent_market_commentary": None,
            "agent_executive_summary": None,
            "agent_methodology_notes": None,
        },
        "listings": [],
    }

    for rank, listing in enumerate(scored, 1):
        entry = _listing_to_raw(listing, rank, ura_data)
        result["listings"].append(entry)

    return result


def save_raw_analysis(
    scored: list[ScoredListing],
    output_path: str,
    ura_data: dict,
    run_config: Optional[dict] = None,
) -> str:
    """Generate and save raw analysis JSON to file.

    Returns:
        Path to saved file.
    """
    data = generate_raw_analysis(scored, ura_data, run_config)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return output_path


def load_reviewed_analysis(path: str) -> tuple[dict, list[dict]]:
    """Load a reviewed analysis JSON (after AI fills in agent fields).

    Returns:
        Tuple of (report_level dict, list of listing dicts with agent fields).
    """
    with open(path) as f:
        data = json.load(f)

    report_level = data.get("report_level", {})
    listings = data.get("listings", [])
    return report_level, listings


def apply_review_to_listings(
    scored: list[ScoredListing],
    reviewed_listings: list[dict],
) -> list[ScoredListing]:
    """Apply agent review data back onto ScoredListing objects.

    Matches reviewed listings to scored listings by URL (most reliable),
    then applies all agent_* fields.

    Returns:
        Updated list of ScoredListing objects, re-sorted by total_score.
    """
    # Index reviewed data by URL for matching
    review_by_url = {}
    review_by_title = {}
    for entry in reviewed_listings:
        if entry.get("url"):
            review_by_url[entry["url"]] = entry
        if entry.get("title"):
            review_by_title[entry["title"]] = entry

    for listing in scored:
        review = review_by_url.get(listing.url) or review_by_title.get(listing.title)
        if not review:
            continue

        listing.agent_summary = review.get("agent_summary")
        listing.agent_red_flags = review.get("agent_red_flags", [])
        listing.agent_catalysts = review.get("agent_catalysts", [])
        listing.agent_score_adjustment = review.get("agent_score_adjustment", 0)
        listing.agent_adjustment_reason = review.get("agent_adjustment_reason")
        listing.agent_rental_assessment = review.get("agent_rental_assessment")
        listing.agent_appreciation_assessment = review.get("agent_appreciation_assessment")
        listing.agent_stack_notes = review.get("agent_stack_notes")
        listing.agent_confidence = review.get("agent_confidence")

    # Re-sort by total_score (which now includes agent adjustment)
    scored.sort(key=lambda s: s.total_score, reverse=True)
    return scored
