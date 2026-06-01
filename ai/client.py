"""
OpenAI client stub — disabled at the user's request.
Bypasses all AI features and returns fallback values instantly with zero latency or external calls.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _get_client() -> Any:
    return None


async def chat_completion(*args: Any, **kwargs: Any) -> str | None:
    """Always return None immediately to activate fast non-AI fallbacks."""
    return None


async def chat_json(*args: Any, **kwargs: Any) -> dict[str, Any] | list[Any] | None:
    """Always return None immediately to activate fast non-AI fallbacks."""
    return None
