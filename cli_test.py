#!/usr/bin/env python
"""
Standalone CLI test runner for Reddit Intelligence Bot.
Allows testing the fetch and analysis layers directly in the terminal.

Usage:
    python cli_test.py <username>
"""

import asyncio
import sys
import logging
from pprint import pprint

# Configure logging
logging.basicConfig(level=logging.WARNING)

from fetcher.fetch_router import get_router
from analyzers.user_analyzer import UserAnalyzer
from analyzers.risk_analyzer import RiskAnalyzer

async def analyze_user(username: str):
    print(f"\nInitializing Reddit Intelligence Bot Fetch Layer...")
    router = get_router()
    analyzer = UserAnalyzer()
    risk_analyzer = RiskAnalyzer()

    try:
        print(f"Fetching u/{username} profile details...")
        about = await router.get_user_about(username)
        if not about:
            print(f"Error: Could not find u/{username}. Account may be suspended or private.")
            return

        print(f"Fetching u/{username} submitted posts...")
        posts = await router.get_user_posts(username)
        
        print(f"Fetching u/{username} comment history...")
        comments = await router.get_user_comments(username)

        print("\nRunning Analysis Engine...")
        stats = analyzer.analyze(about, posts, comments)
        risk = risk_analyzer.analyze(stats.user, posts, comments)

        print("\n" + "="*60)
        print(f"PROFILE REPORT: u/{username}")
        print("="*60)
        print(f"• Creation Date   : {stats.user.cake_day}")
        print(f"• Link Karma      : {stats.user.link_karma}")
        print(f"• Comment Karma   : {stats.user.comment_karma}")
        print(f"• Total Karma     : {stats.user.total_karma}")
        print(f"• Premium Member  : {stats.user.is_premium}")

        print("\nACTIVITY METRICS")
        print(f"• Total Posts Analyzed    : {stats.posts_analyzed}")
        print(f"• Total Comments Analyzed : {stats.comments_analyzed}")
        
        if stats.top_subreddits:
            print("\nTOP SUBREDDITS PARTICIPATED IN")
            for sub in stats.top_subreddits[:5]:
                print(f"  - r/{sub.name:20} : {sub.post_count} posts, {sub.comment_count} comments (Score: {sub.total_score})")

        print("\nRISK ASSESSMENT")
        print(f"• Overall Risk Score      : {risk.total_score}/10 ({risk.verdict} {risk.verdict_emoji})")
        print("\nRisk Factors Breakdown:")
        for factor in risk.factors:
            bar = "█" * int(factor.score * 5) + "░" * (5 - int(factor.score * 5))
            print(f"  [{bar}] {factor.name:18} : {factor.description}")

        print("\n"+"="*60)

    finally:
        await router.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python cli_test.py <username>")
        sys.exit(1)
    
    target_user = sys.argv[1]
    asyncio.run(analyze_user(target_user))
