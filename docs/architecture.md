# Architecture: GraphRAG Financial Services Loan Compliance Agent

## Three-Layer Neo4j Graph Model

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            LAYER 1 — ENTITY LAYER                               │
│                                                                                 │
│  ┌───────────────────┐  SUBMITTED_BY  ┌──────────────────────┐                 │
│  │ LoanApplication   │──────────────▶│ Borrower              │                 │
│  │ :ResidentialSecured│               │ :Individual           │                 │
│  │ :CommercialSecured │  GUARANTEED_BY│ :Corporate            │                 │
│  │                   │──────────────▶│                       │                 │
│  │ application_id    │               │ borrower_id           │                 │
│  │ loan_amount       │               │ name                  │                 │
│  │ loan_purpose      │               │ borrower_type         │────────────────▶│
│  └─────────┬─────────┘               └───────────┬───────────┘  RESIDES_IN /  │
│            │ BACKED_BY                            │              REGISTERED_IN  │
│            ▼                                      ▼                             │
│  ┌─────────────────┐               ┌──────────────────────┐                    │
│  │ Collateral      │               │ Jurisdiction         │◀───────────────────┤
│  │ property_type   │               │ jurisdiction_id      │  (bridge to L2)    │
│  │ value           │               │ name, country        │                    │
│  └─────────────────┘               └──────────────────────┘                    │
│                                                                                 │
│  Also: BankAccount, Transaction, Address, Officer, Industry                     │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                         APPLIES_TO_JURISDICTION
                                        │
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          LAYER 2 — REGULATORY LAYER                             │
│                                                                                 │
│  ┌────────────┐  HAS_SECTION       ┌──────────┐  HAS_REQUIREMENT               │
│  │ Regulation │───────────────────▶│ Section  │──────────────────────▶         │
│  │            │                    │          │                                 │
│  │ regulation_id                   │ section_id  ┌─────────────────┐           │
│  │ name       │                    │ title    │──▶│ Requirement     │           │
│  │ issuing_body                    │ text     │   │ requirement_id  │           │
│  └────────────┘                    └────┬─────┘   │ description     │           │
│                                         │          │ severity        │           │
│                              HAS_CHUNK  │          └────────┬────────┘           │
│                                         ▼   NEXT_CHUNK      │ DEFINES_LIMIT      │
│                                    ┌─────────┐ ──────────▶  ▼                   │
│                                    │  Chunk  │         ┌───────────┐            │
│                                    │         │         │ Threshold │            │
│                                    │ chunk_id│         │ metric    │            │
│                                    │ text    │         │ value     │            │
│                                    │ embedding         │ operator  │            │
│                                    └────┬────┘         └───────────┘            │
│                                         │ SEMANTICALLY_SIMILAR (cross-document) │
│                                         └──────────────────────────────▶ Chunk  │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                    LAYER 3 — RUNTIME ASSESSMENT LAYER (future)                  │
│                                                                                 │
│   ┌────────────────────┐  REFERENCES  ┌─────────────────┐                      │
│   │ ComplianceAssessment│────────────▶│  Requirement    │  (from Layer 2)       │
│   │ assessment_id      │              └─────────────────┘                      │
│   │ outcome, score     │                                                        │
│   └────────────────────┘                                                        │
│                                                                                 │
│   ┌─────────────────┐  FLAGGED_ON  ┌──────────────────┐                        │
│   │ ComplianceFlag  │─────────────▶│ LoanApplication  │  (from Layer 1)        │
│   │ flag_id, reason │              └──────────────────┘                        │
│   │ severity, status│                                                           │
│   └─────────────────┘                                                           │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Bridge node:** `(:Jurisdiction {jurisdiction_id: 'JUR-AU-FED'})` connects both layers. Borrowers link to it via `RESIDES_IN` or `REGISTERED_IN`; regulations link to it via `APPLIES_TO_JURISDICTION`.

---

## Layer 2 Regulatory Pipeline

Runs once per document set; re-run when adding new regulatory documents.

