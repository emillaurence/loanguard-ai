# LoanGuard AI

**Intelligent loan compliance monitoring and risk investigation AI Agents powered by Neo4j and Claude**

An advanced agentic GraphRAG application for Australian financial services compliance, built with **Anthropic Claude**, **Neo4j AuraDB**, and **OpenAI embeddings**.

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

## LoanGuard AI - Streamlit Application

Launch the interactive compliance monitoring dashboard:

```bash
streamlit run app.py
```

### Key Features:
- **🎯 Verdict Dashboard** — Full-width coloured cards with compliance status, confidence scores, and visual progress bars
- **📊 Risk Findings** — Severity-sorted findings with colour-coded alerts (HIGH/MEDIUM/LOW/INFO) and detailed descriptions
- **📈 Interactive Charts** — Plotly-powered severity maps and evidence graphs with hover details and click interactions
- **🔍 Evidence Tracing** — Two-column layout showing cited regulatory sections and semantic similarity scores
- **🤖 Agent Pipeline** — Visual routing display showing orchestrator → compliance agent → investigation agent flow
- **📋 Action Items** — Numbered recommendation cards with clear next steps for compliance officers

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

### 6. Launch LoanGuard AI

```bash
streamlit run app.py
```

The application will be available at `http://localhost:8501` with the interactive compliance dashboard.

## Folder Structure

```
graphrag-finserv-compliance/
├── app.py                      # LoanGuard AI Streamlit application
├── notebooks/                  # Data pipeline and agent development notebooks
├── src/
│   ├── graph/                  # Neo4j connection and optimised Cypher queries
│   ├── agent/                  # Multi-agent system (Orchestrator, Compliance, Investigation)
│   ├── mcp/                    # MCP tool implementations and FastMCP server
│   ├── retriever/              # GraphRAG retrieval and NL-to-Cypher conversion
│   └── document/               # PDF processing and Claude streaming utilities
├── data/
│   ├── layer_1/                # Financial entity data (borrowers, loans, transactions)
│   ├── layer_2/                # APRA regulatory documents and processed data
│   └── synthetic/              # Sample data for testing (safe to commit)
├── tests/                      # Comprehensive test suite (fully mocked)
└── .env.example                # Environment configuration template
```

## Running Tests

```bash
pytest tests/ -v
```

All tests are fully mocked — no Neo4j or Anthropic credentials required.

## Architecture Highlights

### 🔧 Optimised Codebase
- **Cleaned and streamlined**: Removed 200+ lines of unused code and 8 unused functions
- **Performance optimised**: Reduced import overhead and improved maintainability
- **Focused functionality**: Only actively used functions remain in the codebase

### 🔍 Key Cypher Patterns
- **Safe queries**: Parameterised syntax (`$param`) prevents injection attacks
- **Efficient paths**: Use `[rel IN r | type(rel)]` for variable-length relationship traversal
- **Vector search**: `CALL db.index.vector.queryNodes('chunk_embeddings', $k, $emb)`
- **Consistent IDs**: Assessment format `ASSESS-{entity_id}-{regulation_id}-{YYYY-MM-DD}`

### 🚀 Performance Features
- **Prompt caching**: Claude system prompts cached for faster responses
- **Rate limiting**: Exponential backoff with retry-after header handling
- **Context management**: Tool results truncated to 3000 chars, history windowed to last 4 message pairs
- **Streaming support**: Large Claude calls use streaming API for better UX
