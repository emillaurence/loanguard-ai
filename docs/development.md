# Developer Guide

This document covers how to extend LoanGuard AI: adding new APRA documents, adding anomaly patterns, adding MCP tools, and modifying agent behaviour.

---

## Project Structure

```
loanguard-ai/
├── app.py                          # Streamlit application entry point
├── requirements.txt                # Python dependencies
├── .env.example                    # Environment variable template
│
├── notebooks/
│   ├── 111_structured_data_loader.ipynb    # Load Layer 1
│   ├── 211_extract_document_structure.ipynb
│   ├── 212_merge_and_resolve_references.ipynb
│   ├── 213_chunk_documents.ipynb
│   ├── 214_ingest_neo4j.ipynb
│   ├── 215_generate_embeddings.ipynb
│   ├── 216_validate_graph.ipynb
│   ├── 311_agent_setup.ipynb               # Bootstrap for 31x series
│   ├── 312_graph_tools.ipynb
│   ├── 313_compliance_agent.ipynb
│   ├── 314_investigation_agent.ipynb
│   ├── 315_anomaly_detection.ipynb
│   ├── 316_orchestrator_and_chat.ipynb
│   └── 317_layer_3_assessment_cleanup.ipynb
│
├── src/
│   ├── agent/
│   │   ├── orchestrator.py         # Routing + synthesis; constructs both specialist agents
│   │   ├── compliance_agent.py     # Agentic loop (max 8 iterations); threshold evaluation
│   │   ├── investigation_agent.py  # Agentic loop (max 14 iterations); graph traversal
│   │   ├── anomaly_detector.py     # Standalone AnomalyDetector class
│   │   ├── dispatcher.py           # make_execute_tool(conn) — single tool dispatch impl
│   │   ├── config.py               # MODEL, MAX_TOKENS, TOOL_RESULT_CHAR_LIMIT, WRITE_KEYWORDS, make_anthropic_client()
│   │   ├── utils.py                # call_claude_with_retry, extract_text, trim_message_history
│   │   └── _security.py            # guard_tool_result — prompt injection defence
│   │
│   ├── graph/
│   │   ├── connection.py           # Neo4jConnection driver wrapper
│   │   └── queries.py              # Parameterised Cypher helpers organised by layer
│   │
│   ├── mcp/
│   │   ├── schema.py               # GRAPH_SCHEMA_HINT, ANOMALY_REGISTRY, Verdict, Severity,
│   │   │                           # SEV_ORDER, VERDICT_PRIORITY, all dataclasses
│   │   ├── tool_defs.py            # TOOLS, NEO4J_MCP_TOOLS, FASTMCP_TOOL_DEFS
│   │   ├── tools_impl.py           # Plain Python tool implementations
│   │   └── investigation_server.py # FastMCP server (registers tools with @mcp.tool())
│   │
│   ├── retriever/
│   │   └── graphrag.py             # GraphRAGRetriever: NL → Cypher via Claude → Neo4j
│   │
│   └── document/
│       ├── config.py               # load_document_config() for document_config.yaml
│       ├── pdf_utils.py            # PDF text extraction utilities
│       └── utils.py                # strip_fences, call_claude_stream, call_claude_stream_json
│
├── data/
│   ├── layer_1/
│   │   ├── entities/               # One CSV per node label
│   │   └── links/                  # One CSV per relationship type
│   ├── layer_2/
│   │   ├── regulatory_documents/   # Source PDF files
│   │   ├── document_config.yaml    # Pipeline control file
│   │   ├── intermediate/           # Per-document extraction outputs from notebook 211
│   │   ├── sections.csv, requirements.csv, thresholds.csv, chunks.csv, cross_references.csv
│   │   └── regulations.csv
│   └── synthetic/                  # Committed synthetic data
│
└── tests/                          # 65 fully mocked unit tests
```

---

## Running Tests

```bash
pytest tests/ -v
```

All 65 tests are fully mocked — no Neo4j instance or API credentials are required.

### Test organisation

| File | What it tests |
|---|---|
| `tests/test_agent.py` | ComplianceAgent and InvestigationAgent agentic loops, tool dispatch, result parsing |
| `tests/test_orchestrator.py` | Orchestrator routing, synthesis, finding aggregation |
| `tests/test_tools.py` | FastMCP tool implementations (with mocked Neo4j and OpenAI) |
| `tests/test_security.py` | `guard_tool_result` pattern detection |
| `tests/test_schema.py` | Dataclass construction, VERDICT_PRIORITY ordering |

### Adding new tests

- Mock `Neo4jConnection.run_query` using `unittest.mock.patch` — see existing tests for the pattern
- Mock Anthropic API calls by patching `anthropic.Anthropic.messages.create`
- Mock OpenAI embedding calls by patching `openai.OpenAI.embeddings.create`
- Use `pytest.fixture` for shared agent/tool setup

---

