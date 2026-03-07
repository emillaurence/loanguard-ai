# GraphRAG Financial Services Loan Compliance Agent

An agentic GraphRAG application for Australian financial services compliance, powered by **Anthropic Claude**, **Neo4j AuraDB**, and **OpenAI embeddings**.

## Overview

This project implements a compliance reasoning agent that:
- Stores financial entities, APRA regulatory obligations, and runtime compliance assessments in a three-layer Neo4j knowledge graph
- Extracts and chunks APRA prudential standards (APS-220, APS-112, APG-223) from source PDFs into a searchable regulatory graph
- Uses Claude's tool-use API to dynamically query the graph in response to natural-language compliance questions
- Applies a GraphRAG pattern to retrieve semantically similar regulatory chunks via vector search

## Graph Layer Architecture

| Layer | Description | Key Nodes |
|---|---|---|
| **1 — Entity** | ADI borrowers, loan applications, accounts, transactions | `Borrower`, `LoanApplication`, `BankAccount`, `Transaction`, `Collateral`, `Jurisdiction` |
| **2 — Regulatory** | APRA prudential standards, sections, requirements, thresholds, and embedded chunks | `Regulation`, `Section`, `Requirement`, `Threshold`, `Chunk` |
| **3 — Runtime Assessment** | Live compliance flags and assessments *(future)* | `ComplianceAssessment`, `ComplianceFlag` |

The `Jurisdiction` node bridges Layers 1 and 2: borrowers link to jurisdictions, and regulations declare which jurisdictions they apply to.

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
| `215_generate_embeddings` | Generates OpenAI embeddings for `Chunk` nodes and creates `SIMILAR_TO` edges |
| `216_validate_graph` | Validates node counts, relationships, and index health |

### 6. Run the compliance agent

```bash
# Run: notebooks/910_compliance_agent_prototype.ipynb
```

## Folder Structure

```
graphrag-finserv-compliance/
├── notebooks/                  # Ordered pipeline notebooks + prototype
├── src/
│   ├── graph/                  # Neo4j connection and Cypher query helpers
│   ├── agent/                  # Claude tool definitions and agentic loop
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
