"""
User behavioral profiling using LLM.
"""

from __future__ import annotations

import logging

from ai.client import chat_completion
from ai.prompts import SYSTEM_ANALYST, USER_PROFILE_PROMPT
from models.user import UserStats

logger = logging.getLogger(__name__)


async def generate_user_profile(stats: UserStats) -> str:
    """
    Generate a behavioral profile summary for a Reddit user.
    Returns a 3-4 sentence plain text summary.
    """
    top_subs = ", ".join(
        f"r/{s.name}" for s in stats.top_subreddits[:5]
    ) or "None"

    top_post_titles = "; ".join(
        p.get("title", "")[:60] for p in stats.top_posts[:3]
    ) or "None"

    hour_labels = [f"{h:02d}:00 UTC" for h in stats.active_hours[:3]]
    active_hours_str = ", ".join(hour_labels) or "Unknown"

    prompt = USER_PROFILE_PROMPT.format(
        username=stats.user.username,
        account_age=stats.user.account_age_str,
        total_karma=f"{stats.user.total_karma:,}",
        posts_count=stats.posts_analyzed,
        comments_count=stats.comments_analyzed,
        top_subreddits=top_subs,
        avg_post_score=f"{stats.avg_post_score:.1f}",
        avg_comment_score=f"{stats.avg_comment_score:.1f}",
        active_hours=active_hours_str,
        top_posts=top_post_titles,
    )

    result = await chat_completion(SYSTEM_ANALYST, prompt, max_tokens=300)
    return result or "AI behavioral summary unavailable."


async def analyze_comment(
    body: str,
    subreddit: str,
    post_title: str,
    parent_context: str,
) -> dict[str, str | float]:
    """
    Analyze a single comment for sentiment, tone, toxicity, and constructiveness.
    """
    from ai.client import chat_json
    from ai.prompts import COMMENT_ANALYSIS_PROMPT, SYSTEM_SENTIMENT

    prompt = COMMENT_ANALYSIS_PROMPT.format(
        body=body[:500],
        subreddit=subreddit,
        post_title=post_title[:100],
        parent_context=parent_context[:200] if parent_context else "None (top-level comment)",
    )

    result = await chat_json(SYSTEM_SENTIMENT, prompt, max_tokens=300)
    if not result or not isinstance(result, dict):
        return {
            "sentiment": "Neutral",
            "tone": "Informative",
            "topic": "General",
            "toxicity_score": 0.0,
            "constructiveness_score": 0.5,
            "summary": "AI analysis unavailable.",
        }
    return result
