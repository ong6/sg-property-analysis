"""Configuration for Property Finder v2.2."""

import os

PROPERTYGURU_BASE_URL = "https://www.propertyguru.com.sg"
PROPERTYGURU_SEARCH_PATH = "/apartment-condo-for-sale"

BROWSER_USER_DATA_DIR = os.getenv("BROWSER_USER_DATA_DIR", "./chrome-profile")
BROWSER_CHANNEL = "chromium"

# Timeouts (milliseconds)
PAGE_LOAD_TIMEOUT = 60_000
NEXT_DATA_WAIT_TIMEOUT = 15_000  # Short timeout — may not exist anymore
DOM_CONTENT_WAIT_TIMEOUT = 30_000  # Wait for rendered listing cards
CLOUDFLARE_WAIT_TIMEOUT = 120_000

# Rate limiting
REQUEST_DELAY_SECONDS = 2
REQUEST_DELAY_JITTER = 0.30  # +/- 30% jitter to avoid fixed intervals

# Retry settings
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = [5, 10, 20]

# Selectors — tried in order
NEXT_DATA_SELECTOR = "script#__NEXT_DATA__"
# Listing card selectors (PropertyGuru DOM)
LISTING_CARD_SELECTORS = [
    ".listing-card-v2.card",  # PropertyGuru 2024+ primary selector
    "[data-listing-id]",
    "div[class*='listing-card']",
    "div[class*='ListingCard']",
    "div[class*='listingCard']",
    "article[data-listing-id]",
    "div.listing-widget-new",
    "div[class*='SearchResult']",
]

# Default search parameters
DEFAULT_LISTING_TYPE = "sale"
DEFAULT_PROPERTY_TYPE = "C"  # Condo
DEFAULT_SORT = "date"
DEFAULT_ORDER = "desc"
LISTINGS_PER_PAGE = 20

# Per-district scraping & smart pagination
MAX_PAGES_LIMIT = 15  # Hard cap per district (~300 listings)
ENRICH_TOP_N = 0  # Default: disabled (set via --enrich-top flag)
DETAIL_PAGE_DELAY = 2  # Seconds between detail page visits

# ============================================================================
# SCORING SYSTEM v2.0
# ============================================================================
# New scoring weights with better variance and forward-looking factors

# Property scoring weights (v2.2 - total: 100 pts, no URA bias)
SCORE_WEIGHT_RENTAL_YIELD = 15  # Reduced from 30 - yields mechanically low at $2M+
SCORE_WEIGHT_CAPITAL_APPRECIATION = 30  # v2.1: increased from 25, absorbs old URA bonus
SCORE_WEIGHT_FUTURE_POTENTIAL = 20  # MRT, govt zones, transformation
SCORE_WEIGHT_LIQUIDITY = 25  # v2.1: increased from 20
SCORE_WEIGHT_COST_EFFICIENCY = 10  # Unchanged
SCORE_WEIGHT_RED_FLAGS = -10  # Max penalty

# Tier thresholds (v2.0 - adjusted for better distribution)
SCORE_TIER1_MIN = 60  # Recommended (was 65)
SCORE_TIER2_MIN = 45  # Consider (unchanged)

# ============================================================================
# MMR SCORING (v3) — continuous, uncapped, normalized to /1000
# ============================================================================
# Raw MMR is Elo-style: base 1500, unbounded sum of continuous components.
# score_1000 = logistic(raw MMR) on a 0-1000 display scale.
MMR_BASE = 1500
MMR_NORM_CENTER = 1516  # v3.7 recalibration: mean raw MMR of 6,349-listing DB = 1516.0 (age-curve reshape lifted old condos)
                        # (dev_size now live via project_units.json, real-rent yields,
                        # yield slope 18.75→10, expanded 2,293-project URA cache)
MMR_NORM_SCALE = 29     # v3.5b: score_1000 sd≈167, tiers (450/650) stable
# Tier thresholds on the /1000 scale
SCORE1000_TIER1_MIN = 650  # Recommended
SCORE1000_TIER2_MIN = 450  # Consider

