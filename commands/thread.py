"""
Thread intelligence command: /thread <url>
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ai.sentiment import classify_sentiment_batch, compute_sentiment_breakdown
from ai.summarizer import summarize_thread
from analyzers.thread_analyzer import ThreadAnalyzer
from embeds.thread_embed import ThreadReportView, build_thread_embeds
from fetcher.fetch_router import get_router

logger = logging.getLogger(__name__)


class ThreadCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._analyzer = ThreadAnalyzer()

    @app_commands.command(name="thread", description="Full intelligence report for a Reddit thread")
    @app_commands.describe(url="Full URL to the Reddit post/thread")
    async def thread_cmd(self, interaction: discord.Interaction, url: str) -> None:
        await interaction.response.defer(thinking=True)
        router = get_router()

        post_data, comments = await router.get_post_and_comments(url)
        if not post_data:
            await interaction.followup.send(
                "Could not fetch the thread. Make sure the URL is a valid Reddit post link.",
                ephemeral=True,
            )
            return

        analysis = self._analyzer.analyze(post_data, comments)

        # Sentiment analysis on sample of comments (limit to 100 to control cost)
        sample_comments = [
            c.get("body", "") for c in comments[:100]
            if c.get("body") and c.get("author") not in ("[deleted]", "AutoModerator")
        ]

        if sample_comments:
            sentiments = await classify_sentiment_batch(sample_comments)
            analysis.sentiment_breakdown = compute_sentiment_breakdown(sentiments)

        # AI thread summary
        ai_result = await summarize_thread(
            title=analysis.post.title,
            subreddit=analysis.post.subreddit,
            score=analysis.post.score,
            upvote_ratio=analysis.post.upvote_ratio,
            total_comments=analysis.total_comments,
            unique_participants=analysis.unique_participants,
            top_comments=analysis.top_comments,
        )

        if ai_result:
            analysis.ai_summary = str(ai_result.get("summary", ""))
            analysis.ai_key_arguments = list(ai_result.get("key_arguments", []))
            analysis.ai_consensus = str(ai_result.get("consensus", ""))
            analysis.ai_main_opinions = list(ai_result.get("main_opinions", []))
            # Override AI's sentiment breakdown if it returned one
            if "sentiment_breakdown" in ai_result:
                ai_bd = ai_result["sentiment_breakdown"]
                if isinstance(ai_bd, dict) and all(k in ai_bd for k in ("Positive", "Neutral", "Negative")):
                    analysis.sentiment_breakdown = ai_bd

        embeds = build_thread_embeds(analysis)
        view = ThreadReportView(embeds)
        await interaction.followup.send(embed=embeds[0], view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ThreadCommands(bot))
