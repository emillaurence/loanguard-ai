# Data Directory

This folder holds all data used to seed and operate the Neo4j knowledge graph, organised by layer.

## Subdirectories

### `layer_1/`

Entity graph data for Layer 1 — ADI borrowers, loan applications, accounts, and related entities.

| Subfolder | Contents |
|---|---|
| `entities/` | Node CSVs: borrowers, loan applications, bank accounts, collateral, jurisdictions, etc. |
| `links/` | Relationship CSVs: submitted_by, backed_by, resides_in, registered_in, etc. |

Loaded into Neo4j by `notebooks/111_structured_data_loader.ipynb`.

### `layer_2/`

Regulatory graph data for Layer 2 — APRA prudential standards extracted from source PDFs.

| File / Folder | Contents |
|---|---|
| `regulatory_documents/` | Source PDFs (APS-220, APS-112, APG-223) |
| `document_config.yaml` | Pipeline config — one entry per regulatory document |
| `regulations.csv` | One row per regulation |
| `sections.csv` | Document sections with verbatim text and page ranges |
| `requirements.csv` | Extracted obligations per section |
| `thresholds.csv` | Quantitative thresholds per requirement |
| `chunks.csv` | ~300-token text chunks for vector search |
| `cross_references.csv` | Resolved cross-document section references |
| `intermediate/` | Per-document extraction outputs from notebook 211 (before merging) |

Loaded into Neo4j by the Layer 2 pipeline: notebooks 211 → 212 → 213 → 214 → 215 → 216.

To add a new regulatory document, append an entry to `document_config.yaml` — no code changes needed.

### `synthetic/`

Legacy synthetic stubs used during initial development. Safe to commit.

| File | Description |
|---|---|
| `loans.json` | Synthetic loan accounts and transactions |
| `regulations.json` | Sample APRA obligation stubs |

### `raw/` (gitignored)

Real or sensitive data. **Never commit files in this folder.**
The `.gitignore` excludes `data/raw/` automatically.
