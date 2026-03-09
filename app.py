"""
Streamlit — Graph Investigation Assistant
Mirrors the ipywidgets UI in notebooks/316_orchestrator_and_chat.ipynb.
"""
from __future__ import annotations

import html
import logging
import re
import sys
import tempfile
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# ── Project root on sys.path ─────────────────────────────────────────────────
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

# ── Logging — console + temporary file (untracked, auto-deleted on exit) ─────
_LOG_FILE = Path(tempfile.gettempdir()) / "graphrag_streamlit.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8"),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger(__name__).info("Log file: %s", _LOG_FILE)

# ── Tool definitions (inline, matches 311_agent_setup.ipynb) ─────────────────

NEO4J_MCP_TOOLS = [
    {
        "name": "read-neo4j-cypher",
        "description": (
            "Execute a read-only Cypher query against the Neo4j graph database. "
            "Returns result rows as a list of dicts. "
            "YOU generate the Cypher — use the GRAPH_SCHEMA_HINT in the system prompt. "
            "Always include LIMIT (max 100). Never use MERGE/CREATE/DELETE/SET."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Valid read-only Cypher query with LIMIT clause.",
                },
                "params": {
                    "type": "object",
                    "description": "Optional parameter dict for parameterised queries.",
                    "default": {},
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "write-neo4j-cypher",
        "description": (
            "Execute a write Cypher query (MERGE, CREATE, SET) against Neo4j. "
            "Use ONLY for Layer 3 Assessment/Finding/ReasoningStep writes. "
            "Prefer persist_assessment tool for structured Layer 3 writes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Write Cypher query."},
                "params": {"type": "object", "default": {}},
            },
            "required": ["query"],
        },
    },
]

FASTMCP_TOOL_DEFS = [
    {
        "name": "traverse_compliance_path",
        "description": (
            "Cross-layer compliance traversal. "
            "Walks entity → Borrower → Jurisdiction (RESIDES_IN/REGISTERED_IN) "
            "→ Regulation (APPLIES_TO_JURISDICTION) → Section → Requirement → Threshold. "
            "Call this FIRST for any compliance question to get the full regulatory framework. "
            "Returns applicable thresholds for the entity jurisdiction and loan type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id":     {"type": "string", "description": "e.g. 'LOAN-0002' or 'BRW-0001'"},
                "entity_type":   {"type": "string", "enum": ["LoanApplication", "Borrower"]},
                "regulation_id": {"type": "string", "description": "Optional regulation filter.", "default": ""},
            },
            "required": ["entity_id", "entity_type"],
        },
    },
    {
        "name": "retrieve_regulatory_chunks",
        "description": (
            "Semantic similarity search over regulatory Chunk nodes using the "
            "chunk_embeddings Neo4j vector index (OpenAI text-embedding-3-small, cosine). "
            "Use to retrieve supporting regulation text when writing a finding."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query_text":    {"type": "string", "description": "Regulatory concept to search."},
                "regulation_id": {"type": "string", "default": "", "description": "Optional filter: e.g. 'APG-223'"},
                "top_k":         {"type": "integer", "default": 5, "description": "Number of chunks (max 20)."},
            },
            "required": ["query_text"],
        },
    },
    {
        "name": "detect_graph_anomalies",
        "description": (
            "Run a named rule-based anomaly pattern against the graph. "
            "pattern_name values: 'transaction_structuring', 'high_lvr_loans', "
            "'high_risk_industry', 'layered_ownership', 'high_risk_jurisdiction', "
            "'guarantor_concentration'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern_name": {
                    "type": "string",
                    "enum": [
                        "transaction_structuring", "high_lvr_loans", "high_risk_industry",
                        "layered_ownership", "high_risk_jurisdiction", "guarantor_concentration",
                    ],
                },
                "entity_id": {"type": "string", "default": "", "description": "Optional entity scope."},
            },
            "required": ["pattern_name"],
        },
    },
    {
        "name": "persist_assessment",
        "description": (
            "Persist a compliance Assessment with Findings and ReasoningSteps to Layer 3 (Neo4j). "
            "Idempotent MERGE. Call after completing compliance analysis to store reasoning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id":       {"type": "string"},
                "entity_type":     {"type": "string", "enum": ["LoanApplication", "Borrower"]},
                "regulation_id":   {"type": "string"},
                "verdict":         {"type": "string", "enum": ["COMPLIANT", "NON_COMPLIANT", "REQUIRES_REVIEW", "ANOMALY_DETECTED", "INFORMATIONAL"]},
                "confidence":      {"type": "number", "minimum": 0, "maximum": 1},
                "findings":        {"type": "array", "items": {"type": "object"}},
                "reasoning_steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string", "description": "What this reasoning step checked or concluded."},
                            "cypher_used":  {"type": "string", "description": "The Cypher query used in this step, if any."},
                            "section_ids":  {"type": "array", "items": {"type": "string"}, "description": "section_id values returned by traverse_compliance_path or read-neo4j-cypher that informed this step."},
                            "chunk_ids":    {"type": "array", "items": {"type": "string"}, "description": "chunk_id values returned by retrieve_regulatory_chunks that informed this step."},
                        },
                        "required": ["description"],
                    },
                },
                "agent":           {"type": "string", "default": "compliance_agent"},
            },
            "required": ["entity_id", "entity_type", "regulation_id", "verdict", "confidence"],
        },
    },
    {
        "name": "trace_evidence",
        "description": (
            "Walk a stored Assessment back to all cited regulatory nodes. "
            "Returns findings, reasoning steps, cited sections (with text), "
            "and cited chunks (with text excerpt). "
            "Use when asked 'why was this flagged?' or 'show your reasoning'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "assessment_id": {"type": "string", "description": "e.g. 'ASSESS-LOAN-0002-APG-223-2026-03-09'"},
            },
            "required": ["assessment_id"],
        },
    },
]

TOOLS = NEO4J_MCP_TOOLS + FASTMCP_TOOL_DEFS

EXAMPLES = [
    "Is LOAN-0002 compliant with APG-223?",
    "Show suspicious connections around BRW-0001.",
    "Find all transaction structuring patterns.",
    "Show the ownership chain behind BRW-0582.",
    "Why might LOAN-0013 require manual review?",
    "Which APRA thresholds apply to residential secured loans?",
]

SEV_COLOURS = {
    "HIGH":   ("#f8d7da", "#842029"),
    "MEDIUM": ("#fff3cd", "#664d03"),
    "LOW":    ("#d4edda", "#155724"),
    "INFO":   ("#d1ecf1", "#0c5460"),
}

VERDICT_COLOURS = {
    "COMPLIANT":        "#28a745",
    "NON_COMPLIANT":    "#dc3545",
    "REQUIRES_REVIEW":  "#fd7e14",
    "ANOMALY_DETECTED": "#dc3545",
    "INFORMATIONAL":    "#6c757d",
}

