"""
Pydantic models for Reddit user data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, computed_field, model_validator


class TopSubreddit(BaseModel):
    name: str
    post_count: int = 0
    comment_count: int = 0
    total_score: int = 0

    @computed_field  # type: ignore[misc]
    @property
    def total_activity(self) -> int:
        return self.post_count + self.comment_count


class RedditUser(BaseModel):
    username: str
    created_utc: float
    link_karma: int = 0
    comment_karma: int = 0
    total_karma: int = 0
    is_premium: bool = False
    avatar_url: str | None = None
    is_suspended: bool = False

    @computed_field  # type: ignore[misc]
    @property
    def cake_day(self) -> datetime:
        return datetime.fromtimestamp(self.created_utc, tz=timezone.utc)

    @computed_field  # type: ignore[misc]
    @property
    def account_age_days(self) -> int:
        now = datetime.now(tz=timezone.utc)
        return (now - self.cake_day).days

    @computed_field  # type: ignore[misc]
    @property
    def account_age_str(self) -> str:
        days = self.account_age_days
        if days < 30:
            return f"{days} days"
        if days < 365:
            months = days // 30
            return f"{months} month{'s' if months != 1 else ''}"
        years = days // 365
        months = (days % 365) // 30
        s = f"{years} year{'s' if years != 1 else ''}"
        if months:
            s += f" {months} month{'s' if months != 1 else ''}"
        return s

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RedditUser":
        return cls(
            username=data.get("name", "unknown"),
            created_utc=data.get("created_utc", 0),
            link_karma=data.get("link_karma", 0),
            comment_karma=data.get("comment_karma", 0),
            total_karma=data.get("total_karma", data.get("link_karma", 0) + data.get("comment_karma", 0)),
            is_premium=data.get("is_gold", False),
            avatar_url=data.get("icon_img") or data.get("snoovatar_img") or None,
            is_suspended=data.get("is_suspended", False),
        )


class UserStats(BaseModel):
    user: RedditUser
    posts_analyzed: int = 0
    comments_analyzed: int = 0
    avg_post_score: float = 0.0
    avg_comment_score: float = 0.0
    top_posts: list[dict[str, Any]] = Field(default_factory=list)
    top_comments: list[dict[str, Any]] = Field(default_factory=list)
    top_subreddits: list[TopSubreddit] = Field(default_factory=list)
    active_hours: list[int] = Field(default_factory=list)  # hours 0-23 sorted by activity
    posts_per_day: float = 0.0
    comments_per_day: float = 0.0
    ai_summary: str = ""
