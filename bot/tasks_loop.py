"""
Persistent Platform Loops Cogs — background checker for comment liveness,
hold payouts release scheduler, and weekly Wednesday payouts sweeps.
"""

from __future__ import annotations

import asyncio
import logging
import json
from datetime import datetime, timezone
import discord
from discord.ext import tasks, commands

import bot.db as db
from fetcher.fetch_router import get_router
from fetcher.json_fetcher import _extract_post_parts, _extract_comment_id

logger = logging.getLogger(__name__)

CHAN_TASK_LOGS = "task-logs"
CHAN_WITHDRAWAL_LOGS = "withdrawal-logs"


class PlatformLoops(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_sweep_date: str | None = None  # To prevent double sweeps on Wednesday
        
        # Start background tasks loops
        self.liveness_checker_loop.start()
        self.hold_release_loop.start()
        self.weekly_payout_loop.start()

    def cog_unload(self) -> None:
        self.liveness_checker_loop.cancel()
        self.hold_release_loop.cancel()
        self.weekly_payout_loop.cancel()

    # ─── 1. Liveness Checker Loop (30 minutes) ───────────────────────────────
    @tasks.loop(minutes=30.0)
    async def liveness_checker_loop(self) -> None:
        """Every 30 minutes, re-verify all active submissions on hold. Reverses rewards on clawbacks."""
        logger.info("Starting background liveness checker loop...")
        
        # Get all pending_hold submissions
        active_holds = await db.fetch(
            """
            SELECT s.*, t.type as task_type, t.reward, u.reddit_username 
            FROM submissions s
            JOIN claims c ON s.claim_id = c.claim_id
            JOIN tasks t ON c.task_id = t.task_id
            JOIN users u ON s.discord_id = u.discord_id
            WHERE s.status = 'pending_hold';
            """
        )

        if not active_holds:
            return

        router = get_router()

        for hold in active_holds:
            sub_id = hold["submission_id"]
            proof_url = hold["proof_url"]
            task_type = hold["task_type"]
            reward = hold["reward"]
            discord_id = hold["discord_id"]
            username = hold["reddit_username"]

            is_live = True
            reason = ""

            try:
                if "reddit_comment" in task_type:
                    c_id = _extract_comment_id(proof_url)
                    if c_id:
                        context = await router.get_comment_context(proof_url)
                        if context and context[0]:
                            author = context[0].get("author", "")
                            body = context[0].get("body", "")
                            if author == "[deleted]" or body in ("[deleted]", "[removed]"):
                                is_live = False
                                reason = "Comment deleted or removed during hold."
                
                elif "reddit_post" in task_type:
                    parts = _extract_post_parts(proof_url)
                    if parts:
                        post_data, _ = await router.get_post_and_comments(proof_url)
                        if post_data:
                            author = post_data.get("author", "")
                            selftext = post_data.get("selftext", "")
                            if author == "[deleted]" or selftext in ("[deleted]", "[removed]"):
                                is_live = False
                                reason = "Post deleted or removed during hold."

                if not is_live:
                    logger.warning("CLAWBACK TRIGGERED for submission %d: %s", sub_id, reason)
                    await self._trigger_clawback(sub_id, discord_id, reward, reason)

            except Exception as exc:
                logger.error("Error in liveness checker for submission %d: %s", sub_id, exc)

    async def _trigger_clawback(self, sub_id: int, discord_id: str, reward: float, reason: str) -> None:
        """Trigger clawback: set status rejected, reverse balance, DM user."""
        # Reverse pending balance
        await db.execute(
            "UPDATE users SET balance_pending = balance_pending - ? WHERE discord_id = ?;",
            reward, discord_id
        )
        await db.execute(
            "UPDATE users SET balance_pending = 0.0 WHERE discord_id = ? AND balance_pending < 0.0;",
            discord_id
        )
        # Update submission
        await db.execute(
            "UPDATE submissions SET status = 'rejected', rejection_reason = ? WHERE submission_id = ?;",
            f"Clawback: {reason}", sub_id
        )
        # Find claim ID to mark complete
        claim_row = await db.fetchrow("SELECT claim_id FROM submissions WHERE submission_id = ?;", sub_id)
        if claim_row:
            await db.execute(
                "UPDATE claims SET status = 'completed' WHERE claim_id = ?;", claim_row["claim_id"]
            )

        # Send alert
        for guild in self.bot.guilds:
            member = guild.get_member(int(discord_id))
            if member:
                # Private Workspace alert
                from bot.workspace import get_or_create_workspace_channel
                workspace = await get_or_create_workspace_channel(guild, member)
                embed = discord.Embed(
                    title=f"Clawback Alert: Submission #{sub_id}",
                    description=(
                        f"Your reward of **{reward} credits** has been clawed back because your proof link "
                        f"is no longer active or was deleted.\n\n"
                        f"**Reason**: {reason}"
                    ),
                    color=discord.Color.red(),
                )
                await workspace.send(embed=embed)

                # DM notification
                try:
                    await member.send(
                        f"**Balance Clawback Alert (Submission #{sub_id}):**\n"
                        f"Your pending reward has been reversed because your proof was deleted/removed."
                    )
                except Exception:
                    pass

                # Logs Channel
                logs_chan = discord.utils.get(guild.text_channels, name=CHAN_TASK_LOGS)
                if logs_chan:
                    log_embed = discord.Embed(
                        title=f"Clawback Triggered: Submission #{sub_id}",
                        description=f"**User**: {member.mention}\n**Amount**: -{reward} credits\n**Reason**: {reason}",
                        color=discord.Color.red(),
                    )
                    await logs_chan.send(embed=log_embed)
                break

    # ─── 2. Hold Release Loop (1 minute) ─────────────────────────────────────
    @tasks.loop(minutes=1.0)
    async def hold_release_loop(self) -> None:
        """Every minute, release rewards from pending to available for all matured holds."""
        now = datetime.now(timezone.utc).isoformat()
        
        matured_holds = await db.fetch(
            """
            SELECT s.*, t.type as task_type, t.reward, c.claim_id 
            FROM submissions s
            JOIN claims c ON s.claim_id = c.claim_id
            JOIN tasks t ON c.task_id = t.task_id
            WHERE s.status = 'pending_hold' AND s.hold_expires_at <= ?;
            """,
            now
        )

        if not matured_holds:
            return

        router = get_router()

        for hold in matured_holds:
            sub_id = hold["submission_id"]
            claim_id = hold["claim_id"]
            discord_id = hold["discord_id"]
            reward = hold["reward"]
            proof_url = hold["proof_url"]
            task_type = hold["task_type"]

            # Perform final liveness check before payout release
            is_live = True
            try:
                if "reddit_comment" in task_type:
                    c_id = _extract_comment_id(proof_url)
                    if c_id:
                        context = await router.get_comment_context(proof_url)
                        if context and context[0]:
                            author = context[0].get("author", "")
                            body = context[0].get("body", "")
                            if author == "[deleted]" or body in ("[deleted]", "[removed]"):
                                is_live = False
                elif "reddit_post" in task_type:
                    parts = _extract_post_parts(proof_url)
                    if parts:
                        post_data, _ = await router.get_post_and_comments(proof_url)
                        if post_data:
                            author = post_data.get("author", "")
                            selftext = post_data.get("selftext", "")
                            if author == "[deleted]" or selftext in ("[deleted]", "[removed]"):
                                is_live = False
            except Exception:
                # Scraper issue — treat as inconclusive rather than false
                logger.warning("Scraper error during final hold release verification for sub %d", sub_id)
                await self._flag_inconclusive(sub_id, discord_id)
                continue

            if not is_live:
                # Clawback
                await self._trigger_clawback(sub_id, discord_id, reward, "Proof comment/post deleted prior to hold payout.")
                continue

            # Release Hold
            # Deduct pending, Add available
            await db.execute(
                "UPDATE users SET balance_pending = balance_pending - ?, balance_available = balance_available + ? WHERE discord_id = ?;",
                reward, reward, discord_id
            )
            await db.execute(
                "UPDATE users SET balance_pending = 0.0 WHERE discord_id = ? AND balance_pending < 0.0;",
                discord_id
            )
            # Complete submission
            await db.execute(
                "UPDATE submissions SET status = 'completed' WHERE submission_id = ?;",
                sub_id
            )
            # Complete claim
            await db.execute(
                "UPDATE claims SET status = 'completed' WHERE claim_id = ?;",
                claim_id
            )

            # Notifications
            for guild in self.bot.guilds:
                member = guild.get_member(int(discord_id))
                if member:
                    # Workspace Channel
                    from bot.workspace import get_or_create_workspace_channel
                    workspace = await get_or_create_workspace_channel(guild, member)
                    embed = discord.Embed(
                        title="Reward Released!",
                        description=(
                            f"Congratulations! Your hold period for submission #{sub_id} has expired.\n"
                            f"**{reward} credits** have been moved to your **available balance**! Use `/setwallet` or `/setpaypal` to receive payouts."
                        ),
                        color=discord.Color.green(),
                    )
                    await workspace.send(embed=embed)

                    # DM Notification
                    try:
                        await member.send(
                            f"**Reward Released!** **+{reward} credits** has matured and is now payable!"
                        )
                    except Exception:
                        pass
                    break

    async def _flag_inconclusive(self, sub_id: int, discord_id: str) -> None:
        """Flag the hold release as inconclusive and alert admins (Reddit down, proxy issues)."""
        await db.execute(
            "UPDATE submissions SET status = 'manual_review', rejection_reason = 'Inconclusive final verification.' WHERE submission_id = ?;",
            sub_id
        )

        for guild in self.bot.guilds:
            logs_chan = discord.utils.get(guild.text_channels, name=CHAN_TASK_LOGS)
            if logs_chan:
                embed = discord.Embed(
                    title="Hold Payout Blocked: Inconclusive Check",
                    description=(
                        f"Submission #{sub_id} could not be fully verified on release due to Reddit API timeouts.\n"
                        f"Moved to **manual review queue**."
                    ),
                    color=discord.Color.orange(),
                )
                await logs_chan.send(content="@Admin", embed=embed)
                break

    # ─── 3. Weekly Payout Sweep Loop (Wednesday Sweep) ───────────────────────
    @tasks.loop(hours=1.0)
    async def weekly_payout_loop(self) -> None:
        """Every hour, check if it's Wednesday. Sweep all users with available balances to withdrawals."""
        now = datetime.now(timezone.utc)
        current_date_str = now.strftime("%Y-%m-%d")

        # Check if today is Wednesday (weekday = 2) and we haven't swept today
        if now.weekday() == 2 and self._last_sweep_date != current_date_str:
            logger.info("Wednesday weekly payout sweep triggered!")
            self._last_sweep_date = current_date_str
            await self._run_weekly_payout_sweep()

    async def _run_weekly_payout_sweep(self) -> None:
        """Sweep all available positive balances to pending withdrawals."""
        payable_users = await db.fetch(
            "SELECT discord_id, balance_available, upi_id, paypal_email, crypto_wallet, crypto_network FROM users WHERE balance_available > 0.0 AND is_flagged = 0;"
        )

        if not payable_users:
            return

        for user in payable_users:
            discord_id = user["discord_id"]
            amount = user["balance_available"]
            upi = user["upi_id"]
            paypal = user["paypal_email"]
            wallet = user["crypto_wallet"]
            network = user["crypto_network"]

            # Select payment target in order of priority
            method = "None"
            info = ""
            if wallet:
                method = "crypto"
                info = f"Address: {wallet} (Network: {network or 'Primary'})"
            elif paypal:
                method = "paypal"
                info = paypal
            elif upi:
                method = "upi"
                info = upi

            if method == "None":
                # Skip user since they haven't configured a payout method yet
                continue

            # Atomic database withdrawal request
            withdrawal_id = await db.request_withdrawal(discord_id, amount, method, info)
            if not withdrawal_id:
                continue

            # Notify user & Administrators
            for guild in self.bot.guilds:
                member = guild.get_member(int(discord_id))
                if member:
                    # DM Notification
                    try:
                        await member.send(
                            f"**Weekly Payout Swept!**\n"
                            f"Your available balance of **{amount} credits** has been swept into withdrawal request #{withdrawal_id}.\n"
                            f"Method: **{method.upper()}** ({info})\n"
                            f"Payout will be processed shortly by server administrators."
                        )
                    except Exception:
                        pass

                    # Payout Logs
                    logs_chan = discord.utils.get(guild.text_channels, name=CHAN_WITHDRAWAL_LOGS)
                    if logs_chan:
                        embed = discord.Embed(
                            title=f"Pending Withdrawal #{withdrawal_id}",
                            description=(
                                f"**User**: {member.mention} ({member.name})\n"
                                f"**Amount**: **{amount} credits**\n"
                                f"**Payment Method**: {method.upper()}\n"
                                f"**Details**: {info}\n"
                            ),
                            color=discord.Color.yellow(),
                        )
                        embed.set_footer(text=f"Withdrawal ID: {withdrawal_id}")
                        await logs_chan.send(embed=embed)
                    break


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PlatformLoops(bot))