VERDICT_ICONS = {
    "COMPLIANT":        "✓",
    "NON_COMPLIANT":    "✗",
    "REQUIRES_REVIEW":  "⚠",
    "ANOMALY_DETECTED": "⚑",
    "INFORMATIONAL":    "ℹ",
}

VERDICT_LABELS = {
    "COMPLIANT":        "Compliant",
    "NON_COMPLIANT":    "Non-Compliant",
    "REQUIRES_REVIEW":  "Requires Review",
    "ANOMALY_DETECTED": "Anomaly Detected",
    "INFORMATIONAL":    "Informational",
}

VERDICT_EXPLANATIONS = {
    "COMPLIANT":        "All checked thresholds and requirements are satisfied.",
    "NON_COMPLIANT":    "One or more regulatory requirements are not met.",
    "REQUIRES_REVIEW":  "Manual review is recommended before proceeding.",
    "ANOMALY_DETECTED": "Suspicious patterns were identified in the graph.",
    "INFORMATIONAL":    "No compliance issues found; result is for information only.",
}

SEV_BAR_COLOURS = {
    "HIGH":   "#dc3545",
    "MEDIUM": "#fd7e14",
    "LOW":    "#28a745",
    "INFO":   "#17a2b8",
}

_WRITE_KEYWORDS = {"MERGE", "CREATE", "DELETE", "SET", "DETACH"}


# ── CSS injection ─────────────────────────────────────────────────────────────

def _inject_css() -> None:
    st.markdown(
        """
<style>
/* ── Custom properties ─────────────────────────────── */
:root {
  --sev-HIGH-bg:     #f8d7da;
  --sev-HIGH-fg:     #842029;
  --sev-HIGH-border: #f5c2c7;
  --sev-MEDIUM-bg:   #fff3cd;
  --sev-MEDIUM-fg:   #664d03;
  --sev-MEDIUM-border:#ffe69c;
  --sev-LOW-bg:      #d4edda;
  --sev-LOW-fg:      #155724;
  --sev-LOW-border:  #badbcc;
  --sev-INFO-bg:     #d1ecf1;
  --sev-INFO-fg:     #0c5460;
  --sev-INFO-border: #bee5eb;

  --verdict-COMPLIANT:        #28a745;
  --verdict-NON_COMPLIANT:    #dc3545;
  --verdict-REQUIRES_REVIEW:  #fd7e14;
  --verdict-ANOMALY_DETECTED: #dc3545;
  --verdict-INFORMATIONAL:    #6c757d;

  --surface:         #ffffff;
  --border:          #dee2e6;
  --text-primary:    #212529;
  --text-secondary:  #6c757d;
  --radius-lg:       12px;
  --radius-md:       8px;
  --shadow-sm:       0 1px 3px rgba(0,0,0,.08);
}

/* ── Layout ────────────────────────────────────────── */
.block-container {
  padding-top: 1.5rem !important;
  max-width: 1100px !important;
}

/* ── Cards ─────────────────────────────────────────── */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 0.75rem 1rem;
  margin-bottom: 0.5rem;
}
.card-accent-HIGH   { border-left: 4px solid var(--sev-HIGH-fg)   !important; }
.card-accent-MEDIUM { border-left: 4px solid var(--sev-MEDIUM-fg) !important; }
.card-accent-LOW    { border-left: 4px solid var(--sev-LOW-fg)    !important; }
.card-accent-INFO   { border-left: 4px solid var(--sev-INFO-fg)   !important; }

/* ── Severity pills ─────────────────────────────────── */
.sev-pill {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .04em;
  text-transform: uppercase;
  white-space: nowrap;
}
.sev-pill.sev-HIGH   { background: var(--sev-HIGH-bg);   color: var(--sev-HIGH-fg);   border: 1px solid var(--sev-HIGH-border); }
.sev-pill.sev-MEDIUM { background: var(--sev-MEDIUM-bg); color: var(--sev-MEDIUM-fg); border: 1px solid var(--sev-MEDIUM-border); }
.sev-pill.sev-LOW    { background: var(--sev-LOW-bg);    color: var(--sev-LOW-fg);    border: 1px solid var(--sev-LOW-border); }
.sev-pill.sev-INFO   { background: var(--sev-INFO-bg);   color: var(--sev-INFO-fg);   border: 1px solid var(--sev-INFO-border); }

/* ── Verdict banner ─────────────────────────────────── */
.verdict-banner {
  border-radius: var(--radius-lg);
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}
.verdict-banner .vb-left  { display: flex; align-items: center; gap: 0.75rem; }
.verdict-banner .vb-icon  { font-size: 2rem; line-height: 1; }
.verdict-banner .vb-text  { font-size: 1.25rem; font-weight: 700; }
.verdict-banner .vb-expl  { font-size: 0.85rem; opacity: .85; margin-top: 2px; }
.verdict-banner .vb-right { text-align: right; min-width: 150px; }
.verdict-banner .vb-pct   { font-size: 1.5rem; font-weight: 700; }
.verdict-banner .vb-pct-label { font-size: 0.75rem; opacity: .75; text-transform: uppercase; letter-spacing: .05em; }

.conf-bar-track {
  width: 140px;
  height: 8px;
  background: rgba(0,0,0,.15);
  border-radius: 4px;
  margin-top: 6px;
  display: inline-block;
  vertical-align: middle;
}
.conf-bar-fill {
  height: 100%;
  border-radius: 4px;
  background: rgba(255,255,255,.75);
}

/* ── Routing chips ──────────────────────────────────── */
.chip {
  display: inline-block;
  padding: 2px 9px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
  margin: 2px 3px 2px 0;
  white-space: nowrap;
}
.chip-purple { background: #e9d8fd; color: #553c9a; }
.chip-amber  { background: #fef3c7; color: #92400e; }
.chip-green  { background: #d1fae5; color: #065f46; }
.chip-blue   { background: #dbeafe; color: #1e40af; }

.routing-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5rem 1.5rem;
  margin-top: 0.25rem;
}
.routing-row-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .05em;
  color: var(--text-secondary);
  margin-bottom: 3px;
}

/* ── Section label ──────────────────────────────────── */
.section-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--text-secondary);
  margin: 0.75rem 0 0.35rem 0;
}

/* ── Next-step cards ────────────────────────────────── */
.step-card {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 0.6rem 0.9rem;
  margin-bottom: 0.4rem;
  box-shadow: var(--shadow-sm);
}
.step-num {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  min-width: 24px;
  border-radius: 50%;
  background: #e9ecef;
  color: #495057;
  font-size: 12px;
  font-weight: 700;
}

/* ── Assessment footer ──────────────────────────────── */
.assess-footer {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--border);
}
.assess-footer code {
  font-size: 11px;
  background: #f1f3f5;
  padding: 1px 6px;
  border-radius: 4px;
}
</style>
        """,
        unsafe_allow_html=True,
    )


# ── Cached resource initialisation ───────────────────────────────────────────

@st.cache_resource
def _get_connection():
    from src.graph.connection import Neo4jConnection
    conn = Neo4jConnection()
    conn.connect()
    return conn


