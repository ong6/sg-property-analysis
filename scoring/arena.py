"""Condo arena — pairwise round-robin value tournament.

Every condo fights every other condo. A fight contests six weighted
dimensions built from MMR components; whoever takes the larger weighted share
wins the fight and gains Elo. The output is an Elo ranking, per-condo win
rates, and the Pareto-efficient frontier.

Why not just sort by MMR? MMR is a weighted SUM — a single extreme component
can carry an otherwise mediocre condo. The arena rewards breadth: a condo
that beats most of the field dimension-by-dimension is a safer "best value"
pick than one with one spike. Condos on the Pareto frontier are not beaten
on ALL fronts by anything else — the efficient set to choose from.

Fights are deterministic (seeded pair order, multiple Elo epochs) so the
ranking is reproducible for the same input set.
"""

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

# Dimension -> (component keys summed, weight in a fight)
DIMENSIONS: dict[str, tuple[list[str], float]] = {
    "appreciation": (["appreciation", "momentum"], 0.30),
    "value": (["psf_value", "age_value"], 0.25),
    "liquidity": (["txn_volume", "buyer_pool", "dev_size", "price_band"], 0.20),
    "yield": (["yield"], 0.10),
    "future": (["future"], 0.10),
    "condition": (["age", "lease", "mrt", "cost", "red_flags"], 0.05),
}

ELO_START = 1200.0
ELO_K = 32.0
EPOCHS = 3          # passes over all pairs; Elo converges, order effects wash out
TIE_MARGIN = 0.75   # dimension scores closer than this are a split


