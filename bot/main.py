"""
Reddit Intelligence Bot — main entry point.
Loads all command cogs and starts the Discord bot.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord.ext import commands

from bot.config import settings

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("redditintel")

# ── Intents ───────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = False  # Not needed for slash commands only


# ── Bot class ─────────────────────────────────────────────────────────────────
class RedditIntelBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix="!",  # Legacy prefix (unused — bot uses slash commands)
            intents=intents,
            help_command=None,
        )

    async def setup_hook(self) -> None:
        """Called once before the bot connects. Loads cogs and syncs commands."""
        # Initialize campaign database tables
        from bot.db import init_db
        await init_db()
        logger.info("Platform database initialized successfully.")

        # Start Web Portal Dashboard Server
        from bot.web_portal import start_web_server
        asyncio.create_task(start_web_server(self))
        logger.info("Web Portal Dashboard Server scheduled successfully.")

        cogs = [
            "commands.user",
            "commands.comment",
            "commands.post",
            "commands.thread",
            "commands.verify",
            "commands.tasks",
            "commands.user_reference",
            "commands.admin",
            "commands.campaign",
        ]

        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info("Loaded cog: %s", cog)
            except Exception as exc:
                logger.error("Failed to load cog %s: %s", cog, exc)

        # Sync slash commands
        if settings.guild_ids:
            for guild_id in settings.guild_ids:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                logger.info("Synced commands to guild %d (instant)", guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced commands globally (may take up to 1 hour)")

    async def on_ready(self) -> None:
        logger.info("=" * 50)
        logger.info("Reddit Intelligence Bot is online!")
        logger.info("Logged in as: %s (ID: %s)", self.user, self.user.id if self.user else "?")
        logger.info("Guilds: %d", len(self.guilds))
        logger.info("=" * 50)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Reddit",
            )
        )

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        logger.error("Slash command error: %s", error)
        msg = "An unexpected error occurred. Please try again."
        if isinstance(error, discord.app_commands.CommandOnCooldown):
            msg = f"Command on cooldown. Try again in {error.retry_after:.1f}s."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


# ── Entry point ───────────────────────────────────────────────────────────────
async def main() -> None:
    bot = RedditIntelBot()
    try:
        async with bot:
            await bot.start(settings.discord_bot_token)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except discord.LoginFailure:
        logger.critical("Invalid Discord bot token. Check your .env file.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
