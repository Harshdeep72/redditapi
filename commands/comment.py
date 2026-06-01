"""
Comment analysis command: /comment <url>
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ai.profiler import analyze_comment
from analyzers.comment_analyzer import CommentAnalyzer
from embeds.comment_embed import build_comment_embed
from fetcher.fetch_router import get_router

logger = logging.getLogger(__name__)


class CommentCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._analyzer = CommentAnalyzer()

    @app_commands.command(name="comment", description="Analyze a Reddit comment")
    @app_commands.describe(url="Full URL to the Reddit comment")
    async def comment_cmd(self, interaction: discord.Interaction, url: str) -> None:
        await interaction.response.defer(thinking=True)
        router = get_router()

        comment_data, post_data = await router.get_comment_context(url)
        if not comment_data:
            await interaction.followup.send(
                "Could not fetch the comment. Make sure the URL is a valid Reddit comment link.",
                ephemeral=True,
            )
            return

        # Fetch sibling comments for context (reply count)
        _, all_comments = await router.get_post_and_comments(url)

        analysis = self._analyzer.analyze(comment_data, post_data, all_comments)

        # AI analysis
        parent_context = ""
        if analysis.parent_comment_body:
            parent_context = f"u/{analysis.parent_comment_author}: {analysis.parent_comment_body}"

        ai_result = await analyze_comment(
            body=analysis.comment.body,
            subreddit=analysis.comment.subreddit,
            post_title=analysis.post_title,
            parent_context=parent_context,
        )

        if ai_result:
            analysis.sentiment = str(ai_result.get("sentiment", "Neutral"))
            analysis.tone = str(ai_result.get("tone", "Informative"))
            analysis.topic = str(ai_result.get("topic", "General"))
            analysis.toxicity_score = float(ai_result.get("toxicity_score", 0.0))
            analysis.constructiveness_score = float(ai_result.get("constructiveness_score", 0.5))
            analysis.ai_summary = str(ai_result.get("summary", ""))

        embed = build_comment_embed(analysis)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CommentCommands(bot))