@dataclass
class Fighter:
    """One contender: a condo's unit type, represented by its best listing.

    Unit types fight separately — a project's 2BR and 3BR have different PSF,
    yield, efficiency and value profiles, so each is its own contender.
    """
    key: str                      # (project, beds) key
    name: str
    beds: Optional[int]
    listing: Any                  # ScoredListing of the representative unit
    dims: dict[str, float] = field(default_factory=dict)
    elo: float = ELO_START
    wins: int = 0
    losses: int = 0
    draws: int = 0
    dims_won: dict[str, int] = field(default_factory=dict)
    on_frontier: bool = False
    # Agent-flow join: the AI's prior evaluation of this condo (from the
    # git-tracked eval memory), shown alongside the technical ranking.
    agent_rating: Optional[str] = None
    agent_eval_date: Optional[str] = None

    @property
    def fights(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def win_rate(self) -> float:
        return self.wins / self.fights if self.fights else 0.0


def _dimension_scores(components: dict) -> dict[str, float]:
    return {
        dim: sum(components.get(k, 0.0) for k in keys)
        for dim, (keys, _w) in DIMENSIONS.items()
    }


def _fight(a: Fighter, b: Fighter) -> float:
    """Run one fight. Returns a's actual score: 1 win, 0.5 draw, 0 loss."""
    a_pts = b_pts = 0.0
    for dim, (_keys, weight) in DIMENSIONS.items():
        diff = a.dims[dim] - b.dims[dim]
        if diff > TIE_MARGIN:
            a_pts += weight
            a.dims_won[dim] = a.dims_won.get(dim, 0) + 1
        elif diff < -TIE_MARGIN:
            b_pts += weight
            b.dims_won[dim] = b.dims_won.get(dim, 0) + 1
        else:
            a_pts += weight / 2
            b_pts += weight / 2
    if a_pts > b_pts:
        return 1.0
    if a_pts < b_pts:
        return 0.0
    return 0.5


def _update_elo(a: Fighter, b: Fighter, a_score: float) -> None:
    expected_a = 1.0 / (1.0 + 10 ** ((b.elo - a.elo) / 400.0))
    a.elo += ELO_K * (a_score - expected_a)
    b.elo += ELO_K * ((1.0 - a_score) - (1.0 - expected_a))


def _pareto_frontier(fighters: list[Fighter]) -> None:
    """Mark fighters not dominated on all dimensions by any other fighter."""
    for f in fighters:
        f.on_frontier = True
        for other in fighters:
            if other is f:
                continue
            ge_all = all(other.dims[d] >= f.dims[d] - 1e-9 for d in DIMENSIONS)
            gt_any = any(other.dims[d] > f.dims[d] + 1e-9 for d in DIMENSIONS)
            if ge_all and gt_any:
                f.on_frontier = False
                break


def run_arena(scored_listings: list) -> list[Fighter]:
    """Run the tournament over scored listings (with MMR components).

    Contenders are (project, unit type) pairs — a condo's 2BR and 3BR fight
    separately. Each contender is represented by its highest-MMR listing (the
    unit you would actually buy). Returns fighters sorted by Elo descending.
    """
    # Group by (project, beds), keep best representative per unit type
    by_unit_type: dict[tuple, Any] = {}
    for s in scored_listings:
        if not s.mmr_components:
            continue
        key_name = (s.project_name or s.title or "").strip().lower()
        if not key_name:
            continue
        key = (key_name, s.beds or 0)
        cur = by_unit_type.get(key)
        if cur is None or (s.mmr or 0) > (cur.mmr or 0):
            by_unit_type[key] = s

    fighters = []
    for key, s in by_unit_type.items():
        f = Fighter(
            key=f"{key[0]}|{key[1]}br",
            name=s.project_name or s.title,
            beds=s.beds,
            listing=s,
            dims=_dimension_scores(s.mmr_components),
        )
        fighters.append(f)

    if len(fighters) < 2:
        return fighters

    # Round-robin pairs; only the FIRST epoch records win/loss tallies
    # (later epochs just settle Elo).
    pairs = [(i, j) for i in range(len(fighters)) for j in range(i + 1, len(fighters))]
    rng = random.Random(42)
    first_results: dict[tuple[int, int], float] = {}
    for epoch in range(EPOCHS):
        rng.shuffle(pairs)
        for i, j in pairs:
            a, b = fighters[i], fighters[j]
            if epoch == 0:
                a_score = _fight(a, b)
                first_results[(i, j)] = a_score
                if a_score == 1.0:
                    a.wins += 1
                    b.losses += 1
                elif a_score == 0.0:
                    b.wins += 1
                    a.losses += 1
                else:
                    a.draws += 1
                    b.draws += 1
            else:
                # pairs are always generated with i < j, so (i, j) is the key
                a_score = first_results[(i, j)]
            _update_elo(a, b, a_score)

    _pareto_frontier(fighters)
    fighters.sort(key=lambda f: f.elo, reverse=True)
    return fighters


def bracket_label(beds: Optional[int]) -> Optional[str]:
    """Weight-class label for a bedroom count. None = no bracket (unknown)."""
    if not beds:
        return None
    if beds >= 4:
        return "4BR+"
    return f"{beds}BR"


def run_brackets(scored_listings: list) -> dict[str, list[Fighter]]:
    """Run separate round-robins per bedroom bracket (2BR vs 2BR, 3BR vs 3BR…).

    Cross-type fights conflate unit-type structure with condo quality — 2BRs
    systematically win yield/price-band, 3BRs win family-demand appreciation.
    Brackets make every fight like-for-like; the all-types ranking remains
    available as the 'open' division.

    Returns {bracket_label: fighters sorted by Elo} for brackets with >=2
    contenders, ordered 1BR, 2BR, 3BR, 4BR+. Unknown-bed listings are
    excluded from brackets (they still appear in the open division).
    """
    by_bracket: dict[str, list] = {}
    for s in scored_listings:
        label = bracket_label(s.beds)
        if label:
            by_bracket.setdefault(label, []).append(s)

    order = {"1BR": 1, "2BR": 2, "3BR": 3, "4BR+": 4}
    results: dict[str, list[Fighter]] = {}
    for label in sorted(by_bracket, key=lambda b: order.get(b, 9)):
        fighters = run_arena(by_bracket[label])
        if len(fighters) >= 2:
            results[label] = fighters
    return results


def build_referee_packet(fighters: list[Fighter], top_n: int = 15) -> dict:
    """Data-quality packet for the AI referee.

    The arena is purely algorithmic — no AI judgment is involved in the
    fights. This packet exposes the stats BEHIND the top ranks plus
    auto-detected anomalies so the AI referee can confirm the fight was fair
    (data real, dimensions measured properly) before results are trusted.
    Auto-flags are leads for the referee, not verdicts.
    """
    packet: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "contenders": [],
        "auto_flags": [],
        "referee_instructions": (
            "You are the referee. For each contender below — especially any with "
            "auto_flags — verify the stats are credible before endorsing the "
            "ranking: (1) is the appreciation rate from real transactions or a "
            "guess? (2) is the PSF/sqft plausible for that project and unit type "
            "(web-check if extreme)? (3) is a yield edge built on estimated rent? "
            "(4) does the age/relative-value adjustment make sense for its "
            "built_year? Demote or disqualify contenders whose edge rests on bad "
            "data, then write a short referee verdict: confirmed ranks, demoted "
            "contenders with reasons, and overall confidence in this run."
        ),
    }

    for rank, f in enumerate(fighters[:top_n], 1):
        s = f.listing
        sb = s.score_breakdown or {}
        cap = sb.get("capital_appreciation", {}).get("appreciation_rate", {})
        rel = sb.get("relative_value", {})
        comps = s.mmr_components or {}

        flags = []
        txn = cap.get("transaction_count") or 0
        apr_pct = (s.appreciation_rate or 0) * 100
        if apr_pct >= 6 and txn < 10:
            flags.append(f"high appreciation ({apr_pct:.1f}%/yr) from thin transactions ({txn})")
        if s.appreciation_source in ("default", "regional_baseline"):
            flags.append("appreciation is a regional guess, not measured data")
        if (s.rent_source or "").startswith("fallback") and comps.get("yield", 0) > 5:
            flags.append("yield advantage built on a fallback rent estimate")
        if abs(comps.get("psf_value", 0)) > 15:
            flags.append(f"extreme psf_value ({comps['psf_value']:+.1f}) — verify listing PSF/sqft is real")
        if abs(comps.get("age_value", 0)) > 10:
            flags.append(f"extreme age_value ({comps['age_value']:+.1f}) — verify built_year and peer set")
        if not s.built_year:
            flags.append("missing built_year — age/lease components neutral, may overrank")
        if s.sqft and s.beds and not (250 <= s.sqft / s.beds <= 800):
            flags.append(f"odd sqft/bed ({s.sqft / s.beds:.0f}) — possible misparse")

        packet["contenders"].append({
            "rank": rank,
            "name": f.name,
            "beds": f.beds,
            "elo": round(f.elo),
            "record": f"{f.wins}-{f.losses}-{f.draws}",
            "on_frontier": f.on_frontier,
            "mmr": s.mmr,
            "score_1000": s.score_1000,
            "price": s.price,
            "psf": s.psf,
            "sqft": s.sqft,
            "district": s.district,
            "built_year": s.built_year,
            "tenure": s.tenure,
            "appreciation_rate_pct": round(apr_pct, 2),
            "appreciation_source": s.appreciation_source,
            "appreciation_txn_count": txn,
            "rent_source": s.rent_source,
            "gross_yield_pct": s.estimated_gross_yield,
            "relative_value": rel or None,
            "mmr_components": comps,
            "agent_rating": f.agent_rating,
            "agent_eval_date": f.agent_eval_date,
            "url": s.url,
            "auto_flags": flags,
        })
        if flags:
            label = f"{f.name} {f.beds}BR" if f.beds else f.name
            packet["auto_flags"].append({"rank": rank, "name": label, "flags": flags})

    return packet


