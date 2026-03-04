# Architecture: GraphRAG Finserv Compliance Agent

## Neo4j Graph Layer Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          LAYER 1 — ENTITY LAYER                             │
│                                                                             │
│   ┌──────────┐   HOLDS    ┌─────────────┐  HAS_TRANSACTION  ┌─────────────┐│
│   │ Customer │──────────▶│ LoanAccount │──────────────────▶│ Transaction ││
│   │          │            │             │                    │             ││
│   │customer_id│           │ account_id  │                    │transaction_id│
│   │name      │            │ product_type│                    │amount       ││
│   │kyc_status│            │ balance     │                    │type         ││
│   │risk_cat  │            │ status      │                    │counterparty ││
│   └──────────┘            │ risk_rating │                    │suspicious   ││
│                           └──────┬──────┘                    └─────────────┘│
└──────────────────────────────────│──────────────────────────────────────────┘
                                   │
                                   │ HAS_ASSESSMENT
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      LAYER 3 — RUNTIME ASSESSMENT LAYER                     │
│                                                                             │
│            ┌────────────────────┐    ┌─────────────────┐                   │
│            │ ComplianceAssessment│    │  ComplianceFlag │                   │
│            │                    │    │                 │                   │
│            │ assessment_id      │    │ flag_id         │                   │
│            │ outcome            │    │ reason          │                   │
│            │ score              │    │ severity        │                   │
│            │ notes              │    │ status          │                   │
│            └────────┬───────────┘    └────────┬────────┘                   │
│                     │ REFERENCES              │ FLAGGED_ON                  │
└─────────────────────│─────────────────────────│────────────────────────────┘
                      ▼                         │ (back to LoanAccount)
┌─────────────────────────────────────────────────────────────────────────────┐
│                        LAYER 2 — REGULATORY LAYER                           │
│                                                                             │
│   ┌────────────┐   CONTAINS   ┌────────────┐                               │
│   │ Regulation │─────────────▶│ Obligation │                               │
│   │            │              │            │                               │
│   │ standard_id│              │obligation_id│                              │
│   │ title      │              │description │                               │
│   │ eff_date   │              │applies_to  │                               │
│   └────────────┘              │severity    │                               │
│                               └────────────┘                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Agent Flow

```
User Query
    │
    ▼
┌──────────────────────────────────────────────────────┐
│                   ComplianceAgent.run()               │
│                                                      │
│  messages = [{"role": "user", "content": query}]     │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │              AGENTIC LOOP                      │  │
│  │                                                │  │
│  │  Claude API ◀──── messages + tools             │  │
│  │       │                                        │  │
│  │       ├── stop_reason == "end_turn"  ──────────┼──┼──▶ Return text
│  │       │                                        │  │
│  │       └── stop_reason == "tool_use"            │  │
│  │               │                                │  │
│  │               ▼                                │  │
│  │         execute_tool()                         │  │
│  │               │                                │  │
│  │               ▼                                │  │
│  │         Neo4j AuraDB                           │  │
│  │         (run_query)                            │  │
│  │               │                                │  │
│  │               ▼                                │  │
│  │    Inject tool_result into messages            │  │
│  │    Loop ──────────────────────────────────▶    │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

---

## GraphRAG Retriever Flow

```
Natural Language Query
    │
    ▼
Claude (NL-to-Cypher)
    │  GRAPH_SCHEMA_HINT provided as system prompt
    │
    ▼
Cypher Query String
    │
    ▼
Neo4j AuraDB (run_query)
    │
    ▼
List[dict] results
    │
    ▼
format_context_for_claude()
    │
    ▼
Context String  ──▶  (inject into downstream Claude prompt)
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Tool-use agent (not pure RAG) | Allows Claude to decide which graph queries to run based on the question, rather than a fixed retrieval strategy |
| Three-layer graph model | Separates concerns: entities (what exists), regulations (what the rules are), assessments (how entities measure up) |
| Parameterised Cypher queries | Prevents Cypher injection; follows Neo4j best practices |
| JSON-encoded tool results | Simple, schema-agnostic format that Claude can reason over natively |
| `MAX_ITERATIONS` guard | Prevents runaway tool-use loops; surface tunable constant |
| `.env` + `python-dotenv` | Keeps credentials out of code; consistent with 12-factor app principles |

---

## TODO — Design Decisions to Finalise

- [ ] Define full node labels and relationship types in the AuraDB schema
- [ ] Decide on graph indexing strategy (account_id, customer_id, obligation_id)
- [ ] Add a data ingestion pipeline (`scripts/seed_graph.py`) to load `data/synthetic/`
- [ ] Decide on token budget management for large graph results (chunking strategy)
- [ ] Add structured output (Pydantic models) for agent final responses
- [ ] Evaluate adding a vector similarity layer (Neo4j vector index) for document chunks
- [ ] Define escalation and alerting logic for HIGH severity compliance flags
