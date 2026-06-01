"""
Tasks and Campaign Commands Cogs — task creation, claim validations,
and private workspace control panels for submitting proof urls.
"""

from __future__ import annotations

import logging
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import uuid

import bot.db as db
from bot.workspace import get_or_create_workspace_channel

logger = logging.getLogger(__name__)

CHAN_TASKS = "tasks"
CHAN_TASK_LOGS = "task-logs"


class WorkspaceControlPanel(discord.ui.View):
    """The control panel posted inside `#workspace-username` once a task is claimed."""

    def __init__(self, claim_id: int, task_id: str, target_url: str, reward: float) -> None:
        super().__init__(timeout=None)
        self.claim_id = claim_id
        self.task_id = task_id
        self.target_url = target_url
        self.reward = reward

    @discord.ui.button(
        label="Submit Proof",
        style=discord.ButtonStyle.success,
        emoji="",
        custom_id="task_submit_proof_btn",
    )
    async def submit_proof_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Check if the claim is still active and valid
        claim = await db.fetchrow(
            "SELECT * FROM claims WHERE claim_id = ? AND status = 'active';", self.claim_id
        )
        
        if not claim:
            await interaction.response.send_message(
                "This claim is no longer active (may have expired or been submitted).",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            SubmitProofModal(self.claim_id, self.task_id, self.reward)
        )


