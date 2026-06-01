"""
Sentiment and tone classification for Reddit comments.
"""

from __future__ import annotations

import logging
from typing import Any

from ai.client import chat_json
from ai.prompts import SENTIMENT_BATCH_PROMPT, SYSTEM_SENTIMENT

logger = logging.getLogger(__name__)

SENTIMENT_VALUES = {"Positive", "Neutral", "Negative"}
TONE_VALUES = {"Helpful", "Aggressive", "Sarcastic", "Informative", "Humorous"}


async def classify_sentiment_batch(
    comments: list[str],
    batch_size: int = 30,
) -> list[str]:
    """
    Classify sentiment for a list of comment strings.
    Returns a list of 'Positive'|'Neutral'|'Negative' values.
    Falls back to 'Neutral' for each comment if AI is unavailable.
    """
    results: list[str] = ["Neutral"] * len(comments)

    for i in range(0, len(comments), batch_size):
        batch = comments[i : i + batch_size]
        numbered = "\n".join(f"{j+1}. {text[:300]}" for j, text in enumerate(batch))
        prompt = SENTIMENT_BATCH_PROMPT.format(comments=numbered)

        data = await chat_json(SYSTEM_SENTIMENT, prompt, max_tokens=512)
        if isinstance(data, list):
            for j, item in enumerate(data):
                if i + j < len(results):
                    sentiment = item.get("sentiment", "Neutral")
                    if sentiment not in SENTIMENT_VALUES:
                        sentiment = "Neutral"
                    results[i + j] = sentiment

    return results


def compute_sentiment_breakdown(sentiments: list[str]) -> dict[str, float]:
    """Convert a list of sentiment strings to a percentage breakdown."""
    if not sentiments:
        return {"Positive": 0.33, "Neutral": 0.34, "Negative": 0.33}

    counts = {"Positive": 0, "Neutral": 0, "Negative": 0}
    for s in sentiments:
        if s in counts:
            counts[s] += 1

    total = len(sentiments)
    return {k: round(v / total, 3) for k, v in counts.items()}
