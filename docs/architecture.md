# LoanGuard AI — System Architecture

**Intelligent loan compliance monitoring and risk investigation powered by multi-agent AI**

This document describes the technical architecture of LoanGuard AI: the three-layer Neo4j graph model, the multi-agent pipeline, the MCP tool layer, the threshold type and verdict system, the security design, and key reference patterns.

---

## Three-Layer Neo4j Graph Model

LoanGuard AI organises all knowledge in three distinct graph layers that are connected through a bridge node.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            LAYER 1 — ENTITY LAYER                               │
│                                                                                 │
│  ┌───────────────────────┐  SUBMITTED_BY  ┌──────────────────────────┐         │
│  │ LoanApplication       │───────────────▶│ Borrower                 │         │
│  │ :ResidentialSecured   │                │ :Individual               │         │
│  │                       │  GUARANTEED_BY │ :Corporate                │         │
│  │ loan_id               │───────────────▶│                           │         │
│  │ amount, lvr           │                │ borrower_id               │         │
│  │ interest_rate_indicative               │ credit_score, risk_rating │─────┐   │
│  │ serviceability_assessment_rate         └───────────┬───────────────┘     │   │
│  │ income_type                                        │ RESIDES_IN /        │   │
│  │ non_salary_income_haircut_pct          REGISTERED_IN                     │   │
│  │ rental_income_gross                                ▼                     │   │
│  │ rental_income_haircut_pct    ┌──────────────────────────────┐            │   │
│  └──────────┬────────────────── │ Jurisdiction                 │◀───────────┘   │
│             │ BACKED_BY         │ jurisdiction_id              │  (bridge to L2) │
│             ▼                   │ name, country                │                │
│  ┌──────────────────┐           │ aml_risk_rating              │                │
│  │ Collateral       │           └──────────────────────────────┘                │
│  │ type, value      │                                                           │
│  │ valuation_source │  Also: BankAccount, Transaction, Address, Officer,        │
│  └──────────────────┘         Industry                                          │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                         APPLIES_TO_JURISDICTION
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          LAYER 2 — REGULATORY LAYER                             │
│                                                                                 │
│  ┌────────────┐  HAS_SECTION  ┌──────────┐  HAS_REQUIREMENT  ┌─────────────┐  │
│  │ Regulation │──────────────▶│ Section  │──────────────────▶│ Requirement │  │
│  │            │               │          │                    │ description │  │
│  │ APS-112    │               │ section_id                    │ severity    │  │
│  │ APG-223    │               │ title    │  DEFINES_LIMIT     └──────┬──────┘  │
│  │ APS-220    │               │ text     │                           │         │
│  └────────────┘               └────┬─────┘                           ▼         │
│                                    │                          ┌─────────────┐  │
│                         HAS_CHUNK  │                          │ Threshold   │  │
│                                    ▼  NEXT_CHUNK              │ metric      │  │
│                              ┌─────────┐──────────▶           │ value       │  │
│                              │  Chunk  │                      │ operator    │  │
│                              │ text    │                      │ threshold_type  │
│                              │ embedding (1536 dims)          └─────────────┘  │
│                              └────┬────┘                                       │
│                                   │ SEMANTICALLY_SIMILAR (cosine > 0.85,       │
│                                   │ cross-document only)                       │
│                                   └──────────────────────────────▶ Chunk       │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                      LAYER 3 — AI ASSESSMENT LAYER (runtime)                   │
│                                                                                 │
│   ┌──────────────────────┐  ASSESSED_UNDER  ┌──────────────────┐               │
│   │ Assessment           │────────────────▶│ Regulation        │ (from Layer 2) │
│   │ assessment_id        │                  └──────────────────┘               │
│   │ verdict, confidence  │                                                      │
│   │ agent, created_at    │  HAS_FINDING    ┌──────────────────┐                │
│   └──────────┬───────────┘────────────────▶│ Finding          │                │
│              │                             │ severity, type   │                │
│              │ HAS_STEP                    │ description      │                │
│              ▼                             └──────────────────┘                │
│   ┌──────────────────────┐  CITES_SECTION  ┌──────────────────┐               │
│   │ ReasoningStep        │────────────────▶│ Section          │ (from Layer 2) │
│   │ step_number          │                  └──────────────────┘               │
│   │ description          │  CITES_CHUNK    ┌──────────────────┐               │
│   │ cypher_used          │─[similarity_   ▶│ Chunk            │ (from Layer 2) │
│   └──────────────────────┘   score]──────── └──────────────────┘               │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### The Jurisdiction Bridge Node

