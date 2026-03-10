# Data Model

LoanGuard AI organises all knowledge across three distinct graph layers in Neo4j, connected through a `Jurisdiction` bridge node.

---

## Three-Layer Architecture

| Layer | Purpose | Loaded from |
|---|---|---|
| **1 — Entity** | Financial entities and relationships: borrowers, loans, accounts, transactions, collateral, officers | CSV files in `data/layer_1/` via notebook 111 |
| **2 — Regulatory** | APRA prudential standards extracted from PDFs: regulations, sections, requirements, thresholds, text chunks with embeddings | CSV files in `data/layer_2/` via notebooks 211–216 |
| **3 — Assessment** | Runtime compliance results written by agents | Neo4j only — no CSV files |

---

## Layer 1 — Entity Nodes

### Borrower

| Property | Type | Notes |
|---|---|---|
| `borrower_id` | str | Primary key (e.g. `BRW-0001`) |
| `name` | str | Full name of individual or company |
| `type` | str | `individual` or `corporate` |
| `entity_subtype` | str | `natural_person` or `proprietary_company` |
| `credit_score` | int | Credit score |
| `risk_rating` | str | `high`, `medium`, or `low` |
| `status` | str | `active` or `inactive` |
| `annual_revenue` | float | Annual revenue in AUD (0 for individuals) |
| `employee_count` | int | Employee count (0 for individuals) |
| `tax_id` | str | Masked TFN (e.g. `TFN-0001-XXX`) |

Secondary labels: `:Individual` (when `type=individual`), `:Corporate` (when `type=corporate`).

**Count in sample dataset:** 628

### LoanApplication

| Property | Type | Notes |
|---|---|---|
| `loan_id` | str | Primary key (e.g. `LOAN-0001`) |
| `loan_type` | str | `residential_secured` |
| `amount` | float | Loan amount in AUD |
| `currency` | str | Always `AUD` |
| `term_months` | int | Loan term in months |
| `purpose` | str | Loan purpose (e.g. `home_purchase`) |
| `interest_rate_indicative` | float | Indicative interest rate in percent |
| `lvr` | float | Loan-to-value ratio in percent |
| `submission_date` | date | Date the application was submitted |
| `status` | str | Application status |
| `description` | str | Free-text description |
| `serviceability_assessment_rate` | float | Rate used in serviceability test |
| `income_type` | str | `salary`, `self_employed`, `mixed`, or `rental` |
| `non_salary_income_haircut_pct` | float (nullable) | Present when `income_type != 'salary'` |
| `rental_income_gross` | float (nullable) | Annual gross rental income in AUD |
| `rental_income_haircut_pct` | float (nullable) | Present when `rental_income_gross` is present |

Secondary label: `:ResidentialSecured`.

**Count in sample dataset:** 466

### BankAccount

| Property | Type | Notes |
|---|---|---|
| `account_id` | str | Primary key (e.g. `ACC-0001`) |
| `bank_name` | str | Name of the institution |
| `account_type` | str | e.g. `personal_transaction`, `business_transaction` |
| `currency` | str | Account currency |
| `country` | str | Country where the account is held |
| `opened_date` | date | Account opening date |
| `status` | str | `active` or `inactive` |
| `average_monthly_balance` | float | Average monthly balance in AUD |

**Count in sample dataset:** 791

### Transaction

| Property | Type | Notes |
|---|---|---|
| `transaction_id` | str | Primary key (e.g. `TXN-0001`) |
| `from_account_id` | str | Source account ID |
| `to_account_id` | str | Destination account ID |
| `amount` | float | Transaction amount in AUD |
| `currency` | str | Always `AUD` |
| `date` | date | Transaction date |
| `type` | str | Transaction type (e.g. `personal_transfer`) |
| `description` | str | Free-text description |
| `flagged_suspicious` | bool | AML review flag |

**Count in sample dataset:** 173

### Collateral

| Property | Type | Notes |
|---|---|---|
| `collateral_id` | str | Primary key (e.g. `COL-0001`) |
| `type` | str | Collateral type (e.g. `residential_property`) |
| `description` | str | Property address |
| `estimated_value` | float | Estimated value in AUD |
| `currency` | str | Always `AUD` |
| `valuation_date` | date | Date of valuation |
| `valuation_source` | str | e.g. `bank_panel_valuer` |
| `encumbered` | bool | Whether already pledged elsewhere |

**Count in sample dataset:** 466

### Officer

