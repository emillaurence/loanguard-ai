"""
Cypher query helpers organised by Neo4j graph layer.

Layer 1 — Financial entities: Borrower, LoanApplication, BankAccount,
           Transaction, Collateral, Officer, Address, Jurisdiction, Industry

Layer 2 — Regulatory graph: Regulation, Section, Requirement, Threshold, Chunk

Layer 3 — Assessment layer: Assessment, Finding, ReasoningStep

All helpers accept a Neo4jConnection and return list[dict].
Schema matches actual CSV data loaded by notebooks 111 and 214.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.graph.connection import Neo4jConnection


# ===========================================================================
# LAYER 1 — Entity helpers
# ===========================================================================


def get_borrower(conn: "Neo4jConnection", borrower_id: str) -> list[dict]:
    """Return a Borrower node and its linked loans, accounts, and jurisdiction."""
    return conn.run_query(
        """
        MATCH (b:Borrower {borrower_id: $id})
        OPTIONAL MATCH (b)-[:RESIDES_IN|REGISTERED_IN]->(j:Jurisdiction)
        OPTIONAL MATCH (b)<-[:SUBMITTED_BY]-(l:LoanApplication)
        OPTIONAL MATCH (b)-[:HAS_ACCOUNT]->(a:BankAccount)
        RETURN b.borrower_id        AS borrower_id,
               b.name               AS name,
               b.type               AS type,
               b.entity_subtype     AS entity_subtype,
               b.credit_score       AS credit_score,
               b.risk_rating        AS risk_rating,
               b.status             AS status,
               b.annual_revenue     AS annual_revenue,
               b.employee_count     AS employee_count,
               j.jurisdiction_id    AS jurisdiction_id,
               j.aml_risk_rating    AS jurisdiction_aml_risk,
               collect(DISTINCT l.loan_id)    AS loan_ids,
               collect(DISTINCT a.account_id) AS account_ids
        """,
        {"id": borrower_id},
    )


def get_loan_application(conn: "Neo4jConnection", loan_id: str) -> list[dict]:
    """Return a LoanApplication with borrower, collateral, guarantors."""
    return conn.run_query(
        """
        MATCH (l:LoanApplication {loan_id: $id})
        OPTIONAL MATCH (l)-[:SUBMITTED_BY]->(b:Borrower)
        OPTIONAL MATCH (l)-[:BACKED_BY]->(c:Collateral)
        OPTIONAL MATCH (l)-[:GUARANTEED_BY]->(g:Borrower)
        RETURN l.loan_id                    AS loan_id,
               l.loan_type                  AS loan_type,
               l.amount                     AS amount,
               l.currency                   AS currency,
               l.term_months                AS term_months,
               l.lvr                        AS lvr,
               l.interest_rate_indicative   AS interest_rate_pct,
               l.purpose                    AS purpose,
               l.status                     AS status,
               l.description                AS description,
               b.borrower_id                AS borrower_id,
               b.name                       AS borrower_name,
               b.risk_rating                AS borrower_risk,
               c.collateral_id              AS collateral_id,
               c.estimated_value            AS collateral_value,
               c.type                       AS collateral_type,
               collect(DISTINCT g.borrower_id) AS guarantor_ids
        """,
        {"id": loan_id},
    )


def get_borrower_neighborhood(
    conn: "Neo4jConnection",
    borrower_id: str,
    depth: int = 2,
    limit: int = 50,
) -> list[dict]:
    """
    Variable-depth BFS from a Borrower node.
    Excludes Layer 3 Assessment/Finding/ReasoningStep nodes.
    Returns paths as (start_id, rel_type, end_id, end_label) rows.
    """
    return conn.run_query(
        """
        MATCH path = (b:Borrower {borrower_id: $id})-[r*1..$depth]-(n)
        WHERE NOT n:Assessment AND NOT n:Finding AND NOT n:ReasoningStep
        WITH DISTINCT r, n,
             [rel IN r | type(rel)]           AS rel_types,
             [rel IN r | startNode(rel)]      AS starts
        RETURN rel_types,
               coalesce(n.borrower_id, n.loan_id, n.account_id,
                        n.officer_id, n.address_id, n.collateral_id,
                        n.jurisdiction_id)    AS end_node_id,
               labels(n)[0]                  AS end_node_label,
               n.name                        AS end_node_name
        LIMIT $limit
        """,
        {"id": borrower_id, "depth": depth, "limit": limit},
    )


def get_transactions_for_account(
    conn: "Neo4jConnection",
    account_id: str,
    limit: int = 50,
) -> list[dict]:
    """Return transactions involving a specific BankAccount (inbound or outbound)."""
    return conn.run_query(
        """
        MATCH (t:Transaction)
        WHERE t.from_account_id = $id OR t.to_account_id = $id
        RETURN t.transaction_id     AS transaction_id,
               t.from_account_id   AS from_account,
               t.to_account_id     AS to_account,
               t.amount            AS amount,
               t.currency          AS currency,
               t.date              AS date,
               t.type              AS type,
               t.description       AS description,
               t.flagged_suspicious AS suspicious
        ORDER BY t.date DESC
        LIMIT $limit
        """,
        {"id": account_id, "limit": limit},
    )


def get_suspicious_transactions(
    conn: "Neo4jConnection",
    limit: int = 100,
) -> list[dict]:
    """Return all transactions flagged as suspicious."""
    return conn.run_query(
        """
        MATCH (t:Transaction)
        WHERE t.flagged_suspicious = true
        OPTIONAL MATCH (t)-[:FROM_ACCOUNT]->(src:BankAccount)
        OPTIONAL MATCH (t)-[:TO_ACCOUNT]->(dst:BankAccount)
        RETURN t.transaction_id     AS transaction_id,
               t.amount             AS amount,
               t.date               AS date,
               t.type               AS type,
               t.description        AS description,
               t.from_account_id    AS from_account_id,
               t.to_account_id      AS to_account_id
        ORDER BY t.date DESC
        LIMIT $limit
        """,
        {"limit": limit},
    )


def get_officers_for_borrower(
    conn: "Neo4jConnection",
    borrower_id: str,
) -> list[dict]:
    """Return officers who are directors of a corporate borrower."""
    return conn.run_query(
        """
        MATCH (o:Officer)-[:DIRECTOR_OF]->(b:Borrower {borrower_id: $id})
        RETURN o.officer_id     AS officer_id,
               o.name           AS name,
               o.is_pep         AS is_pep,
               o.sanctions_match AS sanctions_match,
               o.nationality    AS nationality
        """,
        {"id": borrower_id},
    )


def get_loans_by_risk(
    conn: "Neo4jConnection",
    risk_rating: str = "high",
    limit: int = 50,
) -> list[dict]:
    """Return loan applications for borrowers with a given risk_rating."""
    return conn.run_query(
        """
        MATCH (l:LoanApplication)-[:SUBMITTED_BY]->(b:Borrower)
        WHERE b.risk_rating = $risk
        RETURN l.loan_id    AS loan_id,
               l.amount     AS amount,
               l.lvr        AS lvr,
               b.borrower_id AS borrower_id,
               b.name       AS borrower_name,
               b.risk_rating AS risk_rating
        ORDER BY l.lvr DESC
        LIMIT $limit
        """,
        {"risk": risk_rating, "limit": limit},
    )


# ===========================================================================
# LAYER 2 — Regulatory helpers
# ===========================================================================


def get_regulation(conn: "Neo4jConnection", regulation_id: str) -> list[dict]:
    """Return a regulation and its sections."""
    return conn.run_query(
        """
        MATCH (r:Regulation {regulation_id: $id})
        OPTIONAL MATCH (r)-[:HAS_SECTION]->(s:Section)
        RETURN r.regulation_id   AS regulation_id,
               r.name            AS name,
               r.issuing_body    AS issuing_body,
               r.document_type  AS document_type,
               r.effective_date AS effective_date,
               r.is_enforceable AS is_enforceable,
               collect(DISTINCT {
                 section_id: s.section_id,
                 title: s.title,
                 section_number: s.section_number
               }) AS sections
        """,
        {"id": regulation_id},
    )


def get_requirements_for_loan_type(
    conn: "Neo4jConnection",
    loan_type: str = "residential_secured",
    regulation_id: str | None = None,
) -> list[dict]:
    """Return all requirements applicable to a given loan type, optionally filtered by regulation."""
    if regulation_id:
        return conn.run_query(
            """
            MATCH (reg:Regulation {regulation_id: $reg_id})-[:HAS_SECTION]->(s:Section)
                  -[:HAS_REQUIREMENT]->(req:Requirement)
            WHERE req.applies_to_loan_type = $loan_type
               OR req.applies_to_loan_type IS NULL
            OPTIONAL MATCH (req)-[:DEFINES_LIMIT]->(t:Threshold)
            RETURN reg.regulation_id    AS regulation_id,
                   s.section_id         AS section_id,
                   s.title              AS section_title,
                   req.requirement_id   AS requirement_id,
                   req.description      AS description,
                   req.requirement_type AS req_type,
                   req.is_quantitative  AS is_quantitative,
                   req.severity         AS severity,
                   collect(DISTINCT {
                     threshold_id: t.threshold_id,
                     metric: t.metric,
                     operator: t.operator,
                     value: t.value,
                     unit: t.unit
                   }) AS thresholds
            ORDER BY req.requirement_id
            """,
            {"reg_id": regulation_id, "loan_type": loan_type},
        )
    return conn.run_query(
        """
        MATCH (reg:Regulation)-[:HAS_SECTION]->(s:Section)
              -[:HAS_REQUIREMENT]->(req:Requirement)
        WHERE req.applies_to_loan_type = $loan_type
           OR req.applies_to_loan_type = 'all'
           OR req.applies_to_loan_type IS NULL
        OPTIONAL MATCH (req)-[:DEFINES_LIMIT]->(t:Threshold)
        RETURN reg.regulation_id    AS regulation_id,
               s.section_id         AS section_id,
               req.requirement_id   AS requirement_id,
               req.description      AS description,
               req.is_quantitative  AS is_quantitative,
               req.severity         AS severity,
               collect(DISTINCT {
                 threshold_id: t.threshold_id,
                 metric: t.metric,
                 operator: t.operator,
                 value: t.value,
                 unit: t.unit
               }) AS thresholds
        ORDER BY reg.regulation_id, req.requirement_id
        """,
        {"loan_type": loan_type},
    )


def get_thresholds(
    conn: "Neo4jConnection",
    regulation_id: str | None = None,
) -> list[dict]:
    """Return all thresholds, optionally scoped to one regulation."""
    if regulation_id:
        return conn.run_query(
            """
            MATCH (t:Threshold {regulation_id: $reg_id})
            RETURN t.threshold_id    AS threshold_id,
                   t.regulation_id  AS regulation_id,
                   t.requirement_id AS requirement_id,
                   t.metric         AS metric,
                   t.operator       AS operator,
                   t.value          AS value,
                   t.value_upper    AS value_upper,
                   t.unit           AS unit,
                   t.consequence    AS consequence
            ORDER BY t.threshold_id
            """,
            {"reg_id": regulation_id},
        )
    return conn.run_query(
        """
        MATCH (t:Threshold)
        RETURN t.threshold_id    AS threshold_id,
               t.regulation_id  AS regulation_id,
               t.requirement_id AS requirement_id,
               t.metric         AS metric,
               t.operator       AS operator,
               t.value          AS value,
               t.value_upper    AS value_upper,
               t.unit           AS unit,
               t.consequence    AS consequence
        ORDER BY t.regulation_id, t.threshold_id
        """
    )


def get_compliance_path(
    conn: "Neo4jConnection",
    entity_id: str,
    entity_type: str,
    regulation_id: str | None = None,
) -> dict:
    """
    Cross-layer compliance traversal.
    Walks: entity → Borrower → Jurisdiction → Regulation → Section
           → Requirement → Threshold

    Returns a structured dict with entity, jurisdiction, regulations,
    and all applicable thresholds for the entity's loan type.
    """
    # Step 1: Resolve entity → borrower → jurisdiction
    if entity_type == "LoanApplication":
        entity_rows = conn.run_query(
            """
            MATCH (l:LoanApplication {loan_id: $id})-[:SUBMITTED_BY]->(b:Borrower)
            OPTIONAL MATCH (b)-[:RESIDES_IN|REGISTERED_IN]->(j:Jurisdiction)
            OPTIONAL MATCH (l)-[:BACKED_BY]->(c:Collateral)
            RETURN l.loan_id                    AS loan_id,
                   l.loan_type                  AS loan_type,
                   l.amount                     AS amount,
                   l.lvr                        AS lvr,
                   l.interest_rate_indicative   AS interest_rate_pct,
                   b.borrower_id                AS borrower_id,
                   b.name                       AS borrower_name,
                   b.risk_rating                AS risk_rating,
                   j.jurisdiction_id            AS jurisdiction_id,
                   j.name                       AS jurisdiction_name,
                   j.aml_risk_rating            AS jurisdiction_aml_risk,
                   c.estimated_value            AS collateral_value
            """,
            {"id": entity_id},
        )
        jurisdiction_id = (entity_rows[0].get("jurisdiction_id") or "JUR-AU-FED") if entity_rows else "JUR-AU-FED"
        loan_type = (entity_rows[0].get("loan_type") or "residential_secured") if entity_rows else "residential_secured"
    else:
        entity_rows = conn.run_query(
            """
            MATCH (b:Borrower {borrower_id: $id})
            OPTIONAL MATCH (b)-[:RESIDES_IN|REGISTERED_IN]->(j:Jurisdiction)
            RETURN b.borrower_id     AS borrower_id,
                   b.name            AS name,
                   b.risk_rating     AS risk_rating,
                   j.jurisdiction_id AS jurisdiction_id,
                   j.aml_risk_rating AS jurisdiction_aml_risk
            """,
            {"id": entity_id},
        )
        jurisdiction_id = (entity_rows[0].get("jurisdiction_id") or "JUR-AU-FED") if entity_rows else "JUR-AU-FED"
        loan_type = "residential_secured"

    # Step 2: Regulations → Sections → Requirements → Thresholds via jurisdiction
    reg_filter = "WHERE reg.regulation_id = $reg_id" if regulation_id else ""
    reg_params: dict = {"jur_id": jurisdiction_id, "loan_type": loan_type}
    if regulation_id:
        reg_params["reg_id"] = regulation_id

    reg_rows = conn.run_query(
        f"""
        MATCH (reg:Regulation)-[:APPLIES_TO_JURISDICTION]->(j:Jurisdiction {{jurisdiction_id: $jur_id}})
        {reg_filter}
        WITH reg
        MATCH (reg)-[:HAS_SECTION]->(s:Section)-[:HAS_REQUIREMENT]->(req:Requirement)
        WHERE req.applies_to_loan_type = $loan_type
           OR req.applies_to_loan_type = 'all'
           OR req.applies_to_loan_type IS NULL
        OPTIONAL MATCH (req)-[:DEFINES_LIMIT]->(t:Threshold)
        RETURN reg.regulation_id    AS regulation_id,
               reg.name             AS regulation_name,
               reg.is_enforceable   AS is_enforceable,
               s.section_id         AS section_id,
               s.title              AS section_title,
               req.requirement_id   AS requirement_id,
               req.description      AS requirement_description,
               req.severity         AS severity,
               req.is_quantitative  AS is_quantitative,
               t.threshold_id       AS threshold_id,
               t.metric             AS metric,
               t.operator           AS operator,
               t.value              AS threshold_value,
               t.unit               AS unit,
               t.consequence        AS consequence
        ORDER BY reg.regulation_id, req.requirement_id
        """,
        reg_params,
    )

    # Step 3: Group into structured dict
    result: dict = {
        "entity": entity_rows[0] if entity_rows else {},
        "jurisdiction_id": jurisdiction_id,
        "regulations": {},
    }
    for row in reg_rows:
        rid = row["regulation_id"]
        if rid not in result["regulations"]:
            result["regulations"][rid] = {
                "regulation_id": rid,
                "name": row["regulation_name"],
                "is_enforceable": row["is_enforceable"],
                "sections": {},
            }
        sid = row["section_id"]
        if sid not in result["regulations"][rid]["sections"]:
            result["regulations"][rid]["sections"][sid] = {
                "section_id": sid,
                "title": row["section_title"],
                "requirements": {},
            }
        req_id = row["requirement_id"]
        if req_id not in result["regulations"][rid]["sections"][sid]["requirements"]:
            result["regulations"][rid]["sections"][sid]["requirements"][req_id] = {
                "requirement_id": req_id,
                "description": row["requirement_description"],
                "severity": row["severity"],
                "is_quantitative": row["is_quantitative"],
                "thresholds": [],
            }
        if row.get("threshold_id"):
            result["regulations"][rid]["sections"][sid]["requirements"][req_id][
                "thresholds"
            ].append(
                {
                    "threshold_id": row["threshold_id"],
                    "metric": row["metric"],
                    "operator": row["operator"],
                    "value": row["threshold_value"],
                    "unit": row["unit"],
                    "consequence": row["consequence"],
                }
            )

    return result


def get_chunks_for_section(
    conn: "Neo4jConnection",
    section_id: str,
) -> list[dict]:
    """Return all chunks for a section in order."""
    return conn.run_query(
        """
        MATCH (s:Section {section_id: $id})-[:HAS_CHUNK]->(c:Chunk)
        RETURN c.chunk_id    AS chunk_id,
               c.text        AS text,
               c.chunk_index AS chunk_index,
               c.token_count AS token_count
        ORDER BY c.chunk_index
        """,
        {"id": section_id},
    )


def vector_search_chunks(
    conn: "Neo4jConnection",
    embedding: list[float],
    top_k: int = 5,
    regulation_id: str | None = None,
) -> list[dict]:
    """
    Semantic similarity search over Chunk nodes using the chunk_embeddings
    vector index (cosine similarity).
    """
    if regulation_id:
        return conn.run_query(
            """
            CALL db.index.vector.queryNodes('chunk_embeddings', $k, $emb)
            YIELD node AS c, score
            WHERE c.source_document = $reg_id
            RETURN c.chunk_id    AS chunk_id,
                   c.section_id  AS section_id,
                   c.text        AS text,
                   c.chunk_index AS chunk_index,
                   score
            ORDER BY score DESC
            """,
            {"k": top_k, "emb": embedding, "reg_id": regulation_id},
        )
    return conn.run_query(
        """
        CALL db.index.vector.queryNodes('chunk_embeddings', $k, $emb)
        YIELD node AS c, score
        RETURN c.chunk_id    AS chunk_id,
               c.section_id  AS section_id,
               c.text        AS text,
               c.chunk_index AS chunk_index,
               c.source_document AS source_document,
               score
        ORDER BY score DESC
        """,
        {"k": top_k, "emb": embedding},
    )


# ===========================================================================
# LAYER 3 — Assessment helpers
# ===========================================================================


def get_assessments_for_entity(
    conn: "Neo4jConnection",
    entity_id: str,
) -> list[dict]:
    """Return all Assessment nodes for a given entity."""
    return conn.run_query(
        """
        MATCH (a:Assessment {entity_id: $id})
        OPTIONAL MATCH (a)-[:HAS_FINDING]->(f:Finding)
        RETURN a.assessment_id  AS assessment_id,
               a.entity_id      AS entity_id,
               a.entity_type    AS entity_type,
               a.regulation_id  AS regulation_id,
               a.verdict        AS verdict,
               a.confidence     AS confidence,
               a.agent          AS agent,
               a.created_at     AS created_at,
               collect(DISTINCT {
                 finding_id: f.finding_id,
                 severity: f.severity,
                 description: f.description
               }) AS findings
        ORDER BY a.created_at DESC
        """,
        {"id": entity_id},
    )


def get_assessment_with_evidence(
    conn: "Neo4jConnection",
    assessment_id: str,
) -> dict:
    """
    Walk an Assessment back to all cited sections and chunks.
    Returns the full reasoning chain for display in the evidence panel.
    """
    assessment = conn.run_query(
        """
        MATCH (a:Assessment {assessment_id: $id})
        RETURN a.assessment_id  AS assessment_id,
               a.entity_id      AS entity_id,
               a.entity_type    AS entity_type,
               a.regulation_id  AS regulation_id,
               a.verdict        AS verdict,
               a.confidence     AS confidence,
               a.agent          AS agent,
               a.created_at     AS created_at
        """,
        {"id": assessment_id},
    )

    findings = conn.run_query(
        """
        MATCH (a:Assessment {assessment_id: $id})-[:HAS_FINDING]->(f:Finding)
        RETURN f.finding_id    AS finding_id,
               f.finding_type AS finding_type,
               f.severity     AS severity,
               f.description  AS description,
               f.pattern_name AS pattern_name
        ORDER BY f.severity
        """,
        {"id": assessment_id},
    )

    steps = conn.run_query(
        """
        MATCH (a:Assessment {assessment_id: $id})-[:HAS_STEP]->(rs:ReasoningStep)
        OPTIONAL MATCH (rs)-[:CITES_SECTION]->(s:Section)
        OPTIONAL MATCH (rs)-[:CITES_CHUNK]->(c:Chunk)
        RETURN rs.step_number   AS step_number,
               rs.description   AS description,
               rs.cypher_used   AS cypher_used,
               collect(DISTINCT s.section_id)  AS cited_section_ids,
               collect(DISTINCT c.chunk_id)    AS cited_chunk_ids
        ORDER BY rs.step_number
        """,
        {"id": assessment_id},
    )

    return {
        "assessment": assessment[0] if assessment else {},
        "findings": findings,
        "reasoning_steps": steps,
    }


def merge_assessment(
    conn: "Neo4jConnection",
    assessment_id: str,
    entity_id: str,
    entity_type: str,
    regulation_id: str,
    verdict: str,
    confidence: float,
    agent: str,
    created_at: str,
) -> None:
    """Create or update an Assessment node (idempotent)."""
    id_prop = "loan_id" if entity_type == "LoanApplication" else "borrower_id"
    conn.run_query(
        f"""
        MERGE (a:Assessment {{assessment_id: $aid}})
        SET a.entity_id     = $entity_id,
            a.entity_type   = $entity_type,
            a.regulation_id = $regulation_id,
            a.verdict       = $verdict,
            a.confidence    = $confidence,
            a.agent         = $agent,
            a.created_at    = $created_at
        WITH a
        MATCH (e:{entity_type} {{{id_prop}: $entity_id}})
        MERGE (e)-[:HAS_ASSESSMENT]->(a)
        WITH a
        MATCH (reg:Regulation {{regulation_id: $regulation_id}})
        MERGE (a)-[:ASSESSED_UNDER]->(reg)
        """,
        {
            "aid": assessment_id,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "regulation_id": regulation_id,
            "verdict": verdict,
            "confidence": confidence,
            "agent": agent,
            "created_at": created_at,
        },
    )


def merge_finding(
    conn: "Neo4jConnection",
    finding_id: str,
    assessment_id: str,
    finding_type: str,
    severity: str,
    description: str,
    pattern_name: str | None,
    related_entity_id: str | None,
    related_entity_type: str | None,
) -> None:
    """Create or update a Finding node linked to an Assessment (idempotent)."""
    conn.run_query(
        """
        MERGE (f:Finding {finding_id: $fid})
        SET f.finding_type = $finding_type,
            f.severity     = $severity,
            f.description  = $description,
            f.pattern_name = $pattern_name
        WITH f
        MATCH (a:Assessment {assessment_id: $aid})
        MERGE (a)-[:HAS_FINDING]->(f)
        """,
        {
            "fid": finding_id,
            "aid": assessment_id,
            "finding_type": finding_type,
            "severity": severity,
            "description": description,
            "pattern_name": pattern_name,
        },
    )


def merge_reasoning_step(
    conn: "Neo4jConnection",
    step_id: str,
    assessment_id: str,
    step_number: int,
    description: str,
    cypher_used: str | None,
    section_ids: list[str],
    chunk_ids: list[str],
) -> None:
    """Create a ReasoningStep and link it to cited sections/chunks."""
    conn.run_query(
        """
        MERGE (rs:ReasoningStep {step_id: $sid})
        SET rs.step_number  = $step_number,
            rs.description  = $description,
            rs.cypher_used  = $cypher_used
        WITH rs
        MATCH (a:Assessment {assessment_id: $aid})
        MERGE (a)-[:HAS_STEP]->(rs)
        """,
        {
            "sid": step_id,
            "aid": assessment_id,
            "step_number": step_number,
            "description": description,
            "cypher_used": cypher_used,
        },
    )
    for sec_id in section_ids:
        conn.run_query(
            """
            MATCH (rs:ReasoningStep {step_id: $sid})
            MATCH (s:Section {section_id: $sec_id})
            MERGE (rs)-[:CITES_SECTION]->(s)
            """,
            {"sid": step_id, "sec_id": sec_id},
        )
    for chunk_id in chunk_ids:
        conn.run_query(
            """
            MATCH (rs:ReasoningStep {step_id: $sid})
            MATCH (c:Chunk {chunk_id: $cid})
            MERGE (rs)-[:CITES_CHUNK]->(c)
            """,
            {"sid": step_id, "cid": chunk_id},
        )
