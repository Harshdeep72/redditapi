"""
User analysis commands: /user, /analyze, /risk
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ai.profiler import generate_user_profile
from analyzers.risk_analyzer import RiskAnalyzer
from analyzers.user_analyzer import UserAnalyzer
from embeds.risk_embed import build_risk_embed
from embeds.user_embed import UserReportView, build_user_embeds
from fetcher.fetch_router import get_router

logger = logging.getLogger(__name__)


class UserCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._analyzer = UserAnalyzer()
        self._risk_analyzer = RiskAnalyzer()

    @app_commands.command(name="user", description="Generate a full intelligence report for a Reddit user")
    @app_commands.describe(username="Reddit username (with or without u/)")
    async def user_cmd(self, interaction: discord.Interaction, username: str) -> None:
        await self._run_user_analysis(interaction, username)

    @app_commands.command(name="analyze", description="Alias for /user — analyze a Reddit user")
    @app_commands.describe(username="Reddit username (with or without u/)")
    async def analyze_cmd(self, interaction: discord.Interaction, username: str) -> None:
        await self._run_user_analysis(interaction, username)

    @app_commands.command(name="risk", description="Run a risk assessment on a Reddit user")
    @app_commands.describe(username="Reddit username (with or without u/)")
    async def risk_cmd(self, interaction: discord.Interaction, username: str) -> None:
        await interaction.response.defer(thinking=True)
        router = get_router()

        about = await router.get_user_about(username)
        if not about:
            await interaction.followup.send(
                f"Could not fetch data for **u/{username}**. "
                "The account may be suspended, deleted, or private.",
                ephemeral=True,
            )
            return

        posts, comments = await asyncio.gather(
            router.get_user_posts(username),
            router.get_user_comments(username),
        )

        from models.user import RedditUser
        user = RedditUser.from_json(about)
        report = self._risk_analyzer.analyze(user, posts, comments)
        embed = build_risk_embed(report)
        await interaction.followup.send(embed=embed)

    async def _run_user_analysis(
        self, interaction: discord.Interaction, username: str
    ) -> None:
        await interaction.response.defer(thinking=True)
        router = get_router()

        # Fetch all data concurrently
        about, posts, comments = await asyncio.gather(
            router.get_user_about(username),
            router.get_user_posts(username),
            router.get_user_comments(username),
        )

        if not about:
            await interaction.followup.send(
                f"Could not fetch data for **u/{username}**. "
                "The account may be suspended, deleted, or private.",
                ephemeral=True,
            )
            return

        # Analyze
        stats = self._analyzer.analyze(about, posts, comments)

        # AI profile (non-blocking if AI is unavailable)
        stats.ai_summary = await generate_user_profile(stats)

        # Build embeds
        embeds = build_user_embeds(stats)
        view = UserReportView(embeds)
        await interaction.followup.send(embed=embeds[0], view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UserCommands(bot))
