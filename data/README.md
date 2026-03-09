# LoanGuard AI - Data Directory

This directory contains all data used to power the LoanGuard AI knowledge graph, organised by the three-layer architecture that enables intelligent compliance monitoring and risk investigation.

## Subdirectories

### `layer_1/` - Financial Entity Data

Core financial entities and relationships that form the foundation of compliance monitoring.

| Subfolder | Contents | Purpose |
|---|---|---|
| `entities/` | Node CSVs: borrowers, loan applications, bank accounts, collateral, jurisdictions, industries, officers | Primary entities for compliance analysis |
| `links/` | Relationship CSVs: submitted_by, backed_by, resides_in, registered_in, owns, guarantees | Entity connections and ownership structures |

**Loading**: Processed by `notebooks/111_structured_data_loader.ipynb` into Neo4j Layer 1 nodes and relationships.

**Key Features**:
- 🏦 **Borrower profiles** with risk ratings and jurisdictional information
- 💰 **Loan applications** with LVR, amounts, and collateral backing
- 🔗 **Complex relationships** including ownership chains and guarantee structures
- 🌍 **Jurisdictional mapping** linking entities to regulatory frameworks

### `layer_2/` - APRA Regulatory Framework

Comprehensive APRA prudential standards processed into a queryable regulatory knowledge graph.

| File / Folder | Contents | Purpose |
|---|---|---|
| `regulatory_documents/` | Source PDFs (APS-220, APS-112, APG-223) | Original APRA regulatory documents |
| `document_config.yaml` | Pipeline configuration for each regulatory document | Controls extraction and processing parameters |
| `regulations.csv` | Regulation metadata and hierarchy | Top-level regulatory framework structure |
| `sections.csv` | Document sections with full text and page references | Structured regulatory content |
| `requirements.csv` | Extracted compliance obligations per section | Specific compliance requirements |
| `thresholds.csv` | Quantitative limits and triggers | Measurable compliance thresholds |
| `chunks.csv` | ~300-token semantic chunks for vector search | AI-powered regulatory content retrieval |
| `cross_references.csv` | Resolved inter-document section links | Connected regulatory knowledge |
| `intermediate/` | Per-document Claude extraction outputs | Processing pipeline artifacts |

**Processing Pipeline**: `211_extract` → `212_merge` → `213_chunk` → `214_ingest` → `215_embeddings` → `216_validate`

**Key Features**:
- 📚 **Automated extraction** using Claude AI for accurate regulatory parsing
- 🔍 **Vector search** capabilities with OpenAI embeddings for semantic retrieval
- 🔗 **Cross-reference resolution** linking related regulatory sections
- ⚡ **Extensible design** - add new documents via `document_config.yaml` only

### `layer_3/` - Runtime Assessments

AI-generated compliance assessments and investigation results created by LoanGuard AI agents.

**Note**: This layer is created dynamically by the application and stored directly in Neo4j. No CSV files are maintained for Layer 3 data.

**Contents**:
- 🤖 **Assessment nodes** with compliance verdicts and confidence scores
- 🔍 **Finding records** with severity levels and detailed descriptions  
- 📝 **Reasoning steps** with cited regulatory sections and evidence
- 🔗 **Evidence trails** linking assessments back to source regulations

### `synthetic/` - Development Data

Safe-to-commit sample data used during development and testing.

| File | Description | Usage |
|---|---|---|
| `loans.json` | Synthetic loan accounts and transactions | Development testing |
| `regulations.json` | Sample APRA obligation stubs | Initial prototyping |

### `raw/` - Sensitive Data (gitignored)

**⚠️ IMPORTANT**: Real or sensitive data directory. **Never commit files in this folder.**

The `.gitignore` automatically excludes `data/raw/` to prevent accidental commits of sensitive information.

## 🔄 Data Flow Architecture

```
Layer 1 (Entities) ←→ Layer 2 (Regulations) → Layer 3 (Assessments)
        ↓                      ↓                      ↓
   Financial Data      APRA Standards        AI Analysis
   CSV → Neo4j        PDF → Claude → Neo4j   Runtime → Neo4j
```

This three-layer architecture enables LoanGuard AI to perform intelligent compliance monitoring by connecting financial entities with regulatory requirements and generating actionable compliance assessments.
