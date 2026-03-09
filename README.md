# GraphRAG Financial Services Loan Compliance Agent

An agentic GraphRAG application for Australian financial services compliance, powered by **Anthropic Claude**, **Neo4j AuraDB**, and **OpenAI embeddings**.

## Overview

This project implements a multi-agent compliance reasoning system that:
- Stores financial entities, APRA regulatory obligations, and runtime compliance assessments in a three-layer Neo4j knowledge graph
- Extracts and chunks APRA prudential standards (APS-220, APS-112, APG-223) from source PDFs into a searchable regulatory graph
- Uses Claude's tool-use API to dynamically query the graph in response to natural-language compliance questions
- Applies a GraphRAG pattern to retrieve semantically similar regulatory chunks via vector search
- Persists compliance assessments (verdict, findings, reasoning steps) back to the graph as Layer 3 nodes

## Graph Layer Architecture

| Layer | Description | Key Nodes |
|---|---|---|
| **1 — Entity** | ADI borrowers, loan applications, accounts, transactions | `Borrower`, `LoanApplication`, `BankAccount`, `Transaction`, `Collateral`, `Officer`, `Jurisdiction`, `Industry` |
| **2 — Regulatory** | APRA prudential standards, sections, requirements, thresholds, and embedded chunks | `Regulation`, `Section`, `Requirement`, `Threshold`, `Chunk` |
| **3 — Assessment** | Runtime compliance results written by agents | `Assessment`, `Finding`, `ReasoningStep` |

The `Jurisdiction` node bridges Layers 1 and 2: borrowers link to jurisdictions via `RESIDES_IN`/`REGISTERED_IN`, and regulations declare which jurisdictions they apply to via `APPLIES_TO_JURISDICTION`. All APRA regulations point to `JUR-AU-FED`.

## Agent Pipeline

```
User question
    → Orchestrator.run()          (intent routing via Claude)
    → ComplianceAgent.run()       (APRA threshold checks, persists to Layer 3)
    → InvestigationAgent.run()    (graph traversal, risk signals)
    → synthesis Claude call       (merges outputs → InvestigationResponse)
```

- All Claude calls use `temperature=0`, model `claude-sonnet-4-6`
- `ComplianceAgent` uses prompt caching (`cache_control: ephemeral`) on its system prompt
- Tool results are truncated to 3 000 chars to avoid context bloat; history is windowed to the last 4 message pairs
- Rate limit errors retry with exponential backoff (reads `retry-after` header first)
- Assistant messages are stored as plain dicts (not SDK objects) to guarantee stable tool_use/tool_result ID matching across API calls

## MCP Tool Layer

Five tools callable by agents — implemented as plain Python in `src/mcp/tools_impl.py`:

| Tool | Description |
|---|---|
| `traverse_compliance_path` | Cross-layer L1→L2 traversal via Jurisdiction bridge |
| `retrieve_regulatory_chunks` | OpenAI `text-embedding-3-small` vector search over `Chunk` nodes |
| `detect_graph_anomalies` | Named Cypher patterns from `ANOMALY_REGISTRY` |
| `persist_assessment` | Idempotent MERGE to Layer 3 (Assessment + Finding + ReasoningStep) |
| `trace_evidence` | Walks a stored Assessment back to cited sections and chunks |

## Streamlit UI

```bash
streamlit run app.py
```

Features:
- **Verdict banner** — full-width colored card with icon, plain-English explanation, confidence %, and 140 px fill bar
- **Findings** — severity-sorted colored cards with `.card-accent-{HIGH|MEDIUM|LOW|INFO}` left border; colored section label and expander title reflect the highest severity present
- **Severity map** — Plotly horizontal bar chart below findings; hover shows full description, type, and pattern
- **Evidence** — two-column layout: cited regulatory sections (left) and cited chunks with similarity score (right)
- **Routing** — 2×2 chip grid showing intents, entities, regulations, and agent pipeline
- **Next steps** — numbered card layout with circle badge per step

## Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url>
cd graphrag-finserv-compliance
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, OPENAI_API_KEY, NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD
```

### 4. Load Layer 1 entity data

```bash
jupyter lab
# Run: notebooks/111_structured_data_loader.ipynb
```

### 5. Run the Layer 2 regulatory pipeline (in order)

| Notebook | What it does |
|---|---|
| `211_extract_document_structure` | Extracts sections, requirements, and thresholds from regulatory PDFs via Claude |
| `212_merge_and_resolve_references` | Merges per-document outputs and resolves cross-references via Claude |
| `213_chunk_documents` | Chunks section text into ~300-token segments |
| `214_ingest_neo4j` | Loads all Layer 2 nodes and relationships into Neo4j |
| `215_generate_embeddings` | Generates OpenAI embeddings for `Chunk` nodes; creates `SEMANTICALLY_SIMILAR` edges (cosine > 0.85, cross-document only) |
| `216_validate_graph` | Validates node counts, relationships, and index health |

### 6. Launch the Streamlit app

```bash
streamlit run app.py
```

## Folder Structure

```
graphrag-finserv-compliance/
├── app.py                      # Streamlit UI — single-file, mirrors notebook 316
├── notebooks/                  # Ordered pipeline + prototype notebooks
├── src/
│   ├── graph/                  # Neo4j connection and parameterised Cypher helpers
│   ├── agent/                  # Orchestrator, ComplianceAgent, InvestigationAgent
│   ├── mcp/                    # Tool implementations, schema, FastMCP server
│   ├── retriever/              # GraphRAG: NL-to-Cypher + context formatting
│   └── document/               # PDF extraction, Claude streaming, config utils
├── data/
│   ├── layer_1/                # Entity graph CSVs (borrowers, loans, accounts)
│   ├── layer_2/                # Regulatory graph CSVs + source PDFs
│   └── synthetic/              # Legacy synthetic stubs (safe to commit)
├── tests/                      # Unit tests (fully mocked — no credentials needed)
└── .env.example                # Environment variable template
```

## Running Tests

```bash
pytest tests/ -v
```

All tests are fully mocked — no Neo4j or Anthropic credentials required.

## Key Cypher Patterns

- Never use `type(r)` with variable-length paths — collect with `[rel IN r | type(rel)]` instead
- All queries use parameterised syntax (`$param`) — never string interpolation for user data
- Vector search: `CALL db.index.vector.queryNodes('chunk_embeddings', $k, $emb) YIELD node AS c, score`
- Assessment ID format: `ASSESS-{entity_id}-{regulation_id}-{YYYY-MM-DD}`
