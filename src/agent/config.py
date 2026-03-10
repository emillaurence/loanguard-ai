"""
Shared configuration constants for all agents.

Import from here instead of redefining in each agent module.
"""

from __future__ import annotations

import os

import anthropic

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8096

# Maximum characters serialised per tool result before truncation.
TOOL_RESULT_CHAR_LIMIT = 3000

# Write-operation keywords blocked in the read-neo4j-cypher dispatcher.
# Whole-word matched (uppercase) to avoid false positives like ASSESSMENT
# containing SET, or DETACHMENT containing DETACH.
WRITE_KEYWORDS: frozenset[str] = frozenset({"MERGE", "CREATE", "DELETE", "SET", "DETACH", "REMOVE", "DROP"})


def make_anthropic_client() -> anthropic.Anthropic:
    """Return a configured Anthropic client using ANTHROPIC_API_KEY from the environment."""
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
