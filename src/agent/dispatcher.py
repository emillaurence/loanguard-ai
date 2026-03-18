"""
execute_tool dispatcher factory for all agents.

Single source of truth — previously duplicated between app.py and
notebooks/311_agent_setup.ipynb.

Usage:
    from src.agent.dispatcher import make_execute_tool
    execute_tool = make_execute_tool(conn)
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Callable

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


# Tools whose results are deterministic within a session (same inputs → same output).
# Results are cached in-process to prevent duplicate calls and cross-agent re-fetching.
_CACHEABLE_TOOLS: frozenset[str] = frozenset({
    "traverse_compliance_path",
    "retrieve_regulatory_chunks",
})


def make_execute_tool(conn: "Neo4jConnection") -> Callable[[str, dict], Any]:
    """
    Return an execute_tool dispatcher bound to the given Neo4j connection.

    The caller owns the connection lifecycle — Streamlit uses @st.cache_resource,
    notebooks use a module-level conn opened in 311_agent_setup.

    Neo4j MCP tools (read-neo4j-cypher, write-neo4j-cypher) run Cypher via conn.
    FastMCP tools call tools_impl functions directly (they open their own connections).

    Deterministic tools (traverse_compliance_path, retrieve_regulatory_chunks) are
    cached for the lifetime of this dispatcher instance to prevent duplicate calls
    when agents run in parallel or Claude retries the same tool.
    """
    _cache: dict[str, Any] = {}

    def execute_tool(tool_name: str, tool_input: dict) -> dict:
        # In-session cache for deterministic tools
        if tool_name in _CACHEABLE_TOOLS:
            cache_key = tool_name + ":" + json.dumps(tool_input, sort_keys=True)
            if cache_key in _cache:
                logger.debug("Tool cache hit: %s", tool_name)
                return _cache[cache_key]
        logger.info("Tool: %s | inputs: %s", tool_name, list(tool_input.keys()))
        try:
            result = _dispatch(tool_name, tool_input)
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_name, e, exc_info=True)
            return {"error": str(e)}
        if tool_name in _CACHEABLE_TOOLS:
            _cache[cache_key] = result  # type: ignore[possibly-undefined]
        return result

    def _dispatch(tool_name: str, tool_input: dict) -> dict:
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
            # Pass the shared conn to avoid opening a fresh connection per tool call.
            elif tool_name == "traverse_compliance_path":
                return traverse_compliance_path(**tool_input, conn=conn)
            elif tool_name == "retrieve_regulatory_chunks":
                return retrieve_regulatory_chunks(**tool_input, conn=conn)
            elif tool_name == "detect_graph_anomalies":
                return detect_graph_anomalies(**tool_input, conn=conn)
            elif tool_name == "persist_assessment":
                return persist_assessment(**tool_input, conn=conn)
            elif tool_name == "trace_evidence":
                return trace_evidence(**tool_input, conn=conn)
            elif tool_name == "evaluate_thresholds":
                return evaluate_thresholds(**tool_input, conn=conn)

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except Exception as e:
            raise

    return execute_tool
