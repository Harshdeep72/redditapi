"""
Discord embed builder for risk assessment reports.
"""

from __future__ import annotations

import discord

from analyzers.risk_analyzer import RiskReport

COLOR_GREEN = 0x57F287
COLOR_YELLOW = 0xFEE75C
COLOR_ORANGE = 0xF47B2A
COLOR_RED = 0xED4245
COLOR_DARK = 0x2F3136


def _score_color(score: float) -> int:
    if score <= 2.5:
        return COLOR_GREEN
    if score <= 5.0:
        return COLOR_YELLOW
    if score <= 7.5:
        return COLOR_ORANGE
    return COLOR_RED


def _score_bar(score: float) -> str:
    """Visual bar: 0-10 scale, 20 segments."""
    filled = int(score / 10 * 20)
    return "█" * filled + "░" * (20 - filled)


def build_risk_embed(report: RiskReport) -> discord.Embed:
    embed = discord.Embed(
        title=f"{report.verdict_emoji} Risk Assessment — u/{report.username}",
        color=_score_color(report.total_score),
        url=f"https://reddit.com/user/{report.username}",
    )

    # Score display
    bar = _score_bar(report.total_score)
    embed.add_field(
        name="Risk Score",
        value=f"```{bar}```\n**{report.total_score:.1f} / 10** — {report.verdict}",
        inline=False,
    )

    # Factor breakdown
    factor_lines = []
    for f in report.factors:
        pct = int(f.score * 100)
        bar_short = "█" * int(f.score * 5) + "░" * (5 - int(f.score * 5))
        factor_lines.append(f"`{bar_short}` **{f.name}** ({pct}%): {f.description}")
    embed.add_field(
        name="Factor Breakdown",
        value="\n".join(factor_lines),
        inline=False,
    )

    embed.set_footer(text="Risk score is algorithmic and may not reflect all context.")
    return embed
