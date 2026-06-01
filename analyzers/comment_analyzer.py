"""
Comment analyzer — context, depth, engagement, and AI analysis.
"""

from __future__ import annotations

from typing import Any

from models.comment import Comment, CommentAnalysis


class CommentAnalyzer:
    def analyze(
        self,
        comment_data: dict[str, Any],
        post_data: dict[str, Any] | None,
        all_comments: list[dict[str, Any]] | None = None,
    ) -> CommentAnalysis:
        comment = Comment.from_json(comment_data)

        # Count direct replies
        num_replies = 0
        if all_comments:
            prefix = f"t1_{comment.comment_id}"
            num_replies = sum(1 for c in all_comments if c.get("parent_id") == prefix)
        comment.num_replies = num_replies

        # Post context
        post_title = post_author = post_subreddit = ""
        if post_data:
            post_title = post_data.get("title", "")
            post_author = post_data.get("author", "")
            post_subreddit = str(post_data.get("subreddit", ""))

        # Find parent comment body if parent is a comment (t1_)
        parent_body = parent_author = None
        if comment.parent_id.startswith("t1_") and all_comments:
            parent_id_short = comment.parent_id[3:]
            for c in all_comments:
                if c.get("id") == parent_id_short:
                    parent_body = c.get("body", "")
                    parent_author = c.get("author", "")
                    break

        return CommentAnalysis(
            comment=comment,
            post_title=post_title,
            post_author=post_author,
            post_subreddit=post_subreddit,
            parent_comment_body=parent_body,
            parent_comment_author=parent_author,
        )
