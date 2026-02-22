"""Markdown report generation for scored listings."""

from datetime import datetime
from typing import Optional

from scoring.models import ScoredListing


def format_currency(value: float, decimals: int = 0) -> str:
    """Format number as currency."""
    if decimals == 0:
        return f"${int(value):,}"
    return f"${value:,.{decimals}f}"


def format_percent(value: float, decimals: int = 2) -> str:
    """Format number as percentage."""
    return f"{value:.{decimals}f}%"


def generate_listing_card(listing: ScoredListing, rank: int = 0) -> str:
    """Generate markdown card for a single listing."""
    lines = []

    # Header with rank and score
    rank_str = f"#{rank} " if rank > 0 else ""
    lines.append(f"### {rank_str}{listing.title}")
    lines.append(f"**Score: {listing.total_score:.1f}/100** | Tier {listing.quick_tier}")
    lines.append("")

    # Key metrics
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Price | {format_currency(listing.price)} |")
    if listing.psf:
        lines.append(f"| PSF | {format_currency(listing.psf)} |")
    if listing.sqft:
        lines.append(f"| Size | {listing.sqft:,.0f} sqft |")
    if listing.beds:
        lines.append(f"| Bedrooms | {listing.beds} |")
    if listing.tenure:
        lines.append(f"| Tenure | {listing.tenure} |")
    if listing.remaining_lease and listing.remaining_lease < 999:
        lines.append(f"| Remaining Lease | {listing.remaining_lease} years |")
    if listing.built_year:
        lines.append(f"| Built | {listing.built_year} |")
    if listing.nearest_mrt:
        dist = f" ({listing.mrt_distance_m}m)" if listing.mrt_distance_m else ""
        lines.append(f"| Nearest MRT | {listing.nearest_mrt}{dist} |")
    lines.append("")

    # Score breakdown (v2.1)
    lines.append("**Score Breakdown:**")
    lines.append(f"- Rental Yield: {listing.rental_yield_score:.1f}/15")
    lines.append(f"- Capital Appreciation: {listing.capital_appreciation_score:.1f}/30")
    lines.append(f"- Future Potential: {listing.future_potential_score:.1f}/20")
    lines.append(f"- Liquidity: {listing.liquidity_score:.1f}/25")
    lines.append(f"- Cost Efficiency: {listing.cost_efficiency_score:.1f}/10")
    if listing.red_flag_deductions > 0:
        lines.append(f"- Red Flags: -{listing.red_flag_deductions:.1f}")
    lines.append("")

    # Future catalysts
    if listing.future_score_details:
        fd = listing.future_score_details
        if fd.nearest_future_mrt or fd.govt_zones:
            lines.append("**Future Catalysts:**")
            if fd.nearest_future_mrt:
                lines.append(f"- Future MRT: {fd.nearest_future_mrt} ({fd.future_mrt_distance_m}m) - {fd.future_mrt_line}")
            if fd.govt_zones:
                lines.append(f"- Govt Zones: {', '.join(fd.govt_zones)}")
            lines.append("")

    # Rental estimate
    if listing.estimated_monthly_rent > 0:
        lines.append("**Rental Estimate:**")
        lines.append(f"- Monthly Rent: {format_currency(listing.estimated_monthly_rent)}")
        lines.append(f"- Gross Yield: {format_percent(listing.estimated_gross_yield)}")
        lines.append(f"- Source: {listing.rent_source}")
        lines.append("")

    # ROI projections
    roi_5 = listing.roi_5yr
    roi_7 = listing.roi_7yr
    if roi_5 or roi_7:
        # Show appreciation rate used
        appreciation_pct = listing.appreciation_rate * 100
        source_label = {
            "ura_5yr_cagr": "URA 5yr CAGR",
            "ura_3yr_avg": "URA 3yr avg",
            "ura_data": "URA data",
            "project_history": "historical data",
            "project_trend_3yr": "3yr trend",
            "transaction_data": "transaction history",
            "default": "market estimate",
        }.get(listing.appreciation_source, listing.appreciation_source)
        lines.append(f"**ROI Projections** *(using {appreciation_pct:.1f}%/yr appreciation from {source_label})*:")
        lines.append("| Period | Exit Price | Total Return | Annualized ROI |")
        lines.append("|--------|------------|--------------|----------------|")
        if listing.roi_5yr:
            r = listing.roi_5yr
            lines.append(f"| 5 years | {format_currency(r.estimated_exit_price)} | {format_currency(r.total_return)} | {format_percent(r.annualized_roi)} |")
        if listing.roi_6yr:
            r = listing.roi_6yr
            lines.append(f"| 6 years | {format_currency(r.estimated_exit_price)} | {format_currency(r.total_return)} | {format_percent(r.annualized_roi)} |")
        if listing.roi_7yr:
            r = listing.roi_7yr
            lines.append(f"| 7 years | {format_currency(r.estimated_exit_price)} | {format_currency(r.total_return)} | {format_percent(r.annualized_roi)} |")
        lines.append("")

    # Agent assessment sections (only shown if AI review was done)
    if listing.agent_summary:
        lines.append("**Agent Assessment:**")
        lines.append(listing.agent_summary)
        lines.append("")

    if listing.agent_catalysts:
        lines.append("**Additional Catalysts:**")
        for catalyst in listing.agent_catalysts:
            lines.append(f"- {catalyst}")
        lines.append("")

    if listing.agent_red_flags:
        lines.append("**Risk Factors:**")
        for flag in listing.agent_red_flags:
            lines.append(f"- {flag}")
        lines.append("")

    if listing.agent_stack_notes:
        lines.append("**Stack Notes:**")
        lines.append(listing.agent_stack_notes)
        lines.append("")

    if listing.agent_rental_assessment:
        lines.append("**Rental Outlook:**")
        lines.append(listing.agent_rental_assessment)
        lines.append("")

    if listing.agent_appreciation_assessment:
        lines.append("**Appreciation Outlook:**")
        lines.append(listing.agent_appreciation_assessment)
        lines.append("")

    if listing.agent_score_adjustment != 0:
        adj = listing.agent_score_adjustment
        sign = "+" if adj > 0 else ""
        reason = f" ({listing.agent_adjustment_reason})" if listing.agent_adjustment_reason else ""
        lines.append(f"**Agent Adjustment:** {sign}{adj:.1f}{reason}")
        lines.append("")

    if listing.agent_confidence:
        lines.append(f"**Agent Confidence:** {listing.agent_confidence}")
        lines.append("")

    # Link(s)
    lines.append(f"[View Listing]({listing.url})")
    if listing.additional_urls:
        for j, extra_url in enumerate(listing.additional_urls, 2):
            lines.append(f" | [Listing {j}]({extra_url})")
    lines.append("")
    lines.append("---")
    lines.append("")

    return "\n".join(lines)


