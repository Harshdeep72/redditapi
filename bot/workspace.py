"""
Dynamic workspace manager.
Creates and configures private workspace channels for server verification.
"""

from __future__ import annotations

import logging
import discord
from typing import Any

logger = logging.getLogger(__name__)

CATEGORY_NAME = "WORKSPACES"


async def get_or_create_workspace_category(guild: discord.Guild) -> discord.CategoryChannel:
    """Find or create the parent WORKSPACES category channel."""
    # Find existing category
    for cat in guild.categories:
        if cat.name.upper() == CATEGORY_NAME:
            return cat

    # Create new category
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
    }
    logger.info("Creating parent WORKSPACES category in guild %s", guild.name)
    return await guild.create_category(name=CATEGORY_NAME, overwrites=overwrites)


async def get_or_create_workspace_channel(
    guild: discord.Guild, member: discord.Member
) -> discord.TextChannel:
    """
    Find or create a private text channel visible only to that user and moderators/admins.
    Format: #workspace-{username}
    """
    category = await get_or_create_workspace_category(guild)
    channel_name = f"workspace-{member.name.lower().replace(' ', '-')}"

    # Search for existing channel in the workspace category
    for chan in category.text_channels:
        if chan.name == channel_name:
            return chan

    # Configure private permissions
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member: discord.PermissionOverwrite(
            read_messages=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
        ),
    }

    # Automatically add permissions for administrators & moderators (members with moderate permissions)
    # We also inherit standard admin role permissions automatically, but explicit permissions are safer
    for role in guild.roles:
        if role.permissions.administrator or role.permissions.manage_channels:
            overwrites[role] = discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                read_message_history=True,
            )

    logger.info("Creating private workspace channel for member %s", member.name)
    return await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=f"Private earning workspace for u/{member.name}. Do not share link details.",
    )
