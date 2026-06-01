"""
Automated Proof Validation Pipeline.
Verifies Reddit comment/post author, subreddit, and liveness.
"""

from __future__ import annotations

import logging
import re
import discord
from typing import Any

import bot.db as db
from fetcher.fetch_router import get_router
from fetcher.json_fetcher import _extract_post_parts, _extract_comment_id

logger = logging.getLogger(__name__)

CHAN_TASK_LOGS = "task-logs"


def _extract_subreddit_from_url(url: str) -> str | None:
    """Helper to extract subreddit name from a Reddit URL."""
    match = re.search(r"reddit\.com/r/([^/]+)", url)
    return match.group(1) if match else None


async def validate_submission(submission_id: int, guild: discord.Guild | None, member: discord.Member | None) -> None:
    """
    Core validation pipeline.
    Fetches the Reddit content, checks rules, and sets database status/rewards.
    """
    logger.info("Starting validation pipeline for submission ID %d", submission_id)
    
    # 1. Fetch submission details
    async with db.aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = db.aiosqlite.Row
        async with conn.execute(
            """
            SELECT s.*, t.type as task_type, t.target_url, t.reward, t.hold_hours, t.campaign_id, u.reddit_username 
            FROM submissions s
            JOIN claims c ON s.claim_id = c.claim_id
            JOIN tasks t ON c.task_id = t.task_id
            JOIN users u ON s.discord_id = u.discord_id
            WHERE s.submission_id = ?;
            """,
            (submission_id,),
        ) as cursor:
            sub = await cursor.fetchone()

    if not sub:
        logger.error("Submission %d not found in database.", submission_id)
        return

    proof_url = sub["proof_url"]
    task_type = sub["task_type"]
    target_url = sub["target_url"]
    hold_hours = sub["hold_hours"]
    linked_reddit = sub["reddit_username"]
    discord_id = sub["discord_id"]

    # If the user linked Reddit account somehow is missing
    if not linked_reddit:
        await db.update_submission_status(submission_id, "rejected", reason="User does not have a linked Reddit account.")
        await _notify_result(guild, member, submission_id, "rejected", "User does not have a linked Reddit account.")
        return

    # Target parameters
    target_subreddit = _extract_subreddit_from_url(target_url)

    router = get_router()

    # ── VALIDATION: Reddit Comment ──────────────────────────────────────────
    if "reddit_comment" in task_type:
        comment_id = _extract_comment_id(proof_url)
        if not comment_id:
            reason = "Rejected: Could not parse comment ID from URL. Make sure it is a direct comment link."
            await db.update_submission_status(submission_id, "rejected", reason=reason)
            await _notify_result(guild, member, submission_id, "rejected", reason)
            return

        logger.info("Comment ID parsed: %s. Fetching details...", comment_id)
        context = await router.get_comment_context(proof_url)
        
        # If API is unreachable, queue for manual review
        if not context or not context[0]:
            logger.warning("Reddit API unreachable for comment %s. Moved to manual review.", comment_id)
            await db.update_submission_status(submission_id, "manual_review", reason="Reddit API/scraper busy.")
            await _notify_result(guild, member, submission_id, "manual_review", "Scraper was unable to verify. Queued for manual review.")
            return

        comment_data, post_data = context
        author = comment_data.get("author", "")
        body = comment_data.get("body", "")
        subreddit = comment_data.get("subreddit", "")

        # A. Check Deleted/Removed
        if author == "[deleted]" or body in ("[deleted]", "[removed]"):
            reason = "Rejected: This comment has been deleted or removed by moderators."
            await db.update_submission_status(submission_id, "rejected", reason=reason)
            await _notify_result(guild, member, submission_id, "rejected", reason)
            return

        # B. Check Author
        if author.lower() != linked_reddit.lower():
            reason = f"Rejected: Comment author (u/{author}) does not match your linked Reddit account (u/{linked_reddit})."
            await db.update_submission_status(submission_id, "rejected", reason=reason)
            await _notify_result(guild, member, submission_id, "rejected", reason)
            return

        # C. Check Subreddit
        if target_subreddit and subreddit.lower() != target_subreddit.lower():
            reason = f"Rejected: Comment is in r/{subreddit}. Target requirement is r/{target_subreddit}."
            await db.update_submission_status(submission_id, "rejected", reason=reason)
            await _notify_result(guild, member, submission_id, "rejected", reason)
            return

    # ── VALIDATION: Reddit Post ─────────────────────────────────────────────
    elif "reddit_post" in task_type:
        parts = _extract_post_parts(proof_url)
        if not parts:
            reason = "Rejected: Could not parse post ID from URL. Make sure it is a direct Reddit post link."
            await db.update_submission_status(submission_id, "rejected", reason=reason)
            await _notify_result(guild, member, submission_id, "rejected", reason)
            return

        subreddit_from_url, post_id, slug = parts
        logger.info("Post ID parsed: %s. Fetching details...", post_id)
        post_data, _ = await router.get_post_and_comments(proof_url)

        # Scraper busy -> Manual review
        if not post_data:
            logger.warning("Reddit API unreachable for post %s. Moved to manual review.", post_id)
            await db.update_submission_status(submission_id, "manual_review", reason="Reddit API/scraper busy.")
            await _notify_result(guild, member, submission_id, "manual_review", "Scraper was unable to verify. Queued for manual review.")
            return

        author = post_data.get("author", "")
        subreddit = post_data.get("subreddit", "")
        selftext = post_data.get("selftext", "")

        # A. Check Deleted/Removed
        if author == "[deleted]" or selftext in ("[deleted]", "[removed]"):
            reason = "Rejected: This post has been deleted or removed."
            await db.update_submission_status(submission_id, "rejected", reason=reason)
            await _notify_result(guild, member, submission_id, "rejected", reason)
            return

        # B. Check Author
        if author.lower() != linked_reddit.lower():
            reason = f"Rejected: Post author (u/{author}) does not match your linked Reddit account (u/{linked_reddit})."
            await db.update_submission_status(submission_id, "rejected", reason=reason)
            await _notify_result(guild, member, submission_id, "rejected", reason)
            return

        # C. Check Subreddit
        if target_subreddit and subreddit.lower() != target_subreddit.lower():
            reason = f"Rejected: Post is in r/{subreddit}. Target requirement is r/{target_subreddit}."
            await db.update_submission_status(submission_id, "rejected", reason=reason)
            await _notify_result(guild, member, submission_id, "rejected", reason)
            return

    # ── VALIDATION: Other/Twitter (Manual Approval required by default) ─────
    else:
        # Twitter and others fall back to manual review queues by default
        await db.update_submission_status(submission_id, "manual_review", reason="Platform action requires manual screenshot audit.")
        await _notify_result(guild, member, submission_id, "manual_review", "This task type requires manual administrator screenshot review.")
        return

    # ── SUCCESS ──
    # Place submission on pending hold
    await db.update_submission_status(submission_id, "pending_hold", hold_hours=hold_hours)
    
    # Auto-update post URL for comments if this is a campaign post
    if "reddit_post" in task_type and sub.get("campaign_id"):
        await db.update_campaign_post_url(sub["campaign_id"], proof_url)
        logger.info("Automatically synchronized target post URL for campaign %s comments.", sub["campaign_id"])

    # Notify user of pending hold
    await _notify_result(
        guild, member, submission_id, "pending_hold", 
        f"Proof validated successfully! Your reward enters a **{hold_hours} hour hold** period."
    )