```
  PDF files + document_config.yaml
           │
           ▼
  ┌─────────────────────────────────┐
  │  211_extract_document_structure │  Claude extracts sections, requirements,
  │                                 │  thresholds from PDF text (temperature=0)
  │                                 │  close_page_gaps() absorbs unclaimed pages
  └──────────────┬──────────────────┘  into adjacent sections post-extraction
                 │  intermediate/{rid}_sections.csv
                 │  intermediate/{rid}_requirements.csv
                 │  intermediate/{rid}_thresholds.csv
                 │  intermediate/{rid}_references.csv
                 ▼
  ┌─────────────────────────────────┐
  │  212_merge_and_resolve_references│  Merges per-doc intermediates; Claude
  │                                 │  resolves cross-doc references to known
  │                                 │  section_ids (temperature=0)
  └──────────────┬──────────────────┘
                 │  sections.csv, requirements.csv,
                 │  thresholds.csv, cross_references.csv
                 ▼
  ┌─────────────────────────────────┐
  │  213_chunk_documents            │  Splits section text into ~300-token
  │                                 │  chunks. Raises RuntimeError if any
  │                                 │  page is uncovered (fix in 211 first)
  └──────────────┬──────────────────┘
                 │  chunks.csv
                 ▼
  ┌─────────────────────────────────┐
  │  214_ingest_neo4j               │  Loads all Layer 2 nodes + relationships
  │                                 │  into Neo4j. Re-runnable (clears first)
  └──────────────┬──────────────────┘
                 ▼
  ┌─────────────────────────────────┐
  │  215_generate_embeddings        │  OpenAI text-embedding-3-small (1536 dims)
  │                                 │  written to Chunk.embedding; creates
  │                                 │  SEMANTICALLY_SIMILAR edges (cosine > 0.85,
  │                                 │  cross-document only)
  └──────────────┬──────────────────┘
                 ▼
  ┌─────────────────────────────────┐
  │  216_validate_graph             │  Pass/fail validation of node counts,
  │                                 │  relationships, and index health
  └─────────────────────────────────┘
```

---

## Reasoning Patterns

### 1. Tool-Use Agent (primary)

```
User Query
    │
    ▼
ComplianceAgent.run()
    │
    │  messages = [{role: user, content: query}]
    │
    ▼
┌─────────────────────────────────────────────────┐
│                  AGENTIC LOOP                   │
│              (max 10 iterations)                │
│                                                 │
│  Claude API (temperature=0) ◀── messages+tools  │
│        │                                        │
│        ├── end_turn ──────────────────────────────▶ Return text
│        │                                        │
│        └── tool_use                             │
│                │                                │
│                ▼                                │
│          execute_tool()                         │
│                │                                │
│                ▼                                │
│          Neo4j AuraDB (Cypher)                  │
│                │                                │
│                ▼                                │
│    Inject tool_result → messages → loop ──────▶ │
└─────────────────────────────────────────────────┘
```

### 2. GraphRAG Retriever (supplementary)

```
Natural Language Query
    │
    ▼
Claude NL-to-Cypher (temperature=0, GRAPH_SCHEMA_HINT in system prompt)
    │
    ▼
Cypher Query
    │
    ▼
Neo4j AuraDB
    │
    ▼
format_context_for_claude()
    │
    ▼
Context String ──▶ inject into downstream Claude prompt
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Tool-use agent as primary pattern | Claude decides which graph queries to issue based on the question; avoids fixed retrieval strategies |
| Three-layer graph + bridge node | Clean separation of entities / regulations / assessments; `Jurisdiction` joins both without duplicating data |
| `close_page_gaps()` in notebook 211 | Claude extracts section content correctly but leaves cover/ToC pages unclaimed; post-process absorbs them deterministically to prevent data loss in chunking |
| `RuntimeError` on page gaps in notebook 213 | Forces root-cause fix in notebook 211 rather than silently misattributing chunks to wrong sections |
| `temperature=0` on all Claude calls | Deterministic outputs for structured extraction, NL-to-Cypher, and cross-reference resolution |
| Streaming for large `max_tokens` | Anthropic SDK requires streaming for calls that could exceed 10 min; `call_claude_stream_json()` centralises this with fail-fast truncation detection |
| ~300-token chunks | Fits one complete numbered requirement with sub-clauses — the atomic unit of regulatory compliance assessment |
| `SEMANTICALLY_SIMILAR` edges (cross-document only) | Surfaces thematically related requirements across different APRA standards without polluting within-document chunk chains |
| Parameterised Cypher | Prevents Cypher injection; follows Neo4j best practices |

---

## TODO

- [ ] Build Layer 3 runtime assessment pipeline (flag generation, scoring)
- [ ] Add structured Pydantic output models for agent final responses
- [ ] Define escalation and alerting logic for HIGH severity compliance flags
- [ ] Add `SHOW INDEXES` health check to notebook 216 validation
