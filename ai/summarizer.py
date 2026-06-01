"""
Thread and post summarization using LLM.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.client import chat_json
from ai.prompts import POST_SUMMARY_PROMPT, SYSTEM_ANALYST, THREAD_SUMMARY_PROMPT

logger = logging.getLogger(__name__)


def _format_comments(comments: list[dict[str, Any]], max_comments: int = 10, max_len: int = 200) -> str:
    lines = []
    for c in comments[:max_comments]:
        author = c.get("author", "unknown")
        body = (c.get("body", "") or "")[:max_len].replace("\n", " ")
        score = c.get("score", 0)
        lines.append(f"- u/{author} (+{score}): {body}")
    return "\n".join(lines) or "No comments available."


async def summarize_thread(
    title: str,
    subreddit: str,
    score: int,
    upvote_ratio: float,
    total_comments: int,
    unique_participants: int,
    top_comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Generate a thread intelligence summary.
    Returns dict with summary, key_arguments, consensus, main_opinions, sentiment_breakdown.
    """
    prompt = THREAD_SUMMARY_PROMPT.format(
        title=title,
        subreddit=subreddit,
        score=score,
        upvote_ratio=upvote_ratio,
        total_comments=total_comments,
        unique_participants=unique_participants,
        top_comments=_format_comments(top_comments),
    )

    result = await chat_json(SYSTEM_ANALYST, prompt, max_tokens=800)
    if not result or not isinstance(result, dict):
        return {
            "summary": "AI summary unavailable.",
            "key_arguments": [],
            "consensus": "N/A",
            "main_opinions": [],
            "sentiment_breakdown": {"Positive": 0.33, "Neutral": 0.34, "Negative": 0.33},
        }
    return result


async def summarize_post(
    title: str,
    subreddit: str,
    score: int,
    upvote_ratio: float,
    body: str,
    top_comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Generate a post intelligence summary.
    Returns dict with summary, key_arguments, consensus, sentiment_breakdown.
    """
    prompt = POST_SUMMARY_PROMPT.format(
        title=title,
        subreddit=subreddit,
        score=score,
        upvote_ratio=upvote_ratio,
        body=(body or "")[:500],
        top_comments=_format_comments(top_comments),
    )

    result = await chat_json(SYSTEM_ANALYST, prompt, max_tokens=600)
    if not result or not isinstance(result, dict):
        return {
            "summary": "AI summary unavailable.",
            "key_arguments": [],
            "consensus": "N/A",
            "sentiment_breakdown": {"Positive": 0.33, "Neutral": 0.34, "Negative": 0.33},
        }
    return result
