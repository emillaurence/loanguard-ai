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
from typing import Any, TYPE_CHECKING

import anthropic

from src.mcp.schema import GRAPH_SCHEMA_HINT, ComplianceResult

if TYPE_CHECKING:
    from src.graph.connection import Neo4jConnection

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8096
MAX_ITERATIONS = 12

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
5. Call persist_assessment to save your reasoning to Layer 3.
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

        logger.info("ComplianceAgent: %s", question)

        while iterations < MAX_ITERATIONS:
            iterations += 1
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                tools=self.tools,
                messages=messages,
                temperature=0,
            )

            if response.stop_reason == "end_turn":
                final_text = self._extract_text(response)
                break

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info("Tool: %s(%s)", block.name, list(block.input.keys()))
                        # Track Cypher for transparency panel
                        if block.name == "read-neo4j-cypher" and block.input.get("query"):
                            cypher_used.append(block.input["query"])
                        result = self.execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        })
                messages.append({"role": "user", "content": tool_results})
                continue

            logger.warning("Unexpected stop_reason: %s", response.stop_reason)
            break

        return self._parse_result(final_text, cypher_used)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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

        verdict = _find(r"VERDICT:\s*(\w+)", "INFORMATIONAL")
        confidence_str = _find(r"CONFIDENCE:\s*([\d.]+)", "0.5")
        reqs_str = _find(r"REQUIREMENTS CHECKED:\s*(.+)", "")
        thresh_str = _find(r"THRESHOLDS BREACHED:\s*(.+)", "")
        steps_raw = re.findall(r"^\d+\.\s+(.+)$", text, re.MULTILINE)

        confidence = float(confidence_str) if confidence_str else 0.5
        requirement_ids = [r.strip() for r in reqs_str.split(",") if r.strip() and r.strip().lower() != "none"]
        threshold_ids = [t.strip() for t in thresh_str.split(",") if t.strip() and t.strip().lower() != "none"]

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
