# Notebooks Reference

LoanGuard AI uses 15 Jupyter notebooks across three series. Each series has a distinct purpose and dependency structure.

---

## Overview

| Series | Purpose | Dependencies |
|---|---|---|
| **1xx** | Load Layer 1 financial entity data into Neo4j | Neo4j connection, Layer 1 CSVs |
| **2xx** | Build Layer 2 regulatory knowledge graph from APRA PDFs | Neo4j connection, Anthropic API key (211–212), OpenAI API key (215), Layer 1 loaded |
| **3xx** | Agent development, testing, and demonstration | Neo4j with both layers loaded, all API keys |

The 2xx series notebooks must be run in order (211 → 216). Each notebook produces outputs consumed by the next. The 3xx series can be run in any order after 311 bootstraps the shared environment.

---

## Series 1xx — Entity Data Loading

### `111_structured_data_loader`

**Purpose:** Load all Layer 1 CSV files from `data/layer_1/` into Neo4j as nodes and relationships.

**Inputs:**
- `data/layer_1/entities/` — one CSV per node label
- `data/layer_1/links/` — one CSV per relationship type

**Outputs (Neo4j):**
- 628 `Borrower` nodes (`:Individual` and `:Corporate`)
- 466 `LoanApplication` nodes (`:ResidentialSecured`)
- 791 `BankAccount` nodes
- 173 `Transaction` nodes
- 466 `Collateral` nodes
- 19 `Officer` nodes
- 609 `Address` nodes
- 7 `Jurisdiction` nodes
- 14 `Industry` nodes
- All Layer 1 relationships

**Estimated time:** 2–3 minutes.

**If it fails:** Check that `NEO4J_URI`, `NEO4J_USERNAME`, and `NEO4J_PASSWORD` are set in `.env`. Confirm the AuraDB instance is running (it may have paused after inactivity). Re-run the notebook — it uses MERGE and is safe to re-run.

---

## Series 2xx — Layer 2 Regulatory Pipeline

Run these notebooks in order. Do not skip any step.

### `211_extract_document_structure`

**Purpose:** Extract sections, requirements, thresholds (with `threshold_type`), and cross-references from each APRA PDF using Claude.

**Inputs:**
- `data/layer_2/regulatory_documents/` — source PDF files
- `data/layer_2/document_config.yaml` — controls which documents are processed and provides document-specific extraction prompts

**Outputs (`data/layer_2/intermediate/`):**
- `{regulation_id}_sections.csv`
- `{regulation_id}_requirements.csv`
- `{regulation_id}_thresholds.csv`
- `{regulation_id}_references.csv`

**Key implementation notes:**
- Uses `call_claude_stream_json()` from `src/document/utils.py` because PDF extraction calls can exceed 10 minutes per document; the Anthropic SDK requires streaming for long-running calls
- `close_page_gaps()` runs post-extraction to absorb unclaimed cover pages and ToC pages into adjacent sections
- The `supplemental_prompt` field in `document_config.yaml` provides document-specific instructions to Claude (e.g. "create one threshold row per LVR band cell in risk weight tables")

**Estimated time:** ~40 minutes (three documents, each requires multiple streaming API calls).

**If it fails:**
- Rate limit errors: the notebook retries automatically; wait and re-run from the failed document
- JSON parse errors in Claude's response: re-run the affected document; the streaming parser is tolerant of minor formatting issues
- If a specific document fails consistently, check the `supplemental_prompt` for that document in `document_config.yaml`

---

### `212_merge_and_resolve_references`

**Purpose:** Merge per-document intermediate CSVs into the unified Layer 2 CSVs; Claude resolves cross-document section references to known section IDs.

**Inputs:**
- `data/layer_2/intermediate/{regulation_id}_*.csv` — from notebook 211

**Outputs:**
- `data/layer_2/sections.csv`
- `data/layer_2/requirements.csv`
- `data/layer_2/thresholds.csv`
- `data/layer_2/cross_references.csv`

**Key implementation notes:**
- Cross-reference resolution: Claude receives the full list of known section IDs and maps free-text references (e.g. "refer to Attachment A of APS-112") to canonical section IDs
- Duplicate threshold IDs across documents are merged (same `threshold_id` → same row)

**Estimated time:** ~2 minutes.

**If it fails:** Check that all four sets of intermediate files from notebook 211 are present. Re-run notebook 211 for any missing regulation.

---

### `213_chunk_documents`

**Purpose:** Split section text into ~300-token chunks for semantic search.

**Inputs:**
- `data/layer_2/sections.csv` — from notebook 212

