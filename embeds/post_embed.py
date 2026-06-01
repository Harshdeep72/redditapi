"""
Discord embed builder for post analysis reports.
"""

from __future__ import annotations

import discord

from models.post import PostAnalysis

COLOR_PURPLE = 0x9B59B6
COLOR_BLUE = 0x5865F2


def _sentiment_bar(breakdown: dict[str, float]) -> str:
    pos = int(breakdown.get("Positive", 0) * 20)
    neu = int(breakdown.get("Neutral", 0) * 20)
    neg = int(breakdown.get("Negative", 0) * 20)
    return "[" + "+" * pos + " " * neu + "-" * neg + "]"


def build_post_embeds(analysis: PostAnalysis) -> list[discord.Embed]:
    post = analysis.post
    embeds: list[discord.Embed] = []

    # ── Page 1: Post Stats ───────────────────────────────────────────
    e1 = discord.Embed(
        title=f"{post.title[:100]}",
        color=COLOR_PURPLE,
        url=f"https://reddit.com{post.permalink}" if post.permalink else None,
    )

    e1.add_field(name="Author", value=f"u/{post.author}", inline=True)
    e1.add_field(name="Subreddit", value=f"r/{post.subreddit}", inline=True)
    e1.add_field(name="Posted", value=f"<t:{int(post.created_utc)}:R>", inline=True)
    e1.add_field(name="Score", value=f"`{post.score:,}`", inline=True)
    e1.add_field(name="Upvote Ratio", value=f"`{post.upvote_ratio:.1%}`", inline=True)
    e1.add_field(name="Awards", value=f"`{post.awards}`", inline=True)
    e1.add_field(name="Comments", value=f"`{post.num_comments:,}`", inline=True)
    e1.add_field(name="Comment Velocity", value=f"`{analysis.comment_velocity:.1f}` /hour", inline=True)
    e1.add_field(name="Engagement Rate", value=f"`{analysis.engagement_rate:.1f}`", inline=True)

    if post.selftext:
        preview = post.selftext[:400].replace("\n", " ")
        e1.add_field(name="Post Body", value=f"*{preview}...*" if len(post.selftext) > 400 else f"*{post.selftext}*", inline=False)

    e1.set_footer(text="Page 1/2 — Post Stats")
    embeds.append(e1)

    # ── Page 2: AI Analysis ───────────────────────────────────────────
    e2 = discord.Embed(
        title=f"AI Analysis — Post Report",
        color=COLOR_BLUE,
    )

    if analysis.sentiment_breakdown:
        bar = _sentiment_bar(analysis.sentiment_breakdown)
        breakdown = analysis.sentiment_breakdown
        e2.add_field(
            name="Sentiment Breakdown",
            value=(
                f"{bar}\n"
                f"Positive: `{breakdown.get('Positive', 0):.0%}` | "
                f"Neutral: `{breakdown.get('Neutral', 0):.0%}` | "
                f"Negative: `{breakdown.get('Negative', 0):.0%}`"
            ),
            inline=False,
        )

    if analysis.ai_thread_summary:
        e2.add_field(name="Summary", value=analysis.ai_thread_summary, inline=False)

    if analysis.ai_key_arguments:
        args = "\n".join(f"• {a}" for a in analysis.ai_key_arguments[:5])
        e2.add_field(name="Key Arguments", value=args, inline=False)

    if analysis.ai_consensus:
        e2.add_field(name="Community Consensus", value=analysis.ai_consensus, inline=False)

    e2.set_footer(text="Page 2/2 — AI Analysis · Powered by OpenAI")
    embeds.append(e2)

    return embeds


class PostReportView(discord.ui.View):
    def __init__(self, embeds: list[discord.Embed]) -> None:
        super().__init__(timeout=120)
        self.embeds = embeds
        self.current = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_btn.disabled = self.current == 0
        self.next_btn.disabled = self.current == len(self.embeds) - 1

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, custom_id="post_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="post_next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current], view=self)
