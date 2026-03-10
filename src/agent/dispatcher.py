"""
execute_tool dispatcher factory for all agents.

Single source of truth — previously duplicated between app.py and
notebooks/311_agent_setup.ipynb.

Usage:
    from src.agent.dispatcher import make_execute_tool
    execute_tool = make_execute_tool(conn)
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Callable

from src.agent.config import WRITE_KEYWORDS
from src.mcp.tools_impl import (
    detect_graph_anomalies,
    evaluate_thresholds,
    persist_assessment,
    retrieve_regulatory_chunks,
    trace_evidence,
    traverse_compliance_path,
)

if TYPE_CHECKING:
    from src.graph.connection import Neo4jConnection

logger = logging.getLogger(__name__)


def make_execute_tool(conn: "Neo4jConnection") -> Callable[[str, dict], dict]:
    """
    Return an execute_tool dispatcher bound to the given Neo4j connection.

    The caller owns the connection lifecycle — Streamlit uses @st.cache_resource,
    notebooks use a module-level conn opened in 311_agent_setup.

    Neo4j MCP tools (read-neo4j-cypher, write-neo4j-cypher) run Cypher via conn.
    FastMCP tools call tools_impl functions directly (they open their own connections).
    """

    def execute_tool(tool_name: str, tool_input: dict) -> dict:
        logger.info("Tool: %s | inputs: %s", tool_name, list(tool_input.keys()))
        try:
            # ── Neo4j MCP ────────────────────────────────────────────────────
            if tool_name == "read-neo4j-cypher":
                query  = tool_input.get("query", "")
                params = tool_input.get("params", {})
                query_words = set(re.findall(r"\b[A-Z]+\b", query.upper()))
                if query_words & WRITE_KEYWORDS:
                    return {"error": "read-neo4j-cypher does not allow write operations."}
                return {"rows": conn.run_query(query, params)}

            elif tool_name == "write-neo4j-cypher":
                query  = tool_input.get("query", "")
                params = tool_input.get("params", {})
                return {"rows": conn.run_query(query, params)}

            # ── FastMCP ──────────────────────────────────────────────────────
            elif tool_name == "traverse_compliance_path":
                return traverse_compliance_path(**tool_input)
            elif tool_name == "retrieve_regulatory_chunks":
                return retrieve_regulatory_chunks(**tool_input)
            elif tool_name == "detect_graph_anomalies":
                return detect_graph_anomalies(**tool_input)
            elif tool_name == "persist_assessment":
                return persist_assessment(**tool_input)
            elif tool_name == "trace_evidence":
                return trace_evidence(**tool_input)
            elif tool_name == "evaluate_thresholds":
                return evaluate_thresholds(**tool_input)

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except Exception as e:
            logger.error("Tool %s failed: %s", tool_name, e, exc_info=True)
            return {"error": str(e)}

    return execute_tool
