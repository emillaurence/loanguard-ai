"""
Shared schema, dataclasses, and anomaly registry for the 31x investigation system.

Provides:
  GRAPH_SCHEMA_HINT  — full schema string injected into Claude's system prompt
  ANOMALY_REGISTRY   — dict mapping pattern name → Cypher + metadata
  Dataclasses        — AnomalyFinding, ComplianceResult, InvestigationResponse
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Graph Schema Hint
# Injected into Claude's system prompt so it can generate valid Cypher
# without calling get-neo4j-schema on every turn.
# ---------------------------------------------------------------------------

GRAPH_SCHEMA_HINT = """
## Neo4j Graph Schema

### LAYER 1 — Financial Entity Graph

Node: Borrower
  Properties: borrower_id (str, unique), name (str), type (str: individual|corporate),
              entity_subtype (str: natural_person|proprietary_company),
              credit_score (int), risk_rating (str: high|medium|low),
              status (str: active|inactive), annual_revenue (float),
              employee_count (int), tax_id (str)
  Secondary labels: :Individual (type=individual), :Corporate (type=corporate)

Node: LoanApplication
  Properties: loan_id (str, unique), loan_type (str: residential_secured),
              amount (float, AUD), currency (str), term_months (int),
              purpose (str: home_purchase), interest_rate_indicative (float, %),
              lvr (float, %), submission_date (date), status (str: under_review),
              description (str)
  Secondary labels: :ResidentialSecured

Node: BankAccount
  Properties: account_id (str, unique), bank_name (str), account_type (str),
              currency (str), country (str), opened_date (date),
              status (str: active), average_monthly_balance (float, AUD)

Node: Transaction
  Properties: transaction_id (str, unique), from_account_id (str),
              to_account_id (str), amount (float, AUD), currency (str),
              date (date), type (str), description (str),
              flagged_suspicious (bool)

Node: Collateral
  Properties: collateral_id (str, unique), type (str: residential_property),
              description (str — contains address), estimated_value (float, AUD),
              currency (str), valuation_date (date), valuation_source (str),
              encumbered (bool)

Node: Officer
  Properties: officer_id (str, unique), name (str), date_of_birth (date),
              nationality (str), is_pep (bool), pep_detail (str),
              sanctions_match (bool)

Node: Address
  Properties: address_id (str, unique), street (str), city (str), state (str),
              postcode (str), country (str), address_type (str: residential|business)

Node: Jurisdiction
  Properties: jurisdiction_id (str, unique), name (str), country (str),
              level (str), regulatory_body (str),
              aml_risk_rating (str: low|medium|high)
  Known values: JUR-AU-FED (Australia, low), JUR-SG (Singapore, low),
                JUR-HK (Hong Kong, medium), JUR-VU (Vanuatu, high),
                JUR-MY (Malaysia, medium), JUR-MM (Myanmar, high),
                JUR-KH (Cambodia, high)

Node: Industry
  Properties: industry_id (str, unique), code (str), name (str),
              division (str), risk_category (str: low|medium|high),
              aml_sensitivity (str: low|medium|high)
  Notable: IND-9530 (Gambling, risk_category=high, aml_sensitivity=high),
           IND-6240 (Financial Asset Investing, high/high),
           IND-5120 (Liquor & Tobacco Wholesaling, medium/high)

### LAYER 1 Relationships

(LoanApplication)-[:SUBMITTED_BY {role}]->(Borrower)
(LoanApplication)-[:BACKED_BY {lien_position, coverage_ratio}]->(Collateral)
(LoanApplication)-[:GUARANTEED_BY {guarantee_type, guarantee_amount, currency}]->(Borrower)
(Borrower)-[:HAS_ACCOUNT {role, authorized_signatory}]->(BankAccount)
(Borrower:Individual)-[:RESIDES_IN {residency_type, tax_id}]->(Jurisdiction)
(Borrower:Corporate)-[:REGISTERED_IN {registration_type, registration_number}]->(Jurisdiction)
(Borrower)-[:LOCATED_AT]->(Address)
(Officer)-[:DIRECTOR_OF {role, appointed_date, is_current}]->(Borrower)
(Borrower)-[:BELONGS_TO_INDUSTRY {is_primary, revenue_percentage}]->(Industry)
(Borrower)-[:OWNS {ownership_percentage, ownership_type, effective_date}]->(Borrower)
(Transaction)-[:FROM_ACCOUNT]->(BankAccount)
(Transaction)-[:TO_ACCOUNT]->(BankAccount)