The `Jurisdiction` node is the structural link between financial entities (Layer 1) and regulatory obligations (Layer 2). It avoids a direct many-to-many relationship between regulations and every borrower or loan application.

- `Borrower:Individual` nodes link via `RESIDES_IN`
- `Borrower:Corporate` nodes link via `REGISTERED_IN`
- Every APRA regulation links via `APPLIES_TO_JURISDICTION` to `JUR-AU-FED`

When the `traverse_compliance_path` tool is called for a borrower or loan, it follows this path:

```
(LoanApplication)-[:SUBMITTED_BY]->(Borrower)
    -[:RESIDES_IN|REGISTERED_IN]->(Jurisdiction {jurisdiction_id: 'JUR-AU-FED'})
    <-[:APPLIES_TO_JURISDICTION]-(Regulation)
    -[:HAS_SECTION]->(Section)-[:HAS_REQUIREMENT]->(Requirement)
    -[:DEFINES_LIMIT]->(Threshold)
```

This design means that extending compliance coverage to a new jurisdiction requires only adding new `Jurisdiction` nodes and updating regulation linkage — no structural changes to Layer 1 or the agent pipeline.

---

## Layer 2 Regulatory Pipeline

The Layer 2 pipeline runs once per document set and produces all regulatory graph content from source PDFs. It is controlled by `data/layer_2/document_config.yaml` — adding a new APRA document requires only a new YAML entry and its PDF file; no code changes are needed.

```
  PDF files + document_config.yaml
           │
           ▼
  ┌─────────────────────────────────┐
  │  211_extract_document_structure │  Claude extracts sections, requirements,
  │                                 │  thresholds (with threshold_type), and
  │                                 │  cross-references from PDF text.
  │                                 │  close_page_gaps() absorbs unclaimed
  │                                 │  cover/ToC pages post-extraction.
  └──────────────┬──────────────────┘
                 │  intermediate/{rid}_sections.csv
                 │  intermediate/{rid}_requirements.csv
                 │  intermediate/{rid}_thresholds.csv
                 │  intermediate/{rid}_references.csv
                 ▼
  ┌─────────────────────────────────┐
  │  212_merge_and_resolve_references  Merges per-document intermediates;
  │                                 │  Claude resolves cross-document
  │                                 │  section references to known IDs.
  └──────────────┬──────────────────┘
                 │  sections.csv, requirements.csv,
                 │  thresholds.csv, cross_references.csv
                 ▼
  ┌─────────────────────────────────┐
  │  213_chunk_documents            │  Splits section text into ~300-token
  │                                 │  chunks. Raises RuntimeError if any
  │                                 │  page range is uncovered — fix in
  │                                 │  notebook 211 before proceeding.
  └──────────────┬──────────────────┘
                 │  chunks.csv
                 ▼
  ┌─────────────────────────────────┐
  │  214_ingest_neo4j               │  Loads all Layer 2 nodes and
  │                                 │  relationships into Neo4j.
  │                                 │  Re-runnable: clears then reloads.
  └──────────────┬──────────────────┘
                 ▼
  ┌─────────────────────────────────┐
  │  215_generate_embeddings        │  OpenAI text-embedding-3-small
  │                                 │  (1,536 dims) written to Chunk.embedding.
  │                                 │  Creates SEMANTICALLY_SIMILAR edges
  │                                 │  (cosine > 0.85, cross-document only).
  └──────────────┬──────────────────┘
                 ▼
  ┌─────────────────────────────────┐
  │  216_validate_graph             │  Pass/fail checks on node counts,
  │                                 │  relationships, and index health.
  └─────────────────────────────────┘
```

Large Claude calls in the pipeline use `call_claude_stream_json()` from `src/document/utils.py` because the Anthropic SDK requires streaming for calls that may exceed 10 minutes.

