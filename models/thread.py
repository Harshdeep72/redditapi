"""
Pydantic models for thread intelligence data.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from models.post import Post


class Participant(BaseModel):
    username: str
    comment_count: int
    total_score: int
    avg_score: float


class ThreadAnalysis(BaseModel):
    post: Post
    total_comments: int = 0
    unique_participants: int = 0
    top_comments: list[dict[str, Any]] = []
    controversial_comments: list[dict[str, Any]] = []
    top_participants: list[Participant] = []
    max_depth: int = 0
    sentiment_breakdown: dict[str, float] = {}  # {"Positive": 0.35, ...}
    ai_summary: str = ""
    ai_key_arguments: list[str] = []
    ai_consensus: str = ""
    ai_main_opinions: list[str] = []
