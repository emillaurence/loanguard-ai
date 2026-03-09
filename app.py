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


def _render_findings_chart(findings: list[dict]) -> None:
    import plotly.graph_objects as go

    _sev_ord = {"HIGH": 4, "MEDIUM": 3, "LOW": 2, "INFO": 1}

    sorted_findings = sorted(
        findings,
        key=lambda f: _sev_ord.get(f.get("severity", "INFO"), 1),
    )

    labels   = [f.get("severity") or "INFO" for f in sorted_findings]
    x_values = [_sev_ord.get(s, 1) for s in labels]
    colours  = [SEV_BAR_COLOURS.get(s, "#6c757d") for s in labels]

    custom = [
        (
            f.get("description", ""),
            f.get("finding_type", ""),
            f.get("pattern_name", ""),
        )
        for f in sorted_findings
    ]

    fig = go.Figure(
        go.Bar(
            x=x_values,
            y=[f.get("description", f"Finding {i+1}")[:40] for i, f in enumerate(sorted_findings)],
            orientation="h",
            marker_color=colours,
            customdata=custom,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "%{customdata[0]}<br>"
                "Type: %{customdata[1]}<br>"
                "Pattern: %{customdata[2]}"
                "<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        height=max(120, 48 * len(findings)),
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False),
        yaxis=dict(showgrid=False, zeroline=False, tickfont=dict(size=12)),
        showlegend=False,
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