async def _notify_result(
    guild: discord.Guild | None,
    member: discord.Member | None,
    submission_id: int,
    status: str,
    message: str,
) -> None:
    """Helper to update user DMs and log events in channels."""
    if not member or not guild:
        return

    # 1. Workspace private notification
    from bot.workspace import get_or_create_workspace_channel
    workspace = await get_or_create_workspace_channel(guild, member)
    
    color = discord.Color.green() if status == "pending_hold" else (
        discord.Color.red() if status == "rejected" else discord.Color.orange()
    )

    embed = discord.Embed(
        title=f"Validation Outcome: Submission #{submission_id}",
        description=message,
        color=color,
    )
    embed.set_footer(text=f"Pipeline State: {status}")
    await workspace.send(embed=embed)

    # 2. DM the user
    try:
        await member.send(
            f"**Task Update (Submission #{submission_id}):**\n"
            f"Status: **{status.upper()}**\nDetails: {message}"
        )
    except Exception:
        pass

    # 3. Log to admin log channel if rejected or manual_review
    if status in ("rejected", "manual_review"):
        logs_chan = discord.utils.get(guild.text_channels, name=CHAN_TASK_LOGS)
        if logs_chan:
            log_embed = discord.Embed(
                title=f"Task Validation Alert: #{submission_id}",
                description=(
                    f"**User**: {member.mention} ({member.name})\n"
                    f"**Outcome**: `{status}`\n"
                    f"**Reason**: {message}"
                ),
                color=color,
            )
            await logs_chan.send(embed=log_embed)
