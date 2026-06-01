"""
Fetch router — orchestrates primary JSON fetcher with AsyncPRAW fallback.
All other modules should use this router rather than fetchers directly.
"""

from __future__ import annotations

import logging
from typing import Any

from bot.config import settings
from fetcher.json_fetcher import JSONFetcher
from fetcher.praw_fetcher import PRAWFetcher
from fetcher.session import RedditSession

logger = logging.getLogger(__name__)


class FetchRouter:
    """
    Routes fetch requests through:
      1. Reddit public .json endpoints (curl_cffi TLS impersonation)
      2. AsyncPRAW (Reddit API) as fallback
    """

    def __init__(self) -> None:
        self._session = RedditSession(
            delay_min=settings.request_delay_min,
            delay_max=settings.request_delay_max,
            cookie_refresh_interval=settings.cookie_refresh_interval,
            reddit_session=settings.reddit_session,
            proxy_list_url=settings.proxy_list_url,
        )
        self._json = JSONFetcher(self._session, max_items=settings.max_fetch_items)
        self._praw: PRAWFetcher | None = None

        if settings.has_reddit_api:
            self._praw = PRAWFetcher(
                client_id=settings.reddit_client_id,
                client_secret=settings.reddit_client_secret,
                user_agent=settings.reddit_user_agent,
                max_items=settings.max_fetch_items,
            )
            logger.info("PRAW fallback enabled.")
        else:
            logger.warning("Reddit API credentials not set — PRAW fallback disabled.")

    async def close(self) -> None:
        await self._session.close()
        if self._praw:
            await self._praw.close()

    # ─────────────────────────────────────────────────────
    # User
    # ─────────────────────────────────────────────────────

    async def get_user_about(self, username: str) -> dict[str, Any] | None:
        result = await self._json.get_user_about(username)
        if result is None and self._praw:
            logger.info("JSON failed for user about, falling back to PRAW")
            result = await self._praw.get_user_about(username)
        return result

    async def get_user_posts(self, username: str) -> list[dict[str, Any]]:
        result = await self._json.get_user_posts(username)
        if not result and self._praw:
            logger.info("JSON failed for user posts, falling back to PRAW")
            result = await self._praw.get_user_posts(username)
        return result

    async def get_user_comments(self, username: str) -> list[dict[str, Any]]:
        result = await self._json.get_user_comments(username)
        if not result and self._praw:
            logger.info("JSON failed for user comments, falling back to PRAW")
            result = await self._praw.get_user_comments(username)
        return result

    # ─────────────────────────────────────────────────────
    # Post / Thread
    # ─────────────────────────────────────────────────────

    async def get_post_and_comments(
        self, url: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        post, comments = await self._json.get_post_and_comments(url)
        if post is None and self._praw:
            logger.info("JSON failed for post, falling back to PRAW")
            post, comments = await self._praw.get_post_and_comments(url)
        return post, comments

    async def get_comment_context(
        self, url: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        comment, post = await self._json.get_comment_context(url)
        if comment is None and self._praw:
            logger.info("JSON failed for comment, falling back to PRAW")
            comment, post = await self._praw.get_comment_context(url)
        return comment, post


# ─── Module-level singleton ───────────────────────────────────────────────────
_router: FetchRouter | None = None


def get_router() -> FetchRouter:
    global _router
    if _router is None:
        _router = FetchRouter()
    return _router
