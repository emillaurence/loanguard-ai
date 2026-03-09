"""
FastMCP Investigation Server — 31x notebook series.

Exposes five domain-specific tools that go beyond raw Cypher execution:

  traverse_compliance_path     — cross-layer L1→L2 join via Jurisdiction bridge
  retrieve_regulatory_chunks   — vector similarity search on chunk_embeddings
  detect_graph_anomalies       — named Cypher anomaly pattern registry
  persist_assessment           — validated Layer 3 write-back
  trace_evidence               — walk Assessment back to cited nodes

Run as a subprocess for MCP protocol (stdio transport):
  python -m src.mcp.investigation_server

Or start programmatically via the mcp Python client in notebook 311.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP

# Load .env relative to project root (two levels up from this file)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

from src.graph.connection import Neo4jConnection
from src.graph.queries import (
    get_compliance_path,
    vector_search_chunks,
    get_assessment_with_evidence,
    merge_assessment,
    merge_finding,
    merge_reasoning_step,
)
from src.mcp.schema import ANOMALY_REGISTRY, AnomalyFinding

logger = logging.getLogger(__name__)

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


def _get_conn() -> Neo4jConnection:
    """Open a fresh Neo4j connection per tool call (stateless server)."""
    conn = Neo4jConnection()
    conn.connect()
    return conn


# ---------------------------------------------------------------------------
# Tool 1: traverse_compliance_path
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

    This is the primary tool for compliance assessment — call it first
    before checking specific threshold values.

    Args:
        entity_id:     Loan or borrower ID, e.g. 'LOAN-0002' or 'BRW-0001'.
        entity_type:   'LoanApplication' or 'Borrower'.
        regulation_id: Optional — filter to one regulation: 'APS-112',
                       'APG-223', or 'APS-220'. Omit for all regulations.

    Returns:
        {
          "entity":         { loan/borrower properties },
          "jurisdiction_id": str,
          "regulations": {
            "<regulation_id>": {
              "regulation_id": str,
              "name": str,
              "is_enforceable": bool,
              "sections": {
                "<section_id>": {
                  "section_id": str,
                  "title": str,
                  "requirements": {
                    "<requirement_id>": {
                      "requirement_id": str,
                      "description": str,
                      "severity": str,
                      "is_quantitative": bool,
                      "thresholds": [{ threshold_id, metric, operator, value, unit }]
                    }
                  }
                }
              }
            }
          }
        }
    """
    conn = _get_conn()
    try:
        result = get_compliance_path(
            conn,
            entity_id=entity_id,
            entity_type=entity_type,
            regulation_id=regulation_id or None,
        )
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool 2: retrieve_regulatory_chunks
# ---------------------------------------------------------------------------

