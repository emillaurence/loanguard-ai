# LoanGuard AI — Data Directory

This directory contains all static data used to populate the LoanGuard AI knowledge graph, organised across two loadable layers and one runtime-only layer. Understanding this structure is essential for anyone loading, extending, or troubleshooting the graph database.

---

## Three-Layer Data Architecture

```
Layer 1 (Entities)               Layer 2 (Regulations)            Layer 3 (Assessments)
        │                                │                                 │
Financial entities            APRA prudential standards            AI-generated compliance
CSV files → Neo4j             PDF → Claude → CSV → Neo4j          results, runtime only
                                                                   Neo4j, no CSV files
```

Layers 1 and 2 are loaded from files in this directory. Layer 3 is written dynamically by the agents during query processing and lives only in Neo4j.

---

## Layer 1 — Financial Entity Data

**Location:** `data/layer_1/`

**Loaded by:** `notebooks/111_structured_data_loader.ipynb`

Layer 1 contains all financial entities and the relationships between them. The data is split into two subdirectories:

- `layer_1/entities/` — CSV files, one per node label
- `layer_1/links/` — CSV files, one per relationship type

### Entity files

#### `loan_applications.csv`

The central entity for compliance analysis. Each row is one loan application.

| Field | Type | Description |
|---|---|---|
| `loan_id` | str | Primary key (e.g. `LOAN-0001`) |
| `loan_type` | str | Loan product type (e.g. `residential_secured`) |
| `amount` | float | Loan amount in AUD |
| `currency` | str | Always `AUD` in this dataset |
| `term_months` | int | Loan term in months |
| `purpose` | str | Loan purpose (e.g. `home_purchase`) |
| `interest_rate_indicative` | float | Indicative interest rate in percent |
| `lvr` | float | Loan-to-value ratio in percent |
| `submission_date` | date | Date the application was submitted |
| `status` | str | Application status (e.g. `under_review`) |
| `description` | str | Free-text description |
| `serviceability_assessment_rate` | float | The rate used in the serviceability test — must exceed `interest_rate_indicative` by at least 3 percentage points (APG-223-THR-003) |
| `income_type` | str | Borrower income type: `salary`, `self_employed`, `mixed`, or `rental` — determines which income haircut thresholds apply |
| `non_salary_income_haircut_pct` | float (nullable) | Percentage haircut applied to non-salary income; present only when `income_type != 'salary'` |
| `rental_income_gross` | float (nullable) | Gross annual rental income in AUD; present only when rental income exists |
| `rental_income_haircut_pct` | float (nullable) | Percentage haircut applied to rental income; present only when `rental_income_gross` is present |

The `serviceability_*`, `income_type`, `non_salary_income_*`, and `rental_income_*` fields are the serviceability fields added to support APG-223 threshold evaluation. They are the entity-side values that the `evaluate_thresholds` tool reads when assessing THR-003, THR-006, THR-007, and THR-008.

#### `borrowers.csv`

| Field | Type | Description |
|---|---|---|
| `borrower_id` | str | Primary key (e.g. `BRW-0001`) |
| `name` | str | Full name of individual or company |
| `type` | str | `individual` or `corporate` |
| `entity_subtype` | str | `natural_person` or `proprietary_company` |
| `date_of_birth_or_incorporation` | date | DOB for individuals, incorporation date for companies |
| `tax_id` | str | Tax file number (masked as `TFN-XXXX-XXX`) |
| `annual_revenue` | float | Annual revenue in AUD (0 for individuals) |
| `employee_count` | int | Employee count (0 for individuals) |
| `credit_score` | int | Credit score |
| `risk_rating` | str | `high`, `medium`, or `low` |
| `status` | str | `active` or `inactive` |

#### `bank_accounts.csv`

