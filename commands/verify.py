"""
Verification Flow Cogs — button panel, interactive modals, auto-validation,
and manual moderator review channels for Reddit u/ username verification.
"""

from __future__ import annotations

import logging
import re
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

from fetcher.fetch_router import get_router
from bot.workspace import get_or_create_workspace_channel
import bot.db as db

logger = logging.getLogger(__name__)

ROLE_VERIFIED = "Verified"
CHAN_VERIFY_LOGS = "verification-logs"


class ManualVerificationView(discord.ui.View):
    """Admin controls inside `#verification-logs` to manually approve/reject offline verification attempts."""

    def __init__(self, target_discord_id: str, reddit_username: str, karma: int, age_days: int) -> None:
        super().__init__(timeout=None)
        self.target_discord_id = target_discord_id
        self.reddit_username = reddit_username
        self.karma = karma
        self.age_days = age_days

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="verify_manual_approve")
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        
        # 1. Update database
        success = await db.update_user_reddit(self.target_discord_id, self.reddit_username, verified=True)
        if not success:
            await interaction.followup.send("Error: This Reddit account is already linked to another Discord ID.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            return

        # 2. Grant Verified Role
        member = guild.get_member(int(self.target_discord_id))
        if member:
            role = discord.utils.get(guild.roles, name=ROLE_VERIFIED)
            if role:
                await member.add_roles(role)
            
            # 3. Create private workspace
            await get_or_create_workspace_channel(guild, member)

            # 4. DM the user
            try:
                await member.send(
                    f"**Your verification request for u/{self.reddit_username} has been manually approved!**\n"
                    f"A private workspace channel has been created for you in the server. Go check it out to earn rewards!"
                )
            except Exception:
                pass

        # Disable buttons
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.title = "Verification Approved (Manual)"
        embed.add_field(name="Reviewed By", value=interaction.user.mention, inline=False)
        
        await interaction.message.edit(embed=embed, view=None)
        await interaction.followup.send(f"Approved u/{self.reddit_username} manually.", ephemeral=True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.red, custom_id="verify_manual_reject")
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()

        guild = interaction.guild
        member = guild.get_member(int(self.target_discord_id)) if guild else None
        if member:
            try:
                await member.send(
                    f"**Your verification request for u/{self.reddit_username} has been rejected by moderators.**"
                )
            except Exception:
                pass

        # Disable buttons
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.title = "Verification Rejected (Manual)"
        embed.add_field(name="Reviewed By", value=interaction.user.mention, inline=False)
        
        await interaction.message.edit(embed=embed, view=None)
        await interaction.followup.send(f"Rejected u/{self.reddit_username} manually.", ephemeral=True)


class RedditVerificationModal(discord.ui.Modal, title="Link Reddit Profile"):
    """Modal that pops up when user clicks 'Verify Me'."""
    
    reddit_name = discord.ui.TextInput(
        label="Reddit Username",
        placeholder="e.g. spez (do not include u/)",
        required=True,
        max_length=40,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        username = re.sub(r'^/?u/', '', self.reddit_name.value.strip(), flags=re.IGNORECASE).strip().rstrip("/")
        discord_id = str(interaction.user.id)

        # 1. Database sanity checks
        user = await db.get_user(discord_id)
        if user and user["verified"]:
            await interaction.followup.send("You are already verified!", ephemeral=True)
            return

        if user and user["is_flagged"]:
            await interaction.followup.send(
                f"Verification blocked: Your account is flagged. Reason: {user['flag_reason'] or 'No reason provided.'}",
                ephemeral=True,
            )
            return

        # Check unique link constraints
        existing = await db.get_user_by_reddit(username)
        if existing and existing["discord_id"] != discord_id:
            await interaction.followup.send(
                f"Error: The Reddit account **u/{username}** is already linked to another Discord user.",
                ephemeral=True,
            )
            return

        # 2. Fetch Reddit profile metrics
        router = get_router()
        about = await router.get_user_about(username)

        # Fallback to Manual Moderator Review if Reddit is unreachable
        if not about:
            # Send to manual moderator queue
            guild = interaction.guild
            if guild:
                logs_chan = discord.utils.get(guild.text_channels, name=CHAN_VERIFY_LOGS)
                if not logs_chan:
                    # Fallback — create it
                    logs_chan = await guild.create_text_channel(name=CHAN_VERIFY_LOGS)

                # Send review embed
                embed = discord.Embed(
                    title="Manual Verification Required (API Down)",
                    description=(
                        f"Reddit API could not reach **u/{username}** profile.\n"
                        f"Please verify manually and approve or reject."
                    ),
                    color=discord.Color.orange(),
                )
                embed.add_field(name="Discord User", value=interaction.user.mention, inline=True)
                embed.add_field(name="Reddit Target", value=f"[u/{username}](https://old.reddit.com/user/{username})", inline=True)
                embed.set_footer(text=f"ID: {discord_id}")

                view = ManualVerificationView(
                    target_discord_id=discord_id,
                    reddit_username=username,
                    karma=0,
                    age_days=0,
                )
                await logs_chan.send(embed=embed, view=view)

            await interaction.followup.send(
                "Reddit is currently busy/unreachable. "
                "Your verification request has been queued for manual review. "
                "You will receive a DM notification once approved!",
                ephemeral=True,
            )
            return

        # 3. Perform profile rules checks
        from models.user import RedditUser
        reddit_user = RedditUser.from_json(about)

        # Min karma 100
        if reddit_user.total_karma < 100:
            await interaction.followup.send(
                f"Verification failed: Account **u/{username}** has **{reddit_user.total_karma}** total karma. "
                f"Requirement: $\\ge 100$ total karma.",
                ephemeral=True,
            )
            return

        # Account age 30 days
        if reddit_user.account_age_days < 30:
            await interaction.followup.send(
                f"Verification failed: Account **u/{username}** is **{reddit_user.account_age_days}** days old. "
                f"Requirement: $\\ge 30$ days old.",
                ephemeral=True,
            )
            return

        # 4. Successful validation
        await db.update_user_reddit(discord_id, username, verified=True)
        
        # Grant role
        guild = interaction.guild
        if guild:
            role = discord.utils.get(guild.roles, name=ROLE_VERIFIED)
            if not role:
                # Fallback: create role if missing
                role = await guild.create_role(name=ROLE_VERIFIED, color=discord.Color.blue())
            await interaction.user.add_roles(role)

            # Create private workspace
            await get_or_create_workspace_channel(guild, interaction.user)

            # Check for referral rewards
            ref_row = await db.fetchrow(
                "SELECT * FROM referrals WHERE referee_id = ? AND credited = 0;", discord_id
            )
            
            if ref_row:
                referrer_id = ref_row["referrer_id"]
                # Reward referrer 50.0 available balance
                await db.execute(
                    "UPDATE users SET balance_available = balance_available + 50.0 WHERE discord_id = ?;",
                    referrer_id
                )
                await db.execute(
                    "UPDATE referrals SET credited = 1 WHERE referee_id = ?;", discord_id
                )
                
                # Try to DM referrer
                referrer_member = guild.get_member(int(referrer_id))
                if referrer_member:
                    try:
                        await referrer_member.send(
                            f"**Referral Credited!** Your referee {interaction.user.mention} "
                            f"has successfully verified! **+50.0** reward has been credited to your available balance."
                        )
                    except Exception:
                        pass

        await interaction.followup.send(
            f"**Verification Successful!**\n"
            f"Successfully linked to Reddit account **u/{username}**.\n"
            f"Your private workspace channel has been created, and the `{ROLE_VERIFIED}` role assigned!",
            ephemeral=True,
        )


class PublicVerifyPanel(discord.ui.View):
    """The persistent public verification panel view with 'Verify Me' button."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify Me",
        style=discord.ButtonStyle.primary,
        emoji="",
        custom_id="verify_me_button_panel",
    )
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(RedditVerificationModal())


class VerificationCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="verify", description="Post the persistent public verification panel in the current channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def verify_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        
        embed = discord.Embed(
            title="Earn Rewards — Reddit Verification",
            description=(
                "Welcome to the incentivized earning platform!\n\n"
                "To prevent spam and claim target bounties, you must link your Reddit profile:\n\n"
                "**Minimum Requirements:**\n"
                "• **Account Age**: $\\ge 30$ days\n"
                "• **Total Karma**: $\\ge 100$\n\n"
                "Click the **Verify Me** button below to enter your Reddit username!"
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Secure OAuth-less Verified System")

        await interaction.channel.send(embed=embed, view=PublicVerifyPanel())
        await interaction.followup.send("Verification panel posted successfully.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VerificationCommands(bot))
    # Register the persistent view so it survives restarts
    bot.add_view(PublicVerifyPanel())