@st.cache_resource
def _get_orchestrator():
    from src.agent.orchestrator import Orchestrator
    from src.mcp.tools_impl import (
        detect_graph_anomalies,
        persist_assessment,
        retrieve_regulatory_chunks,
        trace_evidence,
        traverse_compliance_path,
    )

    conn = _get_connection()

    def execute_tool(tool_name: str, tool_input: dict) -> dict:
        logger = logging.getLogger("execute_tool")
        logger.info("Tool: %s | inputs: %s", tool_name, list(tool_input.keys()))
        try:
            if tool_name == "read-neo4j-cypher":
                query = tool_input.get("query", "")
                params = tool_input.get("params", {})
                query_words = set(re.findall(r"\b[A-Z]+\b", query.upper()))
                if query_words & _WRITE_KEYWORDS:
                    return {"error": "read-neo4j-cypher does not allow write operations."}
                return {"rows": conn.run_query(query, params)}
            elif tool_name == "write-neo4j-cypher":
                query = tool_input.get("query", "")
                params = tool_input.get("params", {})
                return {"rows": conn.run_query(query, params)}
            elif tool_name == "traverse_compliance_path":
                return traverse_compliance_path(**tool_input)
            elif tool_name == "retrieve_regulatory_chunks":
                return retrieve_regulatory_chunks(**tool_input)
            elif tool_name == "detect_graph_anomalies":
                return detect_graph_anomalies(**tool_input)
            elif tool_name == "persist_assessment":
                return persist_assessment(**tool_input)
            elif tool_name == "trace_evidence":
                return trace_evidence(**tool_input)
            else:
                return {"error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_name, e)
            return {"error": str(e)}

    return Orchestrator(tools=TOOLS, execute_tool_fn=execute_tool)


# ── Response rendering ────────────────────────────────────────────────────────

def _verdict_badge(verdict: str, confidence: float) -> None:
    colour = VERDICT_COLOURS.get(verdict, "#6c757d")
    icon   = VERDICT_ICONS.get(verdict, "ℹ")
    label  = VERDICT_LABELS.get(verdict, verdict)
    expl   = VERDICT_EXPLANATIONS.get(verdict, "")
    pct    = f"{confidence:.0%}"
    fill_w = int(confidence * 140)

    st.markdown(
        f"""
<div class="verdict-banner" style="background:{colour}20;border:1.5px solid {colour}40;color:{colour};">
  <div class="vb-left">
    <div class="vb-icon">{icon}</div>
    <div>
      <div class="vb-text">{html.escape(label)}</div>
      <div class="vb-expl" style="color:{colour};">{html.escape(expl)}</div>
    </div>
  </div>
  <div class="vb-right">
    <div class="vb-pct">{pct}</div>
    <div class="vb-pct-label">Confidence</div>
    <div class="conf-bar-track" style="background:{colour}30;">
      <div class="conf-bar-fill" style="width:{fill_w}px;background:{colour};"></div>
    </div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def _render_routing(routing: dict) -> None:
    intents  = routing.get("intents", [])
    entities = routing.get("entity_ids", []) or []
    regs     = routing.get("regulations", []) or []
    agents   = []
    if routing.get("needs_compliance_agent"):    agents.append("ComplianceAgent")
    if routing.get("needs_investigation_agent"): agents.append("InvestigationAgent")
    if routing.get("run_anomaly_check"):          agents.append("AnomalyCheck")

    def _chips(items: list[str], cls: str) -> str:
        if not items:
            return '<span style="color:#aaa;font-size:13px">—</span>'
        return "".join(f'<span class="chip {cls}">{html.escape(v)}</span>' for v in items)

    agent_html = (
        " &rarr; ".join(
            f'<span class="chip chip-blue">{html.escape(a)}</span>' for a in agents
        )
        if agents
        else '<span style="color:#aaa;font-size:13px">—</span>'
    )

    st.markdown(
        f"""
<div class="routing-grid">
  <div>
    <div class="routing-row-label">Intents</div>
    {_chips(intents, "chip-purple")}
  </div>
  <div>
    <div class="routing-row-label">Entities</div>
    {_chips(entities, "chip-amber")}
  </div>
  <div>
    <div class="routing-row-label">Regulations</div>
    {_chips(regs, "chip-green")}
  </div>
  <div>
    <div class="routing-row-label">Agent pipeline</div>
    {agent_html}
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )
    if routing.get("entity_ids") or routing.get("regulations"):
        st.markdown(
            '<div class="section-label">Pipeline Graph — hover nodes for detail</div>',
            unsafe_allow_html=True,
        )
        _render_routing_graph(routing)


def _render_findings(findings: list[dict]) -> None:
    if not findings:
        st.markdown("*No findings.*")
        return

    _sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    sorted_findings = sorted(findings, key=lambda f: _sev_order.get(f.get("severity") or "INFO", 3))

    cards_html = ""
    for f in sorted_findings:
        sev   = html.escape(f.get("severity") or "INFO")
        desc  = html.escape(f.get("description") or "")
        ftype = html.escape(f.get("finding_type") or "")
        pname = html.escape(f.get("pattern_name") or "")

        type_chip  = f'<span class="chip chip-blue" style="font-size:11px;">{ftype}</span>' if ftype else ""
        pname_html = (
            f'<div style="margin-top:4px;font-size:11px;color:var(--text-secondary);">'
            f'Pattern: <code style="font-size:10px;">{pname}</code></div>'
            if pname else ""
        )

        cards_html += f"""
<div class="card card-accent-{sev}">
  <div style="display:flex;align-items:flex-start;gap:0.75rem;">
    <div style="min-width:64px;padding-top:2px;">
      <span class="sev-pill sev-{sev}">{sev}</span>
    </div>
    <div style="flex:1;">
      <div style="font-size:14px;color:var(--text-primary);line-height:1.4;">{desc}</div>
      <div style="margin-top:5px;">{type_chip}{pname_html}</div>
    </div>
  </div>
</div>
"""

    st.markdown(cards_html, unsafe_allow_html=True)


def _wrap_text(text: str, width: int = 60) -> str:
    """Word-wrap plain text into Plotly <br>-separated lines."""
    words = (text or "").split()
    lines: list[str] = []
    current: list[str] = []
    for w in words:
        if sum(len(x) + 1 for x in current) + len(w) > width:
            lines.append(" ".join(current))
            current = [w]
        else:
            current.append(w)
    if current:
        lines.append(" ".join(current))
    return "<br>".join(lines)


@st.cache_data(ttl=300)
def _fetch_finding_subgraph(
    entity_id: str,
    entity_type: str,
    regulation_id: str,
    threshold_id: str,
) -> dict:
    """Fetch Layer 1 entity neighbourhood + Layer 2 regulatory chain from Neo4j."""
    if not entity_id:
        return {"l1_nodes": [], "l1_edges": [], "l2_nodes": [], "l2_edges": []}

    conn = _get_connection()

    # ── Layer 1: entity + direct neighbours ──────────────────────────────────
    id_prop = "loan_id" if entity_type == "LoanApplication" else "borrower_id"
    l1_rows = conn.run_query(
        f"""
        MATCH (e:{entity_type} {{{id_prop}: $eid}})
        OPTIONAL MATCH (e)-[r1:SUBMITTED_BY]->(b:Borrower)
        OPTIONAL MATCH (e)-[r2:BACKED_BY]->(c:Collateral)
        OPTIONAL MATCH (e)-[r3:GUARANTEED_BY]->(g:Borrower)
        OPTIONAL MATCH (b)-[r4:RESIDES_IN|REGISTERED_IN]->(j:Jurisdiction)
        OPTIONAL MATCH (b)-[r5:BELONGS_TO_INDUSTRY]->(ind:Industry)
        RETURN
          properties(e)   AS entity_props,
          labels(e)[0]    AS entity_label,
          properties(b)   AS borrower_props,
          properties(c)   AS collateral_props,
          properties(g)   AS guarantor_props,
          type(r1)        AS rel_submitted,
          type(r2)        AS rel_backed,
          type(r3)        AS rel_guaranteed,
          properties(j)   AS jurisdiction_props,
          type(r4)        AS rel_jur,
          properties(ind) AS industry_props
        LIMIT 1
        """,
        {"eid": entity_id},
    )

    l1_nodes: list[dict] = []
    l1_edges: list[dict] = []

    if l1_rows:
        row = l1_rows[0]
        ep = row.get("entity_props") or {}
        l1_nodes.append({
            "id": entity_id, "label": entity_type,
            "display": entity_id,
            "hover": f"<b>{entity_type}</b><br>"
                     + "<br>".join(f"{k}: {v}" for k, v in ep.items()
                                   if k not in ("description",) and v is not None),
            "layer": "primary",
        })
        if row.get("borrower_props"):
            bp = row["borrower_props"]
            bid = bp.get("borrower_id", "Borrower")
            l1_nodes.append({
                "id": bid, "label": "Borrower", "display": bid,
                "hover": f"<b>Borrower</b><br>"
                         + "<br>".join(f"{k}: {v}" for k, v in bp.items() if v is not None),
                "layer": "neighbour",
            })
            l1_edges.append({"src": entity_id, "dst": bid, "label": row.get("rel_submitted", "SUBMITTED_BY")})
        if row.get("collateral_props"):
            cp = row["collateral_props"]
            cid = cp.get("collateral_id", "Collateral")
            val = cp.get("estimated_value")
            l1_nodes.append({
                "id": cid, "label": "Collateral", "display": cid,
                "hover": f"<b>Collateral</b><br>"
                         + "<br>".join(f"{k}: {v}" for k, v in cp.items() if v is not None),
                "layer": "neighbour",
            })
            l1_edges.append({"src": entity_id, "dst": cid, "label": row.get("rel_backed", "BACKED_BY")})
        if row.get("jurisdiction_props"):
            jp = row["jurisdiction_props"]
            jid = jp.get("jurisdiction_id", "Jurisdiction")
            l1_nodes.append({
                "id": jid, "label": "Jurisdiction", "display": jid,
                "hover": f"<b>Jurisdiction</b><br>"
                         + "<br>".join(f"{k}: {v}" for k, v in jp.items() if v is not None),
                "layer": "neighbour",
            })
            bp_id = (row.get("borrower_props") or {}).get("borrower_id", entity_id)
            l1_edges.append({"src": bp_id, "dst": jid, "label": row.get("rel_jur", "RESIDES_IN")})
        if row.get("industry_props"):
            ip = row["industry_props"]
            iid = ip.get("industry_id", "Industry")
            l1_nodes.append({
                "id": iid, "label": "Industry", "display": f"{ip.get('name', iid)}",
                "hover": f"<b>Industry</b><br>"
                         + "<br>".join(f"{k}: {v}" for k, v in ip.items() if v is not None),
                "layer": "neighbour",
            })
            bp_id = (row.get("borrower_props") or {}).get("borrower_id", entity_id)
            l1_edges.append({"src": bp_id, "dst": iid, "label": "BELONGS_TO_INDUSTRY"})

    # ── Layer 2: threshold → requirement → section → regulation ──────────────
    l2_nodes: list[dict] = []
    l2_edges: list[dict] = []

    if threshold_id:
        l2_rows = conn.run_query(
            """
            MATCH (thr:Threshold {threshold_id: $tid})
            OPTIONAL MATCH (req:Requirement)-[:DEFINES_LIMIT]->(thr)
            OPTIONAL MATCH (sec:Section)-[:HAS_REQUIREMENT]->(req)
            OPTIONAL MATCH (reg:Regulation)-[:HAS_SECTION]->(sec)
            RETURN
              properties(thr) AS thr_props,
              properties(req) AS req_props,
              properties(sec) AS sec_props,
              properties(reg) AS reg_props
            LIMIT 1
            """,
            {"tid": threshold_id},
        )
        if l2_rows:
            r2 = l2_rows[0]
            if r2.get("thr_props"):
                tp = r2["thr_props"]
                tid_node = tp.get("threshold_id", threshold_id)
                l2_nodes.append({
                    "id": tid_node, "label": "Threshold", "display": tid_node,
                    "hover": f"<b>Threshold</b><br>"
                             + f"{tp.get('metric','')} {tp.get('operator','')} {tp.get('value','')} {tp.get('unit','')}<br>"
                             + f"<i>{tp.get('consequence','')}</i>",
                    "layer": "threshold",
                })
            if r2.get("req_props"):
                rp = r2["req_props"]
                rid_node = rp.get("requirement_id", "REQ")
                l2_nodes.append({
                    "id": rid_node, "label": "Requirement", "display": rid_node,
                    "hover": f"<b>Requirement</b><br>{_wrap_text(rp.get('description',''))}",
                    "layer": "requirement",
                })
                l2_edges.append({"src": rid_node, "dst": tid_node, "label": "DEFINES_LIMIT"})
            if r2.get("sec_props"):
                sp = r2["sec_props"]
                sid_node = sp.get("section_id", "SEC")
                l2_nodes.append({
                    "id": sid_node, "label": "Section", "display": sid_node,
                    "hover": f"<b>Section</b><br><i>{sp.get('title','')}</i><br>{_wrap_text(sp.get('content_summary','')[:200])}",
                    "layer": "section",
                })
                req_id = (r2.get("req_props") or {}).get("requirement_id", "REQ")
                l2_edges.append({"src": sid_node, "dst": req_id, "label": "HAS_REQUIREMENT"})
            if r2.get("reg_props"):
                rp2 = r2["reg_props"]
                reg_id_node = rp2.get("regulation_id", regulation_id)
                l2_nodes.append({
                    "id": reg_id_node, "label": "Regulation", "display": reg_id_node,
                    "hover": f"<b>Regulation</b><br>{rp2.get('name','')}<br>Issued by: {rp2.get('issuing_body','')}",
                    "layer": "regulation",
                })
                sec_id = (r2.get("sec_props") or {}).get("section_id", "SEC")
                l2_edges.append({"src": reg_id_node, "dst": sec_id, "label": "HAS_SECTION"})
    elif regulation_id:
        reg_rows = conn.run_query(
            """
            MATCH (reg:Regulation {regulation_id: $rid})-[:HAS_SECTION]->(sec:Section)
            RETURN properties(reg) AS reg_props, properties(sec) AS sec_props
            LIMIT 4
            """,
            {"rid": regulation_id},
        )
        reg_added = False
        for r2 in reg_rows:
            if r2.get("reg_props") and not reg_added:
                rp2 = r2["reg_props"]
                reg_id_node = rp2.get("regulation_id", regulation_id)
                l2_nodes.append({
                    "id": reg_id_node, "label": "Regulation", "display": reg_id_node,
                    "hover": f"<b>Regulation</b><br>{rp2.get('name','')}<br>Issued by: {rp2.get('issuing_body','')}",
                    "layer": "regulation",
                })
                reg_added = True
            if r2.get("sec_props"):
                sp = r2["sec_props"]
                sid_node = sp.get("section_id", "SEC")
                l2_nodes.append({
                    "id": sid_node, "label": "Section", "display": sid_node,
                    "hover": f"<b>Section</b><br><i>{sp.get('title','')}</i>",
                    "layer": "section",
                })
                l2_edges.append({"src": regulation_id, "dst": sid_node, "label": "HAS_SECTION"})

    return {
        "l1_nodes": l1_nodes, "l1_edges": l1_edges,
        "l2_nodes": l2_nodes, "l2_edges": l2_edges,
    }


def _render_finding_graph(finding: dict) -> None:
    """Cross-layer Plotly network: L1 entity subgraph ↔ Finding ↔ L2 regulatory chain."""
    import plotly.graph_objects as go

    entity_id    = finding.get("entity_id") or ""
    entity_type  = finding.get("entity_type") or "LoanApplication"
    regulation_id = finding.get("regulation_id") or ""
    threshold_id = finding.get("threshold_id") or ""
    severity     = finding.get("severity") or "INFO"
    description  = finding.get("description") or ""
    finding_type = finding.get("finding_type") or ""

    if not entity_id and not regulation_id:
        st.markdown("*No graph context available for this finding.*")
        return

    with st.spinner("Loading graph evidence…"):
        data = _fetch_finding_subgraph(entity_id, entity_type, regulation_id, threshold_id)

    l1_nodes = data["l1_nodes"]
    l1_edges = data["l1_edges"]
    l2_nodes = data["l2_nodes"]
    l2_edges = data["l2_edges"]

    if not l1_nodes and not l2_nodes:
        st.markdown("*No graph data found for this finding.*")
        return

    # ── X positions per layer ─────────────────────────────────────────────────
    # neighbours(0) → primary entity(1.5) → finding(3) → threshold/req(4.5) → section(5.5) → regulation(6.5)
    _layer_x = {
        "neighbour":   0.0,
        "primary":     1.5,
        "finding":     3.0,
        "threshold":   4.5,
        "requirement": 5.0,
        "section":     5.5,
        "regulation":  6.5,
    }

    _node_colours = {
        "LoanApplication": "#0d6efd",
        "Borrower":        "#fd7e14",
        "Collateral":      "#28a745",
        "Jurisdiction":    "#6f42c1",
        "BankAccount":     "#17a2b8",
        "Transaction":     "#dc3545",
        "Industry":        "#e9a00b",
        "Threshold":       "#dc3545",
        "Requirement":     "#6f42c1",
        "Section":         "#0d6efd",
        "Regulation":      "#28a745",
    }
    _sev_colours = {"HIGH": "#dc3545", "MEDIUM": "#fd7e14", "LOW": "#28a745", "INFO": "#17a2b8"}

    # Finding node
    finding_node = {
        "id": "__finding__", "label": "Finding", "display": finding_type or "Finding",
        "hover": f"<b>Finding [{severity}]</b><br>{_wrap_text(description)}",
        "layer": "finding",
    }

    all_nodes = l1_nodes + [finding_node] + l2_nodes

    # Y positions: spread nodes within each x column
    from collections import defaultdict
    col_buckets: dict[float, list] = defaultdict(list)
    for n in all_nodes:
        col_buckets[_layer_x.get(n["layer"], 3.0)].append(n)

    node_pos: dict[str, tuple[float, float]] = {}
    for x, bucket in col_buckets.items():
        ys = [i / max(len(bucket) - 1, 1) for i in range(len(bucket))]
        for n, y in zip(bucket, ys):
            node_pos[n["id"]] = (x, y)

    # ── Edges ─────────────────────────────────────────────────────────────────
    # L1 internal edges
    all_edges = list(l1_edges)
    # Primary entity → Finding
    if entity_id:
        all_edges.append({"src": entity_id, "dst": "__finding__", "label": "TRIGGERED"})
    # Finding → first L2 node (threshold or section)
    if l2_nodes:
        all_edges.append({"src": "__finding__", "dst": l2_nodes[0]["id"], "label": "BREACHES" if threshold_id else "UNDER"})
    # L2 internal edges
    all_edges.extend(l2_edges)

    edge_x, edge_y, edge_hover_x, edge_hover_y, edge_labels = [], [], [], [], []
    for e in all_edges:
        p0 = node_pos.get(e["src"])
        p1 = node_pos.get(e["dst"])
        if p0 and p1:
            edge_x += [p0[0], p1[0], None]
            edge_y += [p0[1], p1[1], None]
            edge_hover_x.append((p0[0] + p1[0]) / 2)
            edge_hover_y.append((p0[1] + p1[1]) / 2)
            edge_labels.append(e.get("label", ""))

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y, mode="lines",
        line=dict(width=1.5, color="#c8d3e0"),
        hoverinfo="none",
    )
    edge_label_trace = go.Scatter(
        x=edge_hover_x, y=edge_hover_y, mode="markers",
        marker=dict(size=1, color="rgba(0,0,0,0)"),
        text=edge_labels,
        hovertemplate="%{text}<extra></extra>",
    )

    # ── Node trace ────────────────────────────────────────────────────────────
    node_x, node_y, node_text, node_hover, node_colors, node_sizes = [], [], [], [], [], []
    for n in all_nodes:
        pos = node_pos.get(n["id"])
        if not pos:
            continue
        node_x.append(pos[0])
        node_y.append(pos[1])
        node_text.append(n["display"])
        node_hover.append(n["hover"])
        if n["layer"] == "finding":
            node_colors.append(_sev_colours.get(severity, "#6c757d"))
            node_sizes.append(28)
        else:
            node_colors.append(_node_colours.get(n["label"], "#6c757d"))
            node_sizes.append(22)

    node_trace = go.Scatter(
        x=node_x, y=node_y, mode="markers+text",
        marker=dict(size=node_sizes, color=node_colors, line=dict(width=2, color="white")),
        text=node_text,
        textposition="top center",
        textfont=dict(size=10),
        customdata=node_hover,
        hovertemplate="%{customdata}<extra></extra>",
    )

    # ── Layer labels ──────────────────────────────────────────────────────────
    annotations = [
        dict(x=0.0,  y=1.18, text="L1 Neighbours", showarrow=False,
             font=dict(size=9, color="#6c757d"), xref="x", yref="y"),
        dict(x=1.5,  y=1.18, text="L1 Entity",     showarrow=False,
             font=dict(size=9, color="#0d6efd"),   xref="x", yref="y"),
        dict(x=3.0,  y=1.18, text="Finding",       showarrow=False,
             font=dict(size=9, color=_sev_colours.get(severity, "#6c757d")), xref="x", yref="y"),
    ]
    if l2_nodes:
        annotations.append(dict(x=5.5, y=1.18, text="L2 Regulatory", showarrow=False,
                                font=dict(size=9, color="#28a745"), xref="x", yref="y"))

    fig = go.Figure(data=[edge_trace, edge_label_trace, node_trace])
    fig.update_layout(
        height=280,
        margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False, range=[-0.5, 7.2]),
        yaxis=dict(visible=False, range=[-0.2, 1.3]),
        showlegend=False,
        annotations=annotations,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_findings_chart(findings: list[dict]) -> None:
    import plotly.graph_objects as go

    _sev_ord = {"HIGH": 4, "MEDIUM": 3, "LOW": 2, "INFO": 1}

    sorted_findings = sorted(
        findings,
        key=lambda f: _sev_ord.get(f.get("severity") or "INFO", 1),
    )

    labels   = [f.get("severity") or "INFO" for f in sorted_findings]
    x_values = [_sev_ord.get(s, 1) for s in labels]
    colours  = [SEV_BAR_COLOURS.get(s, "#6c757d") for s in labels]

    # Pre-build hover strings (avoids None literals and handles word-wrap)
    hover_texts = []
    for f in sorted_findings:
        sev   = f.get("severity") or "INFO"
        desc  = f.get("description") or ""
        ftype = f.get("finding_type") or ""
        pname = f.get("pattern_name") or ""
        lines = [f"<b>[{sev}]</b>", _wrap_text(desc)]
        if ftype:
            lines.append(f"<span style='color:#888'>Type: {ftype}</span>")
        if pname:
            lines.append(f"<span style='color:#888'>Pattern: {pname}</span>")
        lines.append("<i>Click bar to see graph evidence</i>")
        hover_texts.append("<br>".join(lines))

    # Y-axis labels: short severity+index so the bar label is clean
    y_labels = [f"[{labels[i]}] #{i+1}" for i in range(len(sorted_findings))]

    fig = go.Figure(
        go.Bar(
            x=x_values,
            y=y_labels,
            orientation="h",
            marker_color=colours,
            customdata=hover_texts,
            hovertemplate="%{customdata}<extra></extra>",
        )
    )

    fig.update_layout(
        height=max(120, 52 * len(findings)),
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False),
        yaxis=dict(showgrid=False, zeroline=False, tickfont=dict(size=12)),
        showlegend=False,
        hoverlabel=dict(bgcolor="white", bordercolor="#dee2e6",
                        font=dict(size=13, color="#212529")),
    )

    sel = st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False},
        on_select="rerun",
        key="findings_sev_chart",
    )

    # Render cross-layer evidence graph for the selected bar
    if sel and sel.selection and sel.selection.points:
        idx = sel.selection.points[0].get("point_index", 0)
        if 0 <= idx < len(sorted_findings):
            selected = sorted_findings[idx]
            sev = selected.get("severity") or "INFO"
            sev_colour = SEV_BAR_COLOURS.get(sev, "#6c757d")
            st.markdown(
                f'<div class="section-label" style="color:{sev_colour};">'
                f'Finding Evidence — Layer 1 + Layer 2</div>',
                unsafe_allow_html=True,
            )
            _render_finding_graph(selected)


