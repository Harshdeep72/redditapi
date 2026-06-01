"""
Pydantic models for Reddit comment data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, computed_field


class Comment(BaseModel):
    comment_id: str
    author: str
    body: str
    score: int
    created_utc: float
    subreddit: str
    permalink: str
    awards: int = 0
    parent_id: str = ""
    depth: int = 0
    num_replies: int = 0

    @computed_field  # type: ignore[misc]
    @property
    def created_at(self) -> datetime:
        return datetime.fromtimestamp(self.created_utc, tz=timezone.utc)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Comment":
        return cls(
            comment_id=data.get("id", ""),
            author=data.get("author", "[deleted]"),
            body=data.get("body", ""),
            score=data.get("score", 0),
            created_utc=data.get("created_utc", 0),
            subreddit=str(data.get("subreddit", "")),
            permalink=data.get("permalink", ""),
            awards=data.get("total_awards_received", 0),
            parent_id=data.get("parent_id", ""),
            depth=data.get("_depth", 0),
        )


class CommentAnalysis(BaseModel):
    comment: Comment
    post_title: str = ""
    post_author: str = ""
    post_subreddit: str = ""
    parent_comment_body: str | None = None
    parent_comment_author: str | None = None
    sentiment: str = "Neutral"
    tone: str = "Informative"
    topic: str = ""
    toxicity_score: float = 0.0  # 0.0 - 1.0
    constructiveness_score: float = 0.0  # 0.0 - 1.0
    ai_summary: str = ""
