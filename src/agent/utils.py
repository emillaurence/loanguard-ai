"""Shared utilities for all agent classes."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import anthropic

from src.agent.config import MAX_RETRY_SECONDS, TOOL_RESULT_CHAR_LIMIT

logger = logging.getLogger(__name__)


def call_claude_with_retry(
    client: anthropic.Anthropic, *, label: str = "", **kwargs: Any
) -> anthropic.types.Message:
    """Call client.messages.create with up to 3 attempts on RateLimitError.

    Reads the retry-after response header when available; falls back to
    capped exponential backoff (30s, 60s).

    Logs one INFO line per successful call:
        <model> <label|stop_reason> <elapsed>s | in=N out=N cached=N
    """
    for attempt in range(3):
        try:
            t0 = time.perf_counter()
            response = client.messages.create(**kwargs)
            elapsed = time.perf_counter() - t0
            usage = response.usage
            cached = getattr(usage, "cache_read_input_tokens", 0) or 0
            tag = label or response.stop_reason or ""
            logger.info(
                "%s %s %.2fs | in=%d out=%d cached=%d",
                response.model, tag, elapsed,
                usage.input_tokens, usage.output_tokens, cached,
            )
            return response
        except anthropic.RateLimitError as e:
            if attempt < 2:
                retry_after = None
                try:
                    h = getattr(e, "response", None) and getattr(e.response, "headers", None)
                    if h:
                        retry_after = h.get("retry-after")
                        if retry_after is not None:
                            retry_after = min(int(float(retry_after)), MAX_RETRY_SECONDS)
                except (TypeError, ValueError):
                    pass
                wait = retry_after if retry_after is not None else min(30 * (2 ** attempt), MAX_RETRY_SECONDS)
                logger.warning("Rate limited — waiting %ds (attempt %d/3)", wait, attempt + 1)
                time.sleep(wait)
            else:
                raise


def truncate_tool_result(content: str) -> str:
    """Truncate a tool result string to TOOL_RESULT_CHAR_LIMIT characters."""
    if len(content) > TOOL_RESULT_CHAR_LIMIT:
        return content[:TOOL_RESULT_CHAR_LIMIT] + "… [truncated]"
    return content


def extract_field(text: str, pattern: str, default: str = "") -> str:
    """Return the first capture group of pattern matched against text, or default."""
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1).strip() if m else default


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