---

## Multi-Agent Architecture

### Overview

```
User question
      │
      ▼
┌─────────────────────────────────────────┐
│           Orchestrator                  │
│  - Parses intent (entity ID, regulation)│
│  - Routes to specialist agents          │
│  - Calls trace_evidence for cited refs  │
│  - Synthesises final InvestigationResponse│
└──────────────────┬──────────────────────┘
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
┌──────────────┐    ┌──────────────────────┐
│ Compliance   │    │ Investigation        │
│ Agent        │    │ Agent                │
│              │    │                      │
│ Threshold    │    │ Graph traversal      │
│ evaluation   │    │ Anomaly detection    │
│ Persist to   │    │ Risk signals         │
│ Layer 3      │    │                      │
└──────────────┘    └──────────────────────┘
```

### 1. Orchestrator (`src/agent/orchestrator.py`)

The Orchestrator receives the user question, extracts the target entity ID and entity type, then runs both specialist agents. After both complete, it calls `trace_evidence` on the persisted assessment to populate `cited_sections` and `cited_chunks`, then makes a final Claude synthesis call to produce the combined `InvestigationResponse`.

The Orchestrator constructs both specialist agents and owns the full session lifecycle. It does not make tool calls directly — all graph interaction is delegated to the agents.

### 2. ComplianceAgent (`src/agent/compliance_agent.py`)

The ComplianceAgent runs an agentic loop (up to 10 iterations) against Claude's tool-use API. Its required workflow, in order:

1. **`traverse_compliance_path`** — retrieves the regulatory subgraph including all `Threshold` nodes with their `threshold_type` field
2. **`evaluate_thresholds`** — filters out `informational` thresholds and conditional N/A cases, evaluates all remaining thresholds as PASS / BREACH / TRIGGER
3. Forms the verdict from evaluation results (see Verdict Logic section)
4. **`retrieve_regulatory_chunks`** (optional) — fetches supporting regulatory text for the finding descriptions
5. **`persist_assessment`** — writes the `Assessment`, `Finding`, and `ReasoningStep` nodes to Layer 3

The ComplianceAgent uses `cache_control: ephemeral` on its system prompt to reduce latency on repeated calls.

```
messages = [{role: user, content: query}]
      │
      ▼
┌─────────────────────────────────────────┐
│              AGENTIC LOOP               │
│           (max 10 iterations)           │
│                                         │
│  Claude API (temperature=0)             │
│        │                                │
│        ├── end_turn ───────────────────▶ Return structured text
│        │                                │
│        └── tool_use                     │
│                │                        │
│                ▼                        │
│          execute_tool()                 │
│          guard_tool_result()            │
│                │                        │
│                ▼                        │
│     Append tool_result → messages       │
│     → next iteration                   │
└─────────────────────────────────────────┘
```

### 3. InvestigationAgent (`src/agent/investigation_agent.py`)

The InvestigationAgent focuses on graph-level risk signals rather than per-threshold compliance. Its recommended workflow:

1. One comprehensive Cypher query that retrieves the entity plus all first-degree connections
2. **`detect_graph_anomalies`** with all relevant pattern names supplied in a single call
3. Up to 3 targeted follow-up Cypher queries for confirmed risk signals that warrant deeper investigation
4. Structured summary of findings

The agent's system prompt includes `PATTERN_HINTS`, an auto-generated string listing all available anomaly pattern names and their one-line descriptions (derived from `ANOMALY_REGISTRY`).

---

## MCP Tool Layer

Agents have access to two categories of tools.

**FastMCP tools** — implemented as plain Python in `src/mcp/tools_impl.py` and registered with the FastMCP server in `src/mcp/investigation_server.py` using `@mcp.tool()`. Notebooks and agents import from `tools_impl` directly — not from the server.

