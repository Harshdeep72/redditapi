"""
Pydantic models for Reddit post data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, computed_field


class Post(BaseModel):
    post_id: str
    title: str
    author: str
    subreddit: str
    score: int
    upvote_ratio: float
    num_comments: int
    created_utc: float
    url: str
    selftext: str = ""
    awards: int = 0
    is_self: bool = True
    permalink: str = ""

    @computed_field  # type: ignore[misc]
    @property
    def created_at(self) -> datetime:
        return datetime.fromtimestamp(self.created_utc, tz=timezone.utc)

    @computed_field  # type: ignore[misc]
    @property
    def age_hours(self) -> float:
        now = datetime.now(tz=timezone.utc)
        return (now - self.created_at).total_seconds() / 3600

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Post":
        return cls(
            post_id=data.get("id", ""),
            title=data.get("title", ""),
            author=data.get("author", "[deleted]"),
            subreddit=str(data.get("subreddit", "")),
            score=data.get("score", 0),
            upvote_ratio=data.get("upvote_ratio", 0.5),
            num_comments=data.get("num_comments", 0),
            created_utc=data.get("created_utc", 0),
            url=data.get("url", ""),
            selftext=data.get("selftext", ""),
            awards=data.get("total_awards_received", 0),
            is_self=data.get("is_self", True),
            permalink=data.get("permalink", ""),
        )


class PostAnalysis(BaseModel):
    post: Post
    top_comments: list[dict[str, Any]] = []
    comment_velocity: float = 0.0  # comments per hour
    engagement_rate: float = 0.0   # score / (age_hours + 1)
    sentiment_breakdown: dict[str, float] = {}  # {"Positive": 0.35, ...}
    ai_thread_summary: str = ""
    ai_key_arguments: list[str] = []
    ai_consensus: str = ""