## Adding a New APRA Document

1. Place the PDF in `data/layer_2/regulatory_documents/`

2. Add a new entry to `data/layer_2/document_config.yaml`:

```yaml
documents:
  - regulation_id: "APS-115"
    name: "Capital Adequacy: Advanced Measurement Approaches"
    issuing_body: "APRA"
    document_type: "Prudential Standard"
    effective_date: "2024-01-01"
    is_enforceable: true
    pdf_path: "regulatory_documents/APS_115_Capital_Adequacy_AMA.pdf"
    section_id_prefix: "APS-115-S"
    default_severity: "mandatory"
    supplemental_prompt: |
      For each capital floor table, create one threshold row per row in the table.
      Use threshold_type='minimum' for floor requirements and 'informational' for reference weights.
```

3. Run notebooks 211 → 216 in order. No code changes are needed.

4. Optionally update `GRAPH_SCHEMA_HINT` in `src/mcp/schema.py` to document the new regulation's key thresholds. This improves the accuracy of agent-generated Cypher queries.

---

## Adding a New Anomaly Pattern

1. Add a new entry to `ANOMALY_REGISTRY` in `src/mcp/schema.py`:

```python
ANOMALY_REGISTRY: dict[str, AnomalyPattern] = {
    # ... existing entries ...

    "sanctioned_officer": AnomalyPattern(
        description=(
            "Corporate borrowers with at least one director who has a sanctions list match. "
            "Requires immediate escalation to compliance."
        ),
        severity=Severity.HIGH,
        id_key="borrower_id",
        cypher="""
MATCH (off:Officer {sanctions_match: true})-[:DIRECTOR_OF]->(b:Borrower)
OPTIONAL MATCH (b)<-[:SUBMITTED_BY]-(l:LoanApplication)
RETURN b.borrower_id            AS borrower_id,
       b.name                   AS name,
       off.officer_id           AS officer_id,
       off.name                 AS officer_name,
       off.sanctions_match      AS sanctions_match,
       collect(DISTINCT l.loan_id) AS loan_ids
ORDER BY b.borrower_id
""",
    ),
}
```

### AnomalyPattern dataclass fields

| Field | Type | Description |
|---|---|---|
| `description` | str | Human-readable description. First sentence is used in `PATTERN_HINTS` (injected into agent system prompts). |
| `severity` | str | One of `Severity.HIGH`, `Severity.MEDIUM`, `Severity.LOW` |
| `cypher` | str | The detection Cypher query. Must RETURN at least `id_key`. |
| `id_key` | str | The RETURN column holding the primary entity ID |
| `params` | dict | Default Cypher parameter values (empty dict if none) |
| `threshold_id` | str | Linked Layer 2 Threshold node ID, or empty string |

2. The new pattern is immediately available to agents via `PATTERN_HINTS` — `PATTERN_HINTS` is auto-generated from the registry at module load time. No other code changes are needed.

3. Add a test in `tests/test_tools.py` that mocks `conn.run_query` and verifies the pattern runs without error.

---

## Adding a New MCP Tool

### Step 1: Implement in `src/mcp/tools_impl.py`

```python
def my_new_tool(entity_id: str, some_param: str) -> dict:
    """Brief description of what this tool does."""
    conn = _get_conn()
    try:
        rows = conn.run_query(
            "MATCH (n {id: $id}) RETURN n LIMIT 10",
            {"id": entity_id},
        )
        return {"entity_id": entity_id, "results": rows}
    finally:
        conn.close()
```

### Step 2: Add definition to `src/mcp/tool_defs.py`

Add to `FASTMCP_TOOL_DEFS`:

```python
{
    "name": "my_new_tool",
    "description": "Description shown to Claude in the system prompt.",
    "input_schema": {
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "The entity ID to query."
            },
            "some_param": {
                "type": "string",
                "description": "Description of the parameter."
            },
        },
        "required": ["entity_id"],
    },
},
```

### Step 3: Add dispatch case in `src/agent/dispatcher.py`

```python
from src.mcp.tools_impl import (
    # ... existing imports ...
    my_new_tool,
)

# In the execute_tool function body:
elif tool_name == "my_new_tool":
    return my_new_tool(**tool_input)
```

### Step 4: Add a test

Add to `tests/test_tools.py`:

```python
@patch("src.mcp.tools_impl._get_conn")
def test_my_new_tool(mock_get_conn):
    mock_conn = MagicMock()
    mock_conn.run_query.return_value = [{"id": "LOAN-0001"}]
    mock_get_conn.return_value = mock_conn

    result = my_new_tool(entity_id="LOAN-0001", some_param="value")
    assert result["entity_id"] == "LOAN-0001"
    assert len(result["results"]) == 1
```

### Step 5: Update `GRAPH_SCHEMA_HINT` if needed

If the tool queries new node types or relationship types not already documented in `GRAPH_SCHEMA_HINT`, add them in `src/mcp/schema.py` so agent-generated Cypher queries remain accurate.

