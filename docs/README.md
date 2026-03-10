# LoanGuard AI — Documentation

This directory contains the full technical documentation for LoanGuard AI.

---

## Start here

| If you want to… | Read |
|---|---|
| Set up the project from scratch | [Getting Started](getting-started.md) |
| Understand the overall system design | [Architecture](architecture.md) |
| Understand the Neo4j graph schema | [Data Model](data-model.md) |
| Understand APRA thresholds and verdict logic | [Compliance System](compliance.md) |
| Understand how the agents work | [Agent Pipeline](agents.md) |
| Look up a specific tool's parameters | [MCP Tools Reference](tools.md) |
| Run or extend a notebook | [Notebooks Reference](notebooks.md) |
| Add a document, pattern, or tool | [Developer Guide](development.md) |

---

## All documents

### [Getting Started](getting-started.md)
Complete setup walkthrough from a fresh clone to a running application. Covers prerequisites, installation, environment variables, loading Layer 1 and Layer 2 data, launching the Streamlit app, and troubleshooting common issues.

**Sections:** Prerequisites · Installation · Environment Variables · Verify Your Environment · Load Layer 1 · Load Layer 2 · Launch the Application · Troubleshooting

---

### [Architecture](architecture.md)
End-to-end technical architecture: the three-layer Neo4j graph model, the Layer 2 regulatory pipeline, the multi-agent pipeline, the MCP tool layer, the threshold type and verdict system, security design, schema types, performance design, and Cypher patterns reference.

**Sections:** Three-Layer Graph Model · Layer 2 Pipeline · Multi-Agent Architecture · MCP Tool Layer · Threshold Type System · Anomaly Detection Patterns · Security Design · Schema Types · Performance Design · Cypher Patterns Reference

---

### [Data Model](data-model.md)
Complete Neo4j graph schema reference. Documents all node labels, properties, and entity counts for all three layers, all relationship types with source/target and key properties, the Jurisdiction bridge design, and Cypher best practices.

**Sections:** Three-Layer Architecture · Layer 1 Entity Nodes · Layer 1 Relationships · Layer 2 Regulatory Nodes · Layer 2 Relationships · Layer 3 Assessment Nodes · Layer 3 Relationships · The Jurisdiction Bridge · Cypher Best Practices

---

### [Compliance System](compliance.md)
APRA compliance logic reference. Documents the three covered regulations, the four threshold types (minimum, maximum, trigger, informational), all five APG-223 thresholds with their current IDs, conditional threshold evaluation, verdict derivation, and the `evaluate_thresholds` algorithm.

**Sections:** Covered Regulations · Threshold Type System · APG-223 Thresholds · Conditional Threshold Evaluation · Verdict Derivation · evaluate_thresholds Algorithm · Compliance Distribution · Common Breach Patterns

---

### [Agent Pipeline](agents.md)
Deep-dive into the three agent components. Covers the Orchestrator's routing and synthesis logic, the ComplianceAgent's agentic loop and evidence tracking, the InvestigationAgent's graph traversal workflow, shared utilities, prompt injection defence, and rate limiting.

**Sections:** Overview · Orchestrator · ComplianceAgent · InvestigationAgent · Shared Utilities · Security · Rate Limiting

---

### [MCP Tools Reference](tools.md)
Complete reference for all eight tools available to agents. Covers tool architecture, parameters, return shapes, usage notes, and write-keyword security for each tool.

**Sections:** Tool Architecture · traverse_compliance_path · retrieve_regulatory_chunks · detect_graph_anomalies · persist_assessment · trace_evidence · evaluate_thresholds · read-neo4j-cypher · write-neo4j-cypher · Security

---

### [Notebooks Reference](notebooks.md)
Reference for all 15 notebooks across three series. Documents each notebook's purpose, inputs, outputs, expected run times, failure guidance, and the dependency order between series.

**Sections:** Overview · Series 1xx (entity loading) · Series 2xx (Layer 2 pipeline) · Series 3xx (agent development) · Running Order · Common Issues

---

### [Developer Guide](development.md)
How to extend the system: adding new APRA documents, anomaly patterns, and MCP tools; modifying agent behaviour; code conventions; and the key design decisions behind the architecture.

**Sections:** Project Structure · Running Tests · Adding a New APRA Document · Adding a New Anomaly Pattern · Adding a New MCP Tool · Modifying Agent Behaviour · Code Conventions · Key Design Decisions

---

## Document map

```
Getting Started          ← start here for setup
        │
        ▼
Architecture             ← system-wide overview
   ├── Data Model         ← Neo4j schema reference
   ├── Compliance System  ← threshold and verdict logic
   ├── Agent Pipeline     ← Orchestrator, ComplianceAgent, InvestigationAgent
   ├── MCP Tools          ← tool parameters and return shapes
   └── Notebooks          ← running and extending the pipeline
        │
        ▼
Developer Guide          ← extending and contributing
```
