# MCP Tools Reference

LoanGuard AI agents have access to eight tools across two categories. This document describes each tool's purpose, parameters, return shape, and usage notes.

---

## Tool Architecture

### Two tool categories

**FastMCP tools** — six tools implementing domain-specific compliance logic. Implemented as plain Python in `src/mcp/tools_impl.py` and registered with the FastMCP server in `src/mcp/investigation_server.py`. Agents and notebooks import from `tools_impl` directly.

**Simulated Neo4j MCP tools** — two tools with the same interface as the [official Neo4j MCP server](https://github.com/neo4j-contrib/mcp-neo4j), dispatched locally via `Neo4jConnection`. No external MCP process is involved.

### Single source of truth

All tool definitions for Claude's tool-use API (name, description, input schema) are defined in `src/mcp/tool_defs.py`:

- `FASTMCP_TOOL_DEFS` — definitions for the six FastMCP tools
- `NEO4J_MCP_TOOLS` — definitions for the two Neo4j MCP tools
- `TOOLS` — `FASTMCP_TOOL_DEFS + NEO4J_MCP_TOOLS` — the full list passed to agents

`app.py` and the `311_agent_setup` notebook both import from `tool_defs.py`.

### Dispatcher

`make_execute_tool(conn)` in `src/agent/dispatcher.py` returns a single `execute_tool(tool_name, tool_input)` function that routes by tool name to either the FastMCP implementations or `conn.run_query`. The caller owns the `Neo4jConnection` lifecycle.

### Portability rationale

The Neo4j MCP tool names (`read-neo4j-cypher`, `write-neo4j-cypher`) match the official Neo4j MCP server schema. This means agent system prompts and tool descriptions remain valid in deployments where the real Neo4j MCP server runs over stdio — no prompt changes are needed to switch modes.

---

## Tool 1: `traverse_compliance_path`

Cross-layer traversal from a financial entity through the Jurisdiction bridge to all applicable regulations, sections, requirements, and thresholds.

### Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `entity_id` | str | Yes | The `loan_id` or `borrower_id` |
| `entity_type` | str | Yes | `LoanApplication` or `Borrower` |
| `regulation_id` | str | No | Restrict to one regulation (e.g. `APG-223`) |

### Return shape

```json
{
  "entity_id": "LOAN-0042",
  "entity_type": "LoanApplication",
  "regulations": {
    "APG-223": {
      "sections": {
        "APG-223-S3": {
          "title": "Serviceability Assessment",
          "content_summary": "...",
          "requirements": [...],
          "thresholds": [
            {
              "threshold_id": "APG-223-THR-001",
              "metric": "serviceability_interest_rate_buffer",
              "operator": ">=",
              "value": 3.0,
              "unit": "percent",
              "threshold_type": "minimum",
              "condition_context": "{}"
            }
          ]
        }
      }
    }
  }
}
```

### Notes

- The Cypher path is: `(LoanApplication)-[:SUBMITTED_BY]->(Borrower)-[:RESIDES_IN|REGISTERED_IN]->(Jurisdiction)<-[:APPLIES_TO_JURISDICTION]-(Regulation)-[:HAS_SECTION]->(Section)-[:HAS_REQUIREMENT]->(Requirement)-[:DEFINES_LIMIT]->(Threshold)`
- Each threshold includes `threshold_type` — the ComplianceAgent uses this to decide which thresholds to pass to `evaluate_thresholds`
- If `regulation_id` is supplied, only that regulation's subgraph is returned

---

## Tool 2: `retrieve_regulatory_chunks`

Semantic similarity search over `Chunk` nodes using OpenAI embeddings.

### Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `query_text` | str | Yes | Natural language query to embed and search |
| `regulation_id` | str | No | Filter results to one regulation |
| `top_k` | int | No | Number of chunks to return (default 5, max 20) |

### Return shape

```json
{
  "query": "serviceability buffer requirement",
  "chunks": [
    {
      "chunk_id": "APG-223-S3-C001",
      "section_id": "APG-223-S3",
      "text": "ADIs should apply a minimum interest rate buffer...",
      "chunk_index": 0,
      "source_document": "APG-223",
      "similarity_score": 0.9134
    }
  ]
}
```

### Notes

- Embedding model: OpenAI `text-embedding-3-small` (1,536 dims)
- Vector index name: `chunk_embeddings`
- Similarity metric: cosine
- Cypher used: `CALL db.index.vector.queryNodes('chunk_embeddings', $k, $emb) YIELD node AS c, score`
- Text is truncated to 800 characters per chunk before returning
- `similarity_score` values are rounded to 4 decimal places
- The ComplianceAgent stores these scores in `seen_chunk_scores` and injects them into `persist_assessment` for later retrieval via `trace_evidence`

---

## Tool 3: `detect_graph_anomalies`

Runs one or more named detection patterns from `ANOMALY_REGISTRY` against the graph.

### Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `pattern_names` | list[str] | Yes | One or more keys from `ANOMALY_REGISTRY` |
| `entity_id` | str | No | Scope results to a specific entity (loan or borrower ID) |

### Return shape

```json
{
  "patterns_run": 2,
  "total_findings": 5,
  "results": [
    {
      "pattern_name": "high_lvr_loans",
      "severity": "HIGH",
      "description": "Loan applications with LVR >= 90%...",
      "finding_count": 3,
      "findings": [...],
      "entity_ids": ["LOAN-0042", "LOAN-0107", "LOAN-0218"]
    }
  ]
}
```

### Notes

- If any `pattern_name` is not in `ANOMALY_REGISTRY`, the tool returns an error with the list of valid names
- When `entity_id` is supplied, the pattern Cypher is modified to add a filter on the entity's ID field
- Always call with all relevant patterns in one call — the InvestigationAgent system prompt explicitly instructs this to avoid wasting tool-call budget
- Valid pattern names: `transaction_structuring`, `high_lvr_loans`, `high_risk_industry`, `layered_ownership`, `high_risk_jurisdiction`, `guarantor_concentration`

---

## Tool 4: `persist_assessment`

Writes a compliance assessment to Layer 3 using idempotent `MERGE` operations.

### Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `entity_id` | str | Yes | Entity being assessed |
| `entity_type` | str | Yes | `LoanApplication` or `Borrower` |
| `regulation_id` | str | Yes | Regulation assessed against (e.g. `APG-223`) |
| `verdict` | str | Yes | One of `COMPLIANT`, `NON_COMPLIANT`, `REQUIRES_REVIEW`, `ANOMALY_DETECTED`, `INFORMATIONAL` |
| `confidence` | float | Yes | 0.0 to 1.0; clamped automatically |
| `findings` | list[dict] | Yes | Each dict: `finding_type`, `severity`, `description`, optional `pattern_name`, `related_entity_id`, `related_entity_type` |
| `reasoning_steps` | list[dict] | Yes | Each dict: `description`, optional `cypher_used`, `section_ids` (list), `chunk_ids` (list), `chunk_scores` (dict) |
| `agent` | str | No | Defaults to `"compliance_agent"` |

### Return shape

```json
{
  "assessment_id": "ASSESS-LOAN-0042-APG-223-2026-03-10-143022",
  "findings": [
    {
      "finding_id": "FIND-ASSESS-LOAN-0042-APG-223-2026-03-10-143022-000",
      "finding_type": "compliance_breach",
      "severity": "HIGH",
      "description": "Serviceability buffer of 2.5pp is below the 3.0pp minimum (APG-223-THR-001).",
      "pattern_name": null
    }
  ],
  "step_ids": ["STEP-ASSESS-LOAN-0042-APG-223-2026-03-10-143022-000"]
}
```

### Notes

- **Assessment ID format:** `ASSESS-{entity_id}-{regulation_id}-{YYYY-MM-DD-HHMMSS}` (local time, unique per run)
- `MERGE` is used throughout — re-running with the same assessment ID is safe but will not create duplicates
- `chunk_scores` in each reasoning step dict is a `{chunk_id: score}` map; scores are written to the `similarity_score` property on `CITES_CHUNK` relationships
- The agent may call `persist_assessment` once per regulation when multiple regulations apply; the Orchestrator aggregates all returned `assessment_id` values

---

## Tool 5: `trace_evidence`

Walks a stored `Assessment` node back through its `ReasoningStep` nodes to retrieve all cited regulatory sections and chunks.

### Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `assessment_id` | str | Yes | The assessment to trace |

### Return shape

```json
{
  "assessment": {
    "assessment_id": "ASSESS-LOAN-0042-APG-223-2026-03-10-143022",
    "entity_id": "LOAN-0042",
    "verdict": "NON_COMPLIANT",
    "confidence": 0.92
  },
  "reasoning_steps": [...],
  "cited_sections": [
    {
      "section_id": "APG-223-S3",
      "title": "Serviceability Assessment",
      "content_summary": "Requirements for ADI serviceability testing...",
      "regulation_id": "APG-223"
    }
  ],
  "cited_chunks": [
    {
      "chunk_id": "APG-223-S3-C001",
      "section_id": "APG-223-S3",
      "text_excerpt": "ADIs should apply a minimum interest rate buffer...",
      "chunk_index": 0,
      "similarity_score": 0.9134
    }
  ]
}
```

### Notes

- `similarity_score` on each cited chunk is recovered from the `similarity_score` property on the `CITES_CHUNK` relationship in Neo4j (written at `persist_assessment` time)
- If a chunk was cited without a vector search score (e.g. cited via a Cypher query), `similarity_score` is `null` and the Evidence panel displays "cited" instead of a numeric score
- Text excerpts are truncated to 400 characters
- Called by the Orchestrator after both agents complete, to populate the Evidence panel in the Streamlit UI

---

## Tool 6: `evaluate_thresholds`

Evaluates a list of threshold definitions against an entity's stored property values. This is the core of deterministic verdict derivation.

### Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `entity_id` | str | Yes | The entity to evaluate |
| `entity_type` | str | Yes | `LoanApplication` or `Borrower` |
| `thresholds` | list[dict] | Yes | Threshold dicts from `traverse_compliance_path`, each with `threshold_id`, `metric`, `operator`, `value`, `threshold_type` |

### Return shape

```json
{
  "entity_id": "LOAN-0042",
  "entity_type": "LoanApplication",
  "entity_values": {
    "lvr": 92.5,
    "serviceability_buffer_applied": 2.5,
    "non_salary_income_haircut_pct": null
  },
  "evaluation": [
    {
      "threshold_id": "APG-223-THR-001",
      "metric": "serviceability_interest_rate_buffer",
      "operator": ">=",
      "limit": 3.0,
      "unit": "percent",
      "actual": 2.5,
      "status": "BREACH",
      "breached": true,
      "margin": -0.5,
      "threshold_type": "minimum"
    },
    {
      "threshold_id": "APG-223-THR-005",
      "metric": "LVR",
      "operator": ">=",
      "limit": 90.0,
      "actual": 92.5,
      "status": "TRIGGER",
      "breached": true,
      "margin": 2.5,
      "threshold_type": "trigger"
    }
  ],
  "summary": {"total": 2, "breached": 1, "passed": 0, "unknown": 0, "triggered": 1},
  "breached_threshold_ids": ["APG-223-THR-001"],
  "triggered_threshold_ids": ["APG-223-THR-005"]
}
```

### Threshold type dispatch

| `threshold_type` | Condition true | Condition false |
|---|---|---|
| `minimum` | `PASS` | `BREACH` |
| `maximum` | `PASS` | `BREACH` |
| `trigger` | `TRIGGER` | `PASS` |
| `informational` | `N/A` (always) | `N/A` (always) |

For `minimum` and `maximum`, "condition" means the operator expression `actual OP limit` evaluates to `True`. For `trigger`, the same expression evaluating to `True` fires the concern.

### Conditional N/A logic

The ComplianceAgent applies conditional filtering before calling `evaluate_thresholds`:

- Thresholds with `threshold_type=informational` are excluded from the call
- `non_salary_income_haircut` thresholds are excluded when `income_type == 'salary'`
- `rental_income_haircut` thresholds are excluded when `rental_income_gross` is absent

The tool returns `status=unknown` when the entity does not have a stored value for the threshold's metric. `unknown` is treated as `REQUIRES_REVIEW` for material thresholds.

---

## Tool 7: `read-neo4j-cypher`

Executes a read-only Cypher query directly against the graph.

### Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `query` | str | Yes | A read-only Cypher query |
| `params` | dict | No | Query parameter values |

### Return shape

```json
{"rows": [{"loan_id": "LOAN-0042", "lvr": 92.5}]}
```

### Notes

- Write keywords are blocked at the dispatcher level (whole-word uppercase match): `MERGE`, `CREATE`, `DELETE`, `SET`, `DETACH`, `REMOVE`, `DROP`
- The blocking uses `re.findall(r"\b[A-Z]+\b", query.upper())` — whole-word matching prevents false positives such as `ASSESSMENT` containing `SET` or `DETACHMENT` containing `DETACH`
- Always use parameterised queries (`$param`) — never string interpolation for user data
- Always include a `LIMIT` clause — results are not bounded by the tool itself

---

## Tool 8: `write-neo4j-cypher`

Executes write Cypher queries against Neo4j.

### Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `query` | str | Yes | A write Cypher query (`MERGE`, `CREATE`, `SET`, etc.) |
| `params` | dict | No | Query parameter values |

### Return shape

```json
{"rows": [...]}
```

### Notes

- Use only for Layer 3 writes (`Assessment`, `Finding`, `ReasoningStep` nodes)
- Prefer `persist_assessment` for all structured Layer 3 writes — it handles idempotency, ID generation, and relationship creation
- `write-neo4j-cypher` is appropriate for ad-hoc cleanup or one-off structural writes not covered by `persist_assessment`

---

## Security

### Write keyword blocking

The `read-neo4j-cypher` dispatcher checks for write keywords before executing any query:

```python
query_words = set(re.findall(r"\b[A-Z]+\b", query.upper()))
if query_words & WRITE_KEYWORDS:
    return {"error": "read-neo4j-cypher does not allow write operations."}
```

The `WRITE_KEYWORDS` frozenset (defined in `src/agent/config.py`):

```python
WRITE_KEYWORDS: frozenset[str] = frozenset({
    "MERGE", "CREATE", "DELETE", "SET", "DETACH", "REMOVE", "DROP"
})
```

Whole-word matching is deliberate: `ASSESSMENT` contains the substring `SET` but does not match the whole word `SET`. Similarly, `DETACHMENT` does not match `DETACH`.
