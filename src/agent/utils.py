"""Shared utilities for all agent classes."""

from __future__ import annotations

import logging
import time
from typing import Any

import anthropic

logger = logging.getLogger(__name__)


def call_claude_with_retry(client: anthropic.Anthropic, **kwargs: Any) -> anthropic.types.Message:
    """Call client.messages.create with up to 3 attempts on RateLimitError.

    Reads the retry-after response header when available; falls back to
    capped exponential backoff (30s, 60s).
    """
    for attempt in range(3):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            if attempt < 2:
                retry_after = None
                try:
                    h = getattr(e, "response", None) and getattr(e.response, "headers", None)
                    if h:
                        retry_after = h.get("retry-after")
                        if retry_after is not None:
                            retry_after = min(int(float(retry_after)), 120)
                except (TypeError, ValueError):
                    pass
                wait = retry_after if retry_after is not None else min(30 * (2 ** attempt), 120)
                logger.warning("Rate limited — waiting %ds (attempt %d/3)", wait, attempt + 1)
                time.sleep(wait)
            else:
                raise


def extract_text(response: anthropic.types.Message) -> str:
    """Return the first text block from a Claude response, or empty string."""
    for block in response.content:
        if hasattr(block, "text"):
            return block.text
    return ""


def trim_message_history(messages: list[dict], max_pairs: int) -> list[dict]:
    """Trim message history to at most max_pairs tool-use/tool-result round-trips.

    Always preserves messages[0] (the initial user question).
    Drops orphaned tool_result blocks that lost their assistant/tool_use pair.
    """
    max_msgs = 1 + max_pairs * 2
    if len(messages) <= max_msgs:
        return messages
    tail = messages[-(max_pairs * 2):]
    if tail[0].get("role") == "user":
        tail = tail[1:]
    return [messages[0]] + tail