@mcp.tool()
def retrieve_regulatory_chunks(
    query_text: str,
    regulation_id: str = "",
    top_k: int = 5,
) -> dict:
    """
    Semantic similarity search over regulatory Chunk nodes.

    Embeds the query_text using OpenAI text-embedding-3-small (1536 dims)
    then queries the 'chunk_embeddings' Neo4j vector index (cosine similarity).
    Returns the most relevant regulation text chunks.

    Use this tool to retrieve supporting regulatory language when writing
    a compliance finding or citing a specific rule.

    Args:
        query_text:    Natural language phrase or regulation concept to search,
                       e.g. 'LVR limit high risk lending' or 'serviceability buffer'.
        regulation_id: Optional filter: 'APS-112', 'APG-223', or 'APS-220'.
        top_k:         Number of chunks to return (default 5, max 20).

    Returns:
        {
          "query": str,
          "chunks": [
            {
              "chunk_id": str,
              "section_id": str,
              "text": str,
              "chunk_index": int,
              "source_document": str,
              "similarity_score": float
            }
          ]
        }
    """
    from openai import OpenAI

    top_k = min(int(top_k), 20)
    oai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    response = oai.embeddings.create(
        input=[query_text],
        model="text-embedding-3-small",
    )
    embedding = response.data[0].embedding

    conn = _get_conn()
    try:
        rows = vector_search_chunks(
            conn,
            embedding=embedding,
            top_k=top_k,
            regulation_id=regulation_id or None,
        )
        return {
            "query": query_text,
            "chunks": [
                {
                    "chunk_id": r.get("chunk_id"),
                    "section_id": r.get("section_id"),
                    "text": r.get("text", "")[:800],  # truncate for context window
                    "chunk_index": r.get("chunk_index"),
                    "source_document": r.get("source_document", regulation_id),
                    "similarity_score": round(float(r.get("score", 0)), 4),
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool 3: detect_graph_anomalies
# ---------------------------------------------------------------------------

@mcp.tool()
def detect_graph_anomalies(
    pattern_name: str,
    entity_id: str = "",
) -> dict:
    """
    Run a named rule-based anomaly detection pattern against the graph.

    Each pattern is a confirmed Cypher query that surfaces a specific
    financial crime or compliance risk signal from the Layer 1 graph.

    Available pattern_name values:
      'transaction_structuring'  — sub-$10k suspicious transfers to one account
                                   (currently finds: ACC-0596 receiving 6 transfers)
      'high_lvr_loans'           — LVR >= 90 per APG-223-THR-008
                                   (currently finds: LOAN-0002 LVR=95, LOAN-0013 LVR=92)
      'high_risk_industry'       — borrowers in gambling/financial asset investing
                                   (currently finds: BRW-0624, BRW-0627 in IND-9530)
      'layered_ownership'        — OWNS chains depth >= 2
                                   (currently finds: BRW-0582→0581→0580→0579)
      'high_risk_jurisdiction'   — borrowers in JUR-VU/JUR-MM/JUR-KH (high AML)
      'guarantor_concentration'  — borrowers guaranteeing 2+ loans

    Args:
        pattern_name: One of the pattern names listed above.
        entity_id:    Optional — scope results to one entity where supported.

    Returns:
        {
          "pattern_name": str,
          "severity": "HIGH" | "MEDIUM" | "LOW",
          "description": str,
          "finding_count": int,
          "findings": [ { ...row data... } ],
          "entity_ids": [ str ]
        }
    """
    if pattern_name not in ANOMALY_REGISTRY:
        return {
            "error": f"Unknown pattern '{pattern_name}'.",
            "valid_patterns": list(ANOMALY_REGISTRY.keys()),
        }

    spec = ANOMALY_REGISTRY[pattern_name]
    cypher = spec["cypher"]
    params: dict = {}

    if entity_id:
        if pattern_name == "high_lvr_loans":
            cypher = cypher.replace(
                "MATCH (l:LoanApplication)",
                "MATCH (l:LoanApplication {loan_id: $eid})",
            )
            params["eid"] = entity_id
        elif pattern_name in ("high_risk_industry", "guarantor_concentration",
                               "high_risk_jurisdiction", "layered_ownership"):
            cypher = cypher.replace(
                "MATCH (b:Borrower)",
                "MATCH (b:Borrower {borrower_id: $eid})",
            )
            params["eid"] = entity_id

    conn = _get_conn()
    try:
        rows = conn.run_query(cypher, params)
    except Exception as e:
        logger.error("Anomaly pattern %s failed: %s", pattern_name, e)
        rows = []
    finally:
        conn.close()

    # Collect primary entity IDs for easy downstream reference
    id_keys = {
        "transaction_structuring": "target_account",
        "high_lvr_loans":          "loan_id",
        "high_risk_industry":      "borrower_id",
        "layered_ownership":       "ultimate_owner_id",
        "high_risk_jurisdiction":  "borrower_id",
        "guarantor_concentration": "borrower_id",
    }
    id_key = id_keys.get(pattern_name, "")
    entity_ids = [str(r[id_key]) for r in rows if r.get(id_key) is not None]

    return {
        "pattern_name": pattern_name,
        "severity": spec["severity"],
        "description": spec["description"],
        "finding_count": len(rows),
        "findings": rows,
        "entity_ids": entity_ids,
    }


# ---------------------------------------------------------------------------
# Tool 4: persist_assessment
# ---------------------------------------------------------------------------

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

    Creates Assessment → HAS_FINDING → Finding → RELATES_TO → entity
    and Assessment → HAS_STEP → ReasoningStep → CITES_SECTION → Section.
    Idempotent: MERGE on assessment_id derived from entity+regulation+date.

    Args:
        entity_id:      'LOAN-0002' or 'BRW-0001'.
        entity_type:    'LoanApplication' or 'Borrower'.
        regulation_id:  'APS-112', 'APG-223', or 'APS-220'.
        verdict:        'COMPLIANT' | 'NON_COMPLIANT' | 'REQUIRES_REVIEW' |
                        'ANOMALY_DETECTED' | 'INFORMATIONAL'.
        confidence:     Float 0.0–1.0.
        findings: List of dicts:
          [{ "finding_type": str, "severity": str, "description": str,
             "pattern_name": str|null, "related_entity_id": str|null,
             "related_entity_type": str|null }]
        reasoning_steps: List of dicts:
          [{ "description": str, "cypher_used": str|null,
             "section_ids": [str], "chunk_ids": [str] }]
        agent:          Agent name for attribution (default 'compliance_agent').

    Returns:
        { "assessment_id": str, "finding_ids": [str], "step_ids": [str] }
    """
    valid_verdicts = {"COMPLIANT", "NON_COMPLIANT", "REQUIRES_REVIEW",
                      "ANOMALY_DETECTED", "INFORMATIONAL"}
    if verdict not in valid_verdicts:
        return {"error": f"Invalid verdict '{verdict}'. Must be one of {valid_verdicts}."}

    confidence = max(0.0, min(1.0, float(confidence)))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assessment_id = f"ASSESS-{entity_id}-{regulation_id}-{today}"
    created_at = datetime.now(timezone.utc).isoformat()

    conn = _get_conn()
    try:
        merge_assessment(
            conn,
            assessment_id=assessment_id,
            entity_id=entity_id,
            entity_type=entity_type,
            regulation_id=regulation_id,
            verdict=verdict,
            confidence=confidence,
            agent=agent,
            created_at=created_at,
        )

        finding_ids: list[str] = []
        for i, f in enumerate(findings or []):
            fid = f"FIND-{assessment_id}-{i:03d}"
            merge_finding(
                conn,
                finding_id=fid,
                assessment_id=assessment_id,
                finding_type=f.get("finding_type", "information"),
                severity=f.get("severity", "INFO"),
                description=f.get("description", ""),
                pattern_name=f.get("pattern_name"),
                related_entity_id=f.get("related_entity_id"),
                related_entity_type=f.get("related_entity_type"),
            )
            finding_ids.append(fid)

        step_ids: list[str] = []
        for i, s in enumerate(reasoning_steps or []):
            sid = f"STEP-{assessment_id}-{i:03d}"
            merge_reasoning_step(
                conn,
                step_id=sid,
                assessment_id=assessment_id,
                step_number=i + 1,
                description=s.get("description", ""),
                cypher_used=s.get("cypher_used"),
                section_ids=s.get("section_ids", []),
                chunk_ids=s.get("chunk_ids", []),
            )
            step_ids.append(sid)

        return {
            "assessment_id": assessment_id,
            "finding_ids": finding_ids,
            "step_ids": step_ids,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool 5: trace_evidence
# ---------------------------------------------------------------------------

@mcp.tool()
def trace_evidence(assessment_id: str) -> dict:
    """
    Walk a stored Assessment back to all cited regulatory nodes.

    Returns the complete reasoning chain so users can understand
    why a compliance verdict was reached:
      Assessment → Findings
      Assessment → ReasoningSteps → cited Sections (with text)
                                 → cited Chunks (with text excerpt)

    Use this when a user asks 'why was this flagged?' or 'show your reasoning'.

    Args:
        assessment_id: e.g. 'ASSESS-LOAN-0002-APG-223-2026-03-09'.

    Returns:
        {
          "assessment": { assessment metadata },
          "findings": [ { finding data } ],
          "reasoning_steps": [
            {
              "step_number": int,
              "description": str,
              "cypher_used": str|null,
              "cited_section_ids": [str],
              "cited_chunk_ids": [str]
            }
          ],
          "cited_sections": [ { section_id, title, content_summary } ],
          "cited_chunks":   [ { chunk_id, text_excerpt, section_id } ]
        }
    """
    conn = _get_conn()
    try:
        evidence = get_assessment_with_evidence(conn, assessment_id)

        # Fetch full section and chunk text for all cited IDs
        step_section_ids: list[str] = []
        step_chunk_ids: list[str] = []
        for step in evidence.get("reasoning_steps", []):
            step_section_ids.extend(step.get("cited_section_ids", []))
            step_chunk_ids.extend(step.get("cited_chunk_ids", []))

        cited_sections: list[dict] = []
        if step_section_ids:
            rows = conn.run_query(
                """
                MATCH (s:Section)
                WHERE s.section_id IN $ids
                RETURN s.section_id       AS section_id,
                       s.title            AS title,
                       s.content_summary  AS content_summary,
                       s.regulation_id    AS regulation_id
                """,
                {"ids": list(set(step_section_ids))},
            )
            cited_sections = rows

        cited_chunks: list[dict] = []
        if step_chunk_ids:
            rows = conn.run_query(
                """
                MATCH (c:Chunk)
                WHERE c.chunk_id IN $ids
                RETURN c.chunk_id    AS chunk_id,
                       c.section_id  AS section_id,
                       c.text        AS text_excerpt,
                       c.chunk_index AS chunk_index
                """,
                {"ids": list(set(step_chunk_ids))},
            )
            # Truncate text for readability
            cited_chunks = [
                {**r, "text_excerpt": (r.get("text_excerpt") or "")[:400]}
                for r in rows
            ]

        evidence["cited_sections"] = cited_sections
        evidence["cited_chunks"] = cited_chunks
        return evidence
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
