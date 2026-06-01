#!/usr/bin/env python
"""
Advanced Reddit Intelligence Bot CLI Test Runner.
Allows testing all other analysis modules: Post, Comment, and Thread analysis.

Usage:
    python cli_test_advanced.py --post "<reddit_post_url>"
    python cli_test_advanced.py --comment "<reddit_comment_url>"
"""

import asyncio
import sys
import argparse
import logging
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.WARNING)

from fetcher.fetch_router import get_router
from analyzers.post_analyzer import PostAnalyzer
from analyzers.thread_analyzer import ThreadAnalyzer
from analyzers.comment_analyzer import CommentAnalyzer

async def test_post_and_thread(url: str):
    print(f"\nInitializing Reddit Intelligence Bot Fetch Layer...")
    router = get_router()
    post_analyzer = PostAnalyzer()
    thread_analyzer = ThreadAnalyzer()

    try:
        print(f"Fetching post and comment tree from: {url}")
        post_data, comments = await router.get_post_and_comments(url)
        if not post_data:
            print("Error: Could not fetch post data. URL may be invalid, deleted, or private.")
            return

        print("\nRunning Post & Thread Analysis Engines...")
        post_report = post_analyzer.analyze(post_data, comments)
        thread_report = thread_analyzer.analyze(post_data, comments)

        print("\n" + "="*70)
        print(f"POST ANALYSIS REPORT: \"{post_report.post.title[:50]}...\"")
        print("="*70)
        print(f"• Subreddit          : r/{post_report.post.subreddit}")
        print(f"• Author             : u/{post_report.post.author}")
        print(f"• Upvotes            : {post_report.post.score} (Upvote Ratio: {post_report.post.upvote_ratio:.0%})")
        print(f"• Total Comments     : {post_report.post.num_comments}")
        print(f"• Account Age (Hours): {post_report.post.age_hours:.1f} hours")
        print(f"• Comment Velocity   : {post_report.comment_velocity} comments/hour")
        print(f"• Engagement Rate    : {post_report.engagement_rate} (Score/Hour)")
        
        if post_report.post.selftext:
            print(f"\nBODY PREVIEW:\n{post_report.post.selftext[:300]}...")

        print("\n" + "="*70)
        print(f"THREAD ANALYSIS DASHBOARD")
        print("="*70)
        print(f"• Unique Participants : {thread_report.unique_participants} users")
        print(f"• Maximum Thread Depth: {thread_report.max_depth} nested levels")
        print(f"• Total Comments Tree : {thread_report.total_comments} comments analyzed")

        if thread_report.top_participants:
            print("\nPARTICIPANT LEADERBOARD (Top 5)")
            for i, p in enumerate(thread_report.top_participants[:5], 1):
                print(f"  {i}. u/{p.username:18} : {p.comment_count} comments (Avg Score: {p.avg_score})")

        if thread_report.top_comments:
            print("\nTOP COMMENTS BY SCORE (Top 3)")
            for i, c in enumerate(thread_report.top_comments[:3], 1):
                author = c.get("author", "unknown")
                score = c.get("score", 0)
                body = c.get("body", "")[:120].replace("\n", " ")
                print(f"  {i}. u/{author:15} [+{score:4}] : {body}...")

        if thread_report.controversial_comments:
            print("\nMOST CONTROVERSIAL COMMENTS")
            for i, c in enumerate(thread_report.controversial_comments[:3], 1):
                author = c.get("author", "unknown")
                score = c.get("score", 0)
                body = c.get("body", "")[:120].replace("\n", " ")
                print(f"  {i}. u/{author:15} [Score: {score:3}] : {body}...")

        print("\n" + "="*70)

    finally:
        await router.close()

async def test_comment(url: str):
    print(f"\nInitializing Reddit Intelligence Bot Fetch Layer...")
    router = get_router()
    comment_analyzer = CommentAnalyzer()

    try:
        print(f"Fetching comment context from: {url}")
        comment_data, post_data = await router.get_comment_context(url)
        if not comment_data:
            print("Error: Could not fetch comment data. Check the URL structure.")
            return

        # Fetch comments listing to calculate replies and hierarchy
        parts = url.split("/comments/")
        if len(parts) > 1:
            post_id = parts[1].split("/")[0]
            subreddit = parts[0].split("/r/")[-1].split("/")[0]
            post_url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
            _, all_comments = await router.get_post_and_comments(post_url)
        else:
            all_comments = None

        print("\nRunning Comment Analysis Engine...")
        report = comment_analyzer.analyze(comment_data, post_data, all_comments)

        print("\n" + "="*70)
        print(f"TARGET COMMENT REPORT: {report.comment.comment_id}")
        print("="*70)
        print(f"• Author             : u/{report.comment.author}")
        print(f"• Score              : {report.comment.score} upvotes")
        print(f"• Subreddit          : r/{report.post_subreddit}")
        print(f"• Parent ID          : {report.comment.parent_id}")
        print(f"• Total Direct Replies: {report.comment.num_replies}")
        print(f"• Creation Time      : {report.comment.created_at}")
        print(f"\nCOMMENT BODY:\n{report.comment.body}")

        if report.parent_comment_body:
            print(f"\n↪️ REPLYING TO u/{report.parent_comment_author}:\n\"{report.parent_comment_body[:200]}...\"")
        elif report.post_title:
            print(f"\n↪️ REPLYING TO ROOT POST: \"{report.post_title}\" by u/{report.post_author}")

        print("\n" + "="*70)

    finally:
        await router.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Post, Comment, and Thread Analysis Modules")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--post", help="Reddit Post/Thread URL to analyze")
    group.add_argument("--comment", help="Reddit Comment URL to analyze")

    args = parser.parse_args()

    if args.post:
        asyncio.run(test_post_and_thread(args.post))
    elif args.comment:
        asyncio.run(test_comment(args.comment))