| Field | Type | Description |
|---|---|---|
| `account_id` | str | Primary key (e.g. `ACC-0001`) |
| `bank_name` | str | Name of the institution holding the account |
| `account_type` | str | e.g. `personal_transaction`, `business_transaction` |
| `currency` | str | Account currency |
| `country` | str | Country where the account is held |
| `opened_date` | date | Account opening date |
| `status` | str | `active` or `inactive` |
| `average_monthly_balance` | float | Average monthly balance in AUD |

#### `collateral.csv`

| Field | Type | Description |
|---|---|---|
| `collateral_id` | str | Primary key (e.g. `COL-0001`) |
| `type` | str | Collateral type (e.g. `residential_property`) |
| `description` | str | Property address |
| `estimated_value` | float | Estimated value in AUD |
| `currency` | str | Always `AUD` |
| `valuation_date` | date | Date of the valuation |
| `valuation_source` | str | Who performed the valuation (e.g. `bank_panel_valuer`) |
| `encumbered` | bool | Whether the collateral is already pledged elsewhere |

#### `transactions.csv`

| Field | Type | Description |
|---|---|---|
| `transaction_id` | str | Primary key (e.g. `TXN-0001`) |
| `from_account_id` | str | Source account |
| `to_account_id` | str | Destination account |
| `amount` | float | Transaction amount in AUD |
| `currency` | str | Always `AUD` |
| `date` | date | Transaction date |
| `type` | str | Transaction type (e.g. `personal_transfer`) |
| `description` | str | Free-text description |
| `flagged_suspicious` | bool | Whether the transaction has been flagged for AML review |

#### `officers.csv`

| Field | Type | Description |
|---|---|---|
| `officer_id` | str | Primary key (e.g. `OFF-0001`) |
| `name` | str | Officer's full name |
| `date_of_birth` | date | Date of birth |
| `nationality` | str | Nationality |
| `is_pep` | bool | Whether the officer is a Politically Exposed Person |
| `pep_detail` | str | PEP description if applicable |
| `sanctions_match` | bool | Whether the officer has a sanctions list match |

#### `jurisdictions.csv`

| Field | Type | Description |
|---|---|---|
| `jurisdiction_id` | str | Primary key (e.g. `JUR-AU-FED`, `JUR-VU`) |
| `name` | str | Jurisdiction name |
| `country` | str | Country name |
| `level` | str | `federal` or `national` |
| `regulatory_body` | str | Responsible regulatory body |
| `aml_risk_rating` | str | AML risk rating: `low`, `medium`, or `high` |

Jurisdictions with `aml_risk_rating = 'high'` trigger the `high_risk_jurisdiction` anomaly pattern. In this dataset these are Vanuatu (JUR-VU), Myanmar (JUR-MM), and Cambodia (JUR-KH).

The jurisdiction `JUR-AU-FED` (Federal Australia) is the bridge node connecting all Australian borrowers to all APRA regulations.

#### `industries.csv`

| Field | Type | Description |
|---|---|---|
| `industry_id` | str | Primary key (e.g. `IND-9530`) |
| `code` | str | ANZSIC industry code |
| `name` | str | Industry name |
| `division` | str | ANZSIC division |
| `risk_category` | str | `low`, `medium`, or `high` |
| `aml_sensitivity` | str | AML sensitivity: `low`, `medium`, or `high` |

Industries with `aml_sensitivity = 'high'` or `risk_category = 'high'` trigger the `high_risk_industry` anomaly pattern. Notable examples: Gambling (IND-9530), Financial Asset Investing (IND-6240), Liquor & Tobacco Wholesaling (IND-5120).

#### `addresses.csv`

Residential and business addresses linked to borrowers via `LOCATED_AT`. Fields: `address_id`, `street`, `city`, `state`, `postcode`, `country`, `address_type`.

### Relationship files (`layer_1/links/`)

