"""Markdown report generation for scored listings."""

from datetime import datetime
from typing import Optional

from scoring.models import ScoredListing, verdict_from_rating


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

    # Clear AI verdict + confidence (the headline call), with the technical score as backup
    if listing.score_1000 is not None:
        tech_str = f"Technical score: {listing.score_1000}/1000 · MMR {listing.mmr:.0f} ({listing.final_tier_label})"
    else:
        tech_str = f"Technical score: {listing.total_score:.1f}/100 ({listing.final_tier_label})"
    verdict = verdict_from_rating(listing.agent_rating)
    if verdict:
        badge = {"BUY": "🟢 BUY", "NEUTRAL": "🟡 NEUTRAL", "AVOID": "🔴 AVOID (no buy)"}.get(verdict, verdict)
        conf = (listing.agent_confidence or "unrated").lower()
        rating_detail = f" ({listing.agent_rating})" if listing.agent_rating else ""
        lines.append(f"> ## {badge}{rating_detail}")
        lines.append(f"> **AI confidence: {conf}** · {tech_str}")
        if listing.agent_rating_rationale:
            lines.append(f">")
            lines.append(f"> {listing.agent_rating_rationale}")
    else:
        lines.append(f"**{tech_str}**")
        lines.append("*(No AI verdict yet — run the agent review to get a Buy / Neutral / Avoid call.)*")
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
    if listing.nearest_mrt or listing.mrt_distance_m:
        if listing.nearest_mrt:
            dist = f" ({listing.mrt_distance_m}m)" if listing.mrt_distance_m else ""
            lines.append(f"| Nearest MRT | {listing.nearest_mrt}{dist} |")
        else:
            lines.append(f"| Nearest MRT | {listing.mrt_distance_m}m |")
    lines.append("")

    # Unit variants (condo mode)
    if listing.unit_variants:
        summary = listing.unit_summary or {}
        count = summary.get("count", len(listing.unit_variants))
        price_range = summary.get("price_range", [])
        psf_range = summary.get("psf_range", [])
        lines.append(f"**Unit Availability:** {count} listings")
        if price_range and len(price_range) == 2 and price_range[0] != price_range[1]:
            lines.append(f"- Price range: {format_currency(price_range[0])} - {format_currency(price_range[1])}")
        if psf_range and len(psf_range) == 2 and psf_range[0] != psf_range[1]:
            lines.append(f"- PSF range: {format_currency(psf_range[0])} - {format_currency(psf_range[1])}")
        lines.append("")
        lines.append("| # | Price | PSF | Sqft | Floor | Facing | Link |")
        lines.append("|---|-------|-----|------|-------|--------|------|")
        for j, v in enumerate(listing.unit_variants, 1):
            v_price = format_currency(v.get("price", 0))
            v_psf = format_currency(v["psf"]) if v.get("psf") else "-"
            v_sqft = f"{v['sqft']:,.0f}" if v.get("sqft") else "-"
            v_floor = v.get("floor_level") or "-"
            v_facing = v.get("facing") or "-"
            v_url = f"[Link]({v['url']})" if v.get("url") else "-"
            lines.append(f"| {j} | {v_price} | {v_psf} | {v_sqft} | {v_floor} | {v_facing} | {v_url} |")
        lines.append("")

    # Score breakdown
    if listing.mmr_components:
        lines.append(f"**MMR Breakdown** *(base 1500; positive helps, negative hurts)*:")
        comps = sorted(listing.mmr_components.items(), key=lambda kv: -abs(kv[1]))
        parts = [f"{name} {val:+.1f}" for name, val in comps if val != 0]
        lines.append("- " + " · ".join(parts) if parts else "- all components neutral")
        lines.append("")
    else:
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
            "regional_baseline": "regional baseline",
            "agent_override": "AI research",
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

    # ROI sensitivity (down/base/up)
    if listing.roi_sensitivity:
        sens = listing.roi_sensitivity
        lines.append("**ROI Sensitivity (Annualized ROI):**")
        for years, base_roi in [(5, listing.roi_5yr), (7, listing.roi_7yr)]:
            if not base_roi:
                continue
            period_key = f"{years}yr"
            down = sens.get(period_key, {}).get("downside", {}).get("annualized_roi")
            up = sens.get(period_key, {}).get("upside", {}).get("annualized_roi")
            if down is None or up is None:
                continue
            lines.append(
                f"- {years} years: {format_percent(down)} / {format_percent(base_roi.annualized_roi)} / {format_percent(up)} (down/base/up)"
            )
        assumptions = sens.get("assumptions", {})
        rent_delta = assumptions.get("rent_delta_pct")
        appr_delta = assumptions.get("appreciation_delta_pct")
        if rent_delta is not None and appr_delta is not None:
            lines.append(
                f"- Assumptions: rent ±{format_percent(rent_delta * 100)}; appreciation ±{format_percent(appr_delta * 100)}"
            )
        lines.append("")

    # AI Research & Opinion (only shown if AI review was done)
    has_ai_review = (
        listing.agent_summary
        or listing.agent_catalysts
        or listing.agent_red_flags
        or listing.agent_stack_notes
        or listing.agent_rental_assessment
        or listing.agent_appreciation_assessment
    )
    if has_ai_review:
        lines.append("#### AI Research & Opinion")
        lines.append("")

        if listing.agent_summary:
            lines.append(listing.agent_summary)
            lines.append("")

        if listing.agent_catalysts:
            lines.append("**Catalysts:**")
            for catalyst in listing.agent_catalysts:
                lines.append(f"- {catalyst}")
            lines.append("")

        if listing.agent_red_flags:
            lines.append("**Risk Factors:**")
            for flag in listing.agent_red_flags:
                lines.append(f"- {flag}")
            lines.append("")

        if listing.agent_stack_notes:
            lines.append("**Stack & Unit Notes:**")
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


        if listing.agent_confidence:
            lines.append(f"**Confidence:** {listing.agent_confidence}")
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


