"""
Streamlit — Graph Investigation Assistant
Mirrors the ipywidgets UI in notebooks/316_orchestrator_and_chat.ipynb.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# ── Project root on sys.path ─────────────────────────────────────────────────
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

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
                "reasoning_steps": {"type": "array", "items": {"type": "object"}},
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

_WRITE_KEYWORDS = {"MERGE", "CREATE", "DELETE", "SET", "DETACH"}


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
    st.markdown(
        f'<span style="background:{colour};color:white;padding:4px 14px;'
        f'border-radius:12px;font-size:13px;font-weight:bold">{verdict}</span>'
        f'&nbsp;&nbsp;<span style="color:#888;font-size:13px">confidence: {confidence:.0%}</span>',
        unsafe_allow_html=True,
    )


def _render_routing(routing: dict) -> None:
    intents  = ", ".join(routing.get("intents", []))
    entities = ", ".join(routing.get("entity_ids", []) or ["—"])
    regs     = ", ".join(routing.get("regulations", []) or ["—"])
    agents   = []
    if routing.get("needs_compliance_agent"):   agents.append("ComplianceAgent")
    if routing.get("needs_investigation_agent"): agents.append("InvestigationAgent")
    if routing.get("run_anomaly_check"):          agents.append("AnomalyCheck")
    agent_str = " → ".join(agents) or "—"
    st.markdown(
        f"**Intent:** {intents} &nbsp;|&nbsp; **Entities:** {entities} "
        f"&nbsp;|&nbsp; **Regs:** {regs}  \n**Agents:** {agent_str}"
    )


def _render_findings(findings: list[dict]) -> None:
    if not findings:
        st.markdown("*No findings.*")
        return
    for f in findings:
        sev = f.get("severity", "INFO")
        bg, fg = SEV_COLOURS.get(sev, ("#fff", "#000"))
        desc = f.get("description", "")
        st.markdown(
            f'<div style="background:{bg};color:{fg};padding:5px 10px;'
            f'margin:3px 0;border-radius:4px;font-size:13px">'
            f'<b>[{sev}]</b> {desc}</div>',
            unsafe_allow_html=True,
        )


def render_response(resp) -> None:
    """Render an InvestigationResponse in the Streamlit UI."""
    _verdict_badge(resp.verdict, resp.confidence)
    st.markdown("")

    with st.expander("Routing", expanded=False):
        _render_routing(resp.routing)

    if resp.cypher_used:
        with st.expander(f"Cypher used ({len(resp.cypher_used)} queries)", expanded=False):
            for i, c in enumerate(resp.cypher_used, 1):
                q = c.get("cypher", c) if isinstance(c, dict) else c
                st.code(q, language="cypher")

    st.markdown("**Answer**")
    st.markdown(resp.answer)

    if resp.cited_sections or resp.cited_chunks:
        with st.expander("Evidence", expanded=False):
            if resp.cited_sections:
                st.markdown("**Cited sections:**")
                for s in resp.cited_sections:
                    st.markdown(f"- `{s.get('section_id')}` — {s.get('title', '')}")
            if resp.cited_chunks:
                st.markdown("**Cited chunks:**")
                for c in resp.cited_chunks:
                    excerpt = (c.get("text_excerpt") or "")[:200]
                    st.markdown(
                        f"- `{c.get('chunk_id')}` (score: {c.get('similarity_score', '')})"
                        f"  \n  *{excerpt}…*"
                    )

    if resp.findings:
        with st.expander(f"Findings ({len(resp.findings)})", expanded=True):
            _render_findings(resp.findings)

    if resp.recommended_next_steps:
        with st.expander("Recommended next steps", expanded=False):
            for i, step in enumerate(resp.recommended_next_steps, 1):
                st.markdown(f"{i}. {step}")

    if resp.assessment_id:
        st.caption(f"Assessment stored: `{resp.assessment_id}`")


# ── Page layout ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Graph Investigation Assistant",
    page_icon="🔍",
    layout="wide",
)
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

# ── Sidebar — example questions ──────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Example questions")
    for q in EXAMPLES:
        if st.button(q, use_container_width=True):
            st.session_state["question_input"] = q
            st.session_state["auto_submit"] = True

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