def format_arena_report(
    fighters: list[Fighter],
    top_n: int = 25,
    title: str = "Condo Arena — pairwise value tournament",
    heading_level: int = 1,
) -> str:
    """Markdown report: Elo table, frontier, champion analysis."""
    H = "#" * heading_level
    lines = [f"{H} {title}", ""]
    n = len(fighters)
    lines.append(f"{n} contenders (condo × unit type), {n*(n-1)//2} fights (round-robin), "
                 f"dimensions: {', '.join(f'{d} {int(w*100)}%' for d, (_k, w) in DIMENSIONS.items())}")
    lines.append("")
    lines.append("| Rank | Condo | Type | Elo | W-L-D | Win% | MMR/1000 | Price | PSF | Age-adj premium | Agent eval | Frontier |")
    lines.append("|------|-------|------|-----|-------|------|----------|-------|-----|-----------------|-----------|----------|")
    for rank, f in enumerate(fighters[:top_n], 1):
        s = f.listing
        rel = (s.score_breakdown or {}).get("relative_value", {})
        prem = rel.get("premium_vs_age_adjusted_median_pct")
        prem_str = f"{prem:+.1f}%" if prem is not None else "-"
        price_str = f"${s.price/1e6:.2f}M" if s.price else "-"
        psf_str = f"${s.psf:,.0f}" if s.psf else "-"
        beds_str = f"{f.beds}BR" if f.beds else "?"
        frontier = "⭐" if f.on_frontier else ""
        agent_str = f"{f.agent_rating} ({f.agent_eval_date})" if f.agent_rating else "-"
        lines.append(
            f"| {rank} | {f.name[:30]} | {beds_str} | {f.elo:.0f} | {f.wins}-{f.losses}-{f.draws} "
            f"| {f.win_rate*100:.0f}% | {s.score_1000 or '-'} | {price_str} | {psf_str} "
            f"| {prem_str} | {agent_str} | {frontier} |"
        )
    lines.append("")

    frontier = [f for f in fighters if f.on_frontier]
    lines.append(f"{H}# Pareto frontier ({len(frontier)} condos)")
    lines.append("")
    lines.append("Not beaten on every dimension by any other contender — the efficient set:")
    for f in frontier:
        best_dims = sorted(f.dims_won.items(), key=lambda kv: -kv[1])[:3]
        dims_str = ", ".join(f"{d} ({c} wins)" for d, c in best_dims) if best_dims else "-"
        beds_str = f" {f.beds}BR" if f.beds else ""
        lines.append(f"- **{f.name}{beds_str}** (Elo {f.elo:.0f}) — strongest: {dims_str}")
    lines.append("")

    if fighters:
        champ = fighters[0]
        s = champ.listing
        rel = (s.score_breakdown or {}).get("relative_value", {})
        lines.append(f"{H}# Champion")
        lines.append("")
        champ_beds = f" ({champ.beds}BR)" if champ.beds else ""
        lines.append(f"**{champ.name}{champ_beds}** — Elo {champ.elo:.0f}, "
                     f"{champ.wins}-{champ.losses}-{champ.draws}, MMR {s.mmr:.0f} ({s.score_1000}/1000)")
        if s.url:
            lines.append(f"- Representative unit: {s.beds}BR ${s.price:,} @ ${s.psf:,.0f} psf — {s.url}")
        if rel:
            lines.append(f"- Age-adjusted premium vs district: {rel.get('premium_vs_age_adjusted_median_pct', '?')}% "
                         f"(new-launch median ${rel.get('new_launch_median_psf', '?')} psf)")
        dim_summary = ", ".join(f"{d}: {champ.dims[d]:+.1f}" for d in DIMENSIONS)
        lines.append(f"- Dimension scores: {dim_summary}")
        lines.append("")
        lines.append("⚠ The arena ranks on technical data only — run the full agent "
                     "evaluation (research, red flags) on the champion before acting.")

    return "\n".join(lines)


