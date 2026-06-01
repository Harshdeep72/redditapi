"""
Post analysis command: /post <url>
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ai.summarizer import summarize_post
from analyzers.post_analyzer import PostAnalyzer
from embeds.post_embed import PostReportView, build_post_embeds
from fetcher.fetch_router import get_router

logger = logging.getLogger(__name__)


class PostCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._analyzer = PostAnalyzer()

    @app_commands.command(name="post", description="Analyze a Reddit post")
    @app_commands.describe(url="Full URL to the Reddit post")
    async def post_cmd(self, interaction: discord.Interaction, url: str) -> None:
        await interaction.response.defer(thinking=True)
        router = get_router()

        post_data, comments = await router.get_post_and_comments(url)
        if not post_data:
            await interaction.followup.send(
                "Could not fetch the post. Make sure the URL is a valid Reddit post link.",
                ephemeral=True,
            )
            return

        analysis = self._analyzer.analyze(post_data, comments)

        # AI summary
        ai_result = await summarize_post(
            title=analysis.post.title,
            subreddit=analysis.post.subreddit,
            score=analysis.post.score,
            upvote_ratio=analysis.post.upvote_ratio,
            body=analysis.post.selftext,
            top_comments=analysis.top_comments,
        )

        if ai_result:
            analysis.ai_thread_summary = str(ai_result.get("summary", ""))
            analysis.ai_key_arguments = list(ai_result.get("key_arguments", []))
            analysis.ai_consensus = str(ai_result.get("consensus", ""))
            analysis.sentiment_breakdown = dict(ai_result.get("sentiment_breakdown", {}))

        embeds = build_post_embeds(analysis)
        view = PostReportView(embeds)
        await interaction.followup.send(embed=embeds[0], view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PostCommands(bot))
