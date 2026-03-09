"""
InvestigationAgent — entity network exploration and risk signal surfacing.

Uses:
  read-neo4j-cypher  (Neo4j MCP) — Claude generates all traversal Cypher
  detect_graph_anomalies         (FastMCP) — anomaly pattern registry
  trace_evidence                 (FastMCP) — Layer 3 walkback

Claude generates all graph traversal Cypher itself using GRAPH_SCHEMA_HINT.
No separate text_to_cypher tool needed — Claude IS the text-to-Cypher engine.

Model: claude-sonnet-4-6  temperature=0
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import anthropic

from src.mcp.schema import GRAPH_SCHEMA_HINT, InvestigationResult

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8096
MAX_ITERATIONS = 10
# Keep at most this many tool-result round-trips in the message history.
# Each pair = one {"role":"assistant","content":[tool_use]} + one {"role":"user","content":[tool_result]}.
# Older pairs are dropped to prevent input token growth across iterations.
MAX_HISTORY_PAIRS = 4
_TOOL_RESULT_CHAR_LIMIT = 3000

SYSTEM_PROMPT = f"""You are a financial crimes investigator with expertise in
graph-based entity network analysis and AML/CTF investigations.

You have access to a Neo4j knowledge graph. Use the tools below to investigate
entities, surface connections, and identify risk signals.

## Tools available

Neo4j MCP (raw Cypher execution):
  read-neo4j-cypher: Execute any read Cypher query. YOU generate the Cypher.
    Use variable-length relationships for traversal: (a)-[:REL*1..3]->(b)
    Always LIMIT results (max 100 rows).
    IMPORTANT: When using variable-length paths [r*1..3], `r` is a
    List<Relationship> — do NOT call type(r) on it. Either use a single-hop
    [r] if you need type(r), or omit the relationship type from the RETURN.

FastMCP (domain tools):
  detect_graph_anomalies: Run named anomaly patterns (transaction_structuring,
    high_lvr_loans, high_risk_industry, layered_ownership, high_risk_jurisdiction,
    guarantor_concentration). Always run relevant patterns for the entity.
  trace_evidence: Retrieve prior assessment reasoning for an assessment_id.

## Investigation workflow

1. Start with a direct lookup of the entity (MATCH on borrower_id or loan_id).
2. Traverse first-degree relationships (accounts, loans, collateral, officers).
3. Run detect_graph_anomalies with pattern names relevant to the entity type.
4. Expand to second-degree connections if risk signals are found.
5. Check jurisdiction risk (RESIDES_IN / REGISTERED_IN → Jurisdiction.aml_risk_rating).
6. Check officer PEP/sanctions status (Officer.is_pep, Officer.sanctions_match).
7. Summarise: list risk signals, entity connections, and recommended actions.

## Output format
Structure your final answer as:

ENTITY: <entity_id> (<entity_type>)
RISK SIGNALS: <numbered list — each starts with [HIGH], [MEDIUM], or [LOW]>
CONNECTIONS: <describe key relationship chains>
ANOMALIES FOUND: <list pattern names and finding counts, or 'none'>
RECOMMENDED NEXT STEPS: <numbered list>

