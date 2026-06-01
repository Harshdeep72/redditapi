"""
Reddit session manager with automatic cookie acquisition.

Strategy:
  1. Bootstrap — hit reddit.com with Chrome TLS impersonation to obtain
     a full browser-grade cookie jar (reddit_session, loid, csv, etc.)
  2. Refresh   — re-acquire cookies every COOKIE_REFRESH_INTERVAL seconds
     so the session never goes stale.
  3. All .json requests are sent with these live cookies, making them
     indistinguishable from a logged-out browser visit.

Primary impersonation: curl_cffi with impersonate="chrome120"
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

# How often to re-acquire fresh cookies (seconds)
COOKIE_REFRESH_INTERVAL = 600  # 10 minutes

# Reddit URLs used for cookie bootstrapping
BOOTSTRAP_URLS = [
    "https://www.reddit.com/",
    "https://www.reddit.com/r/popular/",
]

# ── User-Agent pool ────────────────────────────────────────────────────────────
USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
]

# Headers that mimic a real Chrome browser navigating to Reddit
def _browser_headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
    }

# Headers for follow-up .json API requests (same session, simulating XHR)
def _json_headers(user_agent: str, referer: str = "https://www.reddit.com/") -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": referer,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Connection": "keep-alive",
    }


class RedditSession:
    """
    Manages a persistent curl_cffi async session with:
    - Chrome 120 TLS fingerprint (impersonate="chrome120")
    - Automatic cookie bootstrapping on first use
    - Periodic cookie refresh to prevent session expiry
    - Exponential backoff with jitter on failures
    """

    def __init__(
        self,
        delay_min: float = 1.0,
        delay_max: float = 3.0,
        max_retries: int = 3,
        cookie_refresh_interval: int = COOKIE_REFRESH_INTERVAL,
        reddit_session: str | None = None,
    ) -> None:
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_retries = max_retries
        self.cookie_refresh_interval = cookie_refresh_interval
        self.reddit_session = reddit_session

        self._session: AsyncSession | None = None
        self._user_agent: str = random.choice(USER_AGENTS)
        self._last_request_time: float = 0.0
        self._last_cookie_refresh: float = 0.0
        self._cookie_lock = asyncio.Lock()
        self._cookies_ready = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def __aenter__(self) -> "RedditSession":
        await self._ensure_session()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
            self._cookies_ready = False

    # ── Cookie management ──────────────────────────────────────────────────────

    async def _ensure_session(self) -> None:
        """Create the curl_cffi session if not yet created."""
        if self._session is None:
            self._session = AsyncSession(impersonate="chrome120")
            logger.debug("Created new curl_cffi AsyncSession (chrome120 TLS)")
        if self.reddit_session:
            self._session.cookies.set("reddit_session", self.reddit_session, domain=".reddit.com")
            logger.debug("Ensured custom reddit_session cookie is set in AsyncSession")

    async def _acquire_cookies(self) -> bool:
        """
        Visit Reddit pages with browser-grade headers to obtain a full
        session cookie jar. Uses a multi-step warm-up sequence to acquire
        the complete set of session cookies Reddit requires for .json access.

        Key cookies required:
          - loid            : logged-out user ID (required for .json access)
          - csv             : country/variant cookie
          - edgebucket      : CDN routing cookie
          - session_tracker : analytics session (optional)
        """
        await self._ensure_session()

        # Warm-up sequence: visit multiple Reddit pages to trigger full session init
        warmup_sequence = [
            "https://www.reddit.com/",
            f"https://www.reddit.com/r/popular/?t=day",
            f"https://www.reddit.com/r/popular.json?limit=1&raw_json=1",
        ]

        success = False
        for url in warmup_sequence:
            try:
                is_json = url.endswith(".json")
                headers = (
                    _json_headers(self._user_agent, referer="https://www.reddit.com/r/popular/")
                    if is_json
                    else _browser_headers(self._user_agent)
                )

                logger.info("Cookie warm-up: GET %s", url)
                resp = await self._session.get(  # type: ignore[union-attr]
                    url,
                    headers=headers,
                    timeout=20,
                    allow_redirects=True,
                )

                if resp.status_code in (200, 301, 302):
                    cookies = dict(self._session.cookies)
                    logger.debug("After %s: cookies=%s", url, list(cookies.keys()))
                    if "reddit_session" in cookies or "loid" in cookies:
                        logger.info("Cookie requirements satisfied (reddit_session/loid present) after visiting %s", url)
                        success = True
                        break
                    elif "edgebucket" in cookies or "csv" in cookies:
                        # Partial cookies — continue warm-up
                        success = True
                else:
                    logger.debug("Warm-up %s returned HTTP %d", url, resp.status_code)

                # Small human-like pause between page visits
                await asyncio.sleep(random.uniform(0.8, 2.0))

            except Exception as exc:
                logger.error("Cookie warm-up error for %s: %s", url, exc)

        if success:
            cookies = dict(self._session.cookies)
            cookie_names = list(cookies.keys())
            logger.info(
                "Cookie bootstrap complete. Got %d cookies: %s",
                len(cookie_names),
                cookie_names,
            )
            self._last_cookie_refresh = time.monotonic()
            self._cookies_ready = True
            return True

        logger.warning("Cookie bootstrap did not acquire expected cookies")
        # Still mark as attempted so we don't loop endlessly
        self._last_cookie_refresh = time.monotonic()
        self._cookies_ready = True
        return False


    async def _maybe_refresh_cookies(self) -> None:
        """Refresh cookies if they've expired or this is the first request."""
        now = time.monotonic()
        needs_refresh = (
            not self._cookies_ready
            or (now - self._last_cookie_refresh) > self.cookie_refresh_interval
        )

        if needs_refresh:
            async with self._cookie_lock:
                # Double-check inside lock to avoid thundering herd
                now = time.monotonic()
                if (
                    not self._cookies_ready
                    or (now - self._last_cookie_refresh) > self.cookie_refresh_interval
                ):
                    success = await self._acquire_cookies()
                    if not success:
                        # Rotate user agent on failure and retry once
                        self._user_agent = random.choice(USER_AGENTS)
                        await self._acquire_cookies()

    # ── Request throttle ──────────────────────────────────────────────────────

    async def _throttle(self) -> None:
        """Enforce a randomized delay between requests."""
        elapsed = time.monotonic() - self._last_request_time
        delay = random.uniform(self.delay_min, self.delay_max)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)

    # ── Core request method ───────────────────────────────────────────────────

    async def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        referer: str = "https://www.reddit.com/",
        attempt: int = 0,
    ) -> dict[str, Any] | list[Any] | None:
        """
        Fetch a Reddit .json URL using the live cookie session.

        Flow:
          1. Ensure cookies are fresh (bootstrap/refresh if needed)
          2. Throttle to avoid rate limiting
          3. GET the URL with cookie-enriched headers
          4. On 429 → exponential backoff + re-acquire cookies
          5. On 403 → re-acquire cookies once, then retry
          6. Returns parsed JSON or None on failure
        """
        await self._maybe_refresh_cookies()
        await self._throttle()

        headers = _json_headers(self._user_agent, referer=referer)

        try:
            logger.debug("GET %s (attempt %d)", url, attempt + 1)
            response = await self._session.get(  # type: ignore[union-attr]
                url,
                params=params,
                headers=headers,
                timeout=30,
                allow_redirects=True,
            )
            self._last_request_time = time.monotonic()

            # ── Success ───────────────────────────────────────────────
            if response.status_code == 200:
                try:
                    return response.json()
                except Exception:
                    logger.error("Failed to parse JSON from %s", url)
                    return None

            # ── Rate limited ──────────────────────────────────────────
            if response.status_code == 429:
                wait = 2 ** (attempt + 2) + random.uniform(0, 3)
                logger.warning("Rate limited (429) on %s. Waiting %.1fs.", url, wait)
                await asyncio.sleep(wait)
                # Re-acquire cookies to get a fresh session after rate limit
                self._cookies_ready = False
                if attempt < self.max_retries - 1:
                    return await self.get_json(url, params, referer, attempt + 1)
                return None

            # ── Forbidden — cookies may have expired ──────────────────
            if response.status_code == 403:
                if attempt == 0:
                    logger.warning("403 on %s — re-acquiring cookies and retrying.", url)
                    self._cookies_ready = False
                    self._user_agent = random.choice(USER_AGENTS)
                    await self._maybe_refresh_cookies()
                    await asyncio.sleep(random.uniform(2, 5))
                    return await self.get_json(url, params, referer, attempt + 1)
                logger.warning("403 on %s after cookie refresh — giving up.", url)
                return None

            # ── Not found / gone ──────────────────────────────────────
            if response.status_code == 404:
                logger.debug("404 for %s", url)
                return None

            # ── Server error — retry with backoff ─────────────────────
            if response.status_code >= 500:
                wait = 2 ** attempt + random.uniform(0, 1)
                logger.warning("HTTP %d on %s. Retrying in %.1fs.", response.status_code, url, wait)
                await asyncio.sleep(wait)
                if attempt < self.max_retries - 1:
                    return await self.get_json(url, params, referer, attempt + 1)
                return None

            logger.warning("Unexpected HTTP %d for %s", response.status_code, url)
            return None

        except Exception as exc:
            logger.error("Request exception for %s (attempt %d): %s", url, attempt + 1, exc)
            if attempt < self.max_retries - 1:
                wait = 2 ** attempt + random.uniform(0, 1)
                await asyncio.sleep(wait)
                return await self.get_json(url, params, referer, attempt + 1)
            return None

    @property
    def cookie_age_seconds(self) -> float:
        """How old the current cookie set is."""
        if not self._cookies_ready:
            return float("inf")
        return time.monotonic() - self._last_cookie_refresh

    def cookie_status(self) -> str:
        """Human-readable cookie status string for debugging."""
        if not self._cookies_ready:
            return "Not acquired"
        age = self.cookie_age_seconds
        cookies = dict(self._session.cookies) if self._session else {}
        return (
            f"Age: {age:.0f}s | "
            f"Count: {len(cookies)} | "
            f"Keys: {list(cookies.keys())[:6]}"
        )
