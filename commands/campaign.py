"""
Reddit Campaign Importer Cog.
Allows administrators to parse and import structured campaign templates
(post title, subreddit, content, comment trees) directly from Discord messages or modals.
"""

from __future__ import annotations

import logging
import re
import discord
from discord import app_commands
from discord.ext import commands
import uuid
from typing import Any

import bot.db as db
from commands.tasks import TaskClaimView, CHAN_TASKS

logger = logging.getLogger(__name__)


def parse_campaign_text(text: str) -> dict[str, Any] | None:
    """
    Robust line-by-line parsing engine for client-provided campaign formats.
    Extracts Keyword, Subreddit, Title, Content, and Comments tables.
    """
    lines = [line.strip() for line in text.split("\n")]
    
    campaign = {
        "title": "",
        "subreddit": "",
        "keyword": "",
        "content": "",
        "comments": []
    }
    
    state = "meta"
    content_lines = []
    comment_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue
            
        lower_line = line.lower()
        if lower_line.startswith("keyword"):
            campaign["keyword"] = line.split(":", 1)[-1].strip()
        elif lower_line.startswith("subreddit"):
            raw_sub = line.split(":", 1)[-1].strip()
            campaign["subreddit"] = re.sub(r'^/?r/', '', raw_sub, flags=re.IGNORECASE).strip().rstrip("/")
        elif lower_line.startswith("title"):
            campaign["title"] = line.split(":", 1)[-1].strip()
        elif lower_line.startswith("content"):
            state = "content"
            i += 1
            continue
        elif lower_line.startswith("n°") or (lower_line.startswith("comments") and i > 0 and "replies" in lines[i-1].lower()):
            state = "comments"
            i += 1
            continue
            
        if state == "content":
            # Content ends if we hit comments header
            if lower_line.startswith("n°") or "comments" in lower_line or "replies to" in lower_line:
                state = "comments"
            else:
                content_lines.append(line)
        elif state == "comments":
            comment_lines.append(line)
            
        i += 1
        
    campaign["content"] = "\n".join(content_lines).strip()
    
    # Parse comment rows: group into chunks of 4 lines
    # line 1: Index (e.g. 1)
    # line 2: Body text
    # line 3: Parent target (e.g. OP, or 1)
    # line 4: Upvotes/Required (e.g. NO)
    comments = []
    j = 0
    while j < len(comment_lines):
        try:
            val = comment_lines[j].strip()
            if val.isdigit():
                index = int(val)
                body = comment_lines[j+1].strip()
                parent = comment_lines[j+2].strip().split(" ")[0]  # Get base parent (e.g. "1" from "1 (OP account)")
                upvotes = comment_lines[j+3].strip()
                
                comments.append({
                    "index": index,
                    "body": body,
                    "parent": parent,
                    "upvotes": upvotes
                })
                j += 4
            else:
                j += 1
        except Exception:
            j += 1
            
    campaign["comments"] = comments
    return campaign