### LAYER 2 — Regulatory Graph

Node: Regulation
  Properties: regulation_id (str: APS-112|APG-223|APS-220), name (str),
              issuing_body (str: APRA), document_type (str),
              effective_date (date), is_enforceable (bool)

Node: Section
  Properties: section_id (str, e.g. APG-223-S3), regulation_id (str),
              section_number (str), title (str), content_summary (str),
              text (str), section_type (str)

Node: Requirement
  Properties: requirement_id (str, e.g. APG-223-REQ-015), regulation_id (str),
              section_id (str), description (str), requirement_type (str),
              is_quantitative (bool), applies_to_loan_type (str: residential_secured),
              severity (str: mandatory|expected|recommended)

Node: Threshold
  Properties: threshold_id (str, e.g. APG-223-THR-008), regulation_id (str),
              requirement_id (str), metric (str), operator (str: >=|<=|==),
              value (float), value_upper (float|null), unit (str), consequence (str)
  Key thresholds:
    APG-223-THR-003: interest_rate_serviceability_buffer >= 3.0 percent
    APG-223-THR-006: non_salary_income_haircut >= 20.0 percent
    APG-223-THR-008: LVR >= 90.0 percent → HIGH risk (senior review required)
    APS-112-THR-031: commercial_property_haircut_for_LVR == 40.0 percent
    APS-112-THR-032: LMI_loss_coverage >= 40.0 percent

Node: Chunk
  Properties: chunk_id (str), section_id (str), text (str),
              token_count (int), chunk_index (int), source_document (str),
              embedding (list[float] — 1536 dims, OpenAI text-embedding-3-small)

### LAYER 2 Relationships

(Regulation)-[:APPLIES_TO_JURISDICTION]->(Jurisdiction)  ← bridge to Layer 1
(Regulation)-[:HAS_SECTION]->(Section)
(Section)-[:HAS_REQUIREMENT]->(Requirement)
(Section)-[:HAS_CHUNK]->(Chunk)
(Section)-[:NEXT_SECTION]->(Section)
(Section)-[:CROSS_REFERENCES]->(Section)
(Requirement)-[:DEFINES_LIMIT]->(Threshold)
(Chunk)-[:NEXT_CHUNK]->(Chunk)
(Chunk)-[:SEMANTICALLY_SIMILAR {score}]->(Chunk)

### LAYER 3 — Compliance Assessment (runtime, written by agents)

Node: Assessment
  Properties: assessment_id (str, unique), entity_id (str), entity_type (str),
              regulation_id (str), verdict (str: COMPLIANT|NON_COMPLIANT|
              REQUIRES_REVIEW|ANOMALY_DETECTED|INFORMATIONAL),
              confidence (float 0-1), agent (str), created_at (datetime)

Node: Finding
  Properties: finding_id (str), finding_type (str: compliance_breach|anomaly|
              risk_signal|information), severity (str: HIGH|MEDIUM|LOW|INFO),
              description (str), pattern_name (str|null)

Node: ReasoningStep
  Properties: step_id (str), step_number (int), description (str),
              cypher_used (str|null)

### LAYER 3 Relationships

(LoanApplication|Borrower)-[:HAS_ASSESSMENT]->(Assessment)
(Assessment)-[:ASSESSED_UNDER]->(Requirement|Regulation)
(Assessment)-[:HAS_FINDING]->(Finding)
(Assessment)-[:HAS_STEP]->(ReasoningStep)
(ReasoningStep)-[:CITES_SECTION]->(Section)
(ReasoningStep)-[:CITES_CHUNK]->(Chunk)
(Finding)-[:RELATES_TO]->(Borrower|LoanApplication|BankAccount|Transaction)

### Cross-layer bridge

(Borrower)-[:RESIDES_IN|REGISTERED_IN]->(Jurisdiction)<-[:APPLIES_TO_JURISDICTION]-(Regulation)
All APRA regulations link to JUR-AU-FED.

### Cypher Best Practices

- For variable-length paths like `(a)-[r:OWNS*1..3]->(b)`, use `size(r)` to count
  relationships, NOT `length(r)` (which expects a Path, not a List<Relationship>).
