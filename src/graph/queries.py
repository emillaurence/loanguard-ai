"""
Reusable Cypher query helpers organised by Neo4j graph layer.

Three layers:
  1. Entity Layer        - Customers, LoanAccounts, Transactions
  2. Regulatory Layer    - APRA Regulations, Obligations
  3. Runtime Assessment  - ComplianceAssessments, ComplianceFlags

All helpers accept a Neo4jConnection and return List[dict].

# TODO: Replace placeholder node labels and relationship types with your
#       actual graph schema once the AuraDB instance is seeded.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.graph.connection import Neo4jConnection


# ===========================================================================
# LAYER 1 – Entity Layer
# ===========================================================================


def get_loan_accounts(conn: "Neo4jConnection", limit: int = 100) -> list[dict]:
    """
    Return loan account records.

    # TODO: Adjust property names to match your LoanAccount node schema.
    """
    cypher = """
    MATCH (l:LoanAccount)
    RETURN
        l.account_id   AS account_id,
        l.customer_id  AS customer_id,
        l.product_type AS product_type,
        l.balance      AS balance,
        l.currency     AS currency,
        l.status       AS status,
        l.risk_rating  AS risk_rating
    ORDER BY l.account_id
    LIMIT $limit
    """
    return conn.run_query(cypher, {"limit": limit})


def get_transactions_for_account(
    conn: "Neo4jConnection",
    account_id: str,
    limit: int = 50,
) -> list[dict]:
    """
    Return recent transactions linked to a specific loan account.

    # TODO: Confirm the relationship type between LoanAccount and Transaction.
    """
    cypher = """
    MATCH (l:LoanAccount {account_id: $account_id})-[:HAS_TRANSACTION]->(t:Transaction)
    RETURN
        t.transaction_id AS transaction_id,
        t.amount         AS amount,
        t.currency       AS currency,
        t.type           AS type,
        t.timestamp      AS timestamp,
        t.counterparty   AS counterparty,
        t.suspicious     AS suspicious
    ORDER BY t.timestamp DESC
    LIMIT $limit
    """
    return conn.run_query(cypher, {"account_id": account_id, "limit": limit})


def get_customer(conn: "Neo4jConnection", customer_id: str) -> list[dict]:
    """
    Return a customer node and its linked loan accounts.

    # TODO: Adjust Customer node properties.
    """
    cypher = """
    MATCH (c:Customer {customer_id: $customer_id})
    OPTIONAL MATCH (c)-[:HOLDS]->(l:LoanAccount)
    RETURN
        c.customer_id   AS customer_id,
        c.name          AS name,
        c.kyc_status    AS kyc_status,
        c.risk_category AS risk_category,
        collect(l.account_id) AS loan_accounts
    """
    return conn.run_query(cypher, {"customer_id": customer_id})


# ===========================================================================
# LAYER 2 – Regulatory Layer
# ===========================================================================


def get_apra_obligations(
    conn: "Neo4jConnection",
    entity_type: str | None = None,
) -> list[dict]:
    """
    Return APRA regulatory obligations, optionally filtered by entity type.

    Args:
        entity_type: e.g. "ADI", "insurer", "superannuation" — filters
                     obligations that apply to a specific entity type.

    # TODO: Adjust node labels if your schema uses different names
    #       (e.g. Obligation instead of RegulatoryObligation).
    """
    if entity_type:
        cypher = """
        MATCH (r:Regulation)-[:CONTAINS]->(o:Obligation)
        WHERE o.applies_to = $entity_type
        RETURN
            r.standard_id  AS standard_id,
            r.title        AS regulation_title,
            o.obligation_id AS obligation_id,
            o.description  AS description,
            o.applies_to   AS applies_to,
            o.severity     AS severity
        ORDER BY r.standard_id, o.obligation_id
        """
        return conn.run_query(cypher, {"entity_type": entity_type})
    else:
        cypher = """
        MATCH (r:Regulation)-[:CONTAINS]->(o:Obligation)
        RETURN
            r.standard_id  AS standard_id,
            r.title        AS regulation_title,
            o.obligation_id AS obligation_id,
            o.description  AS description,
            o.applies_to   AS applies_to,
            o.severity     AS severity
        ORDER BY r.standard_id, o.obligation_id
        """
        return conn.run_query(cypher)


def get_regulation_by_standard(
    conn: "Neo4jConnection",
    standard_id: str,
) -> list[dict]:
    """
    Return a regulation and all its obligations by APRA standard ID (e.g. "CPS 220").

    # TODO: Confirm standard_id property name on the Regulation node.
    """
    cypher = """
    MATCH (r:Regulation {standard_id: $standard_id})-[:CONTAINS]->(o:Obligation)
    RETURN
        r.standard_id  AS standard_id,
        r.title        AS regulation_title,
        r.effective_date AS effective_date,
        o.obligation_id  AS obligation_id,
        o.description    AS description,
        o.severity       AS severity
    ORDER BY o.obligation_id
    """
    return conn.run_query(cypher, {"standard_id": standard_id})


# ===========================================================================
# LAYER 3 – Runtime Assessment Layer
# ===========================================================================


def get_compliance_assessments(
    conn: "Neo4jConnection",
    account_id: str | None = None,
) -> list[dict]:
    """
    Return compliance assessment records, optionally filtered by account.

    # TODO: Confirm relationship type from LoanAccount to ComplianceAssessment.
    """
    if account_id:
        cypher = """
        MATCH (l:LoanAccount {account_id: $account_id})-[:HAS_ASSESSMENT]->(a:ComplianceAssessment)
        OPTIONAL MATCH (a)-[:REFERENCES]->(o:Obligation)
        RETURN
            a.assessment_id  AS assessment_id,
            a.assessed_at    AS assessed_at,
            a.outcome        AS outcome,
            a.score          AS score,
            a.notes          AS notes,
            o.obligation_id  AS obligation_id,
            o.description    AS obligation_description
        ORDER BY a.assessed_at DESC
        """
        return conn.run_query(cypher, {"account_id": account_id})
    else:
        cypher = """
        MATCH (a:ComplianceAssessment)
        OPTIONAL MATCH (a)-[:REFERENCES]->(o:Obligation)
        RETURN
            a.assessment_id  AS assessment_id,
            a.assessed_at    AS assessed_at,
            a.outcome        AS outcome,
            a.score          AS score,
            a.notes          AS notes,
            o.obligation_id  AS obligation_id
        ORDER BY a.assessed_at DESC
        LIMIT 200
        """
        return conn.run_query(cypher)


def get_compliance_flags(
    conn: "Neo4jConnection",
    severity: str | None = None,
) -> list[dict]:
    """
    Return active compliance flags, optionally filtered by severity.

    Args:
        severity: "HIGH", "MEDIUM", or "LOW"

    # TODO: Adjust ComplianceFlag node properties.
    """
    if severity:
        cypher = """
        MATCH (f:ComplianceFlag)
        WHERE f.severity = $severity AND f.status = 'OPEN'
        OPTIONAL MATCH (f)-[:FLAGGED_ON]->(l:LoanAccount)
        RETURN
            f.flag_id     AS flag_id,
            f.reason      AS reason,
            f.severity    AS severity,
            f.raised_at   AS raised_at,
            f.status      AS status,
            l.account_id  AS account_id
        ORDER BY f.raised_at DESC
        """
        return conn.run_query(cypher, {"severity": severity})
    else:
        cypher = """
        MATCH (f:ComplianceFlag)
        WHERE f.status = 'OPEN'
        OPTIONAL MATCH (f)-[:FLAGGED_ON]->(l:LoanAccount)
        RETURN
            f.flag_id     AS flag_id,
            f.reason      AS reason,
            f.severity    AS severity,
            f.raised_at   AS raised_at,
            f.status      AS status,
            l.account_id  AS account_id
        ORDER BY f.severity, f.raised_at DESC
        """
        return conn.run_query(cypher)
