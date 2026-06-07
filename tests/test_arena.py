"""Tests for the condo arena tournament."""

from scoring.arena import run_arena, format_arena_report, _dimension_scores, DIMENSIONS
from scoring.models import ScoredListing


def _fighter_listing(name, comps, mmr=1500, price=1_500_000, psf=1800):
    s = ScoredListing(id=name, title=name, project_name=name, price=price, url=f"https://x/{name}")
    s.psf = psf
    s.mmr = mmr
    s.score_1000 = 500
    s.mmr_components = comps
    return s


def _comps(**overrides):
    base = {k: 0.0 for keys, _ in DIMENSIONS.values() for k in keys}
    base.update(overrides)
    return base


class TestArena:
    def test_dominant_condo_wins_all(self):
        strong = _fighter_listing("Strong", _comps(appreciation=20, psf_value=10, txn_volume=8, yield_=0))
        weak1 = _fighter_listing("Weak1", _comps(appreciation=-10, psf_value=-5))
        weak2 = _fighter_listing("Weak2", _comps(appreciation=-5, psf_value=-8))
        fighters = run_arena([strong, weak1, weak2])
        assert fighters[0].name == "Strong"
        assert fighters[0].wins == 2 and fighters[0].losses == 0
        assert fighters[0].elo > fighters[-1].elo

    def test_same_unit_type_collapses_to_best_listing(self):
        a1 = _fighter_listing("Condo A", _comps(appreciation=5), mmr=1520)
        a2 = _fighter_listing("Condo A", _comps(appreciation=2), mmr=1480)
        b = _fighter_listing("Condo B", _comps(appreciation=-5), mmr=1490)
        fighters = run_arena([a1, a2, b])
        assert len(fighters) == 2  # A's two same-type listings collapsed
        rep = next(f for f in fighters if f.name == "Condo A")
        assert rep.listing.mmr == 1520  # best unit chosen

    def test_unit_types_fight_separately(self):
        a2br = _fighter_listing("Condo A", _comps(appreciation=5), mmr=1520)
        a2br.beds = 2
        a3br = _fighter_listing("Condo A", _comps(appreciation=2), mmr=1480)
        a3br.beds = 3
        fighters = run_arena([a2br, a3br])
        assert len(fighters) == 2  # 2BR and 3BR are separate contenders
        assert {f.beds for f in fighters} == {2, 3}

    def test_pareto_frontier_excludes_dominated(self):
        top = _fighter_listing("Top", _comps(appreciation=10, psf_value=10, txn_volume=10,
                                             **{"yield": 10}, future=10, age=10))
        dominated = _fighter_listing("Dominated", _comps(appreciation=5, psf_value=5, txn_volume=5,
                                                         **{"yield": 5}, future=5, age=5))
        mixed = _fighter_listing("Mixed", _comps(appreciation=15, psf_value=-5))
        fighters = run_arena([top, dominated, mixed])
        by_name = {f.name: f for f in fighters}
        assert by_name["Top"].on_frontier
        assert not by_name["Dominated"].on_frontier  # beaten everywhere by Top
        assert by_name["Mixed"].on_frontier  # wins appreciation dimension

    def test_deterministic(self):
        listings = [
            _fighter_listing(f"C{i}", _comps(appreciation=i * 2 - 5, psf_value=(5 - i)))
            for i in range(6)
        ]
        r1 = run_arena(listings)
        # fresh copies (run_arena mutates fighters, not listings)
        r2 = run_arena(listings)
        assert [f.name for f in r1] == [f.name for f in r2]
        assert all(abs(a.elo - b.elo) < 1e-9 for a, b in zip(r1, r2))

    def test_report_renders(self):
        listings = [
            _fighter_listing("Alpha", _comps(appreciation=8)),
            _fighter_listing("Beta", _comps(appreciation=-8)),
        ]
        report = format_arena_report(run_arena(listings))
        assert "Champion" in report and "Alpha" in report
