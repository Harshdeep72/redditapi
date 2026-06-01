"""
Post analyzer — engagement rate, comment velocity, and top comments.
"""

from __future__ import annotations

from typing import Any

from models.post import Post, PostAnalysis


class PostAnalyzer:
    def analyze(
        self,
        post_data: dict[str, Any],
        comments: list[dict[str, Any]],
    ) -> PostAnalysis:
        post = Post.from_json(post_data)

        # Comment velocity (comments per hour since posting)
        age_hours = max(post.age_hours, 0.1)
        comment_velocity = post.num_comments / age_hours

        # Engagement rate proxy: score / (hours + 1)
        engagement_rate = post.score / (age_hours + 1)

        # Top comments by score
        top_comments = sorted(comments, key=lambda c: c.get("score", 0), reverse=True)[:10]

        return PostAnalysis(
            post=post,
            top_comments=top_comments,
            comment_velocity=round(comment_velocity, 2),
            engagement_rate=round(engagement_rate, 2),
        )
