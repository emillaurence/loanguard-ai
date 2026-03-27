"""
Anthropic tool definitions for all MCP tools available to agents.

Single source of truth — imported by app.py and notebooks/311_agent_setup.ipynb.
Previously these were duplicated (and diverging) in both places.

Two categories:
  NEO4J_MCP_TOOLS  — match the schema of the mcp-neo4j-cypher package;
                     dispatched locally via Neo4jConnection (no external process).
  FASTMCP_TOOL_DEFS — domain-specific tools implemented in src/mcp/tools_impl.py.

TOOLS = NEO4J_MCP_TOOLS + FASTMCP_TOOL_DEFS (the combined list passed to Claude).
"""

from __future__ import annotations

from src.mcp.schema import ANOMALY_REGISTRY

# ---------------------------------------------------------------------------
# Neo4j MCP tools
# Schema matches mcp-neo4j-cypher so agent prompts are portable to
# environments where the real Neo4j MCP server is running.
# ---------------------------------------------------------------------------

NEO4J_MCP_TOOLS: list[dict] = [
    {
        "name": "read-neo4j-cypher",
        "description": (
            "Execute a read-only Cypher query against the Neo4j graph database. "
            "Returns result rows as a list of dicts. "
            "YOU generate the Cypher — use the GRAPH_SCHEMA_HINT in the system prompt. "
            "Always include LIMIT (max 100). Never use MERGE/CREATE/DELETE/SET."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Valid read-only Cypher query with LIMIT clause.",
                },
                "params": {
                    "type": "object",
                    "description": "Optional parameter dict for parameterised queries.",
                    "default": {},
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "write-neo4j-cypher",
        "description": (
            "Execute a write Cypher query (MERGE, CREATE, SET) against Neo4j. "
            "Use ONLY for Layer 3 Assessment/Finding/ReasoningStep writes. "
            "Prefer persist_assessment tool for structured Layer 3 writes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Write Cypher query."},
                "params": {"type": "object", "default": {}},
            },
            "required": ["query"],
        },
    },
]

# ---------------------------------------------------------------------------
# FastMCP tool definitions
# Mirrors the @mcp.tool() functions in src/mcp/investigation_server.py.
# ---------------------------------------------------------------------------

# Derive anomaly pattern names from the registry — adding a new pattern to
# ANOMALY_REGISTRY automatically makes it available here.
_ANOMALY_PATTERN_NAMES: list[str] = list(ANOMALY_REGISTRY.keys())