# --- v3.4 component slopes (backtest-calibrated) ------------------------------
# Point-in-time URA backtest (backtest.py / backtest_ext.py; split-sample,
# mean-reversion-corrected, Jun 2026) measured each as-of-T feature against
# realized forward resale-PSF appreciation. Findings (Spearman ρ univariate /
# std_β multivariate):
#   trailing appreciation        ρ≈0       → ~0 forward predictive power
#   momentum                     ρ≈-0.03   → flat-to-contrarian (NOT a positive signal)
#   cheap vs district peers      ρ≈-0.26 / std_β -0.13  → STRONGEST region-robust signal
#   region (CCR<RCR<OCR forward)  std_β -0.19           → strongest overall, but regime-bound
#   absolute log-PSF             ρ≈-0.26 BUT std_β -0.04 once region is controlled
#                                → the "absolute price" signal is mostly REGION in disguise;
#                                  do NOT add a separate absolute-price component (it would
#                                  double-count region). Region enters via the baselines below.
# v3.4 (this pass): momentum slope → 0 (was 3.0; ρ≈0/contrarian, wrong sign to reward);
# relvalue cap raised 20→28 so the strongest region-robust signal is no longer throttled
# in its high-conviction (deep-discount) tail. Appreciation slope kept low at 4.0.
# Caveat: single bull regime (2021-26) — magnitudes are de-emphasis, not laws.
# v3.5 (joint out-of-split re-fit, backtest_ext.py PART 5d): a ridge fit of ALL
# joinable component proxies trained on T=2023.75+2024.0 and evaluated on the
# held-out T=2024.25 split reaches forward ρ≈0.26-0.27 vs the current composite's
# 0.232 on the same split. The fit's signed weights: psf_vs_dist (value) and
# region dominate; trailing_cagr ≈ 0 (slightly NEGATIVE marginal), txn_vol
# NEGATIVE marginal (its univariate +0.10 is value/region in disguise), future
# and buyer_pool ≈ 0. So: appreciation slope trimmed 4.0→3.0 (kept as a
# desirability proxy, not a return signal) and liquidity weight 10→6 (kept as
# EXIT-RISK insurance, not a return signal — see MMR_TXN_VOLUME_WEIGHT).
# Composite pooled forward-ρ after this reweight: +0.242 (was +0.240).
MMR_APPRECIATION_SLOPE = 3.0       # pts per %-pt of annual appreciation above center
MMR_APPRECIATION_CENTER_PCT = 4.0  # %/yr treated as neutral
MMR_MOMENTUM_WEIGHT = 0.0          # v3.4: 0 — backtest ρ≈-0.03 (flat/contrarian); surfaced as meta only
# Liquidity (resale txn volume): justified by exit risk over a 5-7yr hold, NOT
# by forward returns (PART 3 std_β -0.05, PART 5d ridge negative). v3.5: 10→6.
MMR_TXN_VOLUME_WEIGHT = 6.0
# Yield: v3.5b, measured on REAL URA rental contracts (backtest_ext PART 5g,
# 113k contracts, n=1438 panel rows): +1pp gross yield -> -0.75pp/yr forward
# PRICE growth (value/region/liquidity controlled) -> net total return only
# ~+0.25pp/yr per +1pp yield. Carry is mostly priced in. Slope compressed
# 18.75 -> 10 pts per %-pt of yield (not lower: the price-drag estimate is one
# bull regime where growth assets beat carry; in flat markets carry wins).
# Confidence-weighted in mmr.py — real-rent sources (ura_project_bed 0.95 /
# ura_project 0.8) replace the synthetic district constants (0.5).
MMR_YIELD_SLOPE_PTS_PER_PP = 10.0
MMR_YIELD_CENTER_PCT = 3.2
# v3.6.1: tanh saturation for the yield component (same idiom as RELVALUE_CAP).
# Realistic SG condo gross yields (~2.5-5%) sit in the near-linear range; a
# computed yield far outside it is almost always a price/rent artifact — the
# fake-cheap price of a mis-scraped/strata listing divided into the project's
# REAL URA rent yields a fake 7-8% gy. After v3.6 capped both value components,
# this was the one uncapped channel left: The Vision (5,349sqft strata @
# $636psf, agent: high-confidence Avoid) earned +46.7 yield pts and still
# ranked 765/1000 with psf_value/age_value damped to ~0.
MMR_YIELD_CAP = 20.0
MMR_RELVALUE_SLOPE = 0.8           # pts per % below age-adjusted district median
# Relative value is a heuristic (age slope × district-peer median) with heavy
# tails — a single bad peer-median estimate or a mis-tagged sqft can imply a
# +200% "premium". So the contribution SATURATES (tanh) at ±CAP instead of
# scaling linearly: full 0.8 slope near zero, but no single value reading can
# dominate the score or blow up on an artifact. Matches the tanh idiom used by
# mrt/txn_volume/dev_size. v3.4: cap raised 20→28 — this is the strongest
# region-robust forward predictor, and the old cap clipped its deep-discount tail
# (a -30% discount was throttled ~30%). Peer-count damping (mmr.py) also softened.
MMR_RELVALUE_CAP = 28.0            # max |pts| from age-adjusted relative value

