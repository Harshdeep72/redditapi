"""
Thread analyzer — full comment tree analysis including participants,
top/controversial comments, and max depth.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from models.post import Post
from models.thread import Participant, ThreadAnalysis

logger = logging.getLogger(__name__)


class ThreadAnalyzer:
    def analyze(
        self,
        post_data: dict[str, Any],
        comments: list[dict[str, Any]],
    ) -> ThreadAnalysis:
        post = Post.from_json(post_data)

        # ── Top & controversial comments ──────────────────────────────
        sorted_by_score = sorted(comments, key=lambda c: c.get("score", 0), reverse=True)
        top_comments = sorted_by_score[:10]

        # Controversial: high score variance — Reddit marks these with `controversiality`
        controversial = sorted(
            [c for c in comments if c.get("controversiality", 0) == 1],
            key=lambda c: abs(c.get("score", 0)),
            reverse=True,
        )[:5]
        # Fallback: low-scoring comments with many replies
        if not controversial:
            controversial = sorted(comments, key=lambda c: c.get("score", 0))[:5]

        # ── Participant leaderboard ───────────────────────────────────
        user_data: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "score": 0})
        for c in comments:
            author = c.get("author", "")
            if author and author not in ("[deleted]", "AutoModerator"):
                user_data[author]["count"] += 1
                user_data[author]["score"] += c.get("score", 0)

        top_participants = sorted(
            [
                Participant(
                    username=u,
                    comment_count=d["count"],
                    total_score=d["score"],
                    avg_score=round(d["score"] / max(d["count"], 1), 1),
                )
                for u, d in user_data.items()
            ],
            key=lambda p: p.comment_count,
            reverse=True,
        )[:10]

        # ── Depth ─────────────────────────────────────────────────────
        max_depth = max((c.get("_depth", 0) for c in comments), default=0)

        return ThreadAnalysis(
            post=post,
            total_comments=len(comments),
            unique_participants=len(user_data),
            top_comments=top_comments,
            controversial_comments=controversial,
            top_participants=top_participants,
            max_depth=max_depth,
        )