**Simulated Neo4j MCP tool** (`read-neo4j-cypher`) — defined with the same tool schema as the [official Neo4j MCP server](https://github.com/neo4j-contrib/mcp-neo4j) but dispatched locally via `Neo4jConnection` (no external MCP process or stdio transport). This keeps agent prompts portable: the `ComplianceAgent` references `read-neo4j-cypher` as a "Neo4j MCP tool" so the same prompt works in environments where the real Neo4j MCP server is running. Write keywords (`CREATE`, `MERGE`, `DELETE`, `SET`, `REMOVE`, `DROP`) are blocked at the dispatcher level in `app.py`.

### Tool 1: `traverse_compliance_path`

Traverses from an entity node across the Jurisdiction bridge to all applicable regulations, sections, requirements, and thresholds.

**Parameters:**
- `entity_id` (str) — the `loan_id` or `borrower_id`
- `entity_type` (str) — `LoanApplication` or `Borrower`
- `regulation_id` (str, optional) — restrict to a single regulation (e.g. `APG-223`)

**Returns:** A dict containing the matched regulation IDs, section summaries, requirement descriptions, and threshold nodes. Each threshold includes `threshold_type`, `metric`, `operator`, `value`, `unit`, and `condition_context`.

### Tool 2: `retrieve_regulatory_chunks`

Embeds the query text using OpenAI `text-embedding-3-small` and runs a vector similarity search over the `chunk_embeddings` index in Neo4j.

**Parameters:**
- `query_text` (str) — natural language query
- `regulation_id` (str, optional) — filter to one regulation
- `top_k` (int, default 5, max 20) — number of chunks to return

**Returns:** A dict with `query` and `chunks` list. Each chunk includes `chunk_id`, `section_id`, `text` (up to 800 characters), `source_document`, and `similarity_score`.

**Cypher used:**
```cypher
CALL db.index.vector.queryNodes('chunk_embeddings', $k, $emb)
YIELD node AS c, score
```

### Tool 3: `detect_graph_anomalies`

Runs one or more named patterns from `ANOMALY_REGISTRY` against the graph and returns all matched entities.

**Parameters:**
- `pattern_names` (list[str]) — one or more keys from `ANOMALY_REGISTRY`
- `entity_id` (str, optional) — scope results to a specific entity

**Returns:** A dict with `patterns_run`, `total_findings`, and a `results` list. Each result includes `pattern_name`, `severity`, `description`, `finding_count`, `findings` (raw rows), and `entity_ids`.

If any pattern name is unrecognised, the tool returns an error with the list of valid pattern names.

### Tool 4: `persist_assessment`

Writes a compliance assessment to Layer 3 using idempotent `MERGE` operations.

**Parameters:**
- `entity_id` (str)
- `entity_type` (str)
- `regulation_id` (str)
- `verdict` (str) — must be one of `COMPLIANT`, `NON_COMPLIANT`, `REQUIRES_REVIEW`, `ANOMALY_DETECTED`, `INFORMATIONAL`
- `confidence` (float, 0–1)
- `findings` (list[dict]) — each with `finding_type`, `severity`, `description`, optional `pattern_name`
- `reasoning_steps` (list[dict]) — each with `description`, optional `cypher_used`, `section_ids`, `chunk_ids`
- `agent` (str, default `compliance_agent`)

**Returns:** `assessment_id`, list of `finding_id` values, and list of `step_id` values.

**Assessment ID format:** `ASSESS-{entity_id}-{regulation_id}-{YYYY-MM-DD-HHMMSS}`

### Tool 5: `trace_evidence`

Walks a stored Assessment node back through its ReasoningStep nodes to retrieve all cited Section and Chunk nodes, including the original similarity scores from the vector search.

**Parameters:**
- `assessment_id` (str) — the assessment to trace

**Returns:** A dict containing the assessment metadata, its reasoning steps, a `cited_sections` list (with `section_id`, `title`, `content_summary`, `regulation_id`), and a `cited_chunks` list (with `chunk_id`, `section_id`, `text_excerpt`, `chunk_index`, `similarity_score`).

The `similarity_score` on each cited chunk is recovered from the `similarity_score` property stored on the `CITES_CHUNK` relationship in Neo4j. This property is written at persist_assessment time by the ComplianceAgent, which tracks scores from `retrieve_regulatory_chunks` results and injects them before calling `persist_assessment`. If a chunk was cited without a vector search score (e.g. cited via a Cypher query), `similarity_score` will be `null` and the Evidence panel displays "cited" instead of a numeric score.

This tool is called by the Orchestrator after both specialist agents complete, to populate the evidence panel in the Streamlit UI.

### Tool 6: `evaluate_thresholds`

Evaluates a list of threshold definitions against an entity's stored property values. This is the core of the ComplianceAgent's verdict logic.

**Parameters:**
- `thresholds` (list[dict]) — threshold nodes returned by `traverse_compliance_path`
- `entity_id` (str) — the entity to evaluate
- `entity_type` (str)

**Returns:** A list of evaluation results. Each result includes the `threshold_id`, `metric`, `threshold_type`, `result` (PASS / BREACH / TRIGGER / N/A), and a `reason` string explaining the evaluation.

`informational` thresholds always return N/A and are excluded from verdict aggregation. Conditional thresholds (e.g. THR-006 which only applies when `income_type != 'salary'`) return N/A when the condition is not met.

### Simulated Neo4j MCP tool: `read-neo4j-cypher`

This tool is not in `tools_impl.py`. It is defined inline in `app.py` (`NEO4J_MCP_TOOLS`) with the same schema as the official [Neo4j MCP server](https://github.com/neo4j-contrib/mcp-neo4j) and dispatched directly via `Neo4jConnection.run_query()`.

**Parameters:**
- `query` (str) — a read-only Cypher query
- `params` (dict, optional) — query parameters

**Returns:** `{"rows": [...]}` — a list of result dicts.

**Security:** The dispatcher in `app.py` scans the query for write keywords (`CREATE`, `MERGE`, `DELETE`, `SET`, `REMOVE`, `DROP`) and returns an error if any are found, preventing write operations through this read-only path.

**Why the Neo4j MCP naming?** The `ComplianceAgent` system prompt refers to this as a "Neo4j MCP tool" so the same prompt is portable to deployments where the real Neo4j MCP server process is running over stdio. In this project, no external MCP process is involved.

---

## Threshold Type System

Every `Threshold` node in Layer 2 carries a `threshold_type` field. This is the primary mechanism for distinguishing enforceable per-entity limits from monitoring triggers and ADI-level reference values.

### Type definitions

**`minimum`** — The entity's measured value must meet or equal the threshold value. Failing to meet the minimum constitutes a breach.

- Example: `APG-223-THR-003` — `serviceability_interest_rate_buffer >= 3.0 percent`
  - The buffer is computed as `serviceability_assessment_rate - interest_rate_indicative`
  - If the buffer is 2.5 percentage points, this is a BREACH → NON_COMPLIANT
- Example: `APG-223-THR-006` — `non_salary_income_haircut >= 20%`
  - Only evaluated when `income_type` is not `salary`
  - Returns N/A for pure salary borrowers

**`maximum`** — The entity's measured value must not exceed the threshold value. Exceeding it constitutes a breach.

- Example: `APG-223-THR-001` — `risk_management_framework_review_frequency <= 3 years`
  - An ADI that has not reviewed its framework within three years is in breach
- Example: `APG-223-THR-002` — `serviceability_policy_review_frequency <= 1 year`

**`trigger`** — The threshold fires a monitoring concern when the condition is met. Unlike minimum/maximum, a trigger does not itself constitute a breach — it escalates the verdict to REQUIRES_REVIEW and generates a finding that senior management review has been triggered.

- Example: `APG-223-THR-008` — `LVR >= 90.0 percent`
  - When LVR is at or above 90%, per APG-223 this requires senior management review with Board oversight
  - The loan is not automatically NON_COMPLIANT, but cannot be COMPLIANT — it is REQUIRES_REVIEW

**`informational`** — An ADI-level reference value used in calculations, not a pass/fail gate for individual loan applications. These are excluded from verdict logic entirely.

- Example: `APG-223-THR-004` — `credit_card_revolving_debt_repayment_rate == 3.0 percent per month`
  - This is an example of a suitably prudent approach, not a binding per-loan limit
- Example: APS-112 risk weight tables — lookup values for capital calculation, not per-loan thresholds

### Verdict derivation

The `ComplianceAgent` applies the following priority order to derive a single verdict from all evaluated thresholds:

| Condition (evaluated in this order) | Resulting verdict |
|---|---|
| One or more thresholds result in BREACH | `NON_COMPLIANT` |
| One or more thresholds result in TRIGGER, and no BREACH | `REQUIRES_REVIEW` |
| All applicable thresholds result in PASS, no TRIGGER | `COMPLIANT` |
| A material entity-level threshold cannot be evaluated (unknown value) | `REQUIRES_REVIEW` |

---

## Anomaly Detection Patterns

The `ANOMALY_REGISTRY` in `src/mcp/schema.py` is the single source of truth for all graph anomaly detection. Each `AnomalyPattern` entry contains a `description`, `severity`, `cypher` query, `id_key` (the return column that identifies the primary entity), optional `params`, and optional `threshold_id`.

`PATTERN_HINTS` is automatically derived from the registry and injected into the InvestigationAgent's system prompt so the agent knows which pattern names are available without needing to query the graph.

### Available patterns

| Pattern name | Severity | Description |
|---|---|---|
| `transaction_structuring` | HIGH | Multiple sub-$10,000 suspicious transfers flowing into the same bank account from distinct sources — consistent with structuring to avoid AUSTRAC threshold reporting obligations |
| `high_lvr_loans` | HIGH | Loan applications with LVR >= 90%, linked to APG-223-THR-008, requiring senior management review |
| `high_risk_industry` | MEDIUM | Borrowers operating in industries with high AML sensitivity: Gambling (IND-9530), Financial Asset Investing (IND-6240), Liquor & Tobacco Wholesaling (IND-5120) |
| `layered_ownership` | MEDIUM | Multi-hop OWNS chains of depth 2 or more, which may be used to obscure beneficial controllers or aggregate exposure across related entities |
| `high_risk_jurisdiction` | HIGH | Borrowers residing in or registered in jurisdictions with `aml_risk_rating = 'high'`: Vanuatu (JUR-VU), Myanmar (JUR-MM), Cambodia (JUR-KH) |
| `guarantor_concentration` | MEDIUM | Borrowers acting as guarantor on 2 or more loan applications, creating contingent liability exposure not visible from single-loan review |

---

## Security Design

### Prompt injection defence

Tool results from Neo4j may contain attacker-controlled strings — for example, a borrower name field containing "Ignore all previous instructions". The `guard_tool_result()` function in `src/agent/_security.py` is applied to every tool result before it is appended to the agent message history.

**Two defences are applied:**

**Structural framing** — Every tool result is wrapped in `[TOOL DATA — {tool_name}]...[END TOOL DATA]` tags. This makes the boundary between instructions and external data explicit in Claude's context, reducing the risk that data content is interpreted as instructions.

**Pattern detection** — Nine regex patterns covering common injection attempts are checked against the content. If a match is found, a `WARNING` is logged with the pattern matched and a 200-character content excerpt. The content is not redacted — doing so could break legitimate results — but the audit trail is created. Patterns include:
- `ignore (all) previous instructions`
- `disregard (your) (previous) instructions`
- `forget your instructions`
- `new system prompt`
- `you are now a`
- `act as a different`
- `override your (instructions|system|prompt)`
- `do not follow your`
- `your new instructions are`

Both `ComplianceAgent` and `InvestigationAgent` call `guard_tool_result()` on every tool result before appending it to the message list.

---

## Schema Types

All shared types are defined in `src/mcp/schema.py` — the single source of truth for enums, dataclasses, the graph schema hint, and the anomaly registry.

### Verdict (StrEnum)

```python
class Verdict(StrEnum):
    COMPLIANT        = "COMPLIANT"
    NON_COMPLIANT    = "NON_COMPLIANT"
    REQUIRES_REVIEW  = "REQUIRES_REVIEW"
    ANOMALY_DETECTED = "ANOMALY_DETECTED"
    INFORMATIONAL    = "INFORMATIONAL"
```

`StrEnum` values compare equal to their string equivalents, so comparisons like `verdict == "NON_COMPLIANT"` continue to work alongside typed comparisons.

### Severity (StrEnum)

```python
class Severity(StrEnum):
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"
    INFO   = "INFO"
```

### AnomalyPattern (dataclass)

```python
@dataclass
class AnomalyPattern:
    description: str      # Human-readable summary (first sentence used in PATTERN_HINTS)
    severity: str         # One of Severity values
    cypher: str           # The detection Cypher query
    id_key: str           # The RETURN column holding the primary entity ID
    params: dict          # Default Cypher parameter values
    threshold_id: str     # Linked Layer 2 Threshold node ID, if applicable
```

### ANOMALY_REGISTRY

`dict[str, AnomalyPattern]` — keys are snake_case pattern names used as the argument to `detect_graph_anomalies`. The registry is the authoritative list of what patterns exist; `PATTERN_HINTS` is auto-generated from it.

### GRAPH_SCHEMA_HINT

A multi-line string injected into every agent system prompt. It documents all node labels, properties, relationship types, and Cypher best practices, enabling Claude to generate valid Cypher queries without a separate schema lookup on every turn.

### Dataclasses

| Dataclass | Purpose |
|---|---|
| `AnomalyFinding` | A single anomaly detection result with evidence rows and entity IDs |
| `ComplianceResult` | Output of the ComplianceAgent: verdict, confidence, threshold breaches, persisted findings, reasoning steps |
| `InvestigationResult` | Output of the InvestigationAgent: connections, risk signals, path summaries |
| `InvestigationResponse` | Top-level response returned to the Streamlit UI: answer, verdict, findings, evidence, cited sections, cited chunks, recommended next steps |

---

## Performance Design

### Prompt caching

The `ComplianceAgent` marks its system prompt with `cache_control: ephemeral`. Because the system prompt contains `GRAPH_SCHEMA_HINT` (the full graph schema) and the regulation-specific context, caching this eliminates repeated token processing across agent turns and across queries for the same regulation.

### Context windowing

The agent message history is windowed to the last 4 message pairs before each Claude API call. This keeps the effective context size bounded regardless of how many tool calls have occurred in the session, preventing runaway token costs.

### Tool result truncation

Every tool result is truncated to 3,000 characters before being appended to the message history. This prevents large Neo4j query results from consuming the context window. The truncation happens inside the tool dispatch logic before `guard_tool_result()` is called.

### Rate limiting with exponential backoff

All Claude API calls are wrapped with retry logic. On a rate limit error (HTTP 429), the code reads the `retry-after` response header if present; otherwise it uses exponential backoff with jitter. This prevents cascading failures under load.

### Streaming for long-running calls

The Layer 2 extraction notebook uses `call_claude_stream_json()` from `src/document/utils.py` because calls that process full PDF documents can exceed 10 minutes. The Anthropic SDK requires streaming mode for calls in that duration range.

---

## Cypher Patterns Reference

These are patterns that appear across `src/graph/queries.py` and the anomaly registry. Developers adding new queries should follow these conventions.

**Always parameterise queries.** Never use string interpolation for user-supplied or entity-derived values.

```cypher
-- Correct
MATCH (l:LoanApplication {loan_id: $loan_id}) RETURN l

-- Wrong — do not do this
MATCH (l:LoanApplication {loan_id: '""" + loan_id + """'}) RETURN l
```

**Collecting relationship types in variable-length paths.** The `type(r)` function does not work on a list of relationships. Use a list comprehension instead.

```cypher
-- Correct
MATCH (a)-[r:OWNS*1..3]->(b)
RETURN [rel IN r | type(rel)] AS rel_types

-- Wrong
MATCH (a)-[r:OWNS*1..3]->(b)
RETURN type(r) AS rel_type   -- this will fail
```

**Vector search.**

```cypher
CALL db.index.vector.queryNodes('chunk_embeddings', $k, $emb)
YIELD node AS c, score
WHERE score > 0.7
RETURN c.chunk_id, c.text, score
```

**Counting relationships in a variable-length path.** Use `size(r)` (treating `r` as a list of relationships), not `length(r)` (which expects a Path object).

```cypher
MATCH (a)-[r:OWNS*1..3]->(b)
RETURN size(r) AS hop_count
```

**Assessment ID construction** (from Python, not Cypher):

```python
assessment_id = f"ASSESS-{entity_id}-{regulation_id}-{now_local.strftime('%Y-%m-%d-%H%M%S')}"
```