def generate_summary_table(listings: list[ScoredListing]) -> str:
    """Generate summary comparison table."""
    lines = []

    lines.append("## Summary Comparison")
    lines.append("")
    lines.append("| Rank | Project | Price | PSF | Score | Yield | 5yr ROI |")
    lines.append("|------|---------|-------|-----|-------|-------|---------|")

    for i, listing in enumerate(listings[:20], 1):
        price = format_currency(listing.price)
        psf = format_currency(listing.psf) if listing.psf else "-"
        score = f"{listing.total_score:.1f}"
        yield_pct = format_percent(listing.estimated_gross_yield) if listing.estimated_gross_yield else "-"
        roi_5 = format_percent(listing.roi_5yr.annualized_roi) if listing.roi_5yr else "-"

        # Truncate title
        title = listing.title[:30] + "..." if len(listing.title) > 30 else listing.title
        if listing.additional_urls:
            title += f" (+{len(listing.additional_urls)})"

        lines.append(f"| {i} | {title} | {price} | {psf} | {score} | {yield_pct} | {roi_5} |")

    lines.append("")
    return "\n".join(lines)


def generate_report(
    listings: list[ScoredListing],
    title: str = "Property Investment Analysis",
    include_details: bool = True,
    max_detailed: int = 10,
    report_level: Optional[dict] = None,
) -> str:
    """
    Generate full markdown report.

    Args:
        listings: List of scored listings (should be sorted by score)
        title: Report title
        include_details: Include detailed cards for top listings
        max_detailed: Max number of detailed listing cards
        report_level: Optional dict with agent report-level fields
            (agent_executive_summary, agent_market_commentary, agent_methodology_notes)

    Returns:
        Complete markdown report
    """
    lines = []
    report_level = report_level or {}

    # Header
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    lines.append("")

    # Executive Summary (from AI agent review)
    exec_summary = report_level.get("agent_executive_summary")
    if exec_summary:
        lines.append("## Executive Summary")
        lines.append("")
        lines.append(exec_summary)
        lines.append("")

    # Market Context (from AI agent review)
    market_commentary = report_level.get("agent_market_commentary")
    if market_commentary:
        lines.append("## Market Context")
        lines.append("")
        lines.append(market_commentary)
        lines.append("")

    # Quick stats
    if listings:
        tier1 = [l for l in listings if l.quick_tier == 1]
        tier2 = [l for l in listings if l.quick_tier == 2]

        lines.append("## Overview")
        lines.append("")
        lines.append(f"- **Total Analyzed:** {len(listings)} properties")
        lines.append(f"- **Tier 1 (Recommended):** {len(tier1)}")
        lines.append(f"- **Tier 2 (Consider):** {len(tier2)}")
        if listings:
            avg_score = sum(l.total_score for l in listings) / len(listings)
            lines.append(f"- **Average Score:** {avg_score:.1f}/100")
        lines.append("")

        # Price range
        prices = [l.price for l in listings]
        lines.append(f"- **Price Range:** {format_currency(min(prices))} - {format_currency(max(prices))}")

        # Best yields
        yields = [l.estimated_gross_yield for l in listings if l.estimated_gross_yield > 0]
        if yields:
            lines.append(f"- **Yield Range:** {format_percent(min(yields))} - {format_percent(max(yields))}")
        lines.append("")

    # Summary table
    lines.append(generate_summary_table(listings))

    # Detailed cards
    if include_details and listings:
        lines.append("## Top Properties (Detailed)")
        lines.append("")

        for i, listing in enumerate(listings[:max_detailed], 1):
            lines.append(generate_listing_card(listing, rank=i))

    # Methodology note (v2.1)
    lines.append("## Scoring Methodology")
    lines.append("")
    lines.append("Properties are scored on a 100-point scale optimized for 5-7 year investment.")
    lines.append("All properties use real transaction data — no bias for cached data.")
    lines.append("")
    lines.append("| Category | Weight | Description |")
    lines.append("|----------|--------|-------------|")
    lines.append("| Rental Yield | 15 pts | Gross yield, MRT proximity, unit config, tenant pool |")
    lines.append("| Capital Appreciation | 30 pts | Real appreciation rate, PSF vs median, tenure, age |")
    lines.append("| Future Potential | 20 pts | Upcoming MRT, govt zones, transformation |")
    lines.append("| Liquidity | 25 pts | Transaction volume, district popularity, dev size, price appeal |")
    lines.append("| Cost Efficiency | 10 pts | MCST, property tax, space efficiency |")
    lines.append("| Red Flags | -10 pts | West-facing, small dev, low lease, old property |")
    lines.append("")

    # Agent methodology notes (from AI agent review)
    methodology_notes = report_level.get("agent_methodology_notes")
    if methodology_notes:
        lines.append("### Notes")
        lines.append("")
        lines.append(methodology_notes)
        lines.append("")

    return "\n".join(lines)