| Property | Type | Notes |
|---|---|---|
| `officer_id` | str | Primary key (e.g. `OFF-0001`) |
| `name` | str | Officer's full name |
| `date_of_birth` | date | Date of birth |
| `nationality` | str | Nationality |
| `is_pep` | bool | Politically Exposed Person flag |
| `pep_detail` | str | PEP description if applicable |
| `sanctions_match` | bool | Sanctions list match flag |

**Count in sample dataset:** 19

### Address

| Property | Type | Notes |
|---|---|---|
| `address_id` | str | Primary key |
| `street` | str | Street address |
| `city` | str | City |
| `state` | str | Australian state or territory |
| `postcode` | str | Postcode |
| `country` | str | Country |
| `address_type` | str | `residential` or `business` |

**Count in sample dataset:** 609

### Jurisdiction

| Property | Type | Notes |
|---|---|---|
| `jurisdiction_id` | str | Primary key (e.g. `JUR-AU-FED`) |
| `name` | str | Jurisdiction name |
| `country` | str | Country name |
| `level` | str | `federal` or `national` |
| `regulatory_body` | str | Responsible regulatory body |
| `aml_risk_rating` | str | `low`, `medium`, or `high` |

Known values: `JUR-AU-FED` (Australia Federal, low), `JUR-SG` (Singapore, low), `JUR-HK` (Hong Kong, medium), `JUR-VU` (Vanuatu, high), `JUR-MY` (Malaysia, medium), `JUR-MM` (Myanmar, high), `JUR-KH` (Cambodia, high).

**Count in sample dataset:** 7

### Industry

| Property | Type | Notes |
|---|---|---|
| `industry_id` | str | Primary key (e.g. `IND-9530`) |
| `code` | str | ANZSIC industry code |
| `name` | str | Industry name |
| `division` | str | ANZSIC division |
| `risk_category` | str | `low`, `medium`, or `high` |
| `aml_sensitivity` | str | `low`, `medium`, or `high` |

Notable high-risk industries: Gambling (IND-9530), Financial Asset Investing (IND-6240), Liquor & Tobacco Wholesaling (IND-5120).

**Count in sample dataset:** 14

---

## Layer 1 — Relationships

| Relationship | Source → Target | Key properties |
|---|---|---|
| `SUBMITTED_BY` | LoanApplication → Borrower | `role` |
| `BACKED_BY` | LoanApplication → Collateral | `lien_position`, `coverage_ratio` |
| `GUARANTEED_BY` | LoanApplication → Borrower | `guarantee_type`, `guarantee_amount`, `currency` |
| `HAS_ACCOUNT` | Borrower → BankAccount | `role`, `authorized_signatory` |
| `RESIDES_IN` | Borrower:Individual → Jurisdiction | `residency_type`, `tax_id` |
| `REGISTERED_IN` | Borrower:Corporate → Jurisdiction | `registration_type`, `registration_number` |
| `LOCATED_AT` | Borrower → Address | — |
| `DIRECTOR_OF` | Officer → Borrower | `role`, `appointed_date`, `is_current` |
| `BELONGS_TO_INDUSTRY` | Borrower → Industry | `is_primary`, `revenue_percentage` |
| `OWNS` | Borrower → Borrower | `ownership_percentage`, `ownership_type`, `effective_date` |
| `FROM_ACCOUNT` | Transaction → BankAccount | — |
| `TO_ACCOUNT` | Transaction → BankAccount | — |

---

## Layer 2 — Regulatory Nodes

### Regulation

| Property | Type | Notes |
|---|---|---|
| `regulation_id` | str | `APS-112`, `APG-223`, or `APS-220` |
| `name` | str | Full regulation name |
| `issuing_body` | str | `APRA` |
| `document_type` | str | `Prudential Standard` or `Practice Guide` |
| `effective_date` | date | Document effective date |
| `is_enforceable` | bool | `true` for APS, `false` for APG |

**Count:** 3

### Section

| Property | Type | Notes |
|---|---|---|
| `section_id` | str | e.g. `APG-223-S3` |
| `regulation_id` | str | Parent regulation |
| `section_number` | str | Section number as it appears in the document |
| `title` | str | Section title |
| `text` | str | Full section text |
| `content_summary` | str | Claude-generated summary |
| `section_type` | str | e.g. `body`, `preamble`, `attachment` |

**Count:** 101

### Requirement

