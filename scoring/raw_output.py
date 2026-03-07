"""Generate raw analysis JSON for AI agent review.

Outputs a structured intermediate file with algo-computed scores and
empty agent fields for the AI to fill in during the review step.
"""

import json
import os
from datetime import datetime
from typing import Any, Optional

from scoring.models import ScoredListing
from utils.geo import normalize_district

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_DISTRICT_PROFILES_FILE = os.path.join(_DATA_DIR, "district_profiles.json")
_DISTRICT_MEDIANS_FILE = os.path.join(_DATA_DIR, "district_medians.json")
_district_profiles_cache: Optional[dict] = None
_district_medians_cache: Optional[dict] = None


def _load_district_profiles() -> dict:
    global _district_profiles_cache
    if _district_profiles_cache is None:
        if os.path.exists(_DISTRICT_PROFILES_FILE):
            with open(_DISTRICT_PROFILES_FILE) as f:
                _district_profiles_cache = json.load(f)
        else:
            _district_profiles_cache = {"districts": {}}
    return _district_profiles_cache


def _load_district_medians() -> dict:
    global _district_medians_cache
    if _district_medians_cache is None:
        if os.path.exists(_DISTRICT_MEDIANS_FILE):
            with open(_DISTRICT_MEDIANS_FILE) as f:
                _district_medians_cache = json.load(f)
        else:
            _district_medians_cache = {"medians": {}}
    return _district_medians_cache


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
        "needs_ai_appreciation": listing.needs_ai_appreciation,
    }
    # Add details from score_breakdown if available
    sb_cap = listing.score_breakdown.get("capital_appreciation", {})
    if sb_cap:
        cap["components"] = {
            "appreciation_rate_points": sb_cap.get("appreciation_rate", {}).get("points"),
            "momentum_points": sb_cap.get("momentum", {}).get("points"),
            "psf_vs_median_points": sb_cap.get("psf_vs_median", {}).get("points"),
            "tenure_points": sb_cap.get("tenure", {}).get("points"),
            "property_age_points": sb_cap.get("property_age", {}).get("points"),
        }
        cap["momentum"] = sb_cap.get("momentum", {}).get("value")
        cap["data_coverage"] = sb_cap.get("momentum", {}).get("data_coverage")
        cap["txn_count"] = sb_cap.get("appreciation_rate", {}).get("transaction_count")
        cap["confidence"] = sb_cap.get("appreciation_rate", {}).get("confidence")
        cap["psf_vs_median_ratio"] = sb_cap.get("psf_vs_median", {}).get("ratio")
        cap["psf_percentile_quality"] = sb_cap.get("psf_vs_median", {}).get("percentile_quality")
        cap["district_median_psf"] = sb_cap.get("psf_vs_median", {}).get("median")
        cap["age_percentile_quality"] = sb_cap.get("property_age", {}).get("percentile_quality")
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
    ccr = {1, 2, 6, 7, 9, 10, 11}
    rcr = {3, 4, 5, 8, 12, 13, 14, 15}
    if d_num:
        if d_num in ccr:
            context["region"] = "CCR"
        elif d_num in rcr:
            context["region"] = "RCR"
        else:
            context["region"] = "OCR"
    else:
        context["region"] = None

    # District appreciation and medians (profile data)
    district_code = normalize_district(listing.district or "")
    profiles = _load_district_profiles().get("districts", {})
    d_key = district_code.replace("D", "").lstrip("0") or "0" if district_code else ""
    profile = profiles.get(d_key, {}) if d_key else {}
    context["district_appreciation"] = profile.get("historical_appreciation")

    # District median PSF from score breakdown or medians fallback
    sb_cap = listing.score_breakdown.get("capital_appreciation", {})
    median_psf = sb_cap.get("psf_vs_median", {}).get("median")
    if median_psf is None and district_code:
        medians = _load_district_medians().get("medians", {})
        median_psf = medians.get(district_code, {}).get("psf")
    context["district_median_psf"] = median_psf

    # New launch bias flag
    context["new_launch_bias"] = listing.appreciation_adjustment != 0

    # Sample projects from URA cache (district proximity unknown)
    sample = []
    project_lower = (listing.project_name or listing.title or "").lower()
    for key, entry in ura_data.items():
        if key == project_lower:
            continue
        entry_name = entry.get("project_name", key)
        sample.append(entry_name)
        if len(sample) >= 5:
            break
    if sample:
        context["sample_projects_in_ura"] = sample
        context["sample_projects_note"] = "Sample of projects in URA cache; district proximity unknown."

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
    if listing.nearest_mrt or listing.mrt_distance_m:
        if listing.nearest_mrt:
            dist_str = f" ({listing.mrt_distance_m}m)" if listing.mrt_distance_m else ""
            entry["nearest_mrt"] = f"{listing.nearest_mrt}{dist_str}"
        elif listing.mrt_distance_m:
            entry["nearest_mrt"] = f"{listing.mrt_distance_m}m"

    # Algo data
    entry["algo_breakdown"] = _build_algo_breakdown(listing)
    entry["roi_projections"] = _build_roi_projections(listing)
    if listing.roi_sensitivity:
        entry["roi_sensitivity"] = listing.roi_sensitivity
    entry["context"] = _build_context(listing, ura_data)

    # Floor/facing info
    if listing.floor_level:
        entry["floor_level"] = listing.floor_level
    if listing.facing:
        entry["facing"] = listing.facing

    # Additional listing URLs (same condo, different units)
    if listing.additional_urls:
        entry["additional_urls"] = listing.additional_urls

    # Condo mode: per-unit-type variant data
    if listing.unit_variants:
        entry["unit_variants"] = listing.unit_variants
    if listing.unit_summary:
        entry["unit_summary"] = listing.unit_summary

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
    entry["agent_appreciation_rate_pct"] = None
    entry["agent_appreciation_source"] = None

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
    config = run_config or {}
    result: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "run_config": config,
        "report_level": {
            "agent_market_commentary": None,
            "agent_executive_summary": None,
            "agent_methodology_notes": None,
        },
        "listings": [],
    }

    # Add AI review instructions (especially detailed for condo mode)
    condo_name = config.get("condo")
    if condo_name:
        result["ai_review_instructions"] = {
            "mode": "condo_analysis",
            "condo_name": condo_name,
            "steps": [
                f"1. Web search '{condo_name} Singapore review' for resident reviews, build quality, developer reputation",
                f"2. Web search '{condo_name} Singapore price trend' for recent transaction prices and market sentiment",
                f"3. Web search '{condo_name} floor plan facing' for unit layout info, best stacks, facing directions",
                "4. For each bedroom type, assess: is the median price fair vs recent transactions?",
                "5. Identify specific catalysts (upcoming MRT, en-bloc potential, area transformation)",
                "6. Identify specific risks (construction defects, oversupply, lease decay, developer issues)",
                "7. Fill ALL agent_* fields for each listing entry",
                "8. Fill report_level fields: agent_executive_summary (2-3 paragraph overall verdict), "
                "agent_market_commentary (area/market context), agent_methodology_notes (data gaps or caveats)",
            ],
            "agent_field_guide": {
                "agent_summary": "1-2 sentence verdict on this unit type as an investment",
                "agent_red_flags": "List of specific risks found from research",
                "agent_catalysts": "List of specific growth drivers found from research",
                "agent_score_adjustment": "Score adjustment -8 to +8 based on research findings",
                "agent_adjustment_reason": "Why the score was adjusted",
                "agent_rental_assessment": "Rental demand outlook for this unit type in this area",
                "agent_appreciation_assessment": "Price appreciation outlook based on transaction data and area trends",
                "agent_stack_notes": "Best stacks/floors, facing preferences, units to avoid",
                "agent_confidence": "high/medium/low based on data availability",
                "agent_appreciation_rate_pct": "Override appreciation rate if research suggests different from algo (as percentage, e.g. 4.5)",
                "agent_appreciation_source": "Source for the override rate",
            },
        }
    else:
        result["ai_review_instructions"] = {
            "mode": "multi_district",
            "steps": [
                "1. For top 3-5 properties: web search for project reviews, issues, transaction history",
                "2. Fill agent_* fields for researched listings",
                "3. Fill report_level fields with market overview",
            ],
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
        listing.agent_appreciation_rate_pct = review.get("agent_appreciation_rate_pct")
        listing.agent_appreciation_source = review.get("agent_appreciation_source")

        # Apply AI appreciation override to ROI and cap score
        if listing.agent_appreciation_rate_pct is not None:
            try:
                agent_rate_pct = float(listing.agent_appreciation_rate_pct)
            except (TypeError, ValueError):
                agent_rate_pct = None
            if agent_rate_pct is not None:
                prev_rate_pct = listing.appreciation_rate * 100 if listing.appreciation_rate is not None else None
                prev_source = listing.appreciation_source
                listing.appreciation_rate = agent_rate_pct / 100
                listing.appreciation_source = "agent_override"

                # Update capital appreciation score if breakdown exists
                sb_cap = listing.score_breakdown.get("capital_appreciation", {})
                if sb_cap:
                    from scoring.full_scorer import FullScorer
                    rate_points = FullScorer.score_appreciation_rate_points(
                        agent_rate_pct,
                        listing.appreciation_source,
                    )
                    other_points = (
                        sb_cap.get("momentum", {}).get("points", 0)
                        + sb_cap.get("psf_vs_median", {}).get("points", 0)
                        + sb_cap.get("tenure", {}).get("points", 0)
                        + sb_cap.get("property_age", {}).get("points", 0)
                    )
                    listing.capital_appreciation_score = rate_points + other_points
                    sb_cap.get("appreciation_rate", {})["points"] = rate_points
                else:
                    from scoring.full_scorer import FullScorer
                    rate_points = FullScorer.score_appreciation_rate_points(
                        agent_rate_pct,
                        listing.appreciation_source,
                    )
                    prev_rate_points = (
                        FullScorer.score_appreciation_rate_points(prev_rate_pct, prev_source)
                        if prev_rate_pct is not None
                        else None
                    )
                    total_cap = listing.capital_appreciation_score
                    if prev_rate_points is not None:
                        listing.capital_appreciation_score = total_cap - prev_rate_points + rate_points
                    else:
                        listing.capital_appreciation_score = rate_points

                # Recompute ROI with agent appreciation rate
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

    # Re-sort by total_score (which now includes agent adjustment)
    scored.sort(key=lambda s: s.total_score, reverse=True)
    return scored
