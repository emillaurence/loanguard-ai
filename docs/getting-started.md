# Getting Started with LoanGuard AI

This guide walks you from a fresh clone to a running application. Complete all steps in order — the application cannot run until both Layer 1 and Layer 2 data are loaded into Neo4j.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.11+** | Earlier versions are untested. Check with `python --version`. |
| **Neo4j AuraDB** | Free tier works. Create a free instance at [console.neo4j.io](https://console.neo4j.io). AuraDB Free gives you one graph with 200k nodes / 400k relationships. |
| **Anthropic API key** | Required for all agent and extraction calls. Get one at [console.anthropic.com](https://console.anthropic.com). |
| **OpenAI API key** | Required for embedding generation (notebook 215) and semantic search at runtime. Get one at [platform.openai.com](https://platform.openai.com). |
| **Jupyter** | Included in `requirements.txt`. Used to run the data loading and validation notebooks. |

---

## Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd loanguard-ai

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## Environment Variables

```bash
cp .env.example .env
```

Open `.env` and fill in all five values:

| Variable | Description | How to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API key for all agent and extraction calls | [console.anthropic.com](https://console.anthropic.com) → API Keys |
| `OPENAI_API_KEY` | OpenAI key for `text-embedding-3-small` embeddings | [platform.openai.com](https://platform.openai.com) → API Keys |
| `NEO4J_URI` | AuraDB connection URI | AuraDB console → Connect → Connection URI. Format: `neo4j+s://xxxxxxxx.databases.neo4j.io` |
| `NEO4J_USERNAME` | Database username | AuraDB console — default is `neo4j` |
| `NEO4J_PASSWORD` | Database password | Set when creating the AuraDB instance (or reset in the console) |

**Important:** Never commit `.env` to version control. It is listed in `.gitignore`.

---

## Verify Your Environment

Run a quick connectivity check before loading any data:

```python
# Run this in a Python shell or notebook cell
import os
from dotenv import load_dotenv
load_dotenv()

# Check all five variables are present
required = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"]
missing = [v for v in required if not os.getenv(v)]
if missing:
    print(f"Missing: {missing}")
else:
    print("All environment variables set.")

# Test Neo4j connection
from src.graph.connection import Neo4jConnection
conn = Neo4jConnection()
conn.connect()
result = conn.run_query("RETURN 1 AS ok")
print("Neo4j connected:", result)
conn.close()
```

Run the test suite (all mocked — no live credentials needed):

```bash
pytest tests/ -v
```

All 65 tests should pass without any API calls.

---

## Load Layer 1 — Financial Entity Data

Layer 1 contains the synthetic financial entities: borrowers, loan applications, bank accounts, transactions, collateral, and officers.

```bash
jupyter lab
# Open and run: notebooks/111_structured_data_loader.ipynb
```

**What it loads:**

| Entity | Count |
|---|---|
| LoanApplication | 466 |
| Borrower | 628 |
| BankAccount | 791 |
| Transaction | 173 |
| Collateral | 466 |
| Officer | 19 |
| Address | 609 |
| Jurisdiction | 7 |
| Industry | 14 |

**Estimated time:** 2–3 minutes on a standard connection.

**Verification query** (run in AuraDB browser or via `read-neo4j-cypher`):

```cypher
MATCH (n)
RETURN labels(n)[0] AS label, count(n) AS count
ORDER BY count DESC
```

---

## Load Layer 2 — APRA Regulatory Framework

Layer 2 contains three APRA documents (APS-112, APG-223, APS-220) parsed into a regulatory knowledge graph with embeddings for semantic search.

Run the notebooks **in order**. Each notebook produces outputs consumed by the next. Do not skip notebooks or run them out of order.

```bash
jupyter lab
# Run notebooks 211 through 216 in sequence
```

### Notebook details

| Notebook | Purpose | Estimated time |
|---|---|---|
| `211_extract_document_structure` | Claude extracts sections, requirements, thresholds (with `threshold_type`), and cross-references from regulatory PDFs. `close_page_gaps()` absorbs unclaimed cover/ToC pages after extraction. | ~40 min (3 PDFs, streaming API calls) |
| `212_merge_and_resolve_references` | Merges per-document CSV outputs from 211; Claude resolves cross-document section references to known section IDs. | ~2 min |
| `213_chunk_documents` | Splits section text into ~300-token chunks. Raises `RuntimeError` if any page range has a coverage gap — fix in notebook 211 before proceeding. | ~1 min |
| `214_ingest_neo4j` | Loads all Layer 2 nodes and relationships into Neo4j. Safe to re-run: clears and reloads Layer 2 without affecting Layers 1 or 3. | ~1 min |
| `215_generate_embeddings` | Generates OpenAI `text-embedding-3-small` embeddings (1,536 dims) for all 189 `Chunk` nodes; creates `SEMANTICALLY_SIMILAR` edges for cross-document chunk pairs with cosine similarity > 0.85. | ~3 min |
| `216_validate_graph` | Validates node counts, relationships, and vector index health. All checks should pass before proceeding. | <1 min |

### Expected outputs after 216

| Node type | Expected count |
|---|---|
| Regulation | 3 |
| Section | 101 |
| Requirement | 219 |
| Threshold | 133 |
| Chunk | 189 |

The `chunk_embeddings` vector index should be present and in `ONLINE` state.

---

## Launch the Application

```bash
streamlit run app.py
```

The dashboard opens at `http://localhost:8501`.

### Example questions to try

```
Is LOAN-0042 compliant with APG-223?
Investigate the network around BRW-0015.
Are there any transaction structuring patterns in the dataset?
What are the high-LVR loans requiring senior management review?
Check LOAN-0001 against all applicable APRA regulations.
```

---

## Troubleshooting

### Neo4j connection refused

- Verify `NEO4J_URI` uses the `neo4j+s://` scheme (not `bolt://` or `neo4j://`)
- Check that your AuraDB instance is running (it may have paused after inactivity — resume it in the console)
- Confirm your IP is not blocked by any network firewall

### Anthropic API key errors

- Ensure `ANTHROPIC_API_KEY` starts with `sk-ant-`
- Check your account has sufficient credits at [console.anthropic.com](https://console.anthropic.com/settings/billing)

### OpenAI embedding errors in notebook 215

- Ensure `OPENAI_API_KEY` is set in `.env` and the environment was reloaded after editing
- If you see rate limit errors, notebook 215 batches embeddings automatically with retry; wait and re-run

### Vector index not ready after notebook 215

The `chunk_embeddings` index is created in notebook 214 and populated in notebook 215. If notebook 216 reports the index is `POPULATING`, wait 30–60 seconds and re-run the validation cell. AuraDB Free can take slightly longer to build vector indexes.

### Rate limit errors during notebook 211

Notebook 211 uses streaming Claude calls (`call_claude_stream_json`) because PDF extraction calls can take more than 10 minutes per document. If you hit rate limits, the notebook will retry automatically. You can also re-run just the failed document by commenting out the others in `document_config.yaml` and re-running from the extraction cell.

### "RuntimeError: page coverage gap" in notebook 213

A page range in the extracted sections data is uncovered — likely a cover page or table of contents that was not absorbed by `close_page_gaps()`. Fix the extraction in notebook 211 by reviewing the `close_page_gaps()` output for the affected document, then re-run notebooks 211 → 216.