# --- v3.6 discount trust (data-quality damping, not a return signal) --------
# Diagnosis (Jun 2026, agent-vs-score divergence audit): the top of the /1000
# ranking was dominated by listings whose "deep discount vs verified comps" was
# a data artifact — mis-scraped sqft/beds (700-1086 sqft "1BRs"), strata
# villas/terraces benchmarked against apartment medians (The Vision 938,
# Thomson Grand 908), stale/bait prices ~30% below trailing prints (Eight
# Riversuites 945). Agent evaluations rejected every one on web verification,
# while the score kept ranking them #1-15: the fake cheapness earned up to
# +28 (age_value) plus an UNCAPPED psf_value, against only -3/-6 from the
# very_low_psf / bedroom_sqft_mismatch red flags.
# Fix (mmr.py): a discount deeper than the knee earns marginal credit at
# EXCESS_CREDIT (on a live open-market listing, >25% below verified comps is
# far more likely bad data, a non-comparable format, or a defect than alpha);
# and when the sqft itself is untrusted (bedroom_sqft_mismatch) or a deep
# discount rests on a thin same-size cohort, the POSITIVE side of both value
# components retains only SUSPECT_VALUE_FACTOR. Premiums (negative side) are
# untouched — over-priced readings stay fully penalized (conservative).
MMR_DISCOUNT_TRUST_KNEE_PCT = 25.0  # % below comps where marginal credit kinks
MMR_DISCOUNT_EXCESS_CREDIT = 0.25   # marginal credit per % of discount beyond the knee
MMR_SUSPECT_VALUE_FACTOR = 0.25     # positive value retained when the reading is suspect

# ============================================================================
# UNIT-SIZE BANDS — like-for-like PSF comparison within a project
# ============================================================================
# A small unit trades at a structurally HIGHER psf than a large one in the same
# project (fixed costs spread over fewer sqft). A project-pooled median psf
# therefore over-penalizes small units (a 1BR looks "overpriced") and under-
# penalizes large ones (a penthouse looks "cheap"). We bucket each project's URA
# transactions into disjoint size bands and compare every listing against
# transactions of SIMILAR SIZE instead of the whole-project median.
#
# Bands are [lower, upper) in sqft; the last is open-ended. Tunable.
UNIT_SIZE_BAND_EDGES_SQFT = [0, 600, 800, 1050, 1400, 1800]
# A band needs at least this many comparable transactions to be trusted as the
# psf benchmark; below it we fall back to the pooled median AND down-weight the
# unit's confidence (thin cohort → we don't really have comparables).
MIN_BAND_TXNS = 5
# How many recent calendar years of transactions feed a band median (keeps the
# benchmark recent; widened automatically if a band is too thin in-window).
BAND_RECENCY_YEARS = 2
# A unit whose own size-cohort is thin can only partially claim the project's
# pooled signals. cohort_factor = clamp(band_txns / MIN_BAND_TXNS, floor, 1.0):
# normal cohorts (>= MIN_BAND_TXNS) are unaffected (1.0); thinner cohorts retain
# only `floor` of the signal. Two floors because the two signals differ in how
# unit-type-specific they are:
#   - Appreciation is a PROJECT trend that mostly applies even to an oddball
#     unit, so it floors high (a thin 3BR in a project that rose 7%/yr probably
#     rose too).
#   - Liquidity is UNIT-TYPE-specific: a size that rarely trades is genuinely
#     hard to exit no matter the building's total volume, so it floors lower.
COHORT_FLOOR_APPRECIATION = 0.55
COHORT_FLOOR_LIQUIDITY = 0.40


# Floor tiers — coarse cut shared by listings (PropertyGuru badge / number) and
# URA transactions (floor band string). Same function both sides → the listing's
# tier and the URA floor-factor benchmark always line up. Absolute, building-
# height-agnostic: 1-5 low, 6-12 mid, 13+ high.
FLOOR_TIER_CUTS = (5, 12)  # <=5 low, <=12 mid, else high


