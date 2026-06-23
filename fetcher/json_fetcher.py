"""
Primary Reddit data fetcher using public .json endpoints with session cookies.

The fetcher passes the correct Referer header for each request to simulate
natural browser navigation (e.g. visiting r/python → fetching its .json).
This pairs with the cookie-aware RedditSession for maximum authenticity.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fetcher.session import RedditSession

logger = logging.getLogger(__name__)

REDDIT_BASE = "https://old.reddit.com"


def _clean_username(username: str) -> str:
    """Normalize u/username or full URL → bare username."""
    username = username.strip()
    # Full URL
    match = re.search(r"reddit\.com/user/([^/?#\s]+)", username)
    if match:
        return match.group(1).rstrip("/")
    # u/ or /u/ prefix
    if username.startswith(("u/", "/u/")):
        return username.split("/")[-1]
    return username.rstrip("/")


def _extract_post_parts(url: str) -> tuple[str, str, str] | None:
    """
    Extract (subreddit, post_id, slug) from any Reddit post or comment URL.
    Handles old.reddit.com, www.reddit.com, and bare paths.
    """
    # Clean query parameters first
    url_clean = url.split("?")[0].strip()
    
    # Match subreddit and path after /r/
    match = re.search(r"reddit\.com/r/([^/]+)/comments/([A-Za-z0-9]+)(?:/([^/]+))?", url_clean)
    if match:
        subreddit = match.group(1)
        post_id = match.group(2)
        slug = match.group(3) or "_"
        return subreddit, post_id, slug
    return None


def _extract_comment_id(url: str) -> str | None:
    """
    Extract the comment ID from a Reddit comment URL.
    Handles various structures:
      - /comments/post_id/slug/comment_id/
      - /comments/post_id/comment_id/
      - /comment/comment_id/
    """
    # Clean query parameters
    url_clean = url.split("?")[0].strip().rstrip("/")
    
    # 1. Check for explicit /comment/comment_id/
    match = re.search(r"/comment/([A-Za-z0-9]+)", url_clean)
    if match:
        return match.group(1)
        
    # 2. Check for /comments/post_id/slug/comment_id/
    if "/comments/" in url_clean:
        parts = url_clean.split("/comments/")[-1].split("/")
        if len(parts) >= 3:
            return parts[2]
        elif len(parts) == 2:
            return parts[1]
            
    return None


class JSONFetcher:
    """
    Fetches Reddit data from public .json endpoints.
    Passes Referer headers that mirror real browser navigation.
    """

    def __init__(self, session: RedditSession, max_items: int = 500) -> None:
        self.session = session
        self.max_items = max_items

    # ─────────────────────────────────────────────────────────────────
    # User endpoints
    # ─────────────────────────────────────────────────────────────────

    async def get_user_about(self, username: str) -> dict[str, Any] | None:
        """Fetch /user/{username}/about.json"""
        username = _clean_username(username)
        url = f"{REDDIT_BASE}/user/{username}/about.json"
        referer = f"{REDDIT_BASE}/user/{username}/"
        data = await self.session.get_json(url, referer=referer)
        if data and isinstance(data, dict) and data.get("kind") == "t2":
            return data["data"]
        if data and isinstance(data, dict) and "name" in data:
            # Some responses return data directly (PRAW fallback normalised)
            return data
        logger.warning("Could not fetch user about for %s — got: %s", username, type(data))
        return None

    async def get_user_posts(self, username: str) -> list[dict[str, Any]]:
        """Paginate /user/{username}/submitted.json"""
        username = _clean_username(username)
        profile_url = f"{REDDIT_BASE}/user/{username}/"
        return await self._paginate(
            url=f"{REDDIT_BASE}/user/{username}/submitted.json",
            expected_kind="t3",
            referer=profile_url,
        )

    async def get_user_comments(self, username: str) -> list[dict[str, Any]]:
        """Paginate /user/{username}/comments.json"""
        username = _clean_username(username)
        profile_url = f"{REDDIT_BASE}/user/{username}/"
        return await self._paginate(
            url=f"{REDDIT_BASE}/user/{username}/comments.json",
            expected_kind="t1",
            referer=profile_url,
        )

    # ─────────────────────────────────────────────────────────────────
    # Post + Thread endpoints
    # ─────────────────────────────────────────────────────────────────

    async def get_post_and_comments(
        self, url: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """
        Fetch a post and its full comment tree.
        The Referer is set to the subreddit listing page for authenticity.
        """
        parts = _extract_post_parts(url)
        if not parts:
            logger.error("Could not parse post URL: %s", url)
            return None, []

        subreddit, post_id, slug = parts
        json_url = f"{REDDIT_BASE}/r/{subreddit}/comments/{post_id}/{slug}.json"
        referer = f"{REDDIT_BASE}/r/{subreddit}/"

        data = await self.session.get_json(
            json_url,
            params={"limit": 500, "depth": 10, "raw_json": 1},
            referer=referer,
        )

        if not data or not isinstance(data, list) or len(data) < 2:
            logger.warning("Unexpected response structure for post %s", url)
            return None, []

        # data[0] = post listing, data[1] = comment listing
        post_data: dict[str, Any] | None = None
        post_listing = data[0]
        if (
            isinstance(post_listing, dict)
            and post_listing.get("data", {}).get("children")
        ):
            post_data = post_listing["data"]["children"][0].get("data")

        comments: list[dict[str, Any]] = []
        comments_listing = data[1]
        if isinstance(comments_listing, dict):
            self._flatten_comments(
                comments_listing.get("data", {}).get("children", []),
                comments,
                depth=0,
            )

        logger.info("Fetched post '%s' with %d comments", post_id, len(comments))
        return post_data, comments

    async def get_comment_context(
        self, url: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """
        Fetch a specific comment and its parent post.
        Uses the comment anchor endpoint for targeted retrieval.
        """
        comment_id = _extract_comment_id(url)
        parts = _extract_post_parts(url)
        if not parts or not comment_id:
            logger.error("Could not parse comment URL: %s", url)
            return None, None

        subreddit, post_id, slug = parts
        json_url = f"{REDDIT_BASE}/r/{subreddit}/comments/{post_id}/{slug}/{comment_id}.json"
        referer = f"{REDDIT_BASE}/r/{subreddit}/comments/{post_id}/{slug}/"

        data = await self.session.get_json(
            json_url,
            params={"limit": 50, "depth": 5, "raw_json": 1},
            referer=referer,
        )

        if not data or not isinstance(data, list) or len(data) < 2:
            return None, None

        post_data: dict[str, Any] | None = None
        if isinstance(data[0], dict) and data[0].get("data", {}).get("children"):
            post_data = data[0]["data"]["children"][0].get("data")

        comment_data: dict[str, Any] | None = None
        if isinstance(data[1], dict) and data[1].get("data", {}).get("children"):
            comment_data = data[1]["data"]["children"][0].get("data")

        return comment_data, post_data

    # ─────────────────────────────────────────────────────────────────
    # Subreddit listing (bonus — useful for future commands)
    # ─────────────────────────────────────────────────────────────────

    async def get_subreddit_posts(
        self,
        subreddit: str,
        sort: str = "hot",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """
        Fetch posts from a subreddit listing (.json endpoint).
        sort: hot | new | top | rising
        """
        url = f"{REDDIT_BASE}/r/{subreddit}/{sort}.json"
        referer = f"{REDDIT_BASE}/r/{subreddit}/"
        data = await self.session.get_json(
            url,
            params={"limit": limit, "raw_json": 1},
            referer=referer,
        )
        if data and isinstance(data, dict):
            children = data.get("data", {}).get("children", [])
            return [c["data"] for c in children if c.get("kind") == "t3"]
        return []

    # ─────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────

    async def _paginate(
        self,
        url: str,
        expected_kind: str,
        referer: str = REDDIT_BASE + "/",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Paginate a Reddit listing using the 'after' cursor token.
        Stops at self.max_items results or when Reddit returns no more pages.
        The Referer rotates to the next page URL for authenticity.
        """
        results: list[dict[str, Any]] = []
        after: str | None = None
        page = 0

        while len(results) < self.max_items:
            params: dict[str, Any] = {"limit": limit, "raw_json": 1}
            if after:
                params["after"] = after

            # Referer for page 2+ looks like the paginated URL
            page_referer = f"{url}?count={page * limit}&after={after}" if after else referer

            data = await self.session.get_json(url, params=params, referer=page_referer)
            if not data or not isinstance(data, dict):
                logger.debug("Pagination stopped: no data returned")
                break

            listing_data = data.get("data", {})
            children = listing_data.get("children", [])
            if not children:
                logger.debug("Pagination stopped: empty children list")
                break

            added = 0
            for child in children:
                if child.get("kind") == expected_kind and "data" in child:
                    results.append(child["data"])
                    added += 1
                    if len(results) >= self.max_items:
                        break

            after = listing_data.get("after")
            page += 1
            logger.debug("Page %d: +%d items (total: %d), after=%s", page, added, len(results), after)

            if not after:
                break

        logger.info("Paginated %d items from %s", len(results), url)
        return results

    def _flatten_comments(
        self,
        children: list[dict[str, Any]],
        out: list[dict[str, Any]],
        depth: int = 0,
    ) -> None:
        """Recursively flatten a Reddit comment tree into a flat list, tracking depth."""
        for child in children:
            kind = child.get("kind")
            if kind == "t1":
                comment = dict(child.get("data", {}))
                comment["_depth"] = depth
                out.append(comment)
                replies = comment.get("replies")
                if isinstance(replies, dict):
                    reply_children = replies.get("data", {}).get("children", [])
                    self._flatten_comments(reply_children, out, depth + 1)
            # kind == "more" → skip "load more" placeholders