def _verdict_label(listing: ScoredListing) -> str:
    """Short verdict cell for summary tables: BUY / NEUTRAL / AVOID, or '-'."""
    v = verdict_from_rating(listing.agent_rating)
    return {"BUY": "🟢 BUY", "NEUTRAL": "🟡 NEUTRAL", "AVOID": "🔴 AVOID"}.get(v, "-")


def generate_summary_table(listings: list[ScoredListing]) -> str:
    """Generate summary comparison table."""
    lines = []

    # Detect condo mode: any listing has unit_variants
    is_condo_mode = any(l.unit_variants for l in listings)

    lines.append("## Summary Comparison")
    lines.append("")

    if is_condo_mode:
        lines.append("| Rank | Type | Verdict | Confidence | Price (Median) | PSF | Score | Yield | Units |")
        lines.append("|------|------|---------|------------|----------------|-----|-------|-------|-------|")

        for i, listing in enumerate(listings[:20], 1):
            price = format_currency(listing.price)
            psf = format_currency(listing.psf) if listing.psf else "-"
            score = str(listing.score_1000) if listing.score_1000 is not None else f"{listing.total_score:.1f}"
            yield_pct = format_percent(listing.estimated_gross_yield) if listing.estimated_gross_yield else "-"
            beds_str = f"{listing.beds}BR" if listing.beds else "-"
            unit_count = listing.unit_summary.get("count", 1) if listing.unit_summary else 1
            verdict = _verdict_label(listing)
            conf = (listing.agent_confidence or "-").lower()

            lines.append(f"| {i} | {beds_str} | {verdict} | {conf} | {price} | {psf} | {score} | {yield_pct} | {unit_count} |")
    else:
        lines.append("| Rank | Project | Verdict | Confidence | Price | PSF | Score | Yield | 5yr ROI |")
        lines.append("|------|---------|---------|------------|-------|-----|-------|-------|---------|")

        for i, listing in enumerate(listings[:20], 1):
            price = format_currency(listing.price)
            psf = format_currency(listing.psf) if listing.psf else "-"
            score = str(listing.score_1000) if listing.score_1000 is not None else f"{listing.total_score:.1f}"
            yield_pct = format_percent(listing.estimated_gross_yield) if listing.estimated_gross_yield else "-"
            roi_5 = format_percent(listing.roi_5yr.annualized_roi) if listing.roi_5yr else "-"
            verdict = _verdict_label(listing)
            conf = (listing.agent_confidence or "-").lower()

            # Truncate title
            title = listing.title[:30] + "..." if len(listing.title) > 30 else listing.title
            if listing.additional_urls:
                title += f" (+{len(listing.additional_urls)})"

            lines.append(f"| {i} | {title} | {verdict} | {conf} | {price} | {psf} | {score} | {yield_pct} | {roi_5} |")

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
        tier1 = [l for l in listings if l.final_tier == 1]
        tier2 = [l for l in listings if l.final_tier == 2]

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

    # Methodology note (v3 MMR)
    lines.append("## Scoring Methodology")
    lines.append("")
    lines.append("Technical score is an **MMR** (Elo-style, base 1500, uncapped) built from")
    lines.append("continuous metrics, normalized onto a **0-1000 display scale** (500 = market-typical;")
    lines.append("650+ recommended tier, <450 below threshold). Components, roughly by weight:")
    lines.append("")
    lines.append("| Component | Driver |")
    lines.append("|-----------|--------|")
    lines.append("| Appreciation | URA rate vs 4%/yr baseline, confidence-weighted by transaction count |")
    lines.append("| PSF value | Symmetric: discount vs URA market PSF adds, premium subtracts |")
    lines.append("| Yield | Gross yield vs 3.2% baseline |")
    lines.append("| Liquidity | Transaction volume, buyer pool, development size, price band |")
    lines.append("| Future | Upcoming MRT, govt zones, transformation |")
    lines.append("| Lease/Age | Continuous decay; 3-7yr age sweet spot |")
    lines.append("| Red flags | West-facing, suspicious PSF, oversized/mismatched layout |")
    lines.append("")
    lines.append("Missing data is neutral (0), never penalized. The technical score is one input —")
    lines.append("the AI verdict above is the actual recommendation.")
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
