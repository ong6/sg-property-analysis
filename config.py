"""Configuration for Property Finder v2.0."""

PROPERTYGURU_BASE_URL = "https://www.propertyguru.com.sg"
PROPERTYGURU_SEARCH_PATH = "/apartment-condo-for-sale"

# Browser settings
BROWSER_USER_DATA_DIR = "./chrome-profile"
BROWSER_CHANNEL = "chrome"

# Timeouts (milliseconds)
PAGE_LOAD_TIMEOUT = 60_000
NEXT_DATA_WAIT_TIMEOUT = 15_000  # Short timeout — may not exist anymore
DOM_CONTENT_WAIT_TIMEOUT = 30_000  # Wait for rendered listing cards
CLOUDFLARE_WAIT_TIMEOUT = 120_000

# Rate limiting
REQUEST_DELAY_SECONDS = 2

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

# Property scoring weights (v2.1 - total: 100 pts, no URA bias)
SCORE_WEIGHT_RENTAL_YIELD = 15  # Reduced from 30 - yields mechanically low at $2M+
SCORE_WEIGHT_CAPITAL_APPRECIATION = 30  # v2.1: increased from 25, absorbs old URA bonus
SCORE_WEIGHT_FUTURE_POTENTIAL = 20  # MRT, govt zones, transformation
SCORE_WEIGHT_LIQUIDITY = 25  # v2.1: increased from 20
SCORE_WEIGHT_COST_EFFICIENCY = 10  # Unchanged
SCORE_WEIGHT_RED_FLAGS = -10  # Max penalty

# Tier thresholds (v2.0 - adjusted for better distribution)
SCORE_TIER1_MIN = 60  # Recommended (was 65)
SCORE_TIER2_MIN = 45  # Consider (unchanged)

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

TARGET_PRICE_MIN = 2_200_000
TARGET_PRICE_MAX = 2_700_000
TARGET_HOLD_YEARS = [5, 6, 7]

# NOTE: TARGET_DISTRICTS removed in v2.0
# Use --auto for AI-selected districts or --discover-districts to see rankings
# Manual override still available via --districts flag

# Buyer profile defaults
DEFAULT_BUYER_TYPE = "SC"  # Singapore Citizen
DEFAULT_PROPERTY_COUNT = 0  # First property (0% ABSD)

# Appreciation estimate (annual) - used only when URA data unavailable
DEFAULT_APPRECIATION_RATE = 0.02  # 2% per year

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

# Regional baseline appreciation rates (URA 2024 full-year index)
# Used as the "floor" when discounting new-launch inflated appreciation
REGIONAL_APPRECIATION_BASELINES = {
    "CCR": 0.045,  # 4.5%/yr - Core Central Region
    "RCR": 0.058,  # 5.8%/yr - Rest of Central Region
    "OCR": 0.037,  # 3.7%/yr - Outside Central Region
}
DEFAULT_REGIONAL_APPRECIATION = 0.04  # 4% fallback

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