**Outputs:**
- `data/layer_2/chunks.csv`

**Key implementation notes:**
- Chunking uses a token-aware splitter targeting ~300 tokens per chunk with overlap
- Raises `RuntimeError` if any page range in the extracted data has a coverage gap (an unclaimed page that was not absorbed by `close_page_gaps()` in notebook 211)
- If a RuntimeError occurs, fix the gap in notebook 211 first, then re-run 212 and 213

**Estimated time:** ~1 minute.

**If it fails:** A `RuntimeError: page coverage gap` means a page in one of the source documents was not assigned to any section. Open notebook 211 for the affected regulation, locate the gap using the diagnostic output, and adjust the extraction or re-run with updated `close_page_gaps()` parameters.

---

### `214_ingest_neo4j`

**Purpose:** Load all Layer 2 CSV data into Neo4j as nodes and relationships.

**Inputs:**
- `data/layer_2/regulations.csv`
- `data/layer_2/sections.csv`
- `data/layer_2/requirements.csv`
- `data/layer_2/thresholds.csv`
- `data/layer_2/chunks.csv`
- `data/layer_2/cross_references.csv`

**Outputs (Neo4j):**
- All Layer 2 nodes and relationships
- Creates the `chunk_embeddings` vector index (1,536 dims, cosine)

**Key implementation notes:**
- Clears and reloads all Layer 2 nodes on every run — safe to re-run without affecting Layers 1 or 3
- Creates `APPLIES_TO_JURISDICTION` relationships linking each regulation to `JUR-AU-FED`
- Creates `SEMANTICALLY_SIMILAR` placeholder relationships (populated by notebook 215)

**Estimated time:** ~1 minute.

**If it fails:** Check Neo4j connection. If the vector index creation fails, verify that your AuraDB instance supports vector indexes (AuraDB Free supports them).

---

### `215_generate_embeddings`

**Purpose:** Generate OpenAI embeddings for all `Chunk` nodes; create `SEMANTICALLY_SIMILAR` edges for cross-document chunk pairs with high cosine similarity.

**Inputs:**
- `Chunk` nodes in Neo4j (from notebook 214)
- OpenAI API key (`OPENAI_API_KEY` env var)

**Outputs (Neo4j):**
- `embedding` property on every `Chunk` node (1,536-dimensional float array)
- `SEMANTICALLY_SIMILAR` edges between cross-document chunk pairs with cosine > 0.85

**Key implementation notes:**
- Embedding model: `text-embedding-3-small`
- Batches embeddings to respect OpenAI rate limits
- Only cross-document pairs are connected by `SEMANTICALLY_SIMILAR` — same-document pairs are excluded regardless of similarity score
- Re-running overwrites existing embeddings and recreates similarity edges

**Estimated time:** ~3 minutes (189 chunks).

**If it fails:** Check `OPENAI_API_KEY` is set. If rate limited, the notebook will retry. Re-running the notebook from the failed batch is safe.

---

### `216_validate_graph`

**Purpose:** Validate that the Layer 2 graph is correctly loaded and the vector index is healthy.

**Checks performed:**
- Node counts: Regulation (3), Section (101), Requirement (219), Threshold (133), Chunk (189)
- Relationship counts for `HAS_SECTION`, `HAS_REQUIREMENT`, `DEFINES_LIMIT`, `HAS_CHUNK`, `SEMANTICALLY_SIMILAR`
- Vector index `chunk_embeddings` is `ONLINE`
- All `Threshold` nodes have a valid `threshold_type` value

**Expected result:** All checks pass. Any failure indicates a problem in a preceding notebook that must be fixed before running the application.

**Estimated time:** < 1 minute.

**If it fails:** The output will indicate which check failed. Common fixes:
- Low node counts: re-run 214 (ingest)
- Missing embeddings or index POPULATING: wait 60 seconds and re-run the validation cell; re-run 215 if embeddings are missing
- Invalid `threshold_type` values: check the extraction prompts in `document_config.yaml` and re-run 211 → 216

---

## Series 3xx — Agent Development and Testing

All 3xx notebooks depend on `311_agent_setup` being executed first. Later notebooks use `%run 311_agent_setup.ipynb` in their first cell to bootstrap the shared environment.

### `311_agent_setup`

**Purpose:** Bootstrap shared setup for the 3xx series — Neo4j connection, tool definitions, and execute_tool dispatcher.

**Provides to downstream notebooks:**
- `conn` — an open `Neo4jConnection`
- `tools` — the full tool list from `tool_defs.py`
- `execute_tool` — the dispatcher from `dispatcher.py`
- `compliance_agent` and `investigation_agent` instances