- Collect relationship types with `[rel IN r | type(rel)]` instead of `type(r)`.
- Always use parameterised queries (`$param`) — never string interpolation.
"""


# ---------------------------------------------------------------------------
# Anomaly Registry
# Each entry: Cypher query confirmed against real data + metadata.
# ---------------------------------------------------------------------------

ANOMALY_REGISTRY: dict[str, dict] = {
    "transaction_structuring": {
        "description": (
            "Multiple sub-$10,000 suspicious transfers flowing into the same "
            "bank account from distinct sources. Pattern consistent with "
            "structuring to avoid AUSTRAC threshold reporting."
        ),
        "severity": "HIGH",
        "cypher": """
MATCH (t:Transaction)-[:TO_ACCOUNT]->(target:BankAccount)
WHERE t.flagged_suspicious = true
  AND t.amount < 10000
WITH target,
     count(t)                                       AS tx_count,
     sum(t.amount)                                  AS total_amount,
     collect(DISTINCT t.from_account_id)[0..10]     AS source_accounts,
     collect(t.transaction_id)[0..10]               AS sample_txn_ids,
     min(t.date)                                    AS earliest,
     max(t.date)                                    AS latest
WHERE tx_count >= 3
RETURN target.account_id   AS target_account,
       tx_count,
       round(total_amount) AS total_amount_aud,
       source_accounts,
       sample_txn_ids,
       earliest,
       latest
ORDER BY tx_count DESC
LIMIT 20
""",
    },

    "high_lvr_loans": {
        "description": (
            "Loan applications with LVR >= 90%. Per APG-223-THR-008, LVRs "
            "above 90% (including capitalised LMI) require senior management "
            "review with Board oversight."
        ),
        "severity": "HIGH",
        "threshold_id": "APG-223-THR-008",
        "cypher": """
MATCH (l:LoanApplication)
WHERE l.lvr >= 90
MATCH (l)-[:SUBMITTED_BY]->(b:Borrower)
OPTIONAL MATCH (l)-[:BACKED_BY]->(c:Collateral)
RETURN l.loan_id            AS loan_id,
       l.lvr                AS lvr,
       l.amount             AS amount_aud,
       l.interest_rate_indicative AS rate_pct,
       b.borrower_id        AS borrower_id,
       b.name               AS borrower_name,
       b.risk_rating        AS borrower_risk,
       c.estimated_value    AS collateral_value,
       c.valuation_source   AS valuation_source
ORDER BY l.lvr DESC
""",
    },

    "high_risk_industry": {
        "description": (
            "Borrowers operating in industries with high AML sensitivity "
            "(Gambling, Financial Asset Investing, Liquor & Tobacco). "
            "Requires enhanced due diligence."
        ),
        "severity": "MEDIUM",
        "cypher": """
MATCH (b:Borrower)-[:BELONGS_TO_INDUSTRY]->(i:Industry)
WHERE i.aml_sensitivity = 'high'
   OR i.risk_category = 'high'
OPTIONAL MATCH (b)<-[:SUBMITTED_BY]-(l:LoanApplication)
OPTIONAL MATCH (b)-[:HAS_ACCOUNT]->(a:BankAccount)
RETURN b.borrower_id        AS borrower_id,
       b.name               AS name,
       b.type               AS borrower_type,
       i.industry_id        AS industry_id,
       i.name               AS industry_name,
       i.risk_category      AS industry_risk,
       i.aml_sensitivity    AS aml_sensitivity,
       collect(DISTINCT l.loan_id)   AS loan_ids,
       collect(DISTINCT a.account_id) AS account_ids
ORDER BY i.aml_sensitivity DESC, b.borrower_id
""",
    },

    "layered_ownership": {
        "description": (
            "Multi-hop OWNS chains (depth >= 2). Complex beneficial ownership "
            "structures may be used to obscure true controllers or aggregate "
            "exposure across related entities."
        ),
        "severity": "MEDIUM",
        "cypher": """
MATCH path = (owner:Borrower)-[:OWNS*2..]->(subsidiary:Borrower)
WITH owner,
     subsidiary,
     length(path)                                              AS chain_depth,
     [n IN nodes(path) | n.borrower_id]                       AS ownership_chain,
     [r IN relationships(path) | r.ownership_percentage]      AS pct_chain,
     [r IN relationships(path) | r.ownership_type]            AS type_chain
WHERE chain_depth >= 2
OPTIONAL MATCH (subsidiary)<-[:SUBMITTED_BY]-(l:LoanApplication)
RETURN owner.borrower_id       AS ultimate_owner_id,
       owner.name              AS ultimate_owner_name,
       subsidiary.borrower_id  AS subsidiary_id,
       subsidiary.name         AS subsidiary_name,
       chain_depth,
       ownership_chain,
       pct_chain,
       type_chain,
       collect(DISTINCT l.loan_id) AS subsidiary_loans
