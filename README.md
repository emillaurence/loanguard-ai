# GraphRAG Finserv Compliance Agent

An agentic GraphRAG application for financial services compliance, powered by **Anthropic Claude** and **Neo4j AuraDB**.

## Overview

This project implements a compliance reasoning agent that:
- Stores financial entities, APRA regulatory obligations, and compliance assessments in a Neo4j knowledge graph
- Uses Claude's tool-use API to dynamically query the graph in response to compliance questions
- Applies a GraphRAG pattern to retrieve relevant graph context and reason over it

## Graph Layer Architecture

| Layer | Description | Example Nodes |
|---|---|---|
| **Entity** | Customers, loan accounts, transactions | `Customer`, `LoanAccount`, `Transaction` |
| **Regulatory** | APRA prudential standards and obligations | `Regulation`, `Obligation` |
| **Runtime Assessment** | Live compliance flags and assessments | `ComplianceAssessment`, `ComplianceFlag` |

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
# Edit .env and fill in your Anthropic and Neo4j AuraDB credentials
```

### 4. Run the prototype notebook

```bash
jupyter notebook notebooks/01_compliance_agent_prototype.ipynb
```

## Folder Structure

```
graphrag-finserv-compliance/
├── notebooks/          # Jupyter prototype (thin orchestration layer)
├── src/
│   ├── graph/          # Neo4j connection and Cypher query helpers
│   ├── agent/          # Claude tool definitions and agentic loop
│   └── retriever/      # GraphRAG: NL-to-Cypher + context formatting
├── data/
│   └── synthetic/      # Sample loan and APRA regulation stubs
├── tests/              # Unit tests (mocked Neo4j and Claude)
└── docs/               # Architecture diagrams and design notes
```

## Running Tests

```bash
pytest tests/ -v
```