FASTMCP_TOOL_DEFS: list[dict] = [
    {
        "name": "traverse_compliance_path",
        "description": (
            "Cross-layer compliance traversal. "
            "Walks entity → Borrower → Jurisdiction (RESIDES_IN/REGISTERED_IN) "
            "→ Regulation (APPLIES_TO_JURISDICTION) → Section → Requirement → Threshold. "
            "Call this FIRST for any compliance question to get the full regulatory framework. "
            "Returns applicable thresholds for the entity jurisdiction and loan type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id":     {"type": "string", "description": "e.g. 'LOAN-0002' or 'BRW-0001'"},
                "entity_type":   {"type": "string", "enum": ["LoanApplication", "Borrower"]},
                "regulation_id": {"type": "string", "description": "Optional regulation filter.", "default": ""},
            },
            "required": ["entity_id", "entity_type"],
        },
    },
    {
        "name": "retrieve_regulatory_chunks",
        "description": (
            "Semantic similarity search over regulatory Chunk nodes using the "
            "chunk_embeddings Neo4j vector index (OpenAI text-embedding-3-small, cosine). "
            "Use to retrieve supporting regulation text when writing a finding."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query_text":    {"type": "string", "description": "Regulatory concept to search."},
                "regulation_id": {"type": "string", "default": "", "description": "Optional filter: e.g. 'APG-223'"},
                "top_k":         {"type": "integer", "default": 5, "description": "Number of chunks (max 20)."},
            },
            "required": ["query_text"],
        },
    },
    {
        "name": "detect_graph_anomalies",
        "description": (
            "Run one or more anomaly patterns in a single call — always batch all relevant patterns. "
            f"pattern_names values: {_ANOMALY_PATTERN_NAMES}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern_names": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": _ANOMALY_PATTERN_NAMES,
                    },
                    "description": "List of patterns to run — pass all relevant ones in one call.",
                },
                "entity_id": {"type": "string", "default": "", "description": "Optional entity scope."},
            },
            "required": ["pattern_names"],
        },
    },
    {
        "name": "persist_assessment",
        "description": (
            "Persist a compliance Assessment with Findings and ReasoningSteps to Layer 3 (Neo4j). "
            "Idempotent MERGE. Call after completing compliance analysis to store reasoning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id":       {"type": "string"},
                "entity_type":     {"type": "string", "enum": ["LoanApplication", "Borrower"]},
                "regulation_id":   {"type": "string"},
                "verdict":         {"type": "string", "enum": ["COMPLIANT", "NON_COMPLIANT", "REQUIRES_REVIEW", "ANOMALY_DETECTED", "INFORMATIONAL"]},
                "confidence":      {"type": "number", "minimum": 0, "maximum": 1},
                "findings":        {"type": "array", "items": {"type": "object"}},
                "reasoning_steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string", "description": "What this reasoning step checked or concluded."},
                            "cypher_used":  {"type": "string", "description": "The Cypher query used in this step, if any."},
                            "section_ids":  {"type": "array", "items": {"type": "string"}, "description": "section_id values returned by traverse_compliance_path or read-neo4j-cypher that informed this step."},
                            "chunk_ids":    {"type": "array", "items": {"type": "string"}, "description": "chunk_id values returned by retrieve_regulatory_chunks that informed this step."},
                        },
                        "required": ["description"],
                    },
                },
                "agent":           {"type": "string", "default": "compliance_agent"},
            },
            "required": ["entity_id", "entity_type", "regulation_id", "verdict", "confidence"],
        },
    },
    {
        "name": "trace_evidence",
        "description": (
            "Walk a stored Assessment back to all cited regulatory nodes. "
            "Returns findings, reasoning steps, cited sections (with text), "
            "and cited chunks (with text excerpt). "
            "Use when asked 'why was this flagged?' or 'show your reasoning'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "assessment_id": {"type": "string", "description": "e.g. 'ASSESS-LOAN-0002-APG-223-2026-03-10-143022'"},
            },
            "required": ["assessment_id"],
        },
    },
    {
        "name": "evaluate_thresholds",
        "description": (
            "Evaluate a list of Threshold dicts against the entity's stored values. "
            "Call after traverse_compliance_path, passing the threshold list from its result. "
            "Returns structured PASS/BREACH/unknown per threshold so verdicts are "
            "grounded in deterministic data rather than LLM arithmetic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id":   {"type": "string", "description": "e.g. 'LOAN-0002' or 'BRW-0001'"},
                "entity_type": {"type": "string", "enum": ["LoanApplication", "Borrower"]},
                "thresholds": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "List of threshold dicts from traverse_compliance_path. "
                        "Each should have threshold_id, metric, operator, value, unit."
                    ),
                },
            },
            "required": ["entity_id", "entity_type", "thresholds"],
        },
    },
]

# Cache the entire tools block: Anthropic caches from the start of the tools
# array up to and including the last entry that carries cache_control.
# Adding it to the last FastMCP tool caches all 8 tool schemas for 5 minutes.
FASTMCP_TOOL_DEFS[-1] = {**FASTMCP_TOOL_DEFS[-1], "cache_control": {"type": "ephemeral"}}

TOOLS: list[dict] = NEO4J_MCP_TOOLS + FASTMCP_TOOL_DEFS
