"""
Risk analyzer — multi-factor risk scoring system.

Score: 0 (very low risk) → 10 (very high risk / likely bot or spam)
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

from models.user import RedditUser

logger = logging.getLogger(__name__)

# ── Risk thresholds ───────────────────────────────────────────────────────────
VERY_NEW_DAYS = 30
NEW_DAYS = 90
SPAM_BURST_THRESHOLD = 20  # posts in any rolling 24h window = spam
SINGLE_SUB_RATIO = 0.9    # >90% activity in one sub = suspicious


class RiskFactor:
    def __init__(self, name: str, score: float, weight: float, description: str) -> None:
        self.name = name
        self.score = score          # 0.0 - 1.0 (normalized risk contribution)
        self.weight = weight        # relative weight in final score
        self.description = description

    @property
    def weighted(self) -> float:
        return self.score * self.weight


class RiskReport:
    def __init__(
        self,
        username: str,
        factors: list[RiskFactor],
        total_score: float,
        verdict: str,
        verdict_emoji: str,
        summary: str,
    ) -> None:
        self.username = username
        self.factors = factors
        self.total_score = round(total_score, 1)
        self.verdict = verdict
        self.verdict_emoji = verdict_emoji
        self.summary = summary


class RiskAnalyzer:
    """
    Produces a risk score and verdict for a Reddit user.

    Factors (weighted):
        1. Account age       (25%) — new accounts are higher risk
        2. Karma pattern     (20%) — very low / negative / sudden spike
        3. Activity pattern  (25%) — spam bursts, repetitive content
        4. Community focus   (15%) — single-subreddit concentration
        5. Engagement quality (15%) — awards, positive reception
    """

    def analyze(
        self,
        user: RedditUser,
        posts: list[dict[str, Any]],
        comments: list[dict[str, Any]],
    ) -> RiskReport:
        factors: list[RiskFactor] = [
            self._account_age_factor(user),
            self._karma_factor(user, posts, comments),
            self._activity_pattern_factor(posts, comments),
            self._community_focus_factor(posts, comments),
            self._engagement_quality_factor(posts, comments),
        ]

        total_weight = sum(f.weight for f in factors)
        weighted_sum = sum(f.weighted for f in factors)
        raw_score = (weighted_sum / total_weight) * 10  # normalize to 0-10

        verdict, emoji = self._verdict(raw_score)
        summary = self._build_summary(user.username, factors, raw_score, verdict)

        return RiskReport(
            username=user.username,
            factors=factors,
            total_score=raw_score,
            verdict=verdict,
            verdict_emoji=emoji,
            summary=summary,
        )

    # ── Individual Factors ────────────────────────────────────────────────────

    def _account_age_factor(self, user: RedditUser) -> RiskFactor:
        days = user.account_age_days
        if days < VERY_NEW_DAYS:
            score, desc = 0.9, f"Account is only {days} days old (very new)"
        elif days < NEW_DAYS:
            score, desc = 0.5, f"Account is {days} days old (relatively new)"
        elif days < 365:
            score, desc = 0.25, f"Account is {days} days old (< 1 year)"
        else:
            score, desc = 0.0, f"Established account ({user.account_age_str})"
        return RiskFactor("Account Age", score, 0.25, desc)

    def _karma_factor(
        self,
        user: RedditUser,
        posts: list[dict[str, Any]],
        comments: list[dict[str, Any]],
    ) -> RiskFactor:
        total = user.total_karma
        if total < 0:
            return RiskFactor("Karma Pattern", 0.95, 0.20, "Negative total karma")
        if total < 10 and user.account_age_days > 30:
            return RiskFactor("Karma Pattern", 0.8, 0.20, "Extremely low karma for account age")
        if total < 100:
            return RiskFactor("Karma Pattern", 0.5, 0.20, "Very low karma")

        # Check for karma spike: if >80% of karma comes from 1 post
        post_scores = sorted([p.get("score", 0) for p in posts], reverse=True)
        if post_scores and total > 0:
            if post_scores[0] / max(total, 1) > 0.8:
                return RiskFactor("Karma Pattern", 0.6, 0.20, "Most karma from a single viral post")

        return RiskFactor("Karma Pattern", 0.1, 0.20, f"Normal karma distribution ({total:,} total)")

    def _activity_pattern_factor(
        self,
        posts: list[dict[str, Any]],
        comments: list[dict[str, Any]],
    ) -> RiskFactor:
        all_items = posts + comments
        if not all_items:
            return RiskFactor("Activity Pattern", 0.3, 0.25, "No activity data available")

        # Check for spam bursts (>20 items in any 24h window)
        timestamps = sorted(item.get("created_utc", 0) for item in all_items)
        max_in_window = 0
        for i, ts in enumerate(timestamps):
            window_end = ts + 86400
            count = sum(1 for t in timestamps[i:] if t <= window_end)
            max_in_window = max(max_in_window, count)

        if max_in_window > SPAM_BURST_THRESHOLD:
            return RiskFactor(
                "Activity Pattern", 0.85, 0.25,
                f"Spam burst detected: {max_in_window} items in a single day"
            )

        # Check for repetitive content in posts
        titles = [p.get("title", "") for p in posts[:50]]
        if self._has_repetitive_content(titles):
            return RiskFactor("Activity Pattern", 0.75, 0.25, "Repetitive post titles detected")

        return RiskFactor("Activity Pattern", 0.1, 0.25, "Normal activity pattern")

    def _community_focus_factor(
        self,
        posts: list[dict[str, Any]],
        comments: list[dict[str, Any]],
    ) -> RiskFactor:
        sub_counter: Counter[str] = Counter()
        for p in posts:
            sub = str(p.get("subreddit", ""))
            if sub:
                sub_counter[sub] += 1
        for c in comments:
            sub = str(c.get("subreddit", ""))
            if sub:
                sub_counter[sub] += 1

        total = sum(sub_counter.values())
        if total == 0:
            return RiskFactor("Community Focus", 0.2, 0.15, "No community data")

        top_sub_pct = sub_counter.most_common(1)[0][1] / total if sub_counter else 0
        unique_subs = len(sub_counter)

        if top_sub_pct > SINGLE_SUB_RATIO and unique_subs <= 2:
            return RiskFactor(
                "Community Focus", 0.7, 0.15,
                f"95%+ activity concentrated in {sub_counter.most_common(1)[0][0]}"
            )
        if unique_subs <= 3:
            return RiskFactor("Community Focus", 0.4, 0.15, f"Active in only {unique_subs} subreddits")

        return RiskFactor("Community Focus", 0.0, 0.15, f"Diverse participation in {unique_subs} communities")

    def _engagement_quality_factor(
        self,
        posts: list[dict[str, Any]],
        comments: list[dict[str, Any]],
    ) -> RiskFactor:
        total_awards = sum(p.get("total_awards_received", 0) for p in posts)
        total_awards += sum(c.get("total_awards_received", 0) for c in comments)

        all_scores = [p.get("score", 0) for p in posts] + [c.get("score", 0) for c in comments]
        positive_count = sum(1 for s in all_scores if s > 0)
        positive_ratio = positive_count / len(all_scores) if all_scores else 0.5

        if total_awards > 5 and positive_ratio > 0.7:
            return RiskFactor("Engagement Quality", 0.0, 0.15, f"High quality: {total_awards} awards, {positive_ratio:.0%} positive reception")
        if positive_ratio < 0.3:
            return RiskFactor("Engagement Quality", 0.8, 0.15, "Mostly downvoted content")
        if total_awards == 0 and positive_ratio < 0.5:
            return RiskFactor("Engagement Quality", 0.4, 0.15, "Low engagement quality")

        return RiskFactor("Engagement Quality", 0.1, 0.15, "Normal engagement quality")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _has_repetitive_content(self, titles: list[str]) -> bool:
        if len(titles) < 5:
            return False
        # Simple heuristic: strip numbers, check for near-duplicates
        normalized = [re.sub(r"\d+", "#", t.lower().strip()) for t in titles]
        counter: Counter[str] = Counter(normalized)
        most_common_count = counter.most_common(1)[0][1] if counter else 0
        return most_common_count > len(titles) * 0.4

    def _verdict(self, score: float) -> tuple[str, str]:
        if score <= 2.0:
            return "Likely Genuine User", ""
        if score <= 4.5:
            return "Probably Genuine", ""
        if score <= 6.5:
            return "Potential Throwaway Account", ""
        if score <= 8.5:
            return "Potential Spam Account", ""
        return "Suspected Bot / Inauthentic Account", ""

    def _build_summary(
        self,
        username: str,
        factors: list[RiskFactor],
        score: float,
        verdict: str,
    ) -> str:
        lines = [f"**u/{username}** — Risk Score: **{score:.1f}/10** — {verdict}"]
        for f in factors:
            bar = "█" * int(f.score * 5) + "░" * (5 - int(f.score * 5))
            lines.append(f"`{bar}` **{f.name}**: {f.description}")
        return "\n".join(lines)