def save_report(
    listings: list[ScoredListing],
    output_path: str,
    **kwargs,
) -> str:
    """
    Generate and save report to file.

    Args:
        listings: List of scored listings
        output_path: Path to save markdown file
        **kwargs: Additional arguments for generate_report

    Returns:
        Path to saved file
    """
    report = generate_report(listings, **kwargs)

    with open(output_path, "w") as f:
        f.write(report)

    return output_path


def generate_csv_export(listings: list[ScoredListing]) -> str:
    """Generate CSV export of scored listings (v2.1)."""
    lines = []

    headers = [
        "Rank", "Title", "Price", "PSF", "Sqft", "Beds", "District",
        "Total Score", "Rental Score", "Appreciation Score", "Future Score",
        "Liquidity Score", "Cost Score", "Has Real Data",
        "Monthly Rent Est", "Gross Yield %", "Appreciation %/yr", "Appreciation Source",
        "5yr ROI %", "7yr ROI %",
        "Tenure", "Built Year", "Remaining Lease", "MRT Distance",
        "Future MRT", "Govt Zones",
        "URL", "Additional URLs"
    ]
    lines.append(",".join(headers))

    for i, l in enumerate(listings, 1):
        future_mrt = ""
        govt_zones = ""
        if l.future_score_details:
            fd = l.future_score_details
            if fd.nearest_future_mrt:
                future_mrt = f"{fd.nearest_future_mrt} ({fd.future_mrt_distance_m}m)"
            if fd.govt_zones:
                govt_zones = "|".join(fd.govt_zones)

        row = [
            str(i),
            f'"{l.title}"',
            str(l.price),
            str(l.psf or ""),
            str(l.sqft or ""),
            str(l.beds or ""),
            l.district or "",
            f"{l.total_score:.1f}",
            f"{l.rental_yield_score:.1f}",
            f"{l.capital_appreciation_score:.1f}",
            f"{l.future_potential_score:.1f}",
            f"{l.liquidity_score:.1f}",
            f"{l.cost_efficiency_score:.1f}",
            "Yes" if l.has_ura_data else "No",
            str(int(l.estimated_monthly_rent)) if l.estimated_monthly_rent else "",
            f"{l.estimated_gross_yield:.2f}" if l.estimated_gross_yield else "",
            f"{l.appreciation_rate * 100:.2f}" if l.appreciation_rate else "",
            l.appreciation_source,
            f"{l.roi_5yr.annualized_roi:.2f}" if l.roi_5yr else "",
            f"{l.roi_7yr.annualized_roi:.2f}" if l.roi_7yr else "",
            f'"{l.tenure}"' if l.tenure else "",
            str(l.built_year or ""),
            str(l.remaining_lease) if l.remaining_lease and l.remaining_lease < 999 else "",
            str(l.mrt_distance_m or ""),
            f'"{future_mrt}"',
            f'"{govt_zones}"',
            l.url,
            f'"{" | ".join(l.additional_urls)}"' if l.additional_urls else "",
        ]
        lines.append(",".join(row))

    return "\n".join(lines)
