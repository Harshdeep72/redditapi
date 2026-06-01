"""
Administrator & Moderator commands cog.
Handles database flagging, manual approvals, balance modifications, and dry-run tests.
"""

from __future__ import annotations

import logging
import time
import discord
from discord import app_commands
from discord.ext import commands
from typing import Literal

import bot.db as db
from fetcher.fetch_router import get_router
from bot.workspace import get_or_create_workspace_category

logger = logging.getLogger(__name__)

ROLE_VERIFIED = "Verified"
CHAN_VERIFY_LOGS = "verification-logs"
CHAN_TASK_LOGS = "task-logs"
CHAN_WITHDRAWAL_LOGS = "withdrawal-logs"


class AdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ─── 1. Setup & Channel Bootstrap ─────────────────────────────────────────

    @app_commands.command(name="setup", description="Bootstrap campaign roles, log channels, and workspaces category")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return

        # A. Create Verified Role
        role = discord.utils.get(guild.roles, name=ROLE_VERIFIED)
        if not role:
            role = await guild.create_role(name=ROLE_VERIFIED, color=discord.Color.blue())
            logger.info("Created Verified role in guild %s", guild.name)

        # B. Create Log Channels
        chans = [CHAN_VERIFY_LOGS, CHAN_TASK_LOGS, CHAN_WITHDRAWAL_LOGS]
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
        }
        for name in chans:
            c = discord.utils.get(guild.text_channels, name=name)
            if not c:
                await guild.create_text_channel(name=name, overwrites=overwrites)
                logger.info("Created text channel: %s", name)

        # C. Create Workspace category
        await get_or_create_workspace_category(guild)

        await interaction.followup.send("Platform roles, channels, and categories bootstrapped successfully!", ephemeral=True)

    # ─── 2. Verification Controls ─────────────────────────────────────────────

    @app_commands.command(name="verifyuser", description="Manually grant or revoke verified status for a member")
    @app_commands.describe(user="Member to manage", action="Grant verification or Revoke it", reddit_username="Reddit username if granting link")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def verifyuser_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        action: Literal["verify", "unverify"],
        reddit_username: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_id = str(user.id)
        guild = interaction.guild
        if not guild:
            return

        role = discord.utils.get(guild.roles, name=ROLE_VERIFIED)
        if not role:
            role = await guild.create_role(name=ROLE_VERIFIED, color=discord.Color.blue())

        if action == "verify":
            if not reddit_username:
                await interaction.followup.send("Error: A Reddit username is required when verifying manually.", ephemeral=True)
                return

            # Perform DB linking
            success = await db.update_user_reddit(discord_id, reddit_username, verified=True)
            if not success:
                await interaction.followup.send("Error: That Reddit account is already linked to another user.", ephemeral=True)
                return

            await user.add_roles(role)
            await interaction.followup.send(f"Successfully verified {user.mention} as u/{reddit_username} manually.", ephemeral=True)

        elif action == "unverify":
            await db.execute(
                "UPDATE users SET verified = 0, reddit_username = NULL WHERE discord_id = ?;",
                discord_id,
            )
            
            await user.remove_roles(role)
            await interaction.followup.send(f"Successfully revoked verification for {user.mention}.", ephemeral=True)

    # ─── 3. Flagging & Account Blocks ─────────────────────────────────────────

    @app_commands.command(name="flag", description="Flag a user — blocks task claiming and payout releases")
    @app_commands.describe(user="Target member to flag", reason="Detailed explanation for flagging")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def flag_cmd(self, interaction: discord.Interaction, user: discord.Member, reason: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await db.set_user_flag(str(user.id), True, reason)
        await interaction.followup.send(f"**Flagged {user.display_name}!** Payouts and claims have been blocked.", ephemeral=True)

    @app_commands.command(name="unflag", description="Clear a user's flag status")
    @app_commands.describe(user="Target member to unflag")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def unflag_cmd(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)
        await db.set_user_flag(str(user.id), False)
        await interaction.followup.send(f"Flag cleared for {user.display_name}. Account restored.", ephemeral=True)

    # ─── 4. Payout overrides & controls ───────────────────────────────────────

    @app_commands.command(name="addbalance", description="Manually credit extra available balance to a member")
    @app_commands.describe(user="Target member", amount="Amount to credit", reason="Reason for ledger")
    @app_commands.checks.has_permissions(administrator=True)
    async def addbalance_cmd(self, interaction: discord.Interaction, user: discord.Member, amount: float, reason: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await db.register_user(str(user.id))
        await db.execute(
            "UPDATE users SET balance_available = balance_available + ? WHERE discord_id = ?;",
            amount, str(user.id)
        )
        await interaction.followup.send(f"Credited **+{amount} credits** to {user.mention} available balance.", ephemeral=True)

    @app_commands.command(name="removebalance", description="Manually deduct available balance from a member")
    @app_commands.describe(user="Target member", amount="Amount to deduct", reason="Reason for ledger")
    @app_commands.checks.has_permissions(administrator=True)
    async def removebalance_cmd(self, interaction: discord.Interaction, user: discord.Member, amount: float, reason: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await db.register_user(str(user.id))
        await db.execute(
            "UPDATE users SET balance_available = balance_available - ? WHERE discord_id = ?;",
            amount, str(user.id)
        )
        await db.execute(
            "UPDATE users SET balance_available = 0.0 WHERE discord_id = ? AND balance_available < 0.0;",
            str(user.id)
        )
        await interaction.followup.send(f"Deducted **-{amount} credits** from {user.mention} available balance.", ephemeral=True)

    # ─── 5. Direct submission controls ────────────────────────────────────────

    @app_commands.command(name="approvesubmission", description="Manually approve a rejected/pending submission and release credits")
    @app_commands.describe(submission_id="The ID of the submission to force approve")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def approvesubmission_cmd(self, interaction: discord.Interaction, submission_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        
        # Override to completed
        await db.update_submission_status(submission_id, "completed")
        await interaction.followup.send(f"Submission #{submission_id} manually approved. Funds released to available balance.", ephemeral=True)

    @app_commands.command(name="checksubmission", description="Manually trigger validation pipeline on a submission")
    @app_commands.describe(submission_id="The ID of the submission to audit")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def checksubmission_cmd(self, interaction: discord.Interaction, submission_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        
        from bot.validation import validate_submission
        # Fetch submission user member
        row = await db.fetchrow("SELECT discord_id FROM submissions WHERE submission_id = ?;", submission_id)
        discord_id = row["discord_id"] if row else None

        member = interaction.guild.get_member(int(discord_id)) if (interaction.guild and discord_id) else None
        
        # Run pipeline
        await validate_submission(submission_id, interaction.guild, member)
        await interaction.followup.send(f"Pipeline executed on submission #{submission_id}.", ephemeral=True)

    # ─── 6. Pipeline Dry-Run debugging ────────────────────────────────────────

    @app_commands.command(name="testurl", description="Dry-run a Reddit URL through the validation parser and print details")
    @app_commands.describe(url="Reddit post or comment URL to parse", reddit_username="Linked username to mock check against")
    @app_commands.checks.has_permissions(administrator=True)
    async def testurl_cmd(self, interaction: discord.Interaction, url: str, reddit_username: str) -> None:
        await interaction.response.defer()
        
        from fetcher.json_fetcher import _extract_post_parts, _extract_comment_id
        c_id = _extract_comment_id(url)
        p_parts = _extract_post_parts(url)

        embed = discord.Embed(title="Validation URL Parser Dry-Run", color=discord.Color.blue())
        embed.add_field(name="Target URL", value=url, inline=False)
        embed.add_field(name="Extracted Comment ID", value=str(c_id), inline=True)
        embed.add_field(name="Extracted Post Parts", value=str(p_parts), inline=True)

        router = get_router()
        if c_id:
            context = await router.get_comment_context(url)
            if context and context[0]:
                author = context[0].get("author", "")
                sub = context[0].get("subreddit", "")
                is_match = author.lower() == reddit_username.lower()
                embed.add_field(name="Author Fetched", value=f"u/{author} (Match: {is_match})", inline=True)
                embed.add_field(name="Subreddit Fetched", value=f"r/{sub}", inline=True)
            else:
                embed.add_field(name="Status", value="Comment ID parsed but content unreachable.", inline=False)
        elif p_parts:
            post, _ = await router.get_post_and_comments(url)
            if post:
                author = post.get("author", "")
                sub = post.get("subreddit", "")
                is_match = author.lower() == reddit_username.lower()
                embed.add_field(name="Author Fetched", value=f"u/{author} (Match: {is_match})", inline=True)
                embed.add_field(name="Subreddit Fetched", value=f"r/{sub}", inline=True)
            else:
                embed.add_field(name="Status", value="Post ID parsed but content unreachable.", inline=False)
        else:
            embed.add_field(name="Status", value="Unrecognized URL structure.", inline=False)

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCommands(bot))
