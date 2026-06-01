"""
AsyncPRAW fallback fetcher — used when .json endpoints fail or return restricted data.
Mirrors the same interface as JSONFetcher.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpraw
import asyncpraw.models

logger = logging.getLogger(__name__)


def _user_to_dict(user: asyncpraw.models.Redditor) -> dict[str, Any]:
    """Convert a PRAW Redditor object to a dict matching .json format."""
    return {
        "name": getattr(user, "name", ""),
        "created_utc": getattr(user, "created_utc", 0),
        "link_karma": getattr(user, "link_karma", 0),
        "comment_karma": getattr(user, "comment_karma", 0),
        "total_karma": getattr(user, "total_karma", 0),
        "is_gold": getattr(user, "is_gold", False),
        "icon_img": getattr(user, "icon_img", None),
        "is_suspended": getattr(user, "is_suspended", False),
    }


def _submission_to_dict(sub: asyncpraw.models.Submission) -> dict[str, Any]:
    """Convert a PRAW Submission to a dict matching .json t3 data format."""
    return {
        "id": sub.id,
        "title": sub.title,
        "author": getattr(sub.author, "name", "[deleted]") if sub.author else "[deleted]",
        "subreddit": str(sub.subreddit),
        "score": sub.score,
        "upvote_ratio": sub.upvote_ratio,
        "num_comments": sub.num_comments,
        "created_utc": sub.created_utc,
        "url": f"https://reddit.com{sub.permalink}",
        "selftext": sub.selftext,
        "total_awards_received": sub.total_awards_received,
        "is_self": sub.is_self,
        "permalink": sub.permalink,
    }


def _comment_to_dict(c: asyncpraw.models.Comment, depth: int = 0) -> dict[str, Any]:
    """Convert a PRAW Comment to a dict matching .json t1 data format."""
    return {
        "id": c.id,
        "author": getattr(c.author, "name", "[deleted]") if c.author else "[deleted]",
        "body": c.body,
        "score": c.score,
        "created_utc": c.created_utc,
        "subreddit": str(c.subreddit),
        "link_id": c.link_id,
        "parent_id": c.parent_id,
        "total_awards_received": c.total_awards_received,
        "permalink": f"https://reddit.com{c.permalink}",
        "_depth": depth,
    }


class PRAWFetcher:
    """
    AsyncPRAW-based fallback fetcher.
    Only instantiated if Reddit API credentials are available.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_agent: str,
        max_items: int = 500,
    ) -> None:
        self.max_items = max_items
        self._reddit = asyncpraw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )

    async def close(self) -> None:
        await self._reddit.close()

    async def get_user_about(self, username: str) -> dict[str, Any] | None:
        try:
            user = await self._reddit.redditor(username)
            await user.load()
            return _user_to_dict(user)
        except Exception as exc:
            logger.error("PRAW get_user_about failed for %s: %s", username, exc)
            return None

    async def get_user_posts(self, username: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        try:
            user = await self._reddit.redditor(username)
            async for submission in user.submissions.new(limit=self.max_items):
                results.append(_submission_to_dict(submission))
        except Exception as exc:
            logger.error("PRAW get_user_posts failed for %s: %s", username, exc)
        return results

    async def get_user_comments(self, username: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        try:
            user = await self._reddit.redditor(username)
            async for comment in user.comments.new(limit=self.max_items):
                results.append(_comment_to_dict(comment))
        except Exception as exc:
            logger.error("PRAW get_user_comments failed for %s: %s", username, exc)
        return results

    async def get_post_and_comments(
        self, url: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        try:
            submission = await self._reddit.submission(url=url)
            await submission.comments.replace_more(limit=0)
            post_data = _submission_to_dict(submission)
            comments: list[dict[str, Any]] = []
            self._flatten_comments(submission.comments.list(), comments)
            return post_data, comments
        except Exception as exc:
            logger.error("PRAW get_post_and_comments failed for %s: %s", url, exc)
            return None, []

    async def get_comment_context(
        self, url: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        try:
            comment = await self._reddit.comment(url=url)
            await comment.refresh()
            submission = await comment.submission()
            return _comment_to_dict(comment), _submission_to_dict(submission)
        except Exception as exc:
            logger.error("PRAW get_comment_context failed for %s: %s", url, exc)
            return None, None

    def _flatten_comments(
        self,
        comment_list: list[Any],
        out: list[dict[str, Any]],
        depth: int = 0,
    ) -> None:
        for item in comment_list:
            if isinstance(item, asyncpraw.models.Comment):
                out.append(_comment_to_dict(item, depth))
