"""Valuation score — "is this priced right TODAY?" on a 0–100 scale.

The third money axis, beside MMR (forward return + carry + exit) and livability
(home quality). Where MMR answers "will this make money over a 5–7yr hold?",
valuation answers the narrower, PRESENT-tense question: is the ask cheap, fair,
or dear versus comparable peers, age-adjusted?

This is the BACKTESTED axis — built from the single signal the URA panel found
most robustly predictive (cheap-vs-district-peers, ρ≈−0.25), so unlike
livability it has an evidence basis. But "cheap" in a scraped DB is exactly what
the v3.6–v3.9 trust layer fights: the deepest discounts are usually artifacts
(mis-scraped sqft, strata formats vs apartment medians, stale/bait prices). So
valuation shares MMR's defences — discounts past the knee are compressed, and a
SUSPECT cheap reading (bed/sqft mismatch, thin cohort, own-stack prints
contradicting the discount, oversized) is damped on the cheap side only. A
fake-cheap listing must not score 95 here any more than it tops MMR.

50 = fairly priced. >50 = cheap for its age vs peers. <50 = dear. Missing/thin
comparison data pulls SYMMETRICALLY toward 50 (we don't know — never invents a
verdict). Returns {"score": int 0–100, "components": {...}, "why": str,
"confidence": "high"|"medium"|"low"|None}.

Sign convention (both inputs): NEGATIVE % = cheap, POSITIVE % = dear.
"""

from __future__ import annotations

import math

try:
    from config import (
        MMR_DISCOUNT_TRUST_KNEE_PCT,
        MMR_SUSPECT_VALUE_FACTOR,
        MIN_BAND_TXNS,
    )
except ImportError:  # mirror config.py — only used if the import fails
    MMR_DISCOUNT_TRUST_KNEE_PCT = 25.0
    MMR_SUSPECT_VALUE_FACTOR = 0.25
    MIN_BAND_TXNS = 5

# Tunables — module-local for now (config.py is owned by the live MMR session;
# these move there once the 3-score system settles). Calibrated (Design B, see
# below) so a TYPICALLY-priced ask scores ≈50, an ask ~15% below the typical
# district level scores ≈85 (cheap), and one ~25% above scores ≈12 (dear).
_VAL_SLOPE = 4.0                 # tanh slope on the centered value_pct
# Live-DB structural centers (measured Jun-2026 medians over the scored DB).
# Asks sit ABOVE transacted URA comps — seller markup + market drift since the
# comp window — and by DIFFERENT amounts per comp set, so each signal carries
# its OWN center rather than one blended constant (the earlier single 18% was
# near the district MEAN, skewed by a long right tail, and ignored the much
# smaller same-size-cohort gap). Subtracting each center puts 50 at a typically-
# priced ask, >50 = cheaper than typical (a relative bargain), <50 = dearer —
# matching the system-wide "midpoint = market-typical" convention (livability
# 50, MMR 500). Re-measure when the DB shifts; the raw signal still feeds MMR.
_VAL_CENTER_DISTRICT_PCT = 10.5  # median ask premium vs age-adj district transacted peers
_VAL_CENTER_COHORT_PCT = 5.0     # median ask premium vs same-size same-project cohort
_DISCOUNT_EXCESS_CREDIT = 0.25   # mirrors MMR's knee — discount past the knee earns little
_OVERSIZED_CHEAP_FACTOR = 0.5    # cheap-PSF on a big unit is mostly a size artifact
_LOW_FLOOR_CHEAP_FACTOR = 0.5    # cheap-PSF on a ground/low-floor stack is a floor discount
_PEER_FULL = 10.0                # district peer_count for full-confidence read
_COHORT_FULL = 15.0              # same-size txns for full-confidence read
_CONF_FLOOR = 0.4                # a zero-confidence read still keeps this much signal


def _knee(premium_pct: float) -> float:
    """Compress an implausibly deep discount (mirror of mmr._knee_discount).

    A discount beyond MMR_DISCOUNT_TRUST_KNEE_PCT is far more likely a data
    artifact than alpha, so it earns only marginal extra credit. Premiums pass
    through untouched.
    """
    if premium_pct >= -MMR_DISCOUNT_TRUST_KNEE_PCT:
        return premium_pct
    excess = -premium_pct - MMR_DISCOUNT_TRUST_KNEE_PCT
    return -(MMR_DISCOUNT_TRUST_KNEE_PCT + excess * _DISCOUNT_EXCESS_CREDIT)


def _confidence_label(conf_frac: float) -> str:
    if conf_frac >= 0.8:
        return "high"
    if conf_frac >= 0.4:
        return "medium"
    return "low"


