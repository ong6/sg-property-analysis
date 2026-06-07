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
    """One condo (represented by its best listing) in the arena."""
    key: str                      # project key
    name: str
    listing: Any                  # ScoredListing of the representative unit
    dims: dict[str, float] = field(default_factory=dict)
    elo: float = ELO_START
    wins: int = 0
    losses: int = 0
    draws: int = 0
    dims_won: dict[str, int] = field(default_factory=dict)
    on_frontier: bool = False

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

    Listings are grouped by project; each project's highest-MMR listing is its
    representative (the unit you would actually buy). Returns fighters sorted
    by Elo descending.
    """
    # Group by project, keep best representative
    by_project: dict[str, Any] = {}
    for s in scored_listings:
        if not s.mmr_components:
            continue
        key = (s.project_name or s.title or "").strip().lower()
        if not key:
            continue
        cur = by_project.get(key)
        if cur is None or (s.mmr or 0) > (cur.mmr or 0):
            by_project[key] = s

    fighters = []
    for key, s in by_project.items():
        f = Fighter(
            key=key,
            name=s.project_name or s.title,
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
                a_score = first_results[(i, j)] if (i, j) in first_results else first_results[(j, i)]
            _update_elo(a, b, a_score)

    _pareto_frontier(fighters)
    fighters.sort(key=lambda f: f.elo, reverse=True)
    return fighters


def format_arena_report(fighters: list[Fighter], top_n: int = 25) -> str:
    """Markdown report: Elo table, frontier, champion analysis."""
    lines = ["# Condo Arena — pairwise value tournament", ""]
    n = len(fighters)
    lines.append(f"{n} condos, {n*(n-1)//2} fights (round-robin), "
                 f"dimensions: {', '.join(f'{d} {int(w*100)}%' for d, (_k, w) in DIMENSIONS.items())}")
    lines.append("")
    lines.append("| Rank | Condo | Elo | W-L-D | Win% | MMR/1000 | Price | PSF | Age-adj premium | Frontier |")
    lines.append("|------|-------|-----|-------|------|----------|-------|-----|-----------------|----------|")
    for rank, f in enumerate(fighters[:top_n], 1):
        s = f.listing
        rel = (s.score_breakdown or {}).get("relative_value", {})
        prem = rel.get("premium_vs_age_adjusted_median_pct")
        prem_str = f"{prem:+.1f}%" if prem is not None else "-"
        price_str = f"${s.price/1e6:.2f}M" if s.price else "-"
        psf_str = f"${s.psf:,.0f}" if s.psf else "-"
        frontier = "⭐" if f.on_frontier else ""
        lines.append(
            f"| {rank} | {f.name[:30]} | {f.elo:.0f} | {f.wins}-{f.losses}-{f.draws} "
            f"| {f.win_rate*100:.0f}% | {s.score_1000 or '-'} | {price_str} | {psf_str} "
            f"| {prem_str} | {frontier} |"
        )
    lines.append("")

    frontier = [f for f in fighters if f.on_frontier]
    lines.append(f"## Pareto frontier ({len(frontier)} condos)")
    lines.append("")
    lines.append("Not beaten on every dimension by any other condo — the efficient set:")
    for f in frontier:
        best_dims = sorted(f.dims_won.items(), key=lambda kv: -kv[1])[:3]
        dims_str = ", ".join(f"{d} ({c} wins)" for d, c in best_dims) if best_dims else "-"
        lines.append(f"- **{f.name}** (Elo {f.elo:.0f}) — strongest: {dims_str}")
    lines.append("")

    if fighters:
        champ = fighters[0]
        s = champ.listing
        rel = (s.score_breakdown or {}).get("relative_value", {})
        lines.append("## Champion")
        lines.append("")
        lines.append(f"**{champ.name}** — Elo {champ.elo:.0f}, "
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
