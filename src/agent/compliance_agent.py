"""
ComplianceAgent: Agentic loop wrapping Claude with Neo4j graph tools.

The agent receives a natural-language compliance question, iteratively calls
graph tools via Claude's tool-use API, and returns a structured final answer.

Model: claude-sonnet-4-6
# TODO: Update the model constant below if a newer Claude version is preferred.
"""

from __future__ import annotations
import logging
import os
from typing import TYPE_CHECKING

import anthropic

from src.agent.tools import TOOLS, execute_tool

if TYPE_CHECKING:
    from src.graph.connection import Neo4jConnection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096
MAX_ITERATIONS = 10  # Guard against infinite tool-use loops

SYSTEM_PROMPT = """You are a financial services compliance reasoning agent with \
access to a Neo4j knowledge graph. The graph contains three layers:

1. Entity Layer — loan accounts, customers, and their transactions.
2. Regulatory Layer — APRA prudential standards (e.g. CPS 220, APS 110) and \
their specific obligations.
3. Runtime Assessment Layer — compliance assessments and flags raised against \
individual accounts.

Your role:
- Answer compliance questions by querying the knowledge graph using the provided tools.
- Identify accounts, transactions, or customers that may breach APRA obligations.
- Reason over graph data to produce clear, evidence-based compliance findings.
- Cite specific account IDs, obligation IDs, and flag reasons in your answers.
- Be concise, precise, and professionally appropriate for a financial services context.

When you have gathered sufficient evidence from the graph, provide a final answer \
that includes: a summary of findings, accounts at risk, relevant APRA obligations \
referenced, and recommended next steps.

# TODO: Extend this system prompt with organisation-specific compliance context,
#       risk appetite statements, or escalation procedures."""


# ---------------------------------------------------------------------------
# ComplianceAgent
# ---------------------------------------------------------------------------


class ComplianceAgent:
    """
    Agentic compliance assistant backed by Claude and a Neo4j knowledge graph.

    Usage:
        with Neo4jConnection() as conn:
            agent = ComplianceAgent(neo4j_conn=conn)
            answer = agent.run("Are there any high-risk loan accounts?")
            print(answer)
    """

    def __init__(
        self,
        neo4j_conn: "Neo4jConnection",
        model: str = MODEL,
        max_tokens: int = MAX_TOKENS,
    ) -> None:
        self.conn = neo4j_conn
        self.model = model
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def run(self, user_query: str) -> str:
        """
        Execute the agentic loop for a compliance question.

        Steps:
            1. Send query + tools to Claude.
            2. If stop_reason == "tool_use", execute tool calls and inject results.
            3. Repeat until stop_reason == "end_turn" or MAX_ITERATIONS reached.

        Args:
            user_query: Natural-language compliance question.

        Returns:
            Claude's final text response.
        """
        messages: list[dict] = [{"role": "user", "content": user_query}]
        iterations = 0

        logger.info("ComplianceAgent starting. Query: %s", user_query)

        while iterations < MAX_ITERATIONS:
            iterations += 1
            logger.debug("Iteration %d / %d", iterations, MAX_ITERATIONS)

            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            logger.debug("stop_reason=%s", response.stop_reason)

            # ----------------------------------------------------------------
            # Terminal: Claude has finished reasoning
            # ----------------------------------------------------------------
            if response.stop_reason == "end_turn":
                return self._extract_text(response)

            # ----------------------------------------------------------------
            # Tool use: Claude wants to query the graph
            # ----------------------------------------------------------------
            if response.stop_reason == "tool_use":
                # Append the assistant turn (contains tool_use blocks)
                messages.append({"role": "assistant", "content": response.content})

                # Execute every tool call and collect results
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info("Tool call: %s(%s)", block.name, block.input)
                        result_content = execute_tool(block.name, block.input, self.conn)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result_content,
                            }
                        )

                # Inject tool results as the next user turn
                messages.append({"role": "user", "content": tool_results})
                continue

            # Unexpected stop reason — surface as error
            logger.warning("Unexpected stop_reason: %s", response.stop_reason)
            break

        logger.warning("Max iterations (%d) reached without end_turn.", MAX_ITERATIONS)
        return self._extract_text(response)  # Return whatever Claude last said

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(response: anthropic.types.Message) -> str:
        """Pull the first text block from a Claude response."""
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return "(No text response returned by Claude.)"