def _render_evidence_graph(cited_sections: list[dict], cited_chunks: list[dict]) -> None:
    """Plotly network graph: Regulation → Section → Chunk."""
    import plotly.graph_objects as go

    if not cited_sections or not cited_chunks:
        return

    # ── Build node sets ──────────────────────────────────────────────────────
    reg_ids  = sorted({s.get("regulation_id") or "" for s in cited_sections} - {""})
    sec_list = cited_sections   # each has section_id, title, regulation_id
    chk_list = cited_chunks     # each has chunk_id, section_id, text_excerpt

    # x columns
    X_REG, X_SEC, X_CHK = 0.0, 1.5, 3.0

    def _y_positions(n: int) -> list[float]:
        if n == 1:
            return [0.5]
        return [i / (n - 1) for i in range(n)]

    reg_pos  = {rid: (X_REG, y) for rid, y in zip(reg_ids, _y_positions(len(reg_ids)))}
    sec_pos  = {s["section_id"]: (X_SEC, y)
                for s, y in zip(sec_list, _y_positions(len(sec_list)))}
    chk_pos  = {c["chunk_id"]: (X_CHK, y)
                for c, y in zip(chk_list, _y_positions(len(chk_list)))}

    # ── Edge traces ──────────────────────────────────────────────────────────
    edge_x, edge_y = [], []

    def _add_edge(x0, y0, x1, y1):
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    for s in sec_list:
        rid = s.get("regulation_id") or ""
        sid = s.get("section_id") or ""
        if rid in reg_pos and sid in sec_pos:
            _add_edge(*reg_pos[rid], *sec_pos[sid])

    for c in chk_list:
        sid = c.get("section_id") or ""
        cid = c.get("chunk_id") or ""
        if sid in sec_pos and cid in chk_pos:
            _add_edge(*sec_pos[sid], *chk_pos[cid])

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y,
        mode="lines",
        line=dict(width=1.2, color="#c8d3e0"),
        hoverinfo="none",
    )

    # ── Node traces ──────────────────────────────────────────────────────────
    def _node_trace(positions, labels, hover_texts, color, symbol="circle", size=22):
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        return go.Scatter(
            x=xs, y=ys,
            mode="markers+text",
            marker=dict(size=size, color=color, symbol=symbol,
                        line=dict(width=1.5, color="white")),
            text=labels,
            textposition="top center",
            textfont=dict(size=10),
            customdata=hover_texts,
            hovertemplate="%{customdata}<extra></extra>",
        )

    reg_trace = _node_trace(
        positions=[reg_pos[r] for r in reg_ids],
        labels=reg_ids,
        hover_texts=[f"<b>Regulation</b><br>{r}" for r in reg_ids],
        color="#28a745", size=26,
    )

    sec_trace = _node_trace(
        positions=[sec_pos[s["section_id"]] for s in sec_list],
        labels=[s.get("section_id", "")[:12] for s in sec_list],
        hover_texts=[
            f"<b>Section</b><br>{s.get('section_id','')}<br>"
            f"<i>{(s.get('title') or '')[:60]}</i>"
            for s in sec_list
        ],
        color="#0d6efd", size=20,
    )

    chk_trace = _node_trace(
        positions=[chk_pos[c["chunk_id"]] for c in chk_list],
        labels=[c.get("chunk_id", "")[-8:] for c in chk_list],
        hover_texts=[
            f"<b>Chunk</b><br>{c.get('chunk_id','')}<br>"
            f"Score: {c.get('similarity_score', '')}<br>"
            f"<i>{(c.get('text_excerpt') or '')[:120]}…</i>"
            for c in chk_list
        ],
        color="#fd7e14", size=16,
    )

    n_nodes = len(reg_ids) + len(sec_list) + len(chk_list)
    fig = go.Figure(data=[edge_trace, reg_trace, sec_trace, chk_trace])
    fig.update_layout(
        height=max(200, 60 * max(len(reg_ids), len(sec_list), len(chk_list))),
        margin=dict(l=20, r=20, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False, range=[-0.3, 3.5]),
        yaxis=dict(visible=False, range=[-0.15, 1.15]),
        showlegend=False,
        annotations=[
            dict(x=X_REG, y=1.12, text="Regulation", showarrow=False,
                 font=dict(size=10, color="#28a745"), xref="x", yref="y"),
            dict(x=X_SEC, y=1.12, text="Section", showarrow=False,
                 font=dict(size=10, color="#0d6efd"), xref="x", yref="y"),
            dict(x=X_CHK, y=1.12, text="Chunk", showarrow=False,
                 font=dict(size=10, color="#fd7e14"), xref="x", yref="y"),
        ],
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_routing_graph(routing: dict) -> None:
    """Plotly mini-graph: entities → regulations → agents → orchestrator."""
    import plotly.graph_objects as go

    entity_ids   = routing.get("entity_ids") or []
    entity_types = routing.get("entity_types") or []
    reg_ids      = routing.get("regulations") or []

    agents = []
    if routing.get("needs_compliance_agent"):    agents.append("ComplianceAgent")
    if routing.get("needs_investigation_agent"): agents.append("InvestigationAgent")

    if not entity_ids and not reg_ids:
        return

    # ── Positions ─────────────────────────────────────────────────────────────
    X_ENT, X_REG, X_AGT, X_ORC = 0.0, 1.5, 3.0, 4.5

    def _ys(n):
        if n == 1: return [0.5]
        return [i / (n - 1) for i in range(n)]

    ent_pairs = list(zip(entity_ids, entity_types + [""] * len(entity_ids)))
    ent_pos   = {eid: (X_ENT, y) for (eid, _), y in zip(ent_pairs, _ys(len(ent_pairs) or 1))}
    reg_pos   = {rid: (X_REG, y) for rid, y in zip(reg_ids, _ys(len(reg_ids) or 1))}
    agt_pos   = {a: (X_AGT, y) for a, y in zip(agents, _ys(len(agents) or 1))}
    orc_pos   = {"Orchestrator": (X_ORC, 0.5)}

    # ── Edges ─────────────────────────────────────────────────────────────────
    edge_x, edge_y = [], []

    def _edge(p0, p1):
        edge_x.extend([p0[0], p1[0], None])
        edge_y.extend([p0[1], p1[1], None])

    for eid, etype in ent_pairs:
        for rid in reg_ids:
            _edge(ent_pos[eid], reg_pos[rid])

    for rid in reg_ids:
        for agt in agents:
            _edge(reg_pos[rid], agt_pos[agt])

    for agt in agents:
        _edge(agt_pos[agt], orc_pos["Orchestrator"])

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y,
        mode="lines",
        line=dict(width=1.2, color="#c8d3e0"),
        hoverinfo="none",
    )

    # ── Node helpers ──────────────────────────────────────────────────────────
    def _scatter(xs, ys, labels, hover_texts, color, size=22):
        return go.Scatter(
            x=xs, y=ys,
            mode="markers+text",
            marker=dict(size=size, color=color, line=dict(width=1.5, color="white")),
            text=labels,
            textposition="top center",
            textfont=dict(size=10),
            customdata=hover_texts,
            hovertemplate="%{customdata}<extra></extra>",
        )

    traces = [edge_trace]

    if ent_pairs:
        TYPE_COLOUR = {
            "LoanApplication": "#0d6efd",
            "Borrower": "#fd7e14",
            "BankAccount": "#6f42c1",
            "Transaction": "#dc3545",
        }
        traces.append(_scatter(
            xs=[ent_pos[e][0] for e, _ in ent_pairs],
            ys=[ent_pos[e][1] for e, _ in ent_pairs],
            labels=[e for e, _ in ent_pairs],
            hover_texts=[f"<b>{e}</b><br>Type: {t or '?'}" for e, t in ent_pairs],
            color=[TYPE_COLOUR.get(t, "#6c757d") for _, t in ent_pairs],
            size=24,
        ))

    if reg_ids:
        traces.append(_scatter(
            xs=[reg_pos[r][0] for r in reg_ids],
            ys=[reg_pos[r][1] for r in reg_ids],
            labels=reg_ids,
            hover_texts=[f"<b>{r}</b><br>APRA Regulation" for r in reg_ids],
            color="#28a745", size=22,
        ))

    if agents:
        traces.append(_scatter(
            xs=[agt_pos[a][0] for a in agents],
            ys=[agt_pos[a][1] for a in agents],
            labels=[a.replace("Agent", "\nAgent") for a in agents],
            hover_texts=[f"<b>{a}</b><br>Specialist agent" for a in agents],
            color="#0dcaf0", size=20,
        ))

    traces.append(_scatter(
        xs=[orc_pos["Orchestrator"][0]],
        ys=[orc_pos["Orchestrator"][1]],
        labels=["Orchestrator"],
        hover_texts=["<b>Orchestrator</b><br>Routes & synthesises results"],
        color="#6f42c1", size=26,
    ))

    col_labels = []
    if ent_pairs: col_labels.append(dict(x=X_ENT, text="Entities", color="#6c757d"))
    if reg_ids:   col_labels.append(dict(x=X_REG, text="Regulations", color="#28a745"))
    if agents:    col_labels.append(dict(x=X_AGT, text="Agents", color="#0dcaf0"))
    col_labels.append(dict(x=X_ORC, text="Orchestrator", color="#6f42c1"))

    fig = go.Figure(data=traces)
    fig.update_layout(
        height=200,
        margin=dict(l=20, r=20, t=30, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False, range=[-0.5, 5.2]),
        yaxis=dict(visible=False, range=[-0.2, 1.3]),
        showlegend=False,
        annotations=[
            dict(x=lbl["x"], y=1.22, text=lbl["text"], showarrow=False,
                 font=dict(size=10, color=lbl["color"]), xref="x", yref="y")
            for lbl in col_labels
        ],
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _render_evidence(cited_sections: list[dict], cited_chunks: list[dict]) -> None:
    col_sec, col_chk = st.columns(2)

    with col_sec:
        st.markdown('<div class="section-label">Cited Sections</div>', unsafe_allow_html=True)
        if cited_sections:
            html_parts = ""
            for s in cited_sections:
                reg     = html.escape(s.get("regulation_id") or s.get("reg", ""))
                sid     = html.escape(s.get("section_id", ""))
                title   = html.escape(s.get("title", ""))
                reg_chip = f'<span class="chip chip-green" style="font-size:10px;">{reg}</span>' if reg else ""
                html_parts += f"""
<div class="card" style="padding:0.5rem 0.75rem;">
  <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:3px;">
    {reg_chip}
    <code style="font-size:11px;">{sid}</code>
  </div>
  <div style="font-size:13px;color:var(--text-primary);">{title}</div>
</div>
"""
            st.markdown(html_parts, unsafe_allow_html=True)
        else:
            st.markdown("*No cited sections.*")

    with col_chk:
        st.markdown('<div class="section-label">Cited Chunks</div>', unsafe_allow_html=True)
        if cited_chunks:
            html_parts = ""
            for c in cited_chunks:
                chunk_id = html.escape(c.get("chunk_id", ""))
                score    = c.get("similarity_score", "")
                score_str = f"{score:.3f}" if isinstance(score, float) else html.escape(str(score))
                excerpt  = html.escape((c.get("text_excerpt") or "")[:200])
                html_parts += f"""
<div class="card" style="padding:0.5rem 0.75rem;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
    <code style="font-size:11px;">{chunk_id}</code>
    <span style="font-size:11px;color:var(--text-secondary);">score: {score_str}</span>
  </div>
  <div style="font-size:12px;color:#495057;font-style:italic;">{excerpt}&hellip;</div>
</div>
"""
            st.markdown(html_parts, unsafe_allow_html=True)
        else:
            st.markdown("*No cited chunks.*")

    if cited_sections and cited_chunks:
        st.markdown(
            '<div class="section-label">Regulatory Path — hover nodes for detail</div>',
            unsafe_allow_html=True,
        )
        _render_evidence_graph(cited_sections, cited_chunks)


def render_response(resp) -> None:
    """Render an InvestigationResponse in the Streamlit UI."""
    _verdict_badge(resp.verdict, resp.confidence)

    with st.expander("Routing", expanded=False):
        _render_routing(resp.routing)

    if resp.cypher_used:
        with st.expander(f"Cypher used ({len(resp.cypher_used)} queries)", expanded=False):
            for i, c in enumerate(resp.cypher_used, 1):
                q = c.get("cypher", c) if isinstance(c, dict) else c
                st.code(q, language="cypher")

    st.markdown('<div class="section-label">Analysis</div>', unsafe_allow_html=True)
    st.markdown(resp.answer)

    if resp.cited_sections or resp.cited_chunks:
        with st.expander("Evidence", expanded=False):
            _render_evidence(resp.cited_sections or [], resp.cited_chunks or [])

    if resp.findings:
        _sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
        _sev_emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢", "INFO": "🔵"}
        _sev_css   = {"HIGH": "var(--sev-HIGH-fg)", "MEDIUM": "var(--sev-MEDIUM-fg)",
                      "LOW": "var(--sev-LOW-fg)", "INFO": "var(--sev-INFO-fg)"}
        top_sev = min(resp.findings, key=lambda f: _sev_order.get(f.get("severity") or "INFO", 3)).get("severity") or "INFO"
        emoji   = _sev_emoji.get(top_sev, "")
        colour  = _sev_css.get(top_sev, "var(--text-secondary)")
        st.markdown(
            f'<div class="section-label" style="color:{colour};">'
            f'{emoji} KEY FINDINGS</div>',
            unsafe_allow_html=True,
        )
        with st.expander(f"{emoji} Key Findings by Severity ({len(resp.findings)})", expanded=True):
            _render_findings(resp.findings)
            if len(resp.findings) > 1:
                st.markdown(
                    '<div class="section-label">Severity Map — hover for detail</div>',
                    unsafe_allow_html=True,
                )
                _render_findings_chart(resp.findings)

    if resp.recommended_next_steps:
        with st.expander("Recommended next steps", expanded=True):
            steps_html = ""
            for i, step in enumerate(resp.recommended_next_steps, 1):
                steps_html += f"""
<div class="step-card">
  <div class="step-num">{i}</div>
  <div style="font-size:14px;color:var(--text-primary);line-height:1.5;">{html.escape(step)}</div>
</div>
"""
            st.markdown(steps_html, unsafe_allow_html=True)

    if resp.assessment_id:
        st.markdown(
            f'<div class="assess-footer">Assessment stored: <code>{html.escape(resp.assessment_id)}</code></div>',
            unsafe_allow_html=True,
        )


# ── Page layout ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Graph Investigation Assistant",
    page_icon="🔍",
    layout="wide",
)

