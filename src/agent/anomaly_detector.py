"""
Rule-based graph anomaly detector.

Runs named Cypher patterns against the Layer 1 graph and returns
AnomalyFinding objects. All patterns are confirmed against the actual
data loaded by notebooks/111_structured_data_loader.ipynb.

Usage:
    conn = Neo4jConnection().connect()
    detector = AnomalyDetector(conn)

    # Run one pattern
    findings = detector.run("transaction_structuring")

    # Run all patterns
    all_findings = detector.run_all()
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING

from src.mcp.schema import ANOMALY_REGISTRY, AnomalyFinding

if TYPE_CHECKING:
    from src.graph.connection import Neo4jConnection

logger = logging.getLogger(__name__)


def _extract_entity_ids(rows: list[dict], pattern_name: str) -> list[str]:
    """Pull the primary entity ID from each result row based on the pattern."""
    key = ANOMALY_REGISTRY[pattern_name].id_key if pattern_name in ANOMALY_REGISTRY else ""
    return [str(r[key]) for r in rows if r.get(key) is not None]


class AnomalyDetector:
    """
    Runs rule-based Cypher anomaly patterns and returns AnomalyFinding objects.

    Patterns are defined in src/mcp/schema.ANOMALY_REGISTRY and confirmed
    against actual data in data/layer_1/.
    """

    def __init__(self, conn: "Neo4jConnection") -> None:
        self.conn = conn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, pattern_name: str, entity_id: str = "") -> AnomalyFinding:
        """
        Run a single named anomaly pattern.

        Args:
            pattern_name: Key in ANOMALY_REGISTRY.
            entity_id:    Optional — scopes results to one entity where supported.

        Returns:
            AnomalyFinding with evidence rows and entity_ids populated.

        Raises:
            ValueError: If pattern_name is not registered.
        """
        if pattern_name not in ANOMALY_REGISTRY:
            raise ValueError(
                f"Unknown pattern '{pattern_name}'. "
                f"Valid patterns: {list(ANOMALY_REGISTRY.keys())}"
            )

        spec = ANOMALY_REGISTRY[pattern_name]
        cypher = spec.cypher
        params: dict = {}

        # Scope to entity if the pattern supports it and entity_id is supplied
        if entity_id and pattern_name == "high_lvr_loans":
            cypher = cypher.replace(
                "MATCH (l:LoanApplication)",
                "MATCH (l:LoanApplication {loan_id: $eid})",
            )
            params["eid"] = entity_id
        elif entity_id and pattern_name in ("high_risk_industry", "guarantor_concentration",
                                             "high_risk_jurisdiction", "layered_ownership"):
            cypher = cypher.replace(
                "MATCH (b:Borrower)",
                "MATCH (b:Borrower {borrower_id: $eid})",
            )
            params["eid"] = entity_id

        logger.info("Running anomaly pattern: %s", pattern_name)
        try:
            rows = self.conn.run_query(cypher, params)
        except Exception as e:
            logger.error("Anomaly pattern %s failed: %s", pattern_name, e)
            rows = []

        entity_ids = _extract_entity_ids(rows, pattern_name)

        return AnomalyFinding(
            pattern_name=pattern_name,
            severity=spec.severity,
            description=spec.description,
            cypher_used=spec.cypher.strip(),
            evidence=rows,
            entity_ids=entity_ids,
        )

    def run_all(self, entity_id: str = "") -> list[AnomalyFinding]:
        """
        Run every registered pattern and return findings that have evidence.

        Args:
            entity_id: Optional — scopes each pattern to one entity.

        Returns:
            List of AnomalyFinding objects where evidence is non-empty,
            ordered HIGH → MEDIUM → LOW.
        """
        severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        results: list[AnomalyFinding] = []

        for pattern_name in ANOMALY_REGISTRY:
            finding = self.run(pattern_name, entity_id=entity_id)
            if finding.evidence:
                results.append(finding)
                logger.info(
                    "Pattern %s: %d findings", pattern_name, len(finding.evidence)
                )
            else:
                logger.debug("Pattern %s: no findings", pattern_name)

        results.sort(key=lambda f: severity_order.get(f.severity, 9))
        return results

    def run_for_entity(self, entity_id: str, entity_type: str) -> list[AnomalyFinding]:
        """
        Run all patterns relevant to a specific entity and return those with evidence.

        For a LoanApplication, runs: high_lvr_loans
        For a Borrower, runs: high_risk_industry, high_risk_jurisdiction,
                              layered_ownership, guarantor_concentration
        Also always runs: transaction_structuring (account-level, no entity scoping)
        """
        if entity_type == "LoanApplication":
            patterns = ["high_lvr_loans", "transaction_structuring"]
        else:
            patterns = [
                "high_risk_industry",
                "high_risk_jurisdiction",
                "layered_ownership",
                "guarantor_concentration",
                "transaction_structuring",
            ]

        findings: list[AnomalyFinding] = []
        for p in patterns:
            f = self.run(p, entity_id=entity_id if p != "transaction_structuring" else "")
            if f.evidence:
                # Filter evidence to the entity if not transaction_structuring
                if p != "transaction_structuring" and entity_id:
                    relevant = [
                        r for r in f.evidence
                        if entity_id in str(r.get("borrower_id", ""))
                        or entity_id in str(r.get("loan_id", ""))
                        or entity_id in str(r.get("ultimate_owner_id", ""))
                    ]
                    if relevant:
                        f.evidence = relevant
                        findings.append(f)
                else:
                    findings.append(f)

        return findings
