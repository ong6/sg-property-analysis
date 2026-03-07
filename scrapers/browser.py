import os
import sys
import logging
from patchright.sync_api import sync_playwright, Playwright, BrowserContext

from config import BROWSER_USER_DATA_DIR, BROWSER_CHANNEL, CLOUDFLARE_WAIT_TIMEOUT

logger = logging.getLogger("property-finder")

# Cloudflare challenge indicators
CF_CHALLENGE_SELECTORS = [
    "#challenge-running",
    "#challenge-stage",
    "iframe[src*='challenges.cloudflare.com']",
    "#turnstile-wrapper",
    "[data-ray]",
]

CF_RESOLVED_INDICATORS = [
    "div[class*='listing']",
    "div[data-listing-id]",
]

# Script tags are hidden — checked separately with state="attached"
CF_RESOLVED_SCRIPT_INDICATORS = [
    "script#__NEXT_DATA__",
]


class BrowserManager:
    """Context manager wrapping a Patchright persistent browser context."""

    def __init__(self, headless: bool = False, user_data_dir: str = BROWSER_USER_DATA_DIR):
        self._headless = headless
        self._user_data_dir = os.path.abspath(user_data_dir)
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    def __enter__(self) -> BrowserContext:
        self._playwright = sync_playwright().start()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
            "--window-position=100,100",
        ]

        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._user_data_dir,
            channel=BROWSER_CHANNEL,
            headless=self._headless,
            no_viewport=True,
            args=launch_args,
        )

        # Block heavy resources to speed up scraping and reduce detection noise.
        try:
            self._context.route(
                "**/*",
                lambda route, request: route.abort()
                if request.resource_type in ("image", "media", "font")
                else route.continue_(),
            )
        except Exception:
            pass

        # On macOS, bring Chrome window to foreground
        if not self._headless:
            import subprocess
            try:
                subprocess.run(
                    ["osascript", "-e", f'tell application "{"Google Chrome" if BROWSER_CHANNEL == "chrome" else "Chromium"}" to activate'],
                    timeout=5, capture_output=True,
                )
            except Exception:
                pass
        logger.info("Browser launched (headless=%s)", self._headless)
        return self._context

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        logger.info("Browser closed")
        return False


def _is_past_cloudflare(page) -> bool:
    """Check if page has loaded past Cloudflare."""
    # Check visible resolved indicators
    for sel in CF_RESOLVED_INDICATORS:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            pass

    # Check hidden script tags (state="attached")
    for sel in CF_RESOLVED_SCRIPT_INDICATORS:
        try:
            # Use evaluate to check if the element exists in DOM at all
            count = page.evaluate(f'document.querySelectorAll("{sel}").length')
            if count > 0:
                return True
        except Exception:
            pass

    # Check page title — if it's no longer a CF title, we're past it
    try:
        title = page.title().lower()
        cf_titles = ["just a moment", "attention required", "cloudflare"]
        if title and not any(t in title for t in cf_titles):
            return True
    except Exception:
        pass

    return False


def wait_for_cloudflare(page, timeout_ms: int = CLOUDFLARE_WAIT_TIMEOUT) -> bool:
    """Detect and wait for Cloudflare challenge to resolve.

    Returns True if the page is past CF (or CF not detected), False if still blocked.
    """
    import time

    deadline = time.time() + (timeout_ms / 1000)

    # Quick check — already past Cloudflare?
    if _is_past_cloudflare(page):
        logger.debug("Page already past Cloudflare")
        return True

    # Detect Cloudflare challenge
    cf_detected = False
    for sel in CF_CHALLENGE_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                cf_detected = True
                logger.info("Cloudflare challenge detected (matched: %s)", sel)
                break
        except Exception:
            pass

    # Also check page title for CF indicators
    try:
        title = page.title().lower()
        if "just a moment" in title or "attention required" in title or "cloudflare" in title:
            cf_detected = True
            logger.info("Cloudflare challenge detected (title: %s)", page.title())
    except Exception:
        pass

    if not cf_detected:
        return True

    # Wait for CF to auto-resolve
    logger.info("Waiting for Cloudflare challenge to resolve automatically...")
    poll_interval = 2  # seconds
    user_notified = False

    while time.time() < deadline:
        if _is_past_cloudflare(page):
            logger.info("Cloudflare challenge resolved!")
            return True

        # Check if still challenged
        still_challenged = False
        for sel in CF_CHALLENGE_SELECTORS:
            try:
                if page.locator(sel).count() > 0:
                    still_challenged = True
                    break
            except Exception:
                pass

        # Also still challenged if title is still CF
        try:
            title = page.title().lower()
            if "just a moment" in title:
                still_challenged = True
        except Exception:
            pass

        if not still_challenged:
            # Challenge elements gone but resolved indicators not found yet — give it a moment
            time.sleep(1)
            if _is_past_cloudflare(page):
                logger.info("Cloudflare challenge resolved!")
                return True

        if still_challenged and not user_notified and time.time() > (deadline - timeout_ms / 1000 + 15):
            print(
                "\n*** Cloudflare challenge requires interaction ***\n"
                "Please solve the CAPTCHA in the browser window.\n"
                f"Waiting up to {int(deadline - time.time())}s...\n",
                file=sys.stderr,
            )
            user_notified = True

        time.sleep(poll_interval)

    logger.warning("Cloudflare challenge did not resolve within timeout")
    return False
