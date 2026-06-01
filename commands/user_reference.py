"""
User reference commands cog.
Handles profile views, wallet set commands, referrals, and personal status tracking.
"""

from __future__ import annotations

import logging
import time
import discord
from discord import app_commands
from discord.ext import commands

import bot.db as db

logger = logging.getLogger(__name__)


class UserReferenceCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ─── 1. Wallet Configuration ──────────────────────────────────────────────

    @app_commands.command(name="setupi", description="Save your UPI ID for INR payments")
    @app_commands.describe(upi_id="Your UPI ID (e.g. username@bank)")
    async def setupi_cmd(self, interaction: discord.Interaction, upi_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await db.update_user_wallet(str(interaction.user.id), "upi", upi_id)
        await interaction.followup.send(f"UPI ID successfully saved as: `{upi_id}`", ephemeral=True)

    @app_commands.command(name="setpaypal", description="Save your PayPal email address for payouts")
    @app_commands.describe(email="Your PayPal email address")
    async def setpaypal_cmd(self, interaction: discord.Interaction, email: str) -> None:
        await interaction.response.defer(ephemeral=True)
        # Quick email sanity check
        if "@" not in email:
            await interaction.followup.send("Error: Invalid PayPal email address.", ephemeral=True)
            return
        await db.update_user_wallet(str(interaction.user.id), "paypal", email)
        await interaction.followup.send(f"PayPal email successfully saved as: `{email}`", ephemeral=True)

    @app_commands.command(name="setwallet", description="Save a crypto wallet address for USDT/ETH/BTC payouts")
    @app_commands.describe(address="Wallet Address / Binance Pay ID", network="Blockchain Network (TRC20, ERC20, SOL, BEP20, etc.)")
    async def setwallet_cmd(self, interaction: discord.Interaction, address: str, network: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        await db.update_user_wallet(str(interaction.user.id), "crypto", address, network)
        net_str = f" (Network: {network})" if network else ""
        await interaction.followup.send(f"Crypto wallet successfully saved: `{address}`{net_str}", ephemeral=True)

    # ─── 2. Wallet & Balance Cards ────────────────────────────────────────────

    @app_commands.command(name="wallet", description="Show a public wallet balance card for yourself or another user")
    @app_commands.describe(user="The user to view balance of")
    async def wallet_cmd(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        await interaction.response.defer()
        target = user or interaction.user
        discord_id = str(target.id)

        profile = await db.get_user(discord_id)
        if not profile:
            await db.register_user(discord_id)
            profile = await db.get_user(discord_id)

        # Build card embed
        embed = discord.Embed(
            title=f"WALLET ACCOUNT: {target.display_name}",
            color=discord.Color.blue(),
        )
        embed.set_thumbnail(url=target.avatar.url if target.avatar else "")
        embed.add_field(name="Available Balance (Payable)", value=f"**{profile['balance_available']:.2f} credits**", inline=False)
        embed.add_field(name="Pending Hold Balance", value=f"**{profile['balance_pending']:.2f} credits**", inline=False)
        
        # Payment setup status
        methods = []
        if profile["upi_id"]:
            methods.append(f"• **UPI**: `{profile['upi_id']}`")
        if profile["paypal_email"]:
            methods.append(f"• **PayPal**: `{profile['paypal_email']}`")
        if profile["crypto_wallet"]:
            net = f" ({profile['crypto_network']})" if profile["crypto_network"] else ""
            methods.append(f"• **Crypto**: `{profile['crypto_wallet']}`{net}")
        
        embed.add_field(name="Configured Payment Details", value="\n".join(methods) if methods else "None. Use `/setupi` or `/setwallet` to add.", inline=False)
        embed.set_footer(text="Weekly automatic balance sweeps happen every Wednesday!")
        await interaction.followup.send(embed=embed)

    # ─── 3. Referrals ──────────────────────────────────────────────────────────

    @app_commands.command(name="referral", description="View your personal referral code and statistics")
    async def referral_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        discord_id = str(interaction.user.id)

        profile = await db.register_user(discord_id)

        # Count total referred users
        total_row = await db.fetchrow(
            "SELECT COUNT(*) as cnt FROM referrals WHERE referrer_id = ?;", discord_id
        )
        total_refs = total_row["cnt"] if total_row else 0
        
        cred_row = await db.fetchrow(
            "SELECT COUNT(*) as cnt FROM referrals WHERE referrer_id = ? AND credited = 1;", discord_id
        )
        credited_refs = cred_row["cnt"] if cred_row else 0

        earnings = credited_refs * 50.0

        embed = discord.Embed(
            title="Referral Dashboard",
            description=(
                f"Share your referral code to earn rewards!\n"
                f"Receive **+50.0 available credits** for every user that verifies using your code.\n\n"
                f"**Your Referral Code**: `{profile['referral_code']}`\n\n"
                f"**Referral Statistics:**\n"
                f"• **Total Referees**: {total_refs} users\n"
                f"• **Verified Referees**: {credited_refs} users\n"
                f"• **Referral Earnings**: **{earnings:.2f} credits**"
            ),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="referraluse", description="Apply someone's referral code before verifying")
    @app_commands.describe(code="The 8-character referral code to apply")
    async def referraluse_cmd(self, interaction: discord.Interaction, code: str) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        # Verify user is not already verified
        profile = await db.get_user(discord_id)
        if profile and profile["verified"]:
            await interaction.followup.send("Error: You are already verified and cannot apply a referral.", ephemeral=True)
            return

        success = await db.apply_referral(discord_id, code)
        if success:
            await interaction.followup.send("Referral code successfully registered! Earn rewards once you link your profile.", ephemeral=True)
        else:
            await interaction.followup.send("Error: Invalid referral code, or code already applied.", ephemeral=True)

    # ─── 4. Earnings Status & Digest ──────────────────────────────────────────

    @app_commands.command(name="mystatus", description="View your active claims, hold metrics, and payouts schedule")
    async def mystatus_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        # 1. Get active claims
        active = await db.get_active_claim(discord_id)
        
        # 2. Get pending hold submissions
        holds = await db.fetch(
            "SELECT * FROM submissions WHERE discord_id = ? AND status = 'pending_hold' ORDER BY hold_expires_at ASC;",
            discord_id
        )

        embed = discord.Embed(
            title="Your Claim Status & Pending Holds",
            color=discord.Color.orange(),
        )

        if active:
            embed.add_field(
                name="📋 Active Task Claim",
                value=(
                    f"• **Task ID**: `{active['task_id']}`\n"
                    f"• **Reward**: {active['reward']} credits\n"
                    f"• **Expires**: {active['expires_at'].replace('T', ' ')}"
                ),
                inline=False,
            )
        else:
            embed.add_field(name="📋 Active Task Claim", value="No active task claims. Browse `#tasks` to get started!", inline=False)

        if holds:
            lines = []
            for h in holds:
                lines.append(
                    f"• **Sub #{h['submission_id']}**: Proof URL: {h['proof_url'][:30]}...\n"
                    f"  Hold Expires: `{h['hold_expires_at'].replace('T', ' ')}`"
                )
            embed.add_field(name="Pending Holds", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Pending Holds", value="No pending hold payouts currently maturing.", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="digest", description="Configure daily earnings digest notifications via DMs")
    @app_commands.describe(status="Toggle ON, OFF, or check STATUS")
    async def digest_cmd(self, interaction: discord.Interaction, status: str) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        status = status.lower().strip()

        if status == "on":
            await db.toggle_user_digest(discord_id, True)
            await interaction.followup.send("Opted in for daily DM earnings and tasks digests.", ephemeral=True)
        elif status == "off":
            await db.toggle_user_digest(discord_id, False)
            await interaction.followup.send("Opted out of daily DM digests.", ephemeral=True)
        else:
            profile = await db.get_user(discord_id)
            enabled = profile["digest_enabled"] if profile else False
            await interaction.followup.send(
                f"ℹ️ **Daily Digest Status**: {'ENABLED' if enabled else 'DISABLED'}",
                ephemeral=True,
            )

    @app_commands.command(name="weblogin", description="Generate a temporary 6-digit token to secure your web portal account")
    async def weblogin_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        
        # Generate token
        token = await db.generate_web_login_token(discord_id)
        
        embed = discord.Embed(
            title="Your Web Portal Access Token",
            description=(
                "Use this temporary token to register your password and log into the redditOS Web Portal.\n\n"
                f"**Your Secure Token**: `{token}`\n\n"
                "**Security Warning**:\n"
                "• This token is **ephemeral** and will expire after use or after 15 minutes.\n"
                "• Do **not** share this token with anyone, including staff members."
            ),
            color=discord.Color.purple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── 5. Diagnostics & Info ────────────────────────────────────────────────

    @app_commands.command(name="ping", description="Verify bot network latency, database speed, and proxy pools status")
    async def ping_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        
        # Test bot response latency
        bot_latency = round(self.bot.latency * 1000, 1)

        # Test DB query speed
        start = time.monotonic()
        await db.execute("SELECT 1;")
        db_speed_ms = round((time.monotonic() - start) * 1000, 2)

        # Get proxy/fetcher session status
        from fetcher.fetch_router import get_router
        router = get_router()
        status_str = router._session.cookie_status()

        embed = discord.Embed(
            title="Platform Performance Diagnostics",
            color=discord.Color.green(),
        )
        embed.add_field(name="Discord Gateway", value=f"`{bot_latency} ms`", inline=True)
        embed.add_field(name="Database Query Latency", value=f"`{db_speed_ms} ms`", inline=True)
        embed.add_field(name="Reddit Cookie Status", value=f"`{status_str}`", inline=False)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="stats", description="Display aggregate community reward metrics and leaderboards")
    async def stats_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        # A. Total Earnings
        total_row = await db.fetchrow(
            "SELECT SUM(balance_available + balance_pending) as total FROM users;"
        )
        total_earnings = total_row["total"] if total_row and total_row["total"] else 0.0

        # B. Tasks Completed
        comp_row = await db.fetchrow(
            "SELECT COUNT(*) as total FROM submissions WHERE status = 'completed';"
        )
        completed_tasks = comp_row["total"] if comp_row else 0

        # C. Top Earner
        top_row = await db.fetchrow(
            "SELECT discord_id, (balance_available + balance_pending) as balance FROM users ORDER BY balance DESC LIMIT 1;"
        )

        top_earner_str = "None"
        if top_row and top_row["balance"] > 0:
            top_member = interaction.guild.get_member(int(top_row["discord_id"])) if interaction.guild else None
            top_earner_str = f"{top_member.mention if top_member else 'User ID: ' + top_row['discord_id']} ({top_row['balance']:.2f} credits)"

        embed = discord.Embed(
            title="Community Bounty Stats",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Cumulative Platform Earnings", value=f"**{total_earnings:.2f} credits**", inline=True)
        embed.add_field(name="Total Tasks Completed", value=f"**{completed_tasks} tasks**", inline=True)
        embed.add_field(name="Top Earner", value=top_earner_str, inline=False)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UserReferenceCommands(bot))
