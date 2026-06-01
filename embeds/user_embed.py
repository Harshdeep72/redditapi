"""
Discord embed builder for user intelligence reports.
Uses Discord.py's pagination via views with Prev/Next buttons.
"""

from __future__ import annotations

from datetime import timezone

import discord

from models.user import UserStats

# Color palette
COLOR_BLUE = 0x5865F2
COLOR_GREEN = 0x57F287
COLOR_GOLD = 0xFEE75C
COLOR_RED = 0xED4245
COLOR_GREY = 0x99AAB5


def _karma_bar(value: int, max_val: int = 100000) -> str:
    filled = min(int((value / max(max_val, 1)) * 10), 10)
    return "█" * filled + "░" * (10 - filled)


def build_user_embeds(stats: UserStats) -> list[discord.Embed]:
    """Build a list of paginated embeds for a user report."""
    user = stats.user
    embeds: list[discord.Embed] = []

    # ── Page 1: Profile Overview ─────────────────────────────────────
    e1 = discord.Embed(
        title=f"Reddit Intelligence — u/{user.username}",
        color=COLOR_BLUE,
        url=f"https://reddit.com/user/{user.username}",
    )
    if user.avatar_url:
        e1.set_thumbnail(url=user.avatar_url)

    status = "SUSPENDED" if user.is_suspended else ("Premium" if user.is_premium else "Regular")
    e1.add_field(name="Account Status", value=status, inline=True)
    e1.add_field(name="Account Age", value=user.account_age_str, inline=True)
    e1.add_field(
        name="Cake Day",
        value=f"<t:{int(user.created_utc)}:D>",
        inline=True,
    )
    e1.add_field(
        name="Karma Breakdown",
        value=(
            f"```\n"
            f"Post Karma:    {user.link_karma:>10,}\n"
            f"Comment Karma: {user.comment_karma:>10,}\n"
            f"Total Karma:   {user.total_karma:>10,}\n"
            f"```"
        ),
        inline=False,
    )
    e1.set_footer(text="Page 1/4 — Profile Overview")
    embeds.append(e1)

    # ── Page 2: Activity Metrics ──────────────────────────────────────
    e2 = discord.Embed(
        title=f"Activity Metrics — u/{user.username}",
        color=COLOR_GREEN,
    )
    e2.add_field(name="Posts Analyzed", value=f"`{stats.posts_analyzed:,}`", inline=True)
    e2.add_field(name="Comments Analyzed", value=f"`{stats.comments_analyzed:,}`", inline=True)
    e2.add_field(name="\u200b", value="\u200b", inline=True)

    e2.add_field(
        name="Activity Rate",
        value=(
            f"Posts/day: `{stats.posts_per_day:.2f}`\n"
            f"Comments/day: `{stats.comments_per_day:.2f}`"
        ),
        inline=True,
    )
    e2.add_field(
        name="Avg Scores",
        value=(
            f"Post score: `{stats.avg_post_score:.1f}`\n"
            f"Comment score: `{stats.avg_comment_score:.1f}`"
        ),
        inline=True,
    )

    if stats.active_hours:
        hours_str = " → ".join(f"{h:02d}:00" for h in stats.active_hours[:5])
        e2.add_field(name="🕐 Peak Activity (UTC)", value=f"`{hours_str}`", inline=False)

    if stats.top_posts:
        top = stats.top_posts[0]
        e2.add_field(
            name="Best Post",
            value=f"**{top.get('title', 'N/A')[:60]}**\n"
                  f"Score: `{top.get('score', 0):,}` · r/{top.get('subreddit', '')}",
            inline=False,
        )
    if stats.top_comments:
        top_c = stats.top_comments[0]
        e2.add_field(
            name="Best Comment",
            value=f"*{(top_c.get('body', '') or '')[:80]}...*\n"
                  f"Score: `{top_c.get('score', 0):,}` · r/{top_c.get('subreddit', '')}",
            inline=False,
        )

    e2.set_footer(text="Page 2/4 — Activity Metrics")
    embeds.append(e2)

    # ── Page 3: Community Participation ──────────────────────────────
    e3 = discord.Embed(
        title=f"Community Participation — u/{user.username}",
        color=COLOR_GOLD,
    )

    if stats.top_subreddits:
        lines = []
        for i, sub in enumerate(stats.top_subreddits[:8], 1):
            lines.append(
                f"`{i}.` **r/{sub.name}** — "
                f"{sub.post_count}p · {sub.comment_count}c · "
                f"score: {sub.total_score:,}"
            )
        e3.add_field(
            name="Top Communities",
            value="\n".join(lines),
            inline=False,
        )
    else:
        e3.add_field(name="Top Communities", value="No data available", inline=False)

    e3.set_footer(text="Page 3/4 — Community Participation")
    embeds.append(e3)

    # ── Page 4: AI Summary ────────────────────────────────────────────
    e4 = discord.Embed(
        title=f"AI Behavioral Summary — u/{user.username}",
        color=COLOR_GREY,
    )
    e4.description = stats.ai_summary or "*AI summary unavailable.*"
    e4.set_footer(text="Page 4/4 — AI Behavioral Summary · Powered by OpenAI")
    embeds.append(e4)

    return embeds


class UserReportView(discord.ui.View):
    """Paginated view with Prev/Next buttons for user report."""

    def __init__(self, embeds: list[discord.Embed]) -> None:
        super().__init__(timeout=180)
        self.embeds = embeds
        self.current = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_btn.disabled = self.current == 0
        self.next_btn.disabled = self.current == len(self.embeds) - 1

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, custom_id="user_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="user_next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current], view=self)
