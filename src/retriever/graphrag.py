"""
GraphRAGRetriever: Converts natural language to Cypher via Claude,
executes the query on Neo4j, and formats results as LLM-ready context.

This module implements the core GraphRAG pattern:
  NL query → Claude NL-to-Cypher → Neo4j → formatted context string

Typical use:
    retriever = GraphRAGRetriever(neo4j_conn=conn)
    context = retriever.retrieve_and_format("Which accounts have suspicious transactions?")
    # Pass `context` into a Claude prompt as retrieved graph knowledge.
"""

from __future__ import annotations
import json
import logging
import os
from typing import Any, TYPE_CHECKING

import anthropic

if TYPE_CHECKING:
    from src.graph.connection import Neo4jConnection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema hint passed to Claude for NL-to-Cypher generation
# TODO: Update this schema hint as your graph evolves.
# ---------------------------------------------------------------------------

GRAPH_SCHEMA_HINT = """
Node labels and key properties:
  - Customer         : customer_id, name, kyc_status, risk_category
  - LoanAccount      : account_id, customer_id, product_type, balance, currency, status, risk_rating
  - Transaction      : transaction_id, amount, currency, type, timestamp, counterparty, suspicious
  - Regulation       : standard_id, title, effective_date
  - Obligation       : obligation_id, description, applies_to, severity
  - ComplianceAssessment : assessment_id, assessed_at, outcome, score, notes
  - ComplianceFlag   : flag_id, reason, severity, raised_at, status

Relationships:
  - (Customer)-[:HOLDS]->(LoanAccount)
  - (LoanAccount)-[:HAS_TRANSACTION]->(Transaction)
  - (LoanAccount)-[:HAS_ASSESSMENT]->(ComplianceAssessment)
  - (ComplianceAssessment)-[:REFERENCES]->(Obligation)
  - (ComplianceFlag)-[:FLAGGED_ON]->(LoanAccount)
  - (Regulation)-[:CONTAINS]->(Obligation)

# TODO: Add any additional relationships as the graph schema grows.
"""

NL_TO_CYPHER_SYSTEM = f"""You are a Neo4j Cypher query expert for a financial services \
compliance knowledge graph.

Given a natural language question, generate a valid read-only Cypher query that \
retrieves relevant data to answer the question.

Graph schema:
{GRAPH_SCHEMA_HINT}

Rules:
- Return ONLY the raw Cypher query — no markdown fences, no explanation.
- Use MATCH, OPTIONAL MATCH, WHERE, RETURN, ORDER BY, LIMIT.
- Never use MERGE, CREATE, DELETE, SET, or DETACH DELETE.
- Always include a LIMIT clause (max 200 rows).
- Use parameterised queries where possible (e.g. $account_id).
- If the question cannot be answered from the schema, return: MATCH (n) RETURN null LIMIT 0
"""


class GraphRAGRetriever:
    """
    Converts natural language queries to Cypher, executes on Neo4j,
    and returns formatted context for downstream LLM prompting.

    Usage:
        retriever = GraphRAGRetriever(neo4j_conn=conn)
        context = retriever.retrieve_and_format("Show me high-risk accounts")
        # Use context as RAG context in a Claude prompt
    """

    def __init__(
        self,
        neo4j_conn: "Neo4jConnection",
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self.conn = neo4j_conn
        self.model = model
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def nl_to_cypher(self, natural_language_query: str) -> str:
        """
        Use Claude to translate a natural language question into a Cypher query.

        Args:
            natural_language_query: Plain English question about the graph data.

        Returns:
            Cypher query string.
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=512,
            system=NL_TO_CYPHER_SYSTEM,
            messages=[{"role": "user", "content": natural_language_query}],
        )
        cypher = response.content[0].text.strip()
        logger.info("Generated Cypher:\n%s", cypher)
        return cypher

    def retrieve(self, natural_language_query: str) -> list[dict[str, Any]]:
        """
        Convert NL query to Cypher and run it against Neo4j.

        Args:
            natural_language_query: Plain English question.

        Returns:
            Raw list of result dicts from Neo4j.
        """
        cypher = self.nl_to_cypher(natural_language_query)
        try:
            results = self.conn.run_query(cypher)
            logger.info("Retrieved %d records from Neo4j.", len(results))
            return results
        except Exception as e:
            logger.error("Cypher execution failed: %s\nQuery was:\n%s", e, cypher)
            return []

    def format_context_for_claude(self, results: list[dict[str, Any]]) -> str:
        """
        Format raw Neo4j results into a readable context string for a Claude prompt.

        Args:
            results: List of dicts returned by run_query().

        Returns:
            Formatted multi-line string suitable for injection into a Claude prompt.
        """
        if not results:
            return "No relevant records found in the knowledge graph."

        lines = [f"Retrieved {len(results)} record(s) from the knowledge graph:\n"]
        for i, record in enumerate(results, start=1):
            lines.append(f"  [{i}] {json.dumps(record, default=str)}")
        return "\n".join(lines)

    def retrieve_and_format(self, natural_language_query: str) -> str:
        """
        Convenience method: retrieve + format in one call.

        Args:
            natural_language_query: Plain English question.

        Returns:
            Formatted context string ready for a Claude prompt.
        """
        results = self.retrieve(natural_language_query)
        return self.format_context_for_claude(results)