def format_brackets_report(
    brackets: dict[str, list[Fighter]],
    open_fighters: Optional[list[Fighter]] = None,
    top_n: int = 20,
) -> str:
    """Combined report: one section per weight-class bracket + open division."""
    lines = [
        "# Condo Arena — weight-class brackets",
        "",
        "Like-for-like fights: each unit type only fights its own bracket "
        "(cross-type fights conflate unit-type structure — 2BRs win yield, "
        "3BRs win family appreciation — with condo quality). The open "
        "division (all types) is the pound-for-pound view.",
        "",
    ]
    for label, fighters in brackets.items():
        lines.append(format_arena_report(
            fighters, top_n=top_n,
            title=f"{label} bracket ({len(fighters)} contenders)",
            heading_level=2,
        ))
        lines.append("")
    if open_fighters:
        lines.append(format_arena_report(
            open_fighters, top_n=top_n,
            title=f"Open division — all types, pound-for-pound ({len(open_fighters)} contenders)",
            heading_level=2,
        ))
    return "\n".join(lines)


def build_brackets_referee_packet(
    brackets: dict[str, list[Fighter]],
    top_n_per: int = 8,
) -> dict:
    """Referee packet across brackets — top contenders per bracket, tagged."""
    merged: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "contenders": [],
        "auto_flags": [],
    }
    for label, fighters in brackets.items():
        p = build_referee_packet(fighters, top_n=top_n_per)
        for c in p["contenders"]:
            c["bracket"] = label
            merged["contenders"].append(c)
        for fl in p["auto_flags"]:
            fl["bracket"] = label
            merged["auto_flags"].append(fl)
        merged["referee_instructions"] = p["referee_instructions"]
    return merged
