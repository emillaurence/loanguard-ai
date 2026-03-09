"""
ComplianceAgent — assesses LoanApplications and Borrowers against APRA regulations.

Uses two MCP tools:
  traverse_compliance_path  (FastMCP) — cross-layer L1→L2 subgraph
  read-neo4j-cypher         (Neo4j MCP) — ad-hoc Cypher for specific checks

Claude generates all Cypher itself (text-to-Cypher is native, not a separate tool).
Persists findings to Layer 3 via persist_assessment (FastMCP).

Model: claude-sonnet-4-6  temperature=0
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, TYPE_CHECKING

import anthropic

from src.mcp.schema import GRAPH_SCHEMA_HINT, ComplianceResult

if TYPE_CHECKING:
    from src.graph.connection import Neo4jConnection

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8096
MAX_ITERATIONS = 8
MAX_HISTORY_PAIRS = 4
_TOOL_RESULT_CHAR_LIMIT = 3000

SYSTEM_PROMPT = f"""You are a financial services compliance officer with expert knowledge
of APRA prudential standards (APS-112, APG-223, APS-220).

You have access to a Neo4j knowledge graph and two categories of tools:

1. FastMCP tools (domain-specific):
   - traverse_compliance_path: Your PRIMARY tool. Call this first for any entity.
     Returns the full regulatory subgraph (Regulation→Section→Requirement→Threshold)
     applicable to the entity's jurisdiction and loan type.
   - retrieve_regulatory_chunks: Semantic search for regulatory text.
   - persist_assessment: Write your assessment to Layer 3 when complete.

2. Neo4j MCP tools (raw Cypher):
   - read-neo4j-cypher: Execute any Cypher query to check specific values
     or traverse relationships not covered by the above tools.

## Your workflow

For any compliance question:
1. Call traverse_compliance_path to get the applicable regulatory framework.
2. Use read-neo4j-cypher to check specific entity properties against thresholds
   (e.g. verify LVR, loan amount, interest rate from the LoanApplication node).
3. Optionally call retrieve_regulatory_chunks for supporting regulatory text.
4. Form a verdict: COMPLIANT | NON_COMPLIANT | REQUIRES_REVIEW.
5. Call persist_assessment to save your reasoning to Layer 3. For each reasoning
   step, populate section_ids with any section_id values returned by
   traverse_compliance_path or read-neo4j-cypher that informed that step, and
   populate chunk_ids with any chunk_id values returned by
   retrieve_regulatory_chunks that informed that step.
6. Return a structured final answer citing requirement_ids and threshold_ids.

## Key thresholds (for quick reference)
- APG-223-THR-003: serviceability buffer >= 3.0% over loan rate
- APG-223-THR-006: non-salary income haircut >= 20%
- APG-223-THR-008: LVR >= 90% requires senior management review
- APS-112-THR-032: LMI must cover >= 40% of loan for capital relief

## Output format
Always conclude with:
VERDICT: <verdict>
CONFIDENCE: <0.0-1.0>
REQUIREMENTS CHECKED: <comma-separated requirement_ids>
THRESHOLDS BREACHED: <comma-separated threshold_ids or 'none'>
RECOMMENDED NEXT STEPS: <numbered list>