---

## Modifying Agent Behaviour

### System prompt location

| Agent | System prompt variable | File |
|---|---|---|
| ComplianceAgent | `SYSTEM_PROMPT` | `src/agent/compliance_agent.py` |
| InvestigationAgent | `SYSTEM_PROMPT` | `src/agent/investigation_agent.py` |
| Orchestrator routing | `ROUTING_SYSTEM` | `src/agent/orchestrator.py` |
| Orchestrator synthesis | `SYNTHESIS_SYSTEM` | `src/agent/orchestrator.py` |

### Changing iteration or history limits

In `src/agent/compliance_agent.py`:
```python
MAX_ITERATIONS = 8       # Max agentic loop iterations
MAX_HISTORY_PAIRS = 4    # Max tool-use/tool-result pairs retained in history
```

In `src/agent/investigation_agent.py`:
```python
MAX_ITERATIONS = 14
MAX_HISTORY_PAIRS = 6
```

Increasing `MAX_ITERATIONS` allows the agent more tool calls but increases latency and token costs. Increasing `MAX_HISTORY_PAIRS` increases context retention but grows the input token count per API call.

### Tool workflow instructions

Both agents' system prompts contain a `## Your workflow` section listing the required tool call sequence. Modifying this section changes the agent's default behaviour — be precise about which tools are mandatory vs. optional.

---

## Code Conventions

### Parameterised Cypher — always

Never use string interpolation for user-supplied or entity-derived values in Cypher queries:

```python
# Correct
conn.run_query("MATCH (l:LoanApplication {loan_id: $id}) RETURN l", {"id": loan_id})

# Wrong — never do this
conn.run_query(f"MATCH (l:LoanApplication {{loan_id: '{loan_id}'}}) RETURN l")
```

### Import from `config.py`

All agent modules import shared constants from `src/agent/config.py`:

```python
from src.agent.config import MODEL, MAX_TOKENS, TOOL_RESULT_CHAR_LIMIT, make_anthropic_client
```

Do not hardcode model names or token limits in individual files.

### Import from `schema.py`

Sorting, aggregation, and verdict logic must use the constants from `src/mcp/schema.py`:

```python
from src.mcp.schema import SEV_ORDER, VERDICT_PRIORITY, Verdict, Severity
```

Do not redefine `SEV_ORDER` or `VERDICT_PRIORITY` anywhere else.

### Use shared utilities from `utils.py`

```python
from src.agent.utils import call_claude_with_retry, extract_text, trim_message_history
```

Do not implement retry logic or message history trimming inline in agent classes.

---

## Key Design Decisions

### Why simulated Neo4j MCP tools (not real MCP server)?

Portability. The tool schemas in `src/mcp/tool_defs.py` match the official Neo4j MCP server's schemas exactly. When this project is deployed in an environment where the real Neo4j MCP server runs over stdio, agent prompts remain unchanged — only the dispatcher changes. Running the tools via local dispatch avoids the operational complexity of managing an MCP server process during development and testing.

### Why the Jurisdiction bridge node?

Extensibility. Without the Jurisdiction bridge, every regulation would need a direct relationship to every applicable borrower or loan application — a many-to-many cross between layers. The bridge reduces this to two one-to-many relationships: entities link to jurisdictions, and regulations declare which jurisdictions they govern. Adding compliance coverage for a new jurisdiction (e.g. for a New Zealand expansion) requires only adding new `Jurisdiction` nodes and updating regulation linkage — no structural changes to Layer 1 entity data or agent code.

### Why `threshold_type` in data, not code?

Separation of concerns. The compliance semantics of each threshold (is it a minimum floor, an observation trigger, or a reference value?) are a property of the regulation itself, not a property of the evaluation code. Storing `threshold_type` in the `Threshold` nodes means:
- New thresholds with correct types are available to agents immediately after a pipeline run
- Changing a threshold's type requires only re-running the extraction pipeline
- The evaluation code in `evaluate_thresholds` remains stable regardless of regulatory changes

### Why streaming for 21x notebooks?

Anthropic SDK requirement. The SDK enforces a timeout for synchronous `messages.create` calls. PDF extraction calls (which process 50–150 pages of regulatory text in a single prompt) routinely exceed this limit. `call_claude_stream_json()` in `src/document/utils.py` uses the streaming API with no timeout constraint, reassembles the full response, and parses the JSON from the streamed text.

### Why prompt caching in `ComplianceAgent`?

Latency and cost. The ComplianceAgent's system prompt includes `GRAPH_SCHEMA_HINT` — the full graph schema — plus all threshold types and the agent's required workflow instructions. This is several thousand tokens that are identical across every iteration of the agentic loop and across every query for the same regulation. Marking it with `cache_control: ephemeral` amortises the input token cost across all calls within a session, reducing both latency and billing.
