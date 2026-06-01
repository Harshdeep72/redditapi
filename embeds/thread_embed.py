"""
Discord embed builder for thread intelligence reports.
"""

from __future__ import annotations

import discord

from models.thread import ThreadAnalysis

COLOR_TEAL = 0x1ABC9C
COLOR_ORANGE = 0xE67E22
COLOR_BLUE = 0x5865F2


def _sentiment_bar(breakdown: dict[str, float]) -> str:
    pos = int(breakdown.get("Positive", 0) * 20)
    neu = int(breakdown.get("Neutral", 0) * 20)
    neg = int(breakdown.get("Negative", 0) * 20)
    return "[" + "+" * pos + " " * neu + "-" * neg + "]"


def build_thread_embeds(analysis: ThreadAnalysis) -> list[discord.Embed]:
    post = analysis.post
    embeds: list[discord.Embed] = []

    # ── Page 1: Thread Overview ───────────────────────────────────────
    e1 = discord.Embed(
        title=f"Thread Intelligence",
        description=f"**{post.title[:120]}**",
        color=COLOR_TEAL,
        url=f"https://reddit.com{post.permalink}" if post.permalink else None,
    )
    e1.add_field(name="Subreddit", value=f"r/{post.subreddit}", inline=True)
    e1.add_field(name="Posted by", value=f"u/{post.author}", inline=True)
    e1.add_field(name="Posted", value=f"<t:{int(post.created_utc)}:R>", inline=True)
    e1.add_field(name="Score", value=f"`{post.score:,}`", inline=True)
    e1.add_field(name="Upvote Ratio", value=f"`{post.upvote_ratio:.1%}`", inline=True)
    e1.add_field(name="Awards", value=f"`{post.awards}`", inline=True)
    e1.add_field(name="Total Comments", value=f"`{analysis.total_comments:,}`", inline=True)
    e1.add_field(name="Unique Participants", value=f"`{analysis.unique_participants:,}`", inline=True)
    e1.add_field(name="Max Thread Depth", value=f"`{analysis.max_depth}` levels", inline=True)

    e1.set_footer(text="Page 1/3 — Thread Overview")
    embeds.append(e1)

    # ── Page 2: Top Comments & Participants ───────────────────────────
    e2 = discord.Embed(
        title="Top Comments & Participants",
        color=COLOR_ORANGE,
    )

    if analysis.top_comments:
        lines = []
        for i, c in enumerate(analysis.top_comments[:5], 1):
            author = c.get("author", "unknown")
            score = c.get("score", 0)
            body = (c.get("body", "") or "")[:80].replace("\n", " ")
            lines.append(f"`{i}.` **u/{author}** (+{score:,}): {body}...")
        e2.add_field(name="⬆ Most Upvoted", value="\n".join(lines), inline=False)

    if analysis.controversial_comments:
        lines = []
        for i, c in enumerate(analysis.controversial_comments[:3], 1):
            author = c.get("author", "unknown")
            score = c.get("score", 0)
            body = (c.get("body", "") or "")[:60].replace("\n", " ")
            lines.append(f"`{i}.` u/{author} ({score:,}): {body}...")
        e2.add_field(name="Controversial", value="\n".join(lines), inline=False)

    if analysis.top_participants:
        lines = []
        for i, p in enumerate(analysis.top_participants[:8], 1):
            lines.append(
                f"`{i}.` **u/{p.username}** — "
                f"{p.comment_count} comments, avg score: `{p.avg_score}`"
            )
        e2.add_field(name="Most Active Participants", value="\n".join(lines), inline=False)

    e2.set_footer(text="Page 2/3 — Top Comments & Participants")
    embeds.append(e2)

    # ── Page 3: AI Analysis ───────────────────────────────────────────
    e3 = discord.Embed(title="AI Thread Analysis", color=COLOR_BLUE)

    if analysis.sentiment_breakdown:
        bar = _sentiment_bar(analysis.sentiment_breakdown)
        bd = analysis.sentiment_breakdown
        e3.add_field(
            name="Sentiment Breakdown",
            value=(
                f"{bar}\n"
                f"Positive: `{bd.get('Positive', 0):.0%}` | "
                f"Neutral: `{bd.get('Neutral', 0):.0%}` | "
                f"Negative: `{bd.get('Negative', 0):.0%}`"
            ),
            inline=False,
        )

    if analysis.ai_summary:
        e3.add_field(name="Thread Summary", value=analysis.ai_summary, inline=False)

    if analysis.ai_key_arguments:
        args = "\n".join(f"• {a}" for a in analysis.ai_key_arguments[:5])
        e3.add_field(name="Key Arguments", value=args, inline=False)

    if analysis.ai_consensus:
        e3.add_field(name="Community Consensus", value=analysis.ai_consensus, inline=False)

    if analysis.ai_main_opinions:
        ops = "\n".join(f"• {o}" for o in analysis.ai_main_opinions[:4])
        e3.add_field(name="Main Opinions", value=ops, inline=False)

    e3.set_footer(text="Page 3/3 — AI Analysis · Powered by OpenAI")
    embeds.append(e3)

    return embeds


class ThreadReportView(discord.ui.View):
    def __init__(self, embeds: list[discord.Embed]) -> None:
        super().__init__(timeout=180)
        self.embeds = embeds
        self.current = 0
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_btn.disabled = self.current == 0
        self.next_btn.disabled = self.current == len(self.embeds) - 1

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, custom_id="thread_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, custom_id="thread_next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.current += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current], view=self)