ORDER BY chain_depth DESC, owner.borrower_id
LIMIT 30
""",
    },

    "high_risk_jurisdiction": {
        "description": (
            "Borrowers residing in or registered in jurisdictions with "
            "aml_risk_rating = 'high' (Vanuatu JUR-VU, Myanmar JUR-MM, "
            "Cambodia JUR-KH). Requires enhanced AML/CTF due diligence."
        ),
        "severity": "HIGH",
        "cypher": """
MATCH (b:Borrower)-[r:RESIDES_IN|REGISTERED_IN]->(j:Jurisdiction)
WHERE j.aml_risk_rating = 'high'
OPTIONAL MATCH (b)<-[:SUBMITTED_BY]-(l:LoanApplication)
OPTIONAL MATCH (b)-[:HAS_ACCOUNT]->(a:BankAccount)
RETURN b.borrower_id            AS borrower_id,
       b.name                   AS name,
       b.type                   AS borrower_type,
       type(r)                  AS link_type,
       j.jurisdiction_id        AS jurisdiction_id,
       j.name                   AS jurisdiction_name,
       j.country                AS country,
       j.aml_risk_rating        AS aml_risk_rating,
       collect(DISTINCT l.loan_id)    AS loan_ids,
       collect(DISTINCT a.account_id) AS account_ids
ORDER BY j.jurisdiction_id, b.borrower_id
""",
    },

    "guarantor_concentration": {
        "description": (
            "Borrowers acting as guarantor on 3 or more loan applications. "
            "High guarantor concentration creates contingent liability "
            "exposure that may not be apparent from single-loan review."
        ),
        "severity": "MEDIUM",
        "cypher": """
MATCH (b:Borrower)<-[:GUARANTEED_BY]-(l:LoanApplication)
WITH b,
     count(l)                             AS guarantor_degree,
     sum(l.amount)                        AS total_guaranteed_aud,
     collect(l.loan_id)[0..10]            AS loan_ids
WHERE guarantor_degree >= 2
OPTIONAL MATCH (b)<-[:SUBMITTED_BY]-(own_loan:LoanApplication)
RETURN b.borrower_id           AS borrower_id,
       b.name                  AS name,
       b.risk_rating           AS risk_rating,
       guarantor_degree,
       round(total_guaranteed_aud) AS total_guaranteed_aud,
       loan_ids,
       count(own_loan)         AS own_loan_count
ORDER BY guarantor_degree DESC, total_guaranteed_aud DESC
LIMIT 20
""",
    },
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AnomalyFinding:
    pattern_name: str
    severity: str
    description: str
    cypher_used: str
    evidence: list[dict] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pattern_name": self.pattern_name,
            "severity": self.severity,
            "description": self.description,
            "cypher_used": self.cypher_used,
            "evidence": self.evidence,
            "entity_ids": self.entity_ids,
        }


@dataclass
class ComplianceResult:
    entity_id: str
    entity_type: str
    regulation_id: str
    verdict: str               # COMPLIANT | NON_COMPLIANT | REQUIRES_REVIEW
    confidence: float
    regulation_ids: list[str] = field(default_factory=list)
    requirement_ids: list[str] = field(default_factory=list)
    section_ids: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    threshold_breaches: list[dict] = field(default_factory=list)
    persisted_findings: list[dict] = field(default_factory=list)
    reasoning_steps: list[str] = field(default_factory=list)
    cypher_used: list[str] = field(default_factory=list)
    assessment_id: str | None = None
    assessment_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class InvestigationResult:
    entity_id: str
    entity_type: str
    connections: list[dict] = field(default_factory=list)
    risk_signals: list[str] = field(default_factory=list)
    path_summaries: list[str] = field(default_factory=list)
    cypher_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class InvestigationResponse:
    """Top-level response returned by the Orchestrator to the chat UI."""
    session_id: str
    question: str
    answer: str
    verdict: str = "INFORMATIONAL"
    confidence: float = 0.0
    routing: dict = field(default_factory=dict)
    findings: list[dict] = field(default_factory=list)
    cypher_used: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    cited_sections: list[dict] = field(default_factory=list)
    cited_chunks: list[dict] = field(default_factory=list)
    recommended_next_steps: list[str] = field(default_factory=list)
    assessment_id: str | None = None
    assessment_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()
