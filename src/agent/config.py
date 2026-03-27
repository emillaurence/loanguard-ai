"""
Shared configuration constants for all agents.

Import from here instead of redefining in each agent module.
"""

from __future__ import annotations

import os

import anthropic

# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------
# Use MODEL_FAST for short structured-output calls (routing, NL->Cypher).
# Use MODEL_MAIN for multi-step agentic loops and synthesis.
MODEL_FAST = "claude-haiku-4-5-20251001"
MODEL_MAIN = "claude-sonnet-4-6"
MODEL = MODEL_MAIN  # backward-compat alias

MAX_TOKENS = 8096

# ---------------------------------------------------------------------------
# Per-call token budgets
# ---------------------------------------------------------------------------
ROUTING_MAX_TOKENS = 512
SYNTHESIS_MAX_TOKENS = 2048

# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------
TEMPERATURE = 0

# ---------------------------------------------------------------------------
# Prompt caching
# ---------------------------------------------------------------------------
CACHE_CONTROL_EPHEMERAL: dict = {"type": "ephemeral"}

# ---------------------------------------------------------------------------
# Tool result handling
# ---------------------------------------------------------------------------
# Maximum characters serialised per tool result before truncation.
TOOL_RESULT_CHAR_LIMIT = 3000
# Raised limit for pre-run injected results (traverse, anomaly) so their content
# exceeds the 1024-token Anthropic minimum required for cache checkpoints to fire.
PRE_RUN_RESULT_CHAR_LIMIT = 4096

# ---------------------------------------------------------------------------
# Retry backoff
# ---------------------------------------------------------------------------
MAX_RETRY_SECONDS = 120

# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
EMBEDDING_MODEL = "text-embedding-3-small"

# ---------------------------------------------------------------------------
# Agent loop limits
# ---------------------------------------------------------------------------
COMPLIANCE_MAX_ITERATIONS = 14
COMPLIANCE_MAX_HISTORY_PAIRS = 4

INVESTIGATION_MAX_ITERATIONS = 14
INVESTIGATION_MAX_HISTORY_PAIRS = 6

# ---------------------------------------------------------------------------
# Write-operation guard
# ---------------------------------------------------------------------------
# Whole-word matched (uppercase) to avoid false positives like ASSESSMENT
# containing SET, or DETACHMENT containing DETACH.
WRITE_KEYWORDS: frozenset[str] = frozenset({"MERGE", "CREATE", "DELETE", "SET", "DETACH", "REMOVE", "DROP"})


def make_anthropic_client() -> anthropic.Anthropic:
    """Return a configured Anthropic client using ANTHROPIC_API_KEY from the environment."""
    return anthropic.Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        default_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
    )
