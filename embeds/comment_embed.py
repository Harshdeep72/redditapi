"""
Discord embed builder for comment analysis reports.
"""

from __future__ import annotations

import discord

from models.comment import CommentAnalysis

COLOR_BLUE = 0x5865F2
COLOR_GREEN = 0x57F287
COLOR_RED = 0xED4245
COLOR_YELLOW = 0xFEE75C


SENTIMENT_COLORS = {
    "Positive": COLOR_GREEN,
    "Neutral": COLOR_BLUE,
    "Negative": COLOR_RED,
}

TONE_EMOJIS = {
    "Helpful": "",
    "Aggressive": "",
    "Sarcastic": "",
    "Informative": "",
    "Humorous": "",
}


def _toxicity_bar(score: float) -> str:
    filled = int(score * 10)
    return "[" + "x" * filled + "." * (10 - filled) + "]"


def _constructiveness_bar(score: float) -> str:
    filled = int(score * 10)
    return "[" + "#" * filled + "." * (10 - filled) + "]"


def build_comment_embed(analysis: CommentAnalysis) -> discord.Embed:
    c = analysis.comment
    color = SENTIMENT_COLORS.get(analysis.sentiment, COLOR_BLUE)

    embed = discord.Embed(
        title=f"Comment Analysis — u/{c.author}",
        color=color,
        url=f"https://reddit.com{c.permalink}" if c.permalink else None,
    )

    # Comment body (truncated)
    body_preview = (c.body or "")[:300]
    if len(c.body or "") > 300:
        body_preview += "..."
    embed.description = f"> {body_preview}"

    # Meta info
    embed.add_field(name="Author", value=f"u/{c.author}", inline=True)
    embed.add_field(name="Score", value=f"`{c.score:,}`", inline=True)
    embed.add_field(name="Awards", value=f"`{c.awards}`", inline=True)
    embed.add_field(name="Posted", value=f"<t:{int(c.created_utc)}:R>", inline=True)
    embed.add_field(name="Subreddit", value=f"r/{c.subreddit}", inline=True)
    embed.add_field(name="Depth", value=f"`{c.depth}` (level in thread)", inline=True)
    embed.add_field(name="Replies", value=f"`{c.num_replies}`", inline=True)

    # Context
    if analysis.post_title:
        embed.add_field(
            name="🔗 Parent Post",
            value=f"**{analysis.post_title[:80]}**\nby u/{analysis.post_author} in r/{analysis.post_subreddit}",
            inline=False,
        )
    if analysis.parent_comment_body:
        embed.add_field(
            name="Replying to",
            value=f"u/{analysis.parent_comment_author}: *{analysis.parent_comment_body[:100]}...*",
            inline=False,
        )

    # AI Analysis
    tone_emoji = TONE_EMOJIS.get(analysis.tone, "")
    embed.add_field(
        name="AI Analysis",
        value=(
            f"**Sentiment:** {analysis.sentiment}\n"
            f"**Tone:** {analysis.tone}\n"
            f"**Topic:** {analysis.topic or 'General'}"
        ),
        inline=True,
    )
    embed.add_field(
        name="Scores",
        value=(
            f"Toxicity: {_toxicity_bar(analysis.toxicity_score)} `{analysis.toxicity_score:.1%}`\n"
            f"Constructive: {_constructiveness_bar(analysis.constructiveness_score)} `{analysis.constructiveness_score:.1%}`"
        ),
        inline=False,
    )

    if analysis.ai_summary:
        embed.add_field(name="Summary", value=analysis.ai_summary, inline=False)

    embed.set_footer(text=f"Comment ID: {c.comment_id}")
    return embed