| File | Relationship | Description |
|---|---|---|
| `submitted_by.csv` | `(LoanApplication)-[:SUBMITTED_BY]->(Borrower)` | Links each loan to its primary applicant; includes `role` property |
| `guaranteed_by.csv` | `(LoanApplication)-[:GUARANTEED_BY]->(Borrower)` | Links loans to their guarantors; includes `guarantee_type`, `guarantee_amount`, `currency` |
| `backed_by.csv` | `(LoanApplication)-[:BACKED_BY]->(Collateral)` | Links loans to collateral; includes `lien_position`, `coverage_ratio` |
| `has_account.csv` | `(Borrower)-[:HAS_ACCOUNT]->(BankAccount)` | Links borrowers to their bank accounts |
| `resides_in.csv` | `(Borrower:Individual)-[:RESIDES_IN]->(Jurisdiction)` | Links individual borrowers to their jurisdiction of residence |
| `registered_in.csv` | `(Borrower:Corporate)-[:REGISTERED_IN]->(Jurisdiction)` | Links corporate borrowers to their jurisdiction of registration |
| `owns.csv` | `(Borrower)-[:OWNS]->(Borrower)` | Ownership relationships between corporate entities; includes `ownership_percentage`, `ownership_type`, `effective_date` |
| `director_of.csv` | `(Officer)-[:DIRECTOR_OF]->(Borrower)` | Links officers to the companies they direct; includes `role`, `appointed_date`, `is_current` |
| `belongs_to_industry.csv` | `(Borrower)-[:BELONGS_TO_INDUSTRY]->(Industry)` | Links borrowers to their industry classifications; includes `is_primary`, `revenue_percentage` |
| `located_at.csv` | `(Borrower)-[:LOCATED_AT]->(Address)` | Links borrowers to their addresses |

---

## Layer 2 — APRA Regulatory Framework

**Location:** `data/layer_2/`

**Loaded by:** Notebooks 211 through 216 in sequence (see Layer 2 Pipeline section below)

Layer 2 contains APRA prudential standards parsed from source PDFs and processed into a queryable regulatory knowledge graph. This layer is generated by the extraction pipeline — do not edit the CSV files directly; regenerate them by re-running the pipeline.

### Source documents (`regulatory_documents/`)

The source PDF files for the three APRA regulations currently ingested:

| Document | Regulation ID | Type | Enforceable |
|---|---|---|---|
| `APS_112_Capital_Adequacy.pdf` | `APS-112` | Prudential Standard | Yes |
| `APG_223_Residential_Mortgage_Lending.pdf` | `APG-223` | Practice Guide | No |
| `APS_220_Credit_Risk_Management.pdf` | `APS-220` | Prudential Standard | Yes |

### `document_config.yaml`