{GRAPH_SCHEMA_HINT}
"""


class ComplianceAgent:
    """
    Compliance assessment agent for LoanApplications and Borrowers.

    Usage:
        agent = ComplianceAgent(tools=mcp_tools, execute_tool_fn=dispatcher)
        result = agent.run("Is LOAN-0002 compliant with APG-223?")
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

    def run(self, question: str) -> ComplianceResult:
        """
        Run the compliance assessment agentic loop.

        Args:
            question: Natural-language compliance question.

        Returns:
            ComplianceResult with verdict, cited IDs, and assessment_id.
        """
        messages: list[dict] = [{"role": "user", "content": question}]
        iterations = 0
        cypher_used: list[str] = []
        final_text = ""
        assessment_id: str | None = None
        assessment_ids: list[str] = []
        persisted_findings: list[dict] = []
        seen_section_ids: set[str] = set()
        seen_chunk_ids: set[str] = set()

        logger.info("ComplianceAgent: %s", question)

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
                messages.append({"role": "assistant", "content": self._blocks_to_dicts(response.content)})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info("Tool: %s(%s)", block.name, list(block.input.keys()))
                        # Track Cypher for transparency panel
                        if block.name == "read-neo4j-cypher" and block.input.get("query"):
                            cypher_used.append(block.input["query"])
                        result = self.execute_tool(block.name, block.input)
                        # Accumulate assessment_ids and persisted findings from Layer 3.
                        # persist_assessment may be called once per regulation, so we
                        # extend rather than overwrite to capture findings from all regulations.
                        if block.name == "persist_assessment" and isinstance(result, dict):
                            aid = result.get("assessment_id")
                            if aid:
                                assessment_id = aid  # keep last for backward compat
                                if aid not in assessment_ids:
                                    assessment_ids.append(aid)
                            persisted_findings.extend(result.get("findings", []))
                        # Accumulate section/chunk IDs for the evidence tracker
                        self._extract_evidence_ids(block.name, result, seen_section_ids, seen_chunk_ids)
                        content = json.dumps(result, default=str)
                        if len(content) > _TOOL_RESULT_CHAR_LIMIT:
                            content = content[:_TOOL_RESULT_CHAR_LIMIT] + "… [truncated — use a more specific query]"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": content,
                        })
                # Append evidence tracker to the last tool_result content so IDs survive
                # history truncation. Must not add a separate text block — the API requires
                # the user message following tool_use to contain only tool_result blocks.
                if (seen_section_ids or seen_chunk_ids) and tool_results:
                    parts = []
                    if seen_section_ids:
                        parts.append(f"section_ids seen: {', '.join(sorted(seen_section_ids))}")
                    if seen_chunk_ids:
                        parts.append(f"chunk_ids seen: {', '.join(sorted(seen_chunk_ids))}")
                    tool_results[-1]["content"] += (
                        "\n\n[Evidence tracker] " + " | ".join(parts) +
                        " — populate the relevant IDs into section_ids / chunk_ids"
                        " of each reasoning_step when calling persist_assessment."
                    )
                messages.append({"role": "user", "content": tool_results})

                # Truncate history but preserve tool_use/tool_result pairs.
                # Messages alternate: user, assistant, user (tool_result), assistant, ...
                # We must keep pairs together to avoid API errors about missing tool_results.
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

        result = self._parse_result(final_text, cypher_used)
        result.assessment_id = assessment_id
        result.assessment_ids = assessment_ids
        result.persisted_findings = persisted_findings
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_evidence_ids(
        tool_name: str,
        result: dict,
        seen_section_ids: set[str],
        seen_chunk_ids: set[str],
    ) -> None:
        """Accumulate section_ids and chunk_ids from tool results into the running sets."""
        if not isinstance(result, dict):
            return
        if tool_name == "traverse_compliance_path":
            for reg in result.get("regulations", {}).values():
                for sec_id in reg.get("sections", {}).keys():
                    if sec_id:
                        seen_section_ids.add(sec_id)
        elif tool_name == "retrieve_regulatory_chunks":
            for chunk in result.get("chunks", []):
                if chunk.get("chunk_id"):
                    seen_chunk_ids.add(chunk["chunk_id"])
        elif tool_name == "read-neo4j-cypher":
            for row in result.get("rows", []):
                if row.get("section_id"):
                    seen_section_ids.add(row["section_id"])
                if row.get("chunk_id"):
                    seen_chunk_ids.add(row["chunk_id"])

    @staticmethod
    def _blocks_to_dicts(content) -> list[dict]:
        """Convert Anthropic SDK content blocks to plain dicts for stable serialisation."""
        result = []
        for block in content:
            if isinstance(block, dict):
                result.append(block)
            elif block.type == "text":
                result.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                result.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
            # skip any other block types (e.g. thinking) silently
        return result

    @staticmethod
    def _extract_text(response: anthropic.types.Message) -> str:
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return ""

    @staticmethod
    def _parse_result(text: str, cypher_used: list[str]) -> ComplianceResult:
        """Extract structured fields from Claude's final text response."""
        import re

        def _find(pattern: str, default: str = "") -> str:
            m = re.search(pattern, text, re.IGNORECASE)
            return m.group(1).strip() if m else default

        # Patterns tolerate markdown bold (`**VALUE**`) around values
        verdict = _find(r"VERDICT:\s*\*{0,2}\s*(\w+)", "INFORMATIONAL")
        confidence_str = _find(r"CONFIDENCE:\s*\*{0,2}\s*([\d.]+)", "0.5")
        reqs_str = _find(r"REQUIREMENTS CHECKED:\s*\*{0,2}\s*(.+)", "")
        thresh_str = _find(r"THRESHOLDS BREACHED:\s*\*{0,2}\s*(.+)", "")
        steps_raw = re.findall(r"^\d+\.\s+(.+)$", text, re.MULTILINE)

        confidence = float(confidence_str) if confidence_str else 0.5
        def _clean(s: str) -> str:
            return s.strip().strip("*").strip()

        requirement_ids = [_clean(r) for r in reqs_str.split(",") if _clean(r) and _clean(r).lower() != "none"]
        threshold_ids = [_clean(t) for t in thresh_str.split(",") if _clean(t) and _clean(t).lower() != "none"]

        return ComplianceResult(
            entity_id="",
            entity_type="",
            regulation_id="",
            verdict=verdict.upper(),
            confidence=confidence,
            requirement_ids=requirement_ids,
            threshold_breaches=[{"threshold_id": t} for t in threshold_ids],
            reasoning_steps=steps_raw[:10],
            cypher_used=cypher_used,
        )
