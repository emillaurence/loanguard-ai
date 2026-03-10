"""
Plain Python implementations of the FastMCP tool functions.

Kept separate from investigation_server.py so they can be called directly
from notebooks and tests without going through the FastMCP FunctionTool wrapper.

investigation_server.py imports these and registers them with @mcp.tool().
311_agent_setup.ipynb imports these for the execute_tool dispatcher.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from src.graph.connection import Neo4jConnection
from src.graph.queries import (
    get_compliance_path,
    vector_search_chunks,
    get_assessment_with_evidence,
    merge_assessment,
    merge_finding,
    merge_reasoning_step,
)
from src.mcp.schema import ANOMALY_REGISTRY

logger = logging.getLogger(__name__)


def _get_conn() -> Neo4jConnection:
    """Open a fresh Neo4j connection per tool call (stateless)."""
    conn = Neo4jConnection()
    conn.connect()
    return conn


# ---------------------------------------------------------------------------
# Tool 1
# ---------------------------------------------------------------------------

def traverse_compliance_path(
    entity_id: str,
    entity_type: str,
    regulation_id: str = "",
) -> dict:
    """Cross-layer L1→L2 compliance traversal via the Jurisdiction bridge."""
    conn = _get_conn()
    try:
        return get_compliance_path(
            conn,
            entity_id=entity_id,
            entity_type=entity_type,
            regulation_id=regulation_id or None,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool 2
# ---------------------------------------------------------------------------

def retrieve_regulatory_chunks(
    query_text: str,
    regulation_id: str = "",
    top_k: int = 5,
) -> dict:
    """Semantic similarity search over regulatory Chunk nodes."""
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
                    "text": r.get("text", "")[:800],
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
# Tool 3
# ---------------------------------------------------------------------------

def detect_graph_anomalies(
    pattern_names: list[str],
    entity_id: str = "",
) -> dict:
    """Run one or more named anomaly detection patterns and return combined results."""
    valid = set(ANOMALY_REGISTRY.keys())
    unknown = [p for p in pattern_names if p not in valid]
    if unknown:
        return {
            "error": f"Unknown pattern(s): {unknown}.",
            "valid_patterns": list(valid),
        }

    id_keys = {
        "transaction_structuring": "target_account",
        "high_lvr_loans":          "loan_id",
        "high_risk_industry":      "borrower_id",
        "layered_ownership":       "ultimate_owner_id",
        "high_risk_jurisdiction":  "borrower_id",
        "guarantor_concentration": "borrower_id",
    }

    conn = _get_conn()
    results: list[dict] = []
    try:
        for pattern_name in pattern_names:
            spec   = ANOMALY_REGISTRY[pattern_name]
            cypher = spec["cypher"]
            params: dict = {}

            if entity_id:
                if pattern_name == "high_lvr_loans":
                    cypher = cypher.replace(
                        "MATCH (l:LoanApplication)",
                        "MATCH (l:LoanApplication {loan_id: $eid})",
                    )
                    params["eid"] = entity_id
                elif pattern_name in (
                    "high_risk_industry", "guarantor_concentration",
                    "high_risk_jurisdiction", "layered_ownership",
                ):
                    cypher = cypher.replace(
                        "MATCH (b:Borrower)",
                        "MATCH (b:Borrower {borrower_id: $eid})",
                    )
                    params["eid"] = entity_id

            try:
                rows = conn.run_query(cypher, params)
            except Exception as e:
                logger.error("Anomaly pattern %s failed: %s", pattern_name, e)
                rows = []

            id_key     = id_keys.get(pattern_name, "")
            entity_ids = [str(r[id_key]) for r in rows if r.get(id_key) is not None]
            results.append({
                "pattern_name":  pattern_name,
                "severity":      spec["severity"],
                "description":   spec["description"],
                "finding_count": len(rows),
                "findings":      rows,
                "entity_ids":    entity_ids,
            })
    finally:
        conn.close()

    total = sum(r["finding_count"] for r in results)
    return {"patterns_run": len(results), "total_findings": total, "results": results}


# ---------------------------------------------------------------------------
# Tool 4
# ---------------------------------------------------------------------------

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
    """Persist a compliance Assessment with Findings and ReasoningSteps to Layer 3."""
    valid_verdicts = {
        "COMPLIANT", "NON_COMPLIANT", "REQUIRES_REVIEW",
        "ANOMALY_DETECTED", "INFORMATIONAL",
    }
    if verdict not in valid_verdicts:
        return {"error": f"Invalid verdict '{verdict}'. Must be one of {valid_verdicts}."}

    confidence = max(0.0, min(1.0, float(confidence)))
    now_local = datetime.now()
    assessment_id = f"ASSESS-{entity_id}-{regulation_id}-{now_local.strftime('%Y-%m-%d-%H%M%S')}"
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

        persisted_findings: list[dict] = []
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
            persisted_findings.append({
                "finding_id": fid,
                "finding_type": f.get("finding_type", "information"),
                "severity": f.get("severity", "INFO"),
                "description": f.get("description", ""),
                "pattern_name": f.get("pattern_name"),
            })

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
            "findings": persisted_findings,
            "step_ids": step_ids,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool 5
# ---------------------------------------------------------------------------

def trace_evidence(assessment_id: str) -> dict:
    """Walk a stored Assessment back to all cited regulatory nodes."""
    conn = _get_conn()
    try:
        evidence = get_assessment_with_evidence(conn, assessment_id)

        step_section_ids: list[str] = []
        step_chunk_ids: list[str] = []
        for step in evidence.get("reasoning_steps", []):
            step_section_ids.extend(step.get("cited_section_ids", []))
            step_chunk_ids.extend(step.get("cited_chunk_ids", []))

        cited_sections: list[dict] = []
        if step_section_ids:
            cited_sections = conn.run_query(
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
            cited_chunks = [
                {**r, "text_excerpt": (r.get("text_excerpt") or "")[:400]}
                for r in rows
            ]

        evidence["cited_sections"] = cited_sections
        evidence["cited_chunks"] = cited_chunks
        return evidence
    finally:
        conn.close()