Controls what the extraction pipeline processes. Each document entry specifies the `regulation_id`, `name`, `issuing_body`, `document_type`, `effective_date`, `is_enforceable`, `pdf_path`, `section_id_prefix`, `default_severity`, and a `supplemental_prompt` that gives Claude document-specific extraction instructions (for example, instructing it to create one threshold row per LVR band cell in APS-112's risk weight tables, rather than collapsing them).

To add a new regulatory document, append a new entry to the `documents` list in this file and place the PDF in `regulatory_documents/`. No code changes are required.

### `regulations.csv`

One row per regulation. Columns: `regulation_id`, `name`, `issuing_body`, `document_type`, `effective_date`, `is_enforceable`. This is loaded as `Regulation` nodes in Neo4j.

### `sections.csv`

One row per section extracted from the source PDFs. Columns: `section_id`, `regulation_id`, `section_number`, `title`, `text`, `content_summary`, `section_type`, and page range fields. Loaded as `Section` nodes connected to their parent `Regulation` via `HAS_SECTION`.

### `requirements.csv`

One row per extracted compliance requirement. Columns: `requirement_id`, `regulation_id`, `section_id`, `description`, `requirement_type`, `is_quantitative`, `applies_to_loan_type`, `severity`.

Severity values:
- `mandatory` — binding obligations in prudential standards (APS-112, APS-220)
- `expected` — strong expectations from APRA in practice guides (APG-223, language like "APRA expects")
- `recommended` — good practice guidance (APG-223, language like "a prudent ADI would")

### `thresholds.csv`

One row per quantitative threshold. This is the most important file for compliance evaluation.

Columns: `regulation_id`, `threshold_id`, `requirement_id`, `metric`, `operator`, `value`, `value_upper`, `unit`, `condition_context`, `consequence`, `threshold_type`.

The `threshold_type` column is the key design feature. It classifies how each threshold should be used in the `evaluate_thresholds` tool:

| threshold_type | Meaning | Evaluation outcome |
|---|---|---|
| `minimum` | Entity value must be >= threshold value | PASS if met, BREACH if not |
| `maximum` | Entity value must be <= threshold value | PASS if met, BREACH if not |
| `trigger` | Condition fires a monitoring concern | REQUIRES_REVIEW when true, no effect otherwise |
| `informational` | ADI-level reference value, not a per-entity gate | Always N/A — excluded from verdict logic |

The `condition_context` column is a JSON string describing applicability conditions. For example, `APG-223-THR-006` includes `{"applies_to": "non_salary_income"}` — when the loan's `income_type` is `salary`, this threshold is N/A for that loan.

Key APG-223 thresholds for loan evaluation:

| Threshold ID | Metric | Type | Rule |
|---|---|---|---|
| `APG-223-THR-003` | `serviceability_interest_rate_buffer` | minimum | Buffer = `serviceability_assessment_rate - interest_rate_indicative` must be >= 3.0 percentage points |
| `APG-223-THR-006` | `non_salary_income_haircut` | minimum | `non_salary_income_haircut_pct` must be >= 20% when `income_type != 'salary'` |
| `APG-223-THR-007` | `rental_income_haircut` | minimum | `rental_income_haircut_pct` must be >= 20% when `rental_income_gross` is present |
| `APG-223-THR-008` | `LVR` | trigger | LVR >= 90% fires senior management review concern |

### `chunks.csv`

One row per ~300-token chunk of section text. Columns: `chunk_id`, `section_id`, `regulation_id`, `text`, `token_count`, `chunk_index`, `source_document`. This file is generated by notebook 213 and is loaded into Neo4j as `Chunk` nodes. OpenAI embeddings are added by notebook 215 directly to the Neo4j nodes (not written back to CSV).

### `cross_references.csv`

Resolved cross-document section references extracted by Claude in notebook 212. Columns: `source_section_id`, `target_section_id`, `reference_text`. Loaded as `CROSS_REFERENCES` relationships between `Section` nodes.

### `intermediate/`

Per-document extraction outputs from notebook 211. These are intermediate pipeline artifacts, not directly loaded into Neo4j. Files follow the naming pattern `{regulation_id}_sections.csv`, `{regulation_id}_requirements.csv`, `{regulation_id}_thresholds.csv`, `{regulation_id}_references.csv`. These are merged and resolved by notebook 212 to produce the final CSVs above.

---

## Layer 3 — Runtime Assessments

Layer 3 is entirely runtime — no CSV files are maintained for this layer. Assessment data lives only in Neo4j and is created by the `ComplianceAgent` via the `persist_assessment` tool during query processing.

The three node types in Layer 3 are:

**`Assessment`** — One per (entity, regulation, timestamp) combination. Holds the overall `verdict` (COMPLIANT / NON_COMPLIANT / REQUIRES_REVIEW / ANOMALY_DETECTED / INFORMATIONAL), `confidence` score, the `agent` that produced it, and `created_at` timestamp. Assessment ID format: `ASSESS-{entity_id}-{regulation_id}-{YYYY-MM-DD-HHMMSS}`.

**`Finding`** — One or more per Assessment, linked via `HAS_FINDING`. Each finding has a `finding_type` (e.g. `compliance_breach`, `anomaly`, `risk_signal`, `information`), `severity` (HIGH / MEDIUM / LOW / INFO), and a `description`.

**`ReasoningStep`** — One per agent reasoning step, linked to the Assessment via `HAS_STEP`. Each step records what the agent did (`description`), any Cypher it used (`cypher_used`), and links to the regulatory sections and chunks it cited (`CITES_SECTION`, `CITES_CHUNK`). These links are what the `trace_evidence` tool traverses to produce the evidence panel in the UI.

To clear Layer 3 data from Neo4j (for example, to reset between test runs), run:

```cypher
MATCH (n:Assessment) DETACH DELETE n;
MATCH (n:Finding) DETACH DELETE n;
MATCH (n:ReasoningStep) DETACH DELETE n;
```

---

## Data Pipeline

### Reloading after changes

| What changed | What to re-run |
|---|---|
| Layer 1 entity CSV files changed | Notebook 111 only |
| New APRA PDF added to `document_config.yaml` | Notebooks 211 → 216 in order |
| Existing PDF re-extracted (e.g. after prompt changes in 211) | Notebooks 211 → 216 in order |
| Threshold data changed in `thresholds.csv` directly | Notebook 214 only (ingest) |
| Chunk text changed | Notebooks 213 → 216 (re-chunk, ingest, re-embed, validate) |
| Embeddings need to be regenerated | Notebook 215 only (will overwrite existing embeddings) |

Notebook 214 (`214_ingest_neo4j`) clears and reloads Layer 2 on every run. It is safe to re-run at any point without losing Layer 1 or Layer 3 data.

### Verifying the graph is correctly loaded

Run notebook 216 (`216_validate_graph`) after any reload. It checks:
- Node counts for all Layer 2 types against expected minimums
- Relationship counts for key relationship types
- Presence and health of the `chunk_embeddings` vector index
- That all `Threshold` nodes have a valid `threshold_type` value

---

## Data Distributions

### Layer 1 (current dataset)

| Entity | Count |
|---|---|
| LoanApplication | 466 |
| Borrower | 628 |
| BankAccount | (one or more per borrower) |
| Transaction | Includes flagged suspicious transactions for anomaly testing |
| Collateral | Residential properties with bank panel valuations |
| Officer | Corporate directors, some with PEP or sanctions flags |
| Jurisdiction | 7 (JUR-AU-FED, JUR-SG, JUR-HK, JUR-VU, JUR-MY, JUR-MM, JUR-KH) |

### APG-223 compliance breakdown (466 loan applications)

| Verdict | Count | Share |
|---|---|---|
| COMPLIANT | 366 | 79% |
| NON_COMPLIANT | 39 | 8% |
| REQUIRES_REVIEW | 61 | 13% |

Non-compliant loans typically have a serviceability buffer below 3 percentage points (THR-003 breach). Loans requiring review are predominantly those with LVR >= 90% (THR-008 trigger), or with missing conditional fields that prevent full evaluation.

---

## Important Notes

### Never commit real data

The `data/raw/` directory is listed in `.gitignore` and must never contain real customer data, real transaction records, or unmasked personal information. Any data placed in `raw/` is automatically excluded from version control.

The files in `data/layer_1/` and `data/layer_2/` in this repository are synthetic and safe to commit. Tax file numbers are masked (e.g. `TFN-0001-XXX`). Property addresses are fictitious.

### Do not edit generated files directly

The CSV files in `data/layer_2/` (except `document_config.yaml` and the source PDFs) are generated by the pipeline notebooks. Editing them manually will be overwritten on the next pipeline run. If you need to change regulatory content, either:
- Update the source PDF and re-run the full pipeline, or
- Modify the extraction prompt in `document_config.yaml` and re-run from notebook 211

### Threshold data is the source of truth for compliance logic

The `threshold_type` values in `thresholds.csv` directly control how the `evaluate_thresholds` tool evaluates each threshold. If a threshold is incorrectly typed (for example, `informational` when it should be `minimum`), it will be silently excluded from verdict logic. Verify threshold types in notebook 216 after any pipeline run.
