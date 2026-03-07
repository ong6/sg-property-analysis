#!/usr/bin/env python3
"""Warm up PropertyGuru session and export cookies + user agent.

Creates:
  - cookies.json
  - user_agent.txt
"""

import argparse
import json
from pathlib import Path

from config import PROPERTYGURU_BASE_URL, PAGE_LOAD_TIMEOUT, CLOUDFLARE_WAIT_TIMEOUT
from scrapers.browser import BrowserManager, wait_for_cloudflare


def main() -> int:
    parser = argparse.ArgumentParser(description="Warm up PropertyGuru session")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    args = parser.parse_args()

    root = Path(__file__).parent
    cookies_path = root / "cookies.json"
    ua_path = root / "user_agent.txt"

    with BrowserManager(headless=args.headless) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(PROPERTYGURU_BASE_URL, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")

        cf_ok = wait_for_cloudflare(page, timeout_ms=CLOUDFLARE_WAIT_TIMEOUT)
        if not cf_ok:
            print("Warning: Cloudflare challenge may still be active.", flush=True)

        if not args.headless:
            print("\nComplete any CAPTCHA/login in the browser, then press Enter here to continue...")
            try:
                input()
            except EOFError:
                pass

        cookies = context.cookies()
        cookies_path.write_text(json.dumps(cookies, indent=2))

        ua = page.evaluate("() => navigator.userAgent")
        ua_path.write_text(ua)

        print(f"Saved cookies: {cookies_path}")
        print(f"Saved user agent: {ua_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