{GRAPH_SCHEMA_HINT}
"""


class InvestigationAgent:
    """
    Entity network investigation agent.

    Usage:
        agent = InvestigationAgent(tools=mcp_tools, execute_tool_fn=dispatcher)
        result = agent.run("Show connections around BRW-0001")
    """

    def __init__(
        self,
        tools: list[dict],
        execute_tool_fn: Any,
        model: str = MODEL,
        max_tokens: int = MAX_TOKENS,
    ) -> None:
        self.tools = tools
        self.execute_tool = execute_tool_fn
        self.model = model
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def run(self, question: str) -> InvestigationResult:
        """
        Run the investigation agentic loop.

        Args:
            question: Natural-language investigation question.

        Returns:
            InvestigationResult with connections, risk signals, and cypher used.
        """
        messages: list[dict] = [{"role": "user", "content": question}]
        iterations = 0
        cypher_used: list[str] = []
        final_text = ""

        logger.info("InvestigationAgent: %s", question)

        while iterations < MAX_ITERATIONS:
            iterations += 1
            for attempt in range(3):
                try:
                    response = self.client.messages.create(
                        model=self.model,
                        max_tokens=self.max_tokens,
                        system=[{"type": "text", "text": SYSTEM_PROMPT,
                                 "cache_control": {"type": "ephemeral"}}],
                        tools=self.tools,
                        messages=messages,
                        temperature=0,
                    )
                    break
                except anthropic.RateLimitError as e:
                    if attempt < 2:
                        # Prefer API retry-after; fallback to capped exponential backoff (30s, 60s)
                        retry_after = None
                        try:
                            h = getattr(e, "response", None) and getattr(e.response, "headers", None)
                            if h:
                                retry_after = h.get("retry-after")
                                if retry_after is not None:
                                    retry_after = min(int(float(retry_after)), 120)
                        except (TypeError, ValueError):
                            pass
                        wait = retry_after if retry_after is not None else min(30 * (2**attempt), 120)
                        logger.warning("Rate limited — waiting %ds (attempt %d/3)", wait, attempt + 1)
                        time.sleep(wait)
                    else:
                        raise

            if response.stop_reason == "end_turn":
                final_text = self._extract_text(response)
                break

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info("Tool: %s(%s)", block.name, list(block.input.keys()))
                        if block.name == "read-neo4j-cypher" and block.input.get("query"):
                            cypher_used.append(block.input["query"])
                        result = self.execute_tool(block.name, block.input)
                        content = json.dumps(result, default=str)
                        if len(content) > _TOOL_RESULT_CHAR_LIMIT:
                            content = content[:_TOOL_RESULT_CHAR_LIMIT] + "… [truncated — use a more specific query]"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": content,
                        })
                messages.append({"role": "user", "content": tool_results})

                # Trim history to MAX_HISTORY_PAIRS to prevent input token growth.
                # Always keep messages[0] (initial user question) + the last N pairs.
                # Must preserve tool_use/tool_result pairs to avoid API errors.
                max_msgs = 1 + MAX_HISTORY_PAIRS * 2
                if len(messages) > max_msgs:
                    tail = messages[-(MAX_HISTORY_PAIRS * 2):]
                    # If tail starts with a user/tool_result, its assistant/tool_use was
                    # trimmed off — drop it to avoid orphaned tool_result blocks.
                    if tail[0].get("role") == "user":
                        tail = tail[1:]
                    messages = [messages[0]] + tail

                continue

            logger.warning("Unexpected stop_reason: %s", response.stop_reason)
            break

        return self._parse_result(final_text, cypher_used)

    @staticmethod
    def _extract_text(response: anthropic.types.Message) -> str:
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""

    @staticmethod
    def _parse_result(text: str, cypher_used: list[str]) -> InvestigationResult:
        import re

        def _clean(s: str) -> str:
            """Strip markdown bold/italic markers and surrounding whitespace."""
            return s.strip().strip("*").strip()

        def _section(header: str) -> str:
            m = re.search(rf"{header}:\s*(.+?)(?=\n[A-Z ]+:|$)", text, re.DOTALL | re.IGNORECASE)
            return m.group(1).strip() if m else ""

        # ENTITY: **BRW-0001** (Borrower) → strip ** and take first token
        # Entity ID: try ENTITY: line first, then fall back to first entity ID in text
        entity_match = re.search(r"ENTITY:\s*\*{0,2}\s*([\w-]+)", text, re.IGNORECASE)
        if entity_match:
            entity_id = _clean(entity_match.group(1))
        else:
            id_match = re.search(r"\b((?:BRW|LOAN|ACC|JUR)-\d+)\b", text)
            entity_id = id_match.group(1) if id_match else ""

        # Risk signals: strip trailing ** markdown artifacts from each line
        raw_signals = re.findall(r"\[(?:HIGH|MEDIUM|LOW)\][^\n]+", text, re.IGNORECASE)
        risk_signals = [re.sub(r"\*+$", "", s).rstrip() for s in raw_signals]

        # Connections: try section parse; fall back to CONNECTIONS: line only
        connections_text = _section("CONNECTIONS")
        if not connections_text:
            m = re.search(r"CONNECTIONS:\s*(.+)", text, re.IGNORECASE)
            connections_text = m.group(1).strip() if m else ""

        # If final_text was empty (MAX_ITERATIONS hit mid tool_use), surface a note
        if not text and cypher_used:
            risk_signals = ["[INFO] Investigation incomplete — max iterations reached. "
                            f"{len(cypher_used)} Cypher queries were executed."]

        return InvestigationResult(
            entity_id=entity_id,
            entity_type="",
            connections=[{"description": connections_text}] if connections_text else [],
            risk_signals=risk_signals,
            path_summaries=[],
            cypher_used=cypher_used,
        )