class SubmitProofModal(discord.ui.Modal, title="Submit Task Proof"):
    """Modal that pops up when user clicks 'Submit Proof' in their workspace."""

    def __init__(self, claim_id: int, task_id: str, reward: float) -> None:
        super().__init__()
        self.claim_id = claim_id
        self.task_id = task_id
        self.reward = reward

    proof_url = discord.ui.TextInput(
        label="Direct Proof Link (Reddit comment/post URL)",
        placeholder="e.g. https://www.reddit.com/r/.../comments/.../comment/...",
        required=True,
        max_length=200,
    )
    screenshot_url = discord.ui.TextInput(
        label="Screenshot URL (Optional)",
        placeholder="e.g. https://imgur.com/...",
        required=False,
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        proof = self.proof_url.value.strip()
        screenshot = self.screenshot_url.value.strip() or None
        discord_id = str(interaction.user.id)

        # 1. Submit proof to database
        sub_id = await db.submit_proof(self.claim_id, discord_id, proof, screenshot)

        # 2. Trigger Auto-validation pipeline
        from bot.validation import validate_submission
        await interaction.followup.send(
            "Proof received! Running automated validation checks. Please wait...",
            ephemeral=False,
        )
        
        # Trigger validation asynchronously
        asyncio.create_task(validate_submission(sub_id, interaction.guild, interaction.user))


class TaskClaimView(discord.ui.View):
    """The view containing the "Claim Task" button posted in the public `#tasks` channel."""

    def __init__(self, task_id: str) -> None:
        super().__init__(timeout=None)
        self.task_id = task_id

    @discord.ui.button(
        label="Claim Task",
        style=discord.ButtonStyle.primary,
        emoji="",
        custom_id="tasks_claim_button_panel",
    )
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        discord_id = str(interaction.user.id)

        # 1. Check if user is verified
        user = await db.get_user(discord_id)
        if not user or not user["verified"]:
            await interaction.followup.send(
                "You must be verified first! Click the **Verify Me** button in the verification channel.",
                ephemeral=True,
            )
            return

        if user["is_flagged"]:
            await interaction.followup.send(
                f"Claim blocked: Your account is flagged. Reason: {user['flag_reason']}",
                ephemeral=True,
            )
            return

        # 2. Check active claim limit (max 1 concurrent claim)
        active_claim = await db.get_active_claim(discord_id)
        if active_claim:
            await interaction.followup.send(
                f"You already have an active claim for task `{active_claim['task_id']}`! "
                f"Submit proof or let it expire first.",
                ephemeral=True,
            )
            return

        # 3. Fetch task details
        task = await db.get_task(self.task_id)
        if not task or task["status"] != "open":
            await interaction.followup.send("This task is no longer available.", ephemeral=True)
            return

        if task["slots_filled"] >= task["slots_total"]:
            await interaction.followup.send("All slots for this task have been filled!", ephemeral=True)
            return

        if user["trust_score"] < task["min_trust"]:
            await interaction.followup.send(
                f"Verification failed: Your trust score ({user['trust_score']}) is below the required minimum ({task['min_trust']}).",
                ephemeral=True,
            )
            return

        # 4. Check cooldown
        if task["cooldown_minutes"] > 0:
            last_claim_time = await db.get_user_last_claim_time(discord_id, task["type"])
            if last_claim_time:
                # Parse timestamp and verify cooldown
                last_time = datetime.fromisoformat(last_claim_time.replace("Z", "+00:00"))
                elapsed = datetime.now(timezone.utc) - last_time
                cooldown_delta = timedelta(minutes=task["cooldown_minutes"])
                if elapsed < cooldown_delta:
                    remaining = cooldown_delta - elapsed
                    mins = int(remaining.total_seconds() / 60)
                    await interaction.followup.send(
                        f"Cooldown active for `{task['type']}` task type. Try again in {mins} minutes.",
                        ephemeral=True,
                    )
                    return

        # 5. Claim the task
        claim_id = await db.claim_task(discord_id, self.task_id, task["time_limit"])
        if not claim_id:
            await interaction.followup.send("Failed to claim task. All slots filled.", ephemeral=True)
            return

        # 6. Post control panel in private workspace
        workspace_chan = await get_or_create_workspace_channel(interaction.guild, interaction.user)
        
        embed = discord.Embed(
            title=f"📋 TASK INSTRUCTIONS: {task['task_id']}",
            description=(
                f"You have successfully claimed a **{task['type']}** task!\n\n"
                f"**Task Details:**\n"
                f"• **Target URL**: {task['target_url']}\n"
                f"• **Reward**: **{task['reward']} credits**\n"
                f"• **Time Limit**: {task['time_limit']} minutes (Expires in {task['time_limit']} mins)\n"
                f"• **Hold Period**: {task['hold_hours']} hours\n\n"
                f"**Instructions:**\n"
                f"1. Navigate to the Target URL.\n"
                f"2. Complete the action (e.g. comment/post under the target, matching your linked Reddit username).\n"
                f"3. Click the **Submit Proof** button below to provide the direct comment/post link!"
            ),
            color=discord.Color.yellow(),
        )
        embed.set_footer(text=f"Claim ID: {claim_id}")

        control_view = WorkspaceControlPanel(claim_id, self.task_id, task["target_url"], task["reward"])
        await workspace_chan.send(embed=embed, view=control_view)

        await interaction.followup.send(
            f"**Task Claimed successfully!**\n"
            f"Navigate to your workspace channel {workspace_chan.mention} to read instructions and submit proof.",
            ephemeral=True,
        )


class TasksCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="createtask", description="Create an open campaign task and post it to #tasks channel")
    @app_commands.describe(
        task_type="Platform action type (e.g. reddit_comment, reddit_post)",
        reward="Earning credit amount (e.g. 150.0)",
        slots="Total available slots",
        time_limit="Active minutes to complete after claiming",
        hold_hours="Hold hours before reward is released",
        target_url="Reddit link to complete the task on",
        min_trust="Minimum trust score required to claim",
        cooldown="Minutes of cooldown on this task type after claiming",
        requires_image="Whether a screenshot proof is required",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def create_task_cmd(
        self,
        interaction: discord.Interaction,
        task_type: str,
        reward: float,
        slots: int,
        time_limit: int,
        hold_hours: int,
        target_url: str,
        min_trust: int = 0,
        cooldown: int = 0,
        requires_image: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return

        # Generate unique task ID
        task_id = f"task_{str(uuid.uuid4())[:8].upper()}"

        # 1. Create in database
        await db.create_task(
            task_id=task_id,
            task_type=task_type,
            reward=reward,
            slots_total=slots,
            time_limit=time_limit,
            hold_hours=hold_hours,
            min_trust=min_trust,
            cooldown_minutes=cooldown,
            requires_image=requires_image,
            target_url=target_url,
        )

        # 2. Find or create #tasks channel
        tasks_chan = discord.utils.get(guild.text_channels, name=CHAN_TASKS)
        if not tasks_chan:
            tasks_chan = await guild.create_text_channel(name=CHAN_TASKS)

        # 3. Post task embed
        embed = discord.Embed(
            title=f"NEW CAMPAIGN TASK: {task_id}",
            description=(
                f"A new task is available to claim!\n\n"
                f"• **Type**: `{task_type}`\n"
                f"• **Reward**: **{reward} credits**\n"
                f"• **Available Slots**: {slots}\n"
                f"• **Time Limit**: {time_limit} mins\n"
                f"• **Min Trust Score**: {min_trust}\n"
                f"• **Target Link**: [Visit Target]({target_url})\n"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Bounty Campaign Ref: {task_id}")

        view = TaskClaimView(task_id)
        await tasks_chan.send(embed=embed, view=view)

        await interaction.followup.send(f"Successfully created task `{task_id}` and posted to {tasks_chan.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TasksCommands(bot))