class CampaignImportModal(discord.ui.Modal, title="Import Reddit Campaign"):
    """Modal that pops up for copy-pasting smaller campaign texts."""

    def __init__(self, campaign_id: str, post_reward: float, comment_reward: float, time_limit: int, hold_hours: int) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.post_reward = post_reward
        self.comment_reward = comment_reward
        self.time_limit = time_limit
        self.hold_hours = hold_hours

    campaign_text = discord.ui.TextInput(
        label="Paste Campaign Text Block",
        style=discord.TextStyle.long,
        placeholder="Paste Title, Subreddit, Content, and Comments table...",
        required=True,
        max_length=3800,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        text = self.campaign_text.value
        
        success = await import_campaign_workflow(
            guild=interaction.guild,
            campaign_id=self.campaign_id,
            text=text,
            post_reward=self.post_reward,
            comment_reward=self.comment_reward,
            time_limit=self.time_limit,
            hold_hours=self.hold_hours,
        )

        if success:
            await interaction.followup.send(f"Campaign `{self.campaign_id}` successfully parsed and created!", ephemeral=True)
        else:
            await interaction.followup.send("Error: Failed to parse campaign format. Check the structure.", ephemeral=True)


async def import_campaign_workflow(
    guild: discord.Guild | None,
    campaign_id: str,
    text: str,
    post_reward: float,
    comment_reward: float,
    time_limit: int,
    hold_hours: int,
) -> bool:
    """Main business logic to parse, write to database, and deploy tasks."""
    if not guild:
        return False

    campaign = parse_campaign_text(text)
    if not campaign or not campaign["title"] or not campaign["subreddit"]:
        return False

    subreddit = campaign["subreddit"]
    title = campaign["title"]
    content = campaign["content"]
    keyword = campaign["keyword"]

    # 1. Insert Campaign into DB
    await db.create_campaign(campaign_id, subreddit, title, content, keyword)

    # 2. Deploy Main Post Task
    post_task_id = f"{campaign_id}_POST"
    target_subreddit_url = f"https://www.reddit.com/r/{subreddit}/"
    await db.create_task(
        task_id=post_task_id,
        task_type="reddit_post",
        reward=post_reward,
        slots_total=1,
        time_limit=time_limit,
        hold_hours=hold_hours,
        target_url=target_subreddit_url,
        campaign_id=campaign_id,
        requires_image=False,
    )
    # Update main post task comment_body field to hold the content
    await db.execute("UPDATE tasks SET comment_body = ? WHERE task_id = ?;", content, post_task_id)

    # 3. Deploy Comment Tasks
    for c in campaign["comments"]:
        c_index = c["index"]
        c_parent = c["parent"]
        c_body = c["body"]
        
        c_task_id = f"{campaign_id}_COMMENT_{c_index}"
        
        await db.create_task(
            task_id=c_task_id,
            task_type="reddit_comment",
            reward=comment_reward,
            slots_total=1,
            time_limit=time_limit,
            hold_hours=hold_hours,
            target_url="",  # Starts empty — populated once post task is verified!
            campaign_id=campaign_id,
            requires_image=False,
        )
        # Update comment meta columns in database
        await db.execute(
            "UPDATE tasks SET comment_index = ?, parent_index = ?, comment_body = ? WHERE task_id = ?;",
            c_index, c_parent, c_body, c_task_id
        )

    # 4. Post the main Post task to `#tasks`
    tasks_chan = discord.utils.get(guild.text_channels, name=CHAN_TASKS)
    if not tasks_chan:
        tasks_chan = await guild.create_text_channel(name=CHAN_TASKS)

    embed = discord.Embed(
        title=f"NEW CAMPAIGN POST TASK: {post_task_id}",
        description=(
            f"A new seeding post task has been created!\n\n"
            f"• **Subreddit**: r/{subreddit}\n"
            f"• **Title**: **{title}**\n"
            f"• **Reward**: **{post_reward} credits**\n"
            f"• **Time Limit**: {time_limit} mins\n\n"
            f"Click **Claim Task** to retrieve the exact body text and post it on Reddit!"
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Campaign Ref: {campaign_id}")
    
    view = TaskClaimView(post_task_id)
    await tasks_chan.send(embed=embed, view=view)

    # Post campaign summary to `#task-logs`
    logs_chan = discord.utils.get(guild.text_channels, name="task-logs")
    if logs_chan:
        log_embed = discord.Embed(
            title=f"Seeding Campaign Loaded: {campaign_id}",
            description=(
                f"• **Title**: {title}\n"
                f"• **Subreddit**: r/{subreddit}\n"
                f"• **Keyword**: {keyword or 'None'}\n"
                f"• **Post Reward**: {post_reward} credits\n"
                f"• **Comment Reward**: {comment_reward} credits\n"
                f"• **Seeding Comments**: {len(campaign['comments'])} comment replies queued."
            ),
            color=discord.Color.blue(),
        )
        await logs_chan.send(embed=log_embed)

    return True


class CampaignCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="importcampaign", description="Import a complete Reddit seeding campaign from a text block or message")
    @app_commands.describe(
        message_id="Discord Message ID to copy text from (skips modal 4000-char limit)",
        campaign_id="Unique reference ID for the campaign (blank = random)",
        post_reward="Reward for the main post task",
        comment_reward="Reward for each comment reply task",
        time_limit="Time limit in minutes",
        hold_hours="Hold period hours before credits maturity",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def import_campaign_cmd(
        self,
        interaction: discord.Interaction,
        message_id: str | None = None,
        campaign_id: str | None = None,
        post_reward: float = 200.0,
        comment_reward: float = 100.0,
        time_limit: int = 120,
        hold_hours: int = 168,
    ) -> None:
        guild = interaction.guild
        if not guild:
            return

        ref_id = campaign_id or f"CAMP_{str(uuid.uuid4())[:6].upper()}"

        if message_id:
            await interaction.response.defer(thinking=True, ephemeral=True)
            try:
                # Retrieve the target message
                channel = interaction.channel
                message = await channel.fetch_message(int(message_id))
                text = message.content
                
                success = await import_campaign_workflow(
                    guild=guild,
                    campaign_id=ref_id,
                    text=text,
                    post_reward=post_reward,
                    comment_reward=comment_reward,
                    time_limit=time_limit,
                    hold_hours=hold_hours,
                )

                if success:
                    await interaction.followup.send(f"Successfully imported and deployed campaign `{ref_id}`!", ephemeral=True)
                else:
                    await interaction.followup.send("Error: Failed to parse campaign. Ensure the structure is correct.", ephemeral=True)
            except Exception as exc:
                await interaction.followup.send(f"Error: Message ID not found or could not be parsed: {exc}", ephemeral=True)
        else:
            # Pop up Modal
            modal = CampaignImportModal(
                campaign_id=ref_id,
                post_reward=post_reward,
                comment_reward=comment_reward,
                time_limit=time_limit,
                hold_hours=hold_hours,
            )
            await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CampaignCommands(bot))