| Property | Type | Notes |
|---|---|---|
| `requirement_id` | str | e.g. `APG-223-REQ-015` |
| `regulation_id` | str | Parent regulation |
| `section_id` | str | Parent section |
| `description` | str | Full requirement text |
| `requirement_type` | str | e.g. `quantitative`, `procedural` |
| `is_quantitative` | bool | Whether a numeric threshold is involved |
| `applies_to_loan_type` | str | `residential_secured` |
| `severity` | str | `mandatory`, `expected`, or `recommended` |

**Count:** 219 unique nodes (264 `HAS_REQUIREMENT` relationships including Preamble sections)

### Threshold

| Property | Type | Notes |
|---|---|---|
| `threshold_id` | str | e.g. `APG-223-THR-001` |
| `regulation_id` | str | Parent regulation |
| `requirement_id` | str | Parent requirement |
| `metric` | str | The entity property being measured |
| `operator` | str | `>=`, `<=`, or `==` |
| `value` | float | Threshold value |
| `value_upper` | float (nullable) | Upper bound for range thresholds |
| `unit` | str | `percent`, `years`, `months`, etc. |
| `condition_context` | str | JSON string of applicability conditions |
| `consequence` | str | What happens when threshold is breached/triggered |
| `threshold_type` | str | `minimum`, `maximum`, `trigger`, or `informational` |

**Count:** 133 unique nodes. Type distribution: `minimum` 40, `informational` 177 (note: count exceeds 133 unique nodes because the threshold pipeline may produce thresholds merged to the same node with different relationships), `trigger` 9, `maximum` 18.

### Chunk

| Property | Type | Notes |
|---|---|---|
| `chunk_id` | str | e.g. `APG-223-S3-C001` |
| `section_id` | str | Parent section |
| `regulation_id` | str | Parent regulation |
| `text` | str | Chunk text (~300 tokens) |
| `token_count` | int | Actual token count |
| `chunk_index` | int | Sequential index within the section |
| `source_document` | str | Regulation ID of the source PDF |
| `embedding` | list[float] | 1,536-dimensional OpenAI `text-embedding-3-small` vector |

**Count:** 189

---

## Layer 2 — Relationships

| Relationship | Source → Target | Key properties |
|---|---|---|
| `APPLIES_TO_JURISDICTION` | Regulation → Jurisdiction | — |
| `HAS_SECTION` | Regulation → Section | — |
| `HAS_REQUIREMENT` | Section → Requirement | — |
| `DEFINES_LIMIT` | Requirement → Threshold | — |
| `HAS_CHUNK` | Section → Chunk | — |
| `NEXT_SECTION` | Section → Section | — |
| `NEXT_CHUNK` | Chunk → Chunk | — |
| `CROSS_REFERENCES` | Section → Section | `reference_text` |
| `SEMANTICALLY_SIMILAR` | Chunk → Chunk | `score` (cosine similarity) |

`SEMANTICALLY_SIMILAR` edges are created by notebook 215 for cross-document chunk pairs with cosine similarity > 0.85. Same-document pairs are excluded regardless of similarity score.

---

## Layer 3 — Assessment Nodes

### Assessment

| Property | Type | Notes |
|---|---|---|
| `assessment_id` | str | `ASSESS-{entity_id}-{regulation_id}-{YYYY-MM-DD-HHMMSS}` |
| `entity_id` | str | The assessed entity |
| `entity_type` | str | `LoanApplication` or `Borrower` |
| `regulation_id` | str | Regulation assessed against |
| `verdict` | str | `COMPLIANT`, `NON_COMPLIANT`, `REQUIRES_REVIEW`, `ANOMALY_DETECTED`, `INFORMATIONAL` |
| `confidence` | float | 0.0 to 1.0 |
| `agent` | str | Which agent produced this assessment |
| `created_at` | datetime | UTC ISO 8601 timestamp |

### Finding

| Property | Type | Notes |
|---|---|---|
| `finding_id` | str | `FIND-{assessment_id}-{NNN}` |
| `finding_type` | str | `compliance_breach`, `anomaly`, `risk_signal`, or `information` |
| `severity` | str | `HIGH`, `MEDIUM`, `LOW`, or `INFO` |
| `description` | str | Human-readable finding description |
| `pattern_name` | str (nullable) | Anomaly pattern name if applicable |

### ReasoningStep

