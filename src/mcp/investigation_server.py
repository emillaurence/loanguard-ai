"""
FastMCP Investigation Server — 31x notebook series.

Exposes five domain-specific tools that go beyond raw Cypher execution:

  traverse_compliance_path     — cross-layer L1→L2 join via Jurisdiction bridge
  retrieve_regulatory_chunks   — vector similarity search on chunk_embeddings
  detect_graph_anomalies       — named Cypher anomaly pattern registry
  persist_assessment           — validated Layer 3 write-back
  trace_evidence               — walk Assessment back to cited nodes

Tool implementations live in src/mcp/tools_impl.py (plain callables).
This module wraps them with @mcp.tool() for MCP protocol transport.

Run as a subprocess for MCP protocol (stdio transport):
  python -m src.mcp.investigation_server

Or import from tools_impl directly for in-process use (notebooks, tests).
"""

from __future__ import annotations

from pathlib import Path
from dotenv import load_dotenv

# Load .env relative to project root (two levels up from this file)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

from fastmcp import FastMCP
from src.mcp.tools_impl import (
    traverse_compliance_path as _traverse_compliance_path,
    retrieve_regulatory_chunks as _retrieve_regulatory_chunks,
    detect_graph_anomalies as _detect_graph_anomalies,
    persist_assessment as _persist_assessment,
    trace_evidence as _trace_evidence,
)

# ---------------------------------------------------------------------------
# FastMCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "GraphRAG Investigation Server",
    instructions=(
        "Domain-specific graph tools for financial compliance and investigation "
        "over a three-layer Neo4j graph (Layer 1: entities, Layer 2: APRA "
        "regulations, Layer 3: assessments). Use these tools for cross-layer "
        "traversal, vector search, anomaly detection, and assessment persistence. "
        "For ad-hoc Cypher queries use the Neo4j MCP read-neo4j-cypher tool."
    ),
)


# ---------------------------------------------------------------------------
# Register tools (thin wrappers so docstrings appear in MCP manifest)
# ---------------------------------------------------------------------------

@mcp.tool()
def traverse_compliance_path(
    entity_id: str,
    entity_type: str,
    regulation_id: str = "",
) -> dict:
    """
    Cross-layer compliance traversal.

    Walks the graph from a LoanApplication or Borrower through the
    Jurisdiction bridge node into the regulatory layer, returning every
    applicable Regulation → Section → Requirement → Threshold for the
    entity's loan type and jurisdiction.

    Args:
        entity_id:     Loan or borrower ID, e.g. 'LOAN-0002' or 'BRW-0001'.
        entity_type:   'LoanApplication' or 'Borrower'.
        regulation_id: Optional filter: 'APS-112', 'APG-223', or 'APS-220'.
    """
    return _traverse_compliance_path(entity_id, entity_type, regulation_id)


@mcp.tool()
def retrieve_regulatory_chunks(
    query_text: str,
    regulation_id: str = "",
    top_k: int = 5,
) -> dict:
    """
    Semantic similarity search over regulatory Chunk nodes.

    Embeds query_text using OpenAI text-embedding-3-small then queries
    the 'chunk_embeddings' Neo4j vector index (cosine similarity).

    Args:
        query_text:    Natural language phrase, e.g. 'LVR limit high risk lending'.
        regulation_id: Optional filter: 'APS-112', 'APG-223', or 'APS-220'.
        top_k:         Number of chunks to return (default 5, max 20).
    """
    return _retrieve_regulatory_chunks(query_text, regulation_id, top_k)


@mcp.tool()
def detect_graph_anomalies(
    pattern_name: str,
    entity_id: str = "",
) -> dict:
    """
    Run a named rule-based anomaly detection pattern against the graph.

    pattern_name values:
      'transaction_structuring'  — sub-$10k suspicious transfers (finds ACC-0596)
      'high_lvr_loans'           — LVR >= 90 (finds LOAN-0002, LOAN-0013)
      'high_risk_industry'       — gambling/fin-assets (finds BRW-0624, BRW-0627)
      'layered_ownership'        — OWNS depth>=2 (finds BRW-0582 chain)
      'high_risk_jurisdiction'   — JUR-VU/JUR-MM/JUR-KH
      'guarantor_concentration'  — guarantor on 2+ loans

    Args:
        pattern_name: One of the pattern names listed above.
        entity_id:    Optional — scope results to one entity where supported.
    """
    return _detect_graph_anomalies(pattern_name, entity_id)


@mcp.tool()
def persist_assessment(
    entity_id: str,
    entity_type: str,
    regulation_id: str,
    verdict: str,
    confidence: float,
    findings: list,
    reasoning_steps: list,
    agent: str = "compliance_agent",
) -> dict:
    """
    Persist a compliance Assessment with Findings and ReasoningSteps to Layer 3.

    Creates a new Assessment node per run, identified by entity+regulation+local datetime.

    Args:
        entity_id:      'LOAN-0002' or 'BRW-0001'.
        entity_type:    'LoanApplication' or 'Borrower'.
        regulation_id:  'APS-112', 'APG-223', or 'APS-220'.
        verdict:        'COMPLIANT'|'NON_COMPLIANT'|'REQUIRES_REVIEW'|
                        'ANOMALY_DETECTED'|'INFORMATIONAL'.
        confidence:     Float 0.0–1.0.
        findings:       List of finding dicts.
        reasoning_steps: List of reasoning step dicts.
        agent:          Agent name for attribution.
    """
    return _persist_assessment(
        entity_id, entity_type, regulation_id, verdict,
        confidence, findings, reasoning_steps, agent,
    )


@mcp.tool()
def trace_evidence(assessment_id: str) -> dict:
    """
    Walk a stored Assessment back to all cited regulatory nodes.

    Returns the full reasoning chain: findings, reasoning steps,
    cited sections (with text), and cited chunks (with text excerpt).

    Args:
        assessment_id: e.g. 'ASSESS-LOAN-0002-APG-223-2026-03-10-143022'.
    """
    return _trace_evidence(assessment_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
