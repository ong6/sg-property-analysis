"""Overall score — purpose-weighted combination of the three axes.

The three base scores answer different questions:
  - Valuation  (0–100): priced right TODAY?            [backtested]
  - Livability (0–100): how good a HOME is it?          [heuristic]
  - Investment (score_1000/1000): make money in 5–7yr?  [backtested]

There is no single "good property" number, because own-stay and investment
buyers optimize different things — a great home is often a mediocre investment
and vice versa (large / freehold / prime is the clearest case: most desirable to
live in, weakest measured forward return, because its desirability is already in
the price). So Overall is PURPOSE-WEIGHTED: the inputs combine differently
depending on why you're buying. Own-stay leans on livability (with valuation and
a resale-safety nod to the investment axis); investment leans on MMR — the
backtest-calibrated answer — plus only a capped livability floor, since valuation
is largely MMR's own value signal and re-blending it would just double-count.

Crucially, livability NEVER enters the investment Overall as appreciation — a
nicer home does not mechanically appreciate more (its niceness is priced in). It
enters only as a small, CAPPED demand-floor term: strong end-user desirability
is downside protection / exit-liquidity insurance; weak desirability is exit
risk. That nudge is bounded (±_DEMAND_FLOOR_CAP) so it can never masquerade as
return. For own-stay, the investment axis enters only as a resale-safety net.

All inputs may be None (→ treated as neutral, never penalized). Returns
{"own_stay", "investment", "headline", "purpose"} on a 0–100 scale.
"""

from __future__ import annotations

# Investment Overall = MMR (the backtest-CALIBRATED investment answer) + a capped
# livability demand-floor. Valuation is deliberately NOT re-blended here: it is
# largely MMR's own cheap-vs-peers signal surfaced standalone (measured corr
# ≈0.78), so adding it would re-weight value ABOVE the calibrated blend while
# merely tracking MMR (overall-vs-MMR corr ≈0.94). MMR already prices value
# optimally per the backtest; the only thing Overall adds for an investor is the
# bounded livability floor (downside / exit-liquidity, never appreciation).
# Valuation stands as its own axis/column instead.
_DEMAND_FLOOR_K = 0.20       # livability points → floor points
_DEMAND_FLOOR_CAP = 6.0      # max |adjustment| livability may make to the investment overall

# Own-stay Overall = weighted(livability, valuation, investment-as-resale-safety).
_OWN_W_LIVABILITY = 0.55
_OWN_W_VALUATION = 0.30
_OWN_W_INVESTMENT = 0.15

_OWN_STAY_ALIASES = {"own_stay", "own-stay", "ownstay", "stay", "live", "home"}


def normalize_purpose(purpose) -> str:
    """Map a free-form purpose string to 'own_stay' | 'investment' (default)."""
    return "own_stay" if str(purpose or "").strip().lower() in _OWN_STAY_ALIASES else "investment"


def _n(v, default=50.0) -> float:
    return default if v is None else float(v)


def _clamp(v, lo=0.0, hi=100.0) -> float:
    return max(lo, min(hi, v))


def compute_overall(valuation=None, livability=None, score_1000=None,
                    purpose="investment") -> dict:
    """Combine the three axes into purpose-weighted Overall scores (0–100).

    valuation/livability are 0–100 (or None); score_1000 is 0–1000 (or None,
    → neutral 500). Returns both Overalls plus the one selected by `purpose`.
    """
    val = _n(valuation)
    liv_present = livability is not None
    liv = _n(livability)
    inv100 = _clamp(_n(score_1000, 500.0) / 10.0)

    # Investment Overall: MMR drives it (the calibrated answer); livability is
    # only a bounded demand-floor nudge (downside/liquidity, never appreciation).
    # Valuation is NOT re-blended — see the constants note above.
    demand_floor = 0.0
    if liv_present:
        demand_floor = max(-_DEMAND_FLOOR_CAP,
                           min(_DEMAND_FLOOR_CAP, (liv - 50.0) * _DEMAND_FLOOR_K))
    investment_overall = _clamp(inv100 + demand_floor)

    # Own-stay Overall: home quality leads; don't-overpay (valuation) matters;
    # investment enters only as a resale-safety net.
    own_stay_overall = _clamp(
        _OWN_W_LIVABILITY * liv + _OWN_W_VALUATION * val + _OWN_W_INVESTMENT * inv100)

    purpose = normalize_purpose(purpose)
    headline = own_stay_overall if purpose == "own_stay" else investment_overall
    return {
        "own_stay": int(round(own_stay_overall)),
        "investment": int(round(investment_overall)),
        "headline": int(round(headline)),
        "purpose": purpose,
    }
