"""
Cookie manager utility — provides cookie health status and
a standalone script to test cookie acquisition.

Run directly to test:
    python -m fetcher.cookie_manager
"""

from __future__ import annotations

import asyncio
import logging

from bot.config import settings
from fetcher.session import RedditSession

logger = logging.getLogger(__name__)

# Cookies Reddit sets that indicate a healthy session
REQUIRED_COOKIES = {"loid"}              # Always set for logged-out sessions
VALUABLE_COOKIES = {"reddit_session", "session_tracker", "csv", "d2_token"}


def evaluate_cookies(cookies: dict[str, str]) -> dict[str, str | bool | list[str]]:
    """
    Evaluate the quality of an acquired cookie set.
    Returns a health report dict.
    """
    cookie_keys = set(cookies.keys())
    has_required = REQUIRED_COOKIES.issubset(cookie_keys)
    valuable_present = list(VALUABLE_COOKIES & cookie_keys)

    quality = "Poor"
    if has_required and len(valuable_present) >= 2:
        quality = "Excellent"
    elif has_required and len(valuable_present) >= 1:
        quality = "Good"
    elif has_required:
        quality = "Fair"

    return {
        "quality": quality,
        "has_required": has_required,
        "total_cookies": str(len(cookies)),
        "cookie_names": list(cookie_keys),
        "valuable_present": valuable_present,
        "missing_required": list(REQUIRED_COOKIES - cookie_keys),
    }


async def test_cookie_acquisition() -> None:
    """Standalone test: acquire cookies and print a health report."""
    logging.basicConfig(level=logging.INFO)

    print("\nReddit Cookie Acquisition Test")
    print("=" * 50)

    session = RedditSession(reddit_session=settings.reddit_session)
    try:
        # Force bootstrap
        success = await session._acquire_cookies()
        print(f"\nBootstrap successful: {success}")

        if session._session:
            cookies = dict(session._session.cookies)
            report = evaluate_cookies(cookies)

            print(f"\nCookie Quality: {report['quality']}")
            print(f"Total cookies:  {report['total_cookies']}")
            print(f"Cookie names:   {report['cookie_names']}")
            print(f"Valuable found: {report['valuable_present']}")
            if report['missing_required']:
                print(f"Missing:     {report['missing_required']}")

        # Test a .json request
        print("\n\nTesting .json endpoint...")
        result = await session.get_json(
            "https://www.reddit.com/r/python/hot.json",
            params={"limit": 5, "raw_json": 1},
            referer="https://www.reddit.com/r/python/",
        )
        if result:
            children = result.get("data", {}).get("children", []) if isinstance(result, dict) else []
            print(f".json endpoint works! Got {len(children)} posts from r/python")
        else:
            print(".json endpoint returned no data")

        print(f"\nCookie status: {session.cookie_status()}")

    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(test_cookie_acquisition())
