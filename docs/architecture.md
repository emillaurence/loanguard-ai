# LoanGuard AI - System Architecture

**Intelligent loan compliance monitoring and risk investigation powered by multi-agent AI**

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
│                      LAYER 3 — AI ASSESSMENT LAYER (active)                    │
│                                                                                 │
│   ┌────────────────────┐  ASSESSED_UNDER  ┌─────────────────┐                 │
│   │ Assessment         │─────────────────▶│  Regulation     │  (from Layer 2)  │
│   │ assessment_id      │                  └─────────────────┘                 │
│   │ verdict, confidence│                                                        │
│   │ agent, created_at  │   HAS_FINDING    ┌─────────────────┐                 │
│   └─────────┬──────────┘─────────────────▶│ Finding         │                 │
│             │                             │ severity, type  │                 │
│             │ HAS_STEP                    │ description     │                 │
│             ▼                             └─────────────────┘                 │
│   ┌─────────────────────┐  CITES_SECTION  ┌─────────────────┐                 │
│   │ ReasoningStep       │─────────────────▶│ Section         │ (from Layer 2)  │
│   │ step_number         │                  └─────────────────┘                 │
│   │ description         │  CITES_CHUNK     ┌─────────────────┐                 │
│   │ cypher_used         │─────────────────▶│ Chunk           │ (from Layer 2)  │
│   └─────────────────────┘                  └─────────────────┘                 │
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

## Multi-Agent Architecture

LoanGuard AI employs a sophisticated multi-agent system with specialised AI agents working in concert:

### 1. Orchestrator Agent (Router & Synthesizer)

```
User Query → Intent Analysis → Agent Routing → Result Synthesis
     │              │                │              │
     ▼              ▼                ▼              ▼
Claude Analysis  Entity/Regulation  Parallel      Combined
(temperature=0)  Extraction        Execution     Response
```

### 2. Compliance Agent (Primary Assessment)

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

### 3. Investigation Agent (Risk Analysis)

```
Entity Focus → Graph Traversal → Anomaly Detection → Risk Signals
     │              │                   │                │
     ▼              ▼                   ▼                ▼
Target Entity   Relationship        Pattern          Structured
Identification  Mapping            Matching         Findings
```

### 4. GraphRAG Retriever (Semantic Search)

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

## Key Design Decisions & Optimizations

| Decision | Rationale | Impact |
|---|---|---|
| **Multi-agent orchestration** | Specialised agents for compliance vs investigation tasks; parallel execution where possible | Better accuracy, faster responses |
| **Cleaned codebase** | Removed 200+ lines of unused code and 8 unused functions | Improved performance, easier maintenance |
| **Three-layer graph + bridge** | Clean separation: entities / regulations / assessments; `Jurisdiction` connects layers | Scalable architecture, clear data flow |
| **Prompt caching** | Cache Claude system prompts with `cache_control: ephemeral` | Reduced latency, lower API costs |
| **Tool-use agents** | Claude dynamically chooses graph queries based on context | Flexible, intelligent query selection |
| **Temperature=0** | Deterministic outputs for extraction, NL-to-Cypher, cross-references | Consistent, reliable results |
| **Streaming for large calls** | `call_claude_stream_json()` for operations >10min | Better UX, fail-fast error handling |
| **~300-token chunks** | Atomic regulatory requirement units with sub-clauses | Optimal semantic retrieval granularity |
| **Cross-document similarity** | `SEMANTICALLY_SIMILAR` edges only between different regulations | Rich cross-references without noise |
| **Parameterised Cypher** | All queries use `$param` syntax, never string interpolation | Security, performance, best practices |

---

## Performance & Scalability Features

### 🚀 Optimization Highlights
- **Codebase cleanup**: Removed 8 unused functions (~200 lines) for better performance
- **Import optimization**: Reduced module loading overhead
- **Prompt caching**: System prompts cached for faster agent responses
- **Context management**: Tool results truncated to 3000 chars, history windowed
- **Rate limiting**: Exponential backoff with retry-after header handling

### 📊 Monitoring & Observability
- **Structured logging**: Comprehensive logging across all components
- **Assessment persistence**: All compliance decisions stored with full reasoning chains
- **Evidence tracing**: Complete audit trail from findings back to source regulations
- **Graph validation**: Automated health checks for data integrity

### 🔧 Extensibility
- **Document config**: Add new APRA regulations via YAML configuration only
- **Anomaly patterns**: Registry-based system for adding new detection rules
- **Agent tools**: Modular MCP tool architecture for easy extension
- **Multi-model support**: Architecture supports different Claude models per agent

## Future Enhancements

- [ ] **Real-time monitoring**: Stream processing for continuous compliance monitoring  
- [ ] **Advanced analytics**: Trend analysis and predictive compliance scoring
- [ ] **Integration APIs**: REST/GraphQL APIs for external system integration
- [ ] **Audit reporting**: Automated compliance report generation
- [ ] **Multi-jurisdiction**: Extend beyond APRA to other regulatory frameworks
