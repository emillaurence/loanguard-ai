"""
Prompt injection defence for agent tool result handling.

Tool results from Neo4j or external APIs may contain attacker-controlled
strings (e.g. a borrower name of "Ignore previous instructions and...").
This module provides a single guard function applied to every tool result
before it is appended to the agent message history.

Defences applied:
  1. Structural framing — wraps content in [TOOL DATA] tags so Claude's
     context makes clear the content is external data, not instructions.
  2. Pattern detection — logs a WARNING if common injection patterns are
     found. Does not redact (to avoid breaking legitimate results) but
     creates an audit trail.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Conservative list of multi-word patterns that are unlikely to appear in
# legitimate financial or regulatory data but are common in injection attempts.
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(your\s+)?(previous\s+)?instructions",
        r"forget\s+(your\s+)?instructions",
        r"new\s+system\s+prompt",
        r"you\s+are\s+now\s+a",
        r"act\s+as\s+a\s+different",
        r"override\s+your\s+(instructions|system|prompt)",
        r"do\s+not\s+follow\s+your",
        r"your\s+new\s+instructions\s+are",
    ]
]


def guard_tool_result(content: str, tool_name: str = "") -> str:
    """
    Apply prompt injection defences to a tool result string.

    Wraps the content in structural [TOOL DATA] framing and logs a warning
    if any known injection pattern is detected.

    Args:
        content:   The raw JSON-serialised tool result string.
        tool_name: Name of the tool that produced the result (for logging).

    Returns:
        Framed content string safe to append to agent message history.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(content):
            logger.warning(
                "Possible prompt injection detected in tool result from '%s'. "
                "Pattern matched: '%s'. Content excerpt: %.200s",
                tool_name or "unknown",
                pattern.pattern,
                content,
            )

    label = f"TOOL DATA — {tool_name}" if tool_name else "TOOL DATA"
    return f"[{label}]\n{content}\n[END TOOL DATA]"