_inject_css()

st.title("Graph Investigation Assistant")
st.caption("Multi-agent pipeline: Orchestrator → ComplianceAgent + InvestigationAgent")

# Initialise on first load (shows spinner)
with st.spinner("Connecting to Neo4j and loading agents…"):
    try:
        orchestrator = _get_orchestrator()
        st.success("Connected.", icon="✅")
    except Exception as e:
        st.error(f"Initialisation failed: {e}")
        st.stop()

# ── Sidebar — example questions + log path ───────────────────────────────────
with st.sidebar:
    st.markdown(
        "Ask compliance and investigation questions about the APRA-regulated "
        "financial entities in the knowledge graph."
    )
    st.markdown('<div class="section-label">Example Questions</div>', unsafe_allow_html=True)
    for q in EXAMPLES:
        if st.button(q, use_container_width=True):
            st.session_state["question_input"] = q
            st.session_state["auto_submit"] = True

    st.divider()
    st.caption(f"📄 Log file\n`{_LOG_FILE}`")

# ── Chat history ─────────────────────────────────────────────────────────────
if "history" not in st.session_state:
    st.session_state.history = []

col_input, col_btn = st.columns([8, 1])
with col_input:
    question = st.text_input(
        "Ask a compliance or investigation question",
        placeholder="e.g. Is LOAN-0002 compliant with APG-223?",
        label_visibility="collapsed",
        key="question_input",
    )
with col_btn:
    ask = st.button("Ask", type="primary", use_container_width=True)

if st.button("Clear chat", type="secondary"):
    st.session_state.history = []
    st.rerun()

# ── Run question ──────────────────────────────────────────────────────────────
auto_submit = st.session_state.pop("auto_submit", False)
if (ask or auto_submit) and question.strip():
    st.session_state.history.append({"role": "user", "content": question.strip()})
    with st.spinner("Thinking…"):
        try:
            resp = orchestrator.run(question.strip())
            st.session_state.history.append({"role": "assistant", "content": resp})
        except Exception as e:
            st.session_state.history.append({"role": "assistant", "content": None, "error": str(e)})

# ── Render chat history ───────────────────────────────────────────────────────
for msg in st.session_state.history:
    if msg["role"] == "user":
        with st.chat_message("user"):
            st.markdown(msg["content"])
    else:
        with st.chat_message("assistant"):
            if msg.get("error"):
                st.error(msg["error"])
            else:
                render_response(msg["content"])
