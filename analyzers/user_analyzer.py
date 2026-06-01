"""
User analyzer — computes activity metrics, community stats, and engagement.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from models.user import RedditUser, TopSubreddit, UserStats

logger = logging.getLogger(__name__)


class UserAnalyzer:
    """Compute rich statistics from raw post and comment lists."""

    def analyze(
        self,
        about: dict[str, Any],
        posts: list[dict[str, Any]],
        comments: list[dict[str, Any]],
    ) -> UserStats:
        user = RedditUser.from_json(about)

        # ── Subreddit breakdown ──────────────────────────────────────
        sub_posts: Counter[str] = Counter()
        sub_comments: Counter[str] = Counter()
        sub_score: defaultdict[str, int] = defaultdict(int)

        for p in posts:
            sub = str(p.get("subreddit", ""))
            if sub:
                sub_posts[sub] += 1
                sub_score[sub] += p.get("score", 0)

        for c in comments:
            sub = str(c.get("subreddit", ""))
            if sub:
                sub_comments[sub] += 1
                sub_score[sub] += c.get("score", 0)

        all_subs = set(sub_posts) | set(sub_comments)
        top_subreddits = sorted(
            [
                TopSubreddit(
                    name=s,
                    post_count=sub_posts[s],
                    comment_count=sub_comments[s],
                    total_score=sub_score[s],
                )
                for s in all_subs
            ],
            key=lambda x: x.total_activity,
            reverse=True,
        )[:10]

        # ── Score stats ───────────────────────────────────────────────
        post_scores = [p.get("score", 0) for p in posts]
        comment_scores = [c.get("score", 0) for c in comments]
        avg_post_score = sum(post_scores) / len(post_scores) if post_scores else 0.0
        avg_comment_score = sum(comment_scores) / len(comment_scores) if comment_scores else 0.0

        # ── Top content ───────────────────────────────────────────────
        top_posts = sorted(posts, key=lambda p: p.get("score", 0), reverse=True)[:5]
        top_comments = sorted(comments, key=lambda c: c.get("score", 0), reverse=True)[:5]

        # ── Activity rate ─────────────────────────────────────────────
        age_days = max(user.account_age_days, 1)
        posts_per_day = len(posts) / age_days
        comments_per_day = len(comments) / age_days

        # ── Active hours ──────────────────────────────────────────────
        hour_counter: Counter[int] = Counter()
        for item in posts + comments:
            ts = item.get("created_utc")
            if ts:
                hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
                hour_counter[hour] += 1
        active_hours = [h for h, _ in hour_counter.most_common(5)]

        return UserStats(
            user=user,
            posts_analyzed=len(posts),
            comments_analyzed=len(comments),
            avg_post_score=round(avg_post_score, 2),
            avg_comment_score=round(avg_comment_score, 2),
            top_posts=top_posts,
            top_comments=top_comments,
            top_subreddits=top_subreddits,
            active_hours=active_hours,
            posts_per_day=round(posts_per_day, 3),
            comments_per_day=round(comments_per_day, 3),
        )