| Property | Type | Notes |
|---|---|---|
| `step_id` | str | `STEP-{assessment_id}-{NNN}` |
| `step_number` | int | Sequential step index (1-based) |
| `description` | str | What the agent did in this step |
| `cypher_used` | str (nullable) | Cypher query executed in this step |

---

## Layer 3 — Relationships

| Relationship | Source → Target | Key properties |
|---|---|---|
| `HAS_ASSESSMENT` | LoanApplication/Borrower → Assessment | — |
| `ASSESSED_UNDER` | Assessment → Requirement/Regulation | — |
| `HAS_FINDING` | Assessment → Finding | — |
| `HAS_STEP` | Assessment → ReasoningStep | — |
| `CITES_SECTION` | ReasoningStep → Section | — |
| `CITES_CHUNK` | ReasoningStep → Chunk | `similarity_score` (float, nullable) |
| `RELATES_TO` | Finding → Borrower/LoanApplication/BankAccount/Transaction | — |

The `similarity_score` property on `CITES_CHUNK` is written by `persist_assessment` when the ComplianceAgent injects `chunk_scores` (the similarity scores from `retrieve_regulatory_chunks`) into the reasoning step dict before persisting. It is read back by `trace_evidence` to display scores in the Evidence panel.

---

## The Jurisdiction Bridge

`Jurisdiction` is the structural link between financial entities (Layer 1) and regulatory obligations (Layer 2). It avoids a direct many-to-many relationship between individual loans/borrowers and every regulation.

```
(Borrower:Individual)-[:RESIDES_IN]->(Jurisdiction {jurisdiction_id: 'JUR-AU-FED'})
(Borrower:Corporate)-[:REGISTERED_IN]->(Jurisdiction {jurisdiction_id: 'JUR-AU-FED'})
(Regulation {regulation_id: 'APG-223'})-[:APPLIES_TO_JURISDICTION]->(Jurisdiction {jurisdiction_id: 'JUR-AU-FED'})
```

All three APRA regulations in this dataset link to `JUR-AU-FED`. The `traverse_compliance_path` tool follows this path:

```
(LoanApplication)-[:SUBMITTED_BY]->(Borrower)
    -[:RESIDES_IN|REGISTERED_IN]->(Jurisdiction)
    <-[:APPLIES_TO_JURISDICTION]-(Regulation)
    -[:HAS_SECTION]->(Section)
    -[:HAS_REQUIREMENT]->(Requirement)
    -[:DEFINES_LIMIT]->(Threshold)
```

Adding compliance coverage for a new jurisdiction requires only adding new `Jurisdiction` nodes and creating `APPLIES_TO_JURISDICTION` relationships from the applicable regulations — no changes to Layer 1 or agent code are needed.

---

## Cypher Best Practices

### Always parameterise queries

Never use string interpolation for user-supplied or entity-derived values:

```cypher
-- Correct
MATCH (l:LoanApplication {loan_id: $loan_id}) RETURN l

-- Wrong — never do this
MATCH (l:LoanApplication {loan_id: 'LOAN-0042'}) RETURN l
```

### Variable-length paths: use list comprehension for relationship types

`type(r)` does not work when `r` is a list of relationships (variable-length path). Use a list comprehension:

```cypher
-- Correct
MATCH (a)-[r:OWNS*1..3]->(b)
RETURN [rel IN r | type(rel)] AS rel_types

-- Wrong — this fails at runtime
MATCH (a)-[r:OWNS*1..3]->(b)
RETURN type(r) AS rel_type
```

### Variable-length paths: use `size(r)` not `length(r)`

`length()` expects a `Path` object, not a list of relationships:

```cypher
-- Correct
MATCH (a)-[r:OWNS*1..3]->(b)
RETURN size(r) AS hop_count

-- Wrong
MATCH (a)-[r:OWNS*1..3]->(b)
RETURN length(r) AS hop_count
```

### Vector search pattern

```cypher
CALL db.index.vector.queryNodes('chunk_embeddings', $k, $emb)
YIELD node AS c, score
WHERE score > 0.7
RETURN c.chunk_id, c.text, score
```

Index name: `chunk_embeddings`. Embedding dimensions: 1,536. Similarity metric: cosine.

### Assessment ID construction (Python)

```python
from datetime import datetime
now_local = datetime.now()
assessment_id = f"ASSESS-{entity_id}-{regulation_id}-{now_local.strftime('%Y-%m-%d-%H%M%S')}"
```

Local time is used (not UTC) for the timestamp component to make assessment IDs human-readable in the local context.