**Prerequisites:** Both Layer 1 and Layer 2 loaded in Neo4j; all API keys set.

---

### `312_graph_tools`

**Purpose:** Test all six FastMCP tools against live Neo4j data.

**Demonstrates:** One call per tool with representative inputs; inspection of return shapes.

**Expected output:** Non-empty results from all six tools; `evaluate_thresholds` shows PASS/BREACH/TRIGGER/N/A results for a sample loan.

---

### `313_compliance_agent`

**Purpose:** Run `ComplianceAgent` against real loan applications and inspect full reasoning chains.

**Demonstrates:**
- Full agentic loop execution (traverse → evaluate → persist)
- Structured text parsing (`_parse_result`)
- Assessment ID and persisted findings in Layer 3
- Evidence tracker annotation in tool results

**Expected output:** `ComplianceResult` with verdict, requirement IDs, threshold breach IDs, and assessment ID. Verify the assessment was written to Neo4j with a `MATCH (a:Assessment) RETURN a LIMIT 5` query.

---

### `314_investigation_agent`

**Purpose:** Run `InvestigationAgent` for entity network exploration.

**Demonstrates:**
- Comprehensive first-query pattern
- Single `detect_graph_anomalies` call with multiple patterns
- Structured risk signal output

**Expected output:** `InvestigationResult` with entity connections, risk signals tagged [HIGH]/[MEDIUM]/[LOW], and Cypher queries used.

---

### `315_anomaly_detection`

**Purpose:** Run all six anomaly detection patterns and inspect findings.

**Demonstrates:**
- `detect_graph_anomalies` with all pattern names in one call
- Pattern-level finding counts and entity IDs
- Severity distribution across patterns

**Expected output:** Non-zero findings for `high_lvr_loans`, `high_risk_jurisdiction`, and `guarantor_concentration` patterns (based on the sample dataset).

---

### `316_orchestrator_and_chat`

**Purpose:** Full end-to-end Orchestrator pipeline demonstration.

**Demonstrates:**
- Routing JSON output for various question types
- Both agents running in sequence
- `trace_evidence` populating cited sections and chunks
- Final `InvestigationResponse` structure including answer, findings, and recommended next steps

**Expected output:** `InvestigationResponse` with populated `cited_sections`, `cited_chunks`, `findings`, and `recommended_next_steps`.

---

### `317_layer_3_assessment_cleanup`

**Purpose:** Reset Layer 3 by removing all `Assessment`, `Finding`, and `ReasoningStep` nodes.

**Use this when:** Starting a fresh demo run; clearing test data from the development loop; resetting before running notebook 316.

**Cypher executed:**
```cypher
MATCH (n:Assessment) DETACH DELETE n;
MATCH (n:Finding) DETACH DELETE n;
MATCH (n:ReasoningStep) DETACH DELETE n;
```

**This does not affect:** Layer 1 or Layer 2 nodes.

---

## Running Order

### Starting from scratch

```
111_structured_data_loader
  → 211_extract_document_structure
    → 212_merge_and_resolve_references
      → 213_chunk_documents
        → 214_ingest_neo4j
          → 215_generate_embeddings
            → 216_validate_graph
              → [run any 3xx notebook]
```

### What to re-run when something changes

| What changed | Re-run |
|---|---|
| Layer 1 CSV files | 111 only |
| New APRA PDF added to `document_config.yaml` | 211 → 216 in order |
| Extraction prompt changed in 211 | 211 → 216 in order |
| `thresholds.csv` edited directly | 214 only |
| Chunk text changed | 213 → 216 |
| Embeddings need regeneration | 215 only |
| Layer 3 data needs clearing | 317 only |

---

## Common Notebook Issues

### Kernel restart loses the conn variable

`conn` is opened in the kernel's memory. After a kernel restart, re-run `311_agent_setup` (or its `%run` call in the current notebook) before making any Neo4j calls.

### `%run` path resolution

All 3xx notebooks use `%run 311_agent_setup.ipynb` assuming the notebook is opened from the `notebooks/` directory. If you run from a different working directory, adjust the path to an absolute path or use `%run /abs/path/to/notebooks/311_agent_setup.ipynb`.

### Neo4j connection timeout

AuraDB Free instances pause after a period of inactivity. If you see a connection error after leaving the instance idle, go to the AuraDB console and resume the instance, then retry the connection.

### Long-running cells in notebook 211

The streaming extraction cells for each PDF can run for 10–40 minutes. This is expected — the Anthropic SDK streaming call processes the full document. Do not interrupt the cell unless it appears stuck for more than 60 minutes.