def normalize_floor_tier(raw) -> "str | None":
    """Map a floor descriptor to a tier: 'low' | 'mid' | 'high' (None if absent).

    Within a stack, floor is a major price driver (ground/low floors trade at a
    discount, high floors a premium). Inputs are messy and come from both sides:
    PropertyGuru badges ("High Floor"), keywords ("penthouse", "ground"), a bare
    number, or a URA band ("01 to 05", "B1 to B5"). All collapse to three tiers.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s or s == "-":
        return None
    if "penthouse" in s or "top floor" in s or "high floor" in s:
        return "high"
    if "mid floor" in s or "middle floor" in s:
        return "mid"
    if "ground" in s or "low floor" in s or s.startswith("b"):  # basement/B1
        return "low"
    import re as _re
    m = _re.search(r"\d+", s)  # bare number, or first number of a URA band
    if m:
        floor = int(m.group())
        if floor <= FLOOR_TIER_CUTS[0]:
            return "low"
        if floor <= FLOOR_TIER_CUTS[1]:
            return "mid"
        return "high"
    return None


def size_band_key(sqft) -> "str | None":
    """Disjoint size-band label for a sqft value, e.g. '800-1050' or '1800+'.

    Returns None for missing/invalid sqft. Same labeling is used at cache-build
    time (bucketing URA transactions) and at score time (locating a listing's
    band), so the two always align.
    """
    if not sqft or sqft <= 0:
        return None
    edges = UNIT_SIZE_BAND_EDGES_SQFT
    for i in range(len(edges) - 1):
        if edges[i] <= sqft < edges[i + 1]:
            return f"{edges[i]}-{edges[i + 1]}"
    return f"{edges[-1]}+"


# ============================================================================
# AGE-ADJUSTED RELATIVE VALUE — measured piecewise decay (v3.7)
# ============================================================================
# History: the $50/psf/yr folk rule (v3.0) was halved by the v3.4 hedonic to a
# single LINEAR ~1.6%/yr. v3.7 re-measured the curve SHAPE with a piecewise
# spline + district fixed effects (16.6k leasehold resales, last 2yr, all 3
# regions independently agree) and the truth is both of those at once — the
# decay is strongly KINKED, not linear:
#
#   segment   pooled %/yr   ($/psf/yr @ $1743)   read
#   0–10        ~3.0%           ~$50-55      launch-freshness premium decays fast
#                                            (the folk rule is right HERE)
#   10–15       ~0.5%            ~$8         plateau (2011-16 vintage holds value)
#   15–20       ~2.6%           ~$46         second leg down
#   20–30       ~1.0%           ~$18         slow drift
#   30+         ~1.7%           ~$30         lease-decay territory
#
# Expressed in %/yr (not $/psf/yr): regions agree closely in % shape, and a
# multiplicative adjustment scales automatically with each peer's PSF level —
# the old per-region $ constants existed only because PSF levels differ.
# A flat 1.6%/yr UNDER-adjusts new-vs-old comparisons by ~2× inside 0-10yr
# (making young condos look dear / old ones cheap) and over-adjusts 10-15.
# Segments are (age_from, age_to, log_decay_per_year). Re-measure with
# `python backtest_ext.py` (PART 1c) when the panel grows.
AGE_PSF_DECAY_SEGMENTS = [
    (0, 10, 0.030),
    (10, 15, 0.005),
    (15, 20, 0.026),
    (20, 30, 0.010),
    (30, 99, 0.017),
]
FREEHOLD_SLOPE_FACTOR = 0.6  # freehold decays slower (unmeasured — age not derivable for freehold txns)

# Expected score distribution
# Before v2.0: 42-52 range, ~3 std dev
# After v2.0: 35-85 range, ~15 std dev

# District discovery weights (for --auto and --discover-districts)
DISTRICT_WEIGHT_HISTORICAL = 0.30  # Historical appreciation
DISTRICT_WEIGHT_LIQUIDITY = 0.20  # Transaction volume
DISTRICT_WEIGHT_FUTURE_INFRA = 0.25  # Future MRT lines
DISTRICT_WEIGHT_GOVT_PRIORITY = 0.20  # Government development zones
DISTRICT_WEIGHT_SUPPLY = 0.05  # Supply constraint

# ============================================================================
# TARGET INVESTMENT PARAMETERS
# ============================================================================

TARGET_HOLD_YEARS = [5, 6, 7]

# NOTE: TARGET_DISTRICTS removed in v2.0
# Use --auto for AI-selected districts or --discover-districts to see rankings
# Manual override still available via --districts flag

# Buyer profile defaults
DEFAULT_BUYER_TYPE = "SC"  # Singapore Citizen
DEFAULT_PROPERTY_COUNT = 0  # First property (0% ABSD)

# Appreciation estimate (annual) - used only when URA data unavailable
DEFAULT_APPRECIATION_RATE = 0.02  # 2% per year

# ROI sensitivity assumptions (used for downside/upside scenarios)
# These are absolute deltas, not multipliers.
ROI_SENSITIVITY_RENT_DELTA_PCT = 0.10  # +/-10% rent
ROI_SENSITIVITY_APPRECIATION_DELTA_PCT = 0.015  # +/-1.5% appreciation rate

# v3.5b: rents were held FLAT over the whole hold, understating rental income
# on 5-7yr horizons. SG private rents grew ~2-4%/yr over the last decade
# (URA rental index); 2%/yr is the conservative end. Config-tunable.
ROI_RENT_GROWTH_PER_YEAR = 0.02

# Data freshness thresholds (days) for local datasets
DATA_FRESHNESS_THRESHOLDS_DAYS = {
    "district_medians.json": 180,
    "district_profiles.json": 365,
    "future_infrastructure.json": 365,
    "government_zones.json": 365,
    "ura_cache.json": 180,
    "cost_parameters.json": 365,
}

# ============================================================================
# NEW LAUNCH APPRECIATION BIAS ADJUSTMENT
# ============================================================================
# New launches show inflated appreciation in their first ~5 years because
# URA/transaction data mixes "New Sale" (developer prices) with "Resale"
# (market prices). The CAGR from developer PSF to resale PSF is NOT genuine
# market appreciation — it's mostly a developer-to-market price transition.
#
# Research basis (StackedHomes analysis, 2,621+ transactions):
# - New-to-resale 5yr returns: ~12.3% (inflated by dev pricing)
# - Resale-to-resale 5yr returns: ~14.9% (genuine market growth)
# New launches command a 10-15% PSF premium that decays over time.

# Regional baseline appreciation rates.
# Used as (a) the appreciation FALLBACK when a project has no URA history and
# (b) the FLOOR when discounting new-launch inflated appreciation.
# v3.4 CORRECTION: the old values (CCR 4.5 / RCR 5.8 / OCR 3.7, lifted from a
# trailing 2024 index) were INVERTED vs realized forward returns. backtest_ext.py
# measured realized 2024-26 forward resale-PSF returns at CCR +0.7%/yr, RCR +3.5%,
# OCR +4.0% (OCR > RCR >> CCR) — and region is the single strongest forward signal
# in the panel (std_β -0.19). The old baselines therefore propped up CCR (the worst
# forward performer) and discounted OCR (the best), wrong-signing every fallback and
# new-launch-floor that used them.
# We DE-INVERT and keep the spread BELOW the raw realized gap, but v3.5b
# REGIME-TESTED the tilt (backtest_ext PART 5h, URA non-landed price index by
# locality, 81 rolling 2yr windows 2004-2026): OCR out-returned CCR in EVERY
# regime — down markets +1.75pp/yr (OCR also falls less), flat +4.9pp, bull
# +1.6pp; OCR won 43/81 quarters outright. The OCR>CCR tilt is structural,
# not a 2021-26 bull artifact, so the spread was widened 1.0 -> 1.4pp (still
# ~70% of the ~2pp all-regime average — headroom for mean reversion).
REGIONAL_APPRECIATION_BASELINES = {
    "CCR": 0.028,  # 2.8%/yr - Core Central (realized fwd 2024-26 ~1.9%; worst in every regime since 2004)
    "RCR": 0.037,  # 3.7%/yr - Rest of Central (realized fwd ~3.1%)
    "OCR": 0.042,  # 4.2%/yr - Outside Central (realized fwd ~3.7%; best in every regime since 2004)
}
DEFAULT_REGIONAL_APPRECIATION = 0.035  # ~market median forward (was 0.04)

# New launch maturity discount curve
# Maps property age (years since TOP) to the fraction of "excess"
# appreciation (above regional baseline) that is discounted away.
# Based on the observed 10-15% new launch premium that decays over ~7 years.
NEW_LAUNCH_DISCOUNT_CURVE = {
    # age_max: discount_fraction_of_excess
    1: 0.60,   # 0-1 years: 60% of excess is likely dev pricing artifact
    3: 0.45,   # 2-3 years: early resale still inflated
    5: 0.30,   # 4-5 years: moderating but still above steady-state
    7: 0.15,   # 6-7 years: mostly normalized
    # 8+: 0.0  (no discount)
}

# Minimum proportion of "New Sale" transactions to trigger bias adjustment
# If <20% of transactions are "New Sale", the project is mature enough
NEW_SALE_PROPORTION_THRESHOLD = 0.20