def score_valuation(scored) -> dict:
    """Score how well-priced a listing is TODAY vs comparable peers (0–100)."""
    sb = getattr(scored, "score_breakdown", None) or {}
    rv = sb.get("relative_value") or {}
    flags_block = sb.get("red_flags") or {}
    flag_names = {f.get("flag") for f in flags_block.get("flags", [])}

    rel_premium = rv.get("premium_vs_age_adjusted_median_pct")
    peer_count = rv.get("peer_count") or 0
    psf_premium = flags_block.get("psf_premium_pct")
    cohort_txns = flags_block.get("psf_cohort_txns")
    stack_premium = flags_block.get("stack_premium_pct")
    low_floor_share = flags_block.get("stack_low_floor_share")

    # --- collect signed value signals (negative = cheap) with confidences ---
    # Each signal is knee'd on its RAW premium (so the artifact-discount knee and
    # suspect tests below see true asking depth), then centered by its OWN
    # measured structural premium. We keep both the centered term (drives the
    # score) and the raw knee'd term (reported as value_pct / in `why`).
    signals: list[tuple[float, float]] = []   # (centered value_pct, weight 0..1)
    raw_terms: list[tuple[float, float]] = []  # (raw knee'd premium, weight) — reporting only
    comps: dict = {}
    if rel_premium is not None:
        w = max(0.0, min(1.0, peer_count / _PEER_FULL)) if peer_count else 0.3
        k = _knee(rel_premium)
        signals.append((k - _VAL_CENTER_DISTRICT_PCT, w))
        raw_terms.append((k, w))
        comps["vs_age_adj_peers_pct"] = round(rel_premium, 1)
    if psf_premium is not None:
        cn = cohort_txns or 0
        w = max(0.0, min(1.0, cn / _COHORT_FULL)) if cn else 0.3
        k = _knee(psf_premium)
        signals.append((k - _VAL_CENTER_COHORT_PCT, w))
        raw_terms.append((k, w))
        comps["vs_same_size_cohort_pct"] = round(psf_premium, 1)

    if not signals:
        return {"score": 50, "components": {}, "why": "no comparable price data",
                "confidence": None}

    # weighted blends; confidence = the best single comparison we have
    wsum = sum(w for _, w in signals) or 1.0
    centered = sum(v * w for v, w in signals) / wsum    # vs typical ask — drives the score
    value_pct = sum(v * w for v, w in raw_terms) / wsum  # raw ask-vs-comps premium (reporting)
    conf_frac = max(w for _, w in signals)

    # base points: cheaper-than-typical (negative centered) -> above 50
    pts = 50.0 * math.tanh(-centered / 100.0 * _VAL_SLOPE)
    # symmetric confidence shrink toward 50 (thin comparison = "we don't know")
    pts *= _CONF_FLOOR + (1.0 - _CONF_FLOOR) * conf_frac

    # --- artifact defences (mirror MMR) — damp the CHEAP side only ---
    deep_discount = any(p is not None and p < -MMR_DISCOUNT_TRUST_KNEE_PCT
                        for p in (rel_premium, psf_premium))
    print_contradiction = (stack_premium or 0) > 5.0
    suspect = ("bedroom_sqft_mismatch" in flag_names
               or "ask_below_stack_prints" in flag_names
               or (deep_discount and (cohort_txns or 0) < MIN_BAND_TXNS)
               or print_contradiction)
    oversized = "oversized_unit" in flag_names
    # v3.12: a confirmed low-floor stack's cheapness is a structural floor
    # discount, not a bargain — damp the cheap side (mirror of MMR).
    low_floor = (low_floor_share or 0) >= 0.7
    damp_notes = []
    if pts > 0:
        if oversized:
            pts *= _OVERSIZED_CHEAP_FACTOR
            damp_notes.append("oversized")
        if low_floor:
            pts *= _LOW_FLOOR_CHEAP_FACTOR
            damp_notes.append("low-floor")
        if suspect:
            pts *= MMR_SUSPECT_VALUE_FACTOR
            damp_notes.append("suspect→verify")

    score = int(max(0, min(100, round(50 + pts))))

    # --- explain ---
    bits = []
    if "vs_age_adj_peers_pct" in comps:
        p = comps["vs_age_adj_peers_pct"]
        bits.append(f"vs age-adj peers {p:+.0f}%")
    if "vs_same_size_cohort_pct" in comps:
        p = comps["vs_same_size_cohort_pct"]
        bits.append(f"vs same-size {p:+.0f}%")
    if damp_notes:
        bits.append("damped: " + "+".join(damp_notes))
    # Decisive thresholds: a barely-above-neutral score is "fair", not "cheap".
    verdict = "cheap" if score >= 62 else ("dear" if score <= 38 else "fair")
    # A suspect or low-floor reading whose residual still clears the bar must NOT
    # be sold as cheap — the apparent discount is the artifact we just damped.
    if (suspect or low_floor) and verdict == "cheap":
        verdict = "fair"
    why = f"{verdict} · " + " · ".join(bits) if bits else verdict

    return {
        "score": score,
        "components": comps,
        "value_pct": round(value_pct, 1),       # raw blended ask-vs-comps premium
        "centered_pct": round(centered, 1),      # after DB-centering (drives the score)
        "why": why,
        "confidence": _confidence_label(conf_frac),
    }
