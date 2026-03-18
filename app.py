"""
LoanGuard AI - Streamlit Application
Intelligent loan compliance monitoring and risk investigation powered by Neo4j · Claude Model · OpenAI embeddings
Mirrors the ipywidgets UI in notebooks/316_orchestrator_and_chat.ipynb.
"""
from __future__ import annotations

import html
import logging
import re
import sys
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# ── Project root on sys.path ─────────────────────────────────────────────────
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

# ── Logging — console + project-root log file (gitignored) ───────────────────
_LOG_FILE = ROOT / "loanguard.log"

# Configure logging once per process. Streamlit reruns the script on every
# interaction, so guard with `not logging.root.handlers` to avoid truncating
# the log file and adding duplicate handlers on each rerun.
# Using mode="a" after the explicit truncate avoids null-byte padding caused
# by multiple Streamlit process handles seeking to different positions.
if not logging.root.handlers:
    _LOG_FILE.write_bytes(b"")
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
    # Suppress neo4j's GqlStatusObject notification warnings — their repr contains
    # non-printable characters that corrupt the log file with binary content.
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
    logging.getLogger(__name__).info("Log file: %s", _LOG_FILE)

# ── Tool definitions ─────────────────────────────────────────────────────────
from src.mcp.tool_defs import FASTMCP_TOOL_DEFS, NEO4J_MCP_TOOLS, TOOLS  # noqa: E402
from src.mcp.schema import SEV_ORDER  # noqa: E402

EXAMPLES = [
    "Is LOAN-0002 compliant with APG-223?",
    "Show suspicious connections around BRW-0001.",
    "Find all transaction structuring patterns.",
    "Show the ownership chain behind BRW-0582.",
    "Why might LOAN-0013 require manual review?",
    "Which APRA thresholds apply to residential secured loans?",
]


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

/* ── Entity profile ─────────────────────────────────── */
.profile-metrics {
  display: flex;
  gap: 1.5rem;
  padding: 0.6rem 0.9rem;
  background: #f8f9fa;
  border-radius: var(--radius-md);
  margin-bottom: 0.6rem;
  flex-wrap: wrap;
  border: 1px solid var(--border);
}
.profile-metric { display: flex; flex-direction: column; }
.profile-metric-value { font-size: 1.05rem; font-weight: 700; color: var(--text-primary); }
.profile-metric-label { font-size: 10px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: .05em; margin-top: 1px; }
.profile-grid {
  display: grid;
  grid-template-columns: 100px 1fr;
  gap: 0.3rem 0.75rem;
  align-items: start;
}
.profile-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .05em;
  color: var(--text-secondary);
  padding-top: 3px;
}
.profile-value { font-size: 13px; color: var(--text-primary); line-height: 1.5; }
.badge-danger {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .04em;
  text-transform: uppercase;
  background: var(--sev-HIGH-bg);
  color: var(--sev-HIGH-fg);
  border: 1px solid var(--sev-HIGH-border);
  margin-left: 4px;
  vertical-align: middle;
}
.badge-warning {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .04em;
  text-transform: uppercase;
  background: var(--sev-MEDIUM-bg);
  color: var(--sev-MEDIUM-fg);
  border: 1px solid var(--sev-MEDIUM-border);
  margin-left: 4px;
  vertical-align: middle;
}
.profile-suspicious {
  background: var(--sev-MEDIUM-bg);
  border: 1px solid var(--sev-MEDIUM-border);
  border-radius: var(--radius-md);
  padding: 0.5rem 0.75rem;
  margin-top: 0.6rem;
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
    from src.agent.dispatcher import make_execute_tool
    from src.agent.orchestrator import Orchestrator

    conn = _get_connection()
    return Orchestrator(tools=TOOLS, execute_tool_fn=make_execute_tool(conn))


# ── Response rendering ────────────────────────────────────────────────────────

def _verdict_badge(verdict: str, confidence: float, routing: dict | None = None) -> None:
    colour = VERDICT_COLOURS.get(verdict, "#6c757d")
    icon   = VERDICT_ICONS.get(verdict, "ℹ")
    label  = VERDICT_LABELS.get(verdict, verdict)
    expl   = VERDICT_EXPLANATIONS.get(verdict, "")

    if verdict == "INFORMATIONAL" and routing is not None:
        investigation_only = (
            routing.get("needs_investigation_agent")
            and not routing.get("needs_compliance_agent")
        )
        if investigation_only:
            expl = "Graph investigation complete. Entity connections, transaction patterns, and risk signals have been analysed."

    if verdict == "INFORMATIONAL":
        right_html = ""
    else:
        pct    = f"{confidence:.0%}"
        fill_w = int(confidence * 140)
        right_html = f"""
  <div class="vb-right">
    <div class="vb-pct">{pct}</div>
    <div class="vb-pct-label">Confidence</div>
    <div class="conf-bar-track" style="background:{colour}30;">
      <div class="conf-bar-fill" style="width:{fill_w}px;background:{colour};"></div>
    </div>
  </div>"""

    st.markdown(
        f"""
<div class="verdict-banner" style="background:{colour}20;border:1.5px solid {colour}40;color:{colour};">
  <div class="vb-left">
    <div class="vb-icon">{icon}</div>
    <div>
      <div class="vb-text">{html.escape(label)}</div>
      <div class="vb-expl" style="color:{colour};">{html.escape(expl)}</div>
    </div>
  </div>{right_html}
</div>
        """,
        unsafe_allow_html=True,
    )


def _render_routing(routing: dict, chart_key: str = "routing_graph") -> None:
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
        _render_routing_graph(routing, chart_key=chart_key)


def _render_findings(findings: list[dict]) -> None:
    if not findings:
        st.markdown("*No findings.*")
        return

    sorted_findings = sorted(findings, key=lambda f: SEV_ORDER.get(f.get("severity") or "INFO", 3))

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


def _fetch_suspicious_txns(conn, borrower_id: str, limit: int = 5) -> list[dict]:
    """Return flagged suspicious transactions for a borrower across all their accounts."""
    return conn.run_query(
        """
        MATCH (b:Borrower {borrower_id: $bid})-[:HAS_ACCOUNT]->(acc:BankAccount)
        MATCH (t:Transaction)
        WHERE (t.from_account_id = acc.account_id OR t.to_account_id = acc.account_id)
          AND t.flagged_suspicious = true
        RETURN t.transaction_id AS transaction_id,
               t.amount         AS amount,
               t.currency       AS currency,
               t.date           AS date,
               t.type           AS type,
               t.description    AS description
        ORDER BY t.date DESC
        LIMIT $limit
        """,
        {"bid": borrower_id, "limit": limit},
    )


@st.cache_data(ttl=300)
def _fetch_entity_profile(entity_ids: tuple[str, ...]) -> dict:
    """Fetch Layer 1 entity profile data from Neo4j for each entity ID."""
    conn = _get_connection()
    profile: dict = {}

    for eid in entity_ids:
        if eid.startswith("LOAN-"):
            rows = conn.run_query(
                """
                MATCH (l:LoanApplication {loan_id: $id})
                OPTIONAL MATCH (l)-[:SUBMITTED_BY]->(b:Borrower)
                OPTIONAL MATCH (l)-[:BACKED_BY]->(c:Collateral)
                OPTIONAL MATCH (l)-[:GUARANTEED_BY]->(g:Borrower)
                OPTIONAL MATCH (b)-[:RESIDES_IN|REGISTERED_IN]->(j:Jurisdiction)
                OPTIONAL MATCH (b)-[:BELONGS_TO_INDUSTRY]->(ind:Industry)
                OPTIONAL MATCH (b)-[:HAS_ACCOUNT]->(acc:BankAccount)
                OPTIONAL MATCH (b)<-[:DIRECTOR_OF]-(off:Officer)
                RETURN
                  properties(l)                     AS loan,
                  properties(b)                     AS borrower,
                  properties(c)                     AS collateral,
                  collect(DISTINCT properties(g))   AS guarantors,
                  properties(j)                     AS jurisdiction,
                  properties(ind)                   AS industry,
                  count(DISTINCT acc)               AS account_count,
                  avg(acc.average_monthly_balance)  AS avg_balance,
                  collect(DISTINCT properties(off)) AS officers
                LIMIT 1
                """,
                {"id": eid},
            )
            if not rows:
                continue
            row = rows[0]
            borrower_id = (row.get("borrower") or {}).get("borrower_id")
            suspicious: list[dict] = []
            if borrower_id:
                suspicious = _fetch_suspicious_txns(conn, borrower_id)
            guarantors_basic = [g for g in (row.get("guarantors") or []) if g]

            # Fetch full BRW-* profile for each guarantor
            guarantor_profiles: list[dict] = []
            for g in guarantors_basic:
                g_id = g.get("borrower_id")
                if not g_id:
                    continue
                g_rows = conn.run_query(
                    """
                    MATCH (b:Borrower {borrower_id: $id})
                    OPTIONAL MATCH (b)<-[:SUBMITTED_BY]-(l:LoanApplication)
                    OPTIONAL MATCH (b)-[:RESIDES_IN|REGISTERED_IN]->(j:Jurisdiction)
                    OPTIONAL MATCH (b)-[:BELONGS_TO_INDUSTRY]->(ind:Industry)
                    OPTIONAL MATCH (b)-[:HAS_ACCOUNT]->(acc:BankAccount)
                    OPTIONAL MATCH (b)<-[:DIRECTOR_OF]-(off:Officer)
                    RETURN
                      properties(b)                     AS borrower,
                      collect(DISTINCT properties(l))   AS loans,
                      properties(j)                     AS jurisdiction,
                      properties(ind)                   AS industry,
                      count(DISTINCT acc)               AS account_count,
                      avg(acc.average_monthly_balance)  AS avg_balance,
                      collect(DISTINCT properties(off)) AS officers
                    LIMIT 1
                    """,
                    {"id": g_id},
                )
                if not g_rows:
                    continue
                g_row = g_rows[0]
                g_suspicious = _fetch_suspicious_txns(conn, g_id)
                guarantor_profiles.append({
                    "entity_type":            "Borrower",
                    "borrower":               g_row.get("borrower") or {},
                    "loans":                  [l for l in (g_row.get("loans") or []) if l],
                    "jurisdiction":           g_row.get("jurisdiction") or {},
                    "industry":               g_row.get("industry") or {},
                    "account_count":          g_row.get("account_count") or 0,
                    "avg_balance":            g_row.get("avg_balance"),
                    "officers":               [o for o in (g_row.get("officers") or []) if o],
                    "suspicious_transactions": g_suspicious,
                })

            profile[eid] = {
                "entity_type":            "LoanApplication",
                "loan":                   row.get("loan") or {},
                "borrower":               row.get("borrower") or {},
                "collateral":             row.get("collateral") or {},
                "guarantors":             guarantors_basic,
                "guarantor_profiles":     guarantor_profiles,
                "jurisdiction":           row.get("jurisdiction") or {},
                "industry":               row.get("industry") or {},
                "account_count":          row.get("account_count") or 0,
                "avg_balance":            row.get("avg_balance"),
                "officers":               [o for o in (row.get("officers") or []) if o],
                "suspicious_transactions": suspicious,
            }

        elif eid.startswith("BRW-"):
            rows = conn.run_query(
                """
                MATCH (b:Borrower {borrower_id: $id})
                OPTIONAL MATCH (b)<-[:SUBMITTED_BY]-(l:LoanApplication)
                OPTIONAL MATCH (b)-[:RESIDES_IN|REGISTERED_IN]->(j:Jurisdiction)
                OPTIONAL MATCH (b)-[:BELONGS_TO_INDUSTRY]->(ind:Industry)
                OPTIONAL MATCH (b)-[:HAS_ACCOUNT]->(acc:BankAccount)
                OPTIONAL MATCH (b)<-[:DIRECTOR_OF]-(off:Officer)
                RETURN
                  properties(b)                     AS borrower,
                  collect(DISTINCT properties(l))   AS loans,
                  properties(j)                     AS jurisdiction,
                  properties(ind)                   AS industry,
                  count(DISTINCT acc)               AS account_count,
                  avg(acc.average_monthly_balance)  AS avg_balance,
                  collect(DISTINCT properties(off)) AS officers
                LIMIT 1
                """,
                {"id": eid},
            )
            if not rows:
                continue
            row = rows[0]
            suspicious = _fetch_suspicious_txns(conn, eid)
            profile[eid] = {
                "entity_type":            "Borrower",
                "borrower":               row.get("borrower") or {},
                "loans":                  [l for l in (row.get("loans") or []) if l],
                "jurisdiction":           row.get("jurisdiction") or {},
                "industry":               row.get("industry") or {},
                "account_count":          row.get("account_count") or 0,
                "avg_balance":            row.get("avg_balance"),
                "officers":               [o for o in (row.get("officers") or []) if o],
                "suspicious_transactions": suspicious,
            }

    return profile


def _render_entity_profile(profile: dict) -> None:
    """Render Layer 1 entity profile cards for all entities in the profile dict."""

    def _badge_danger(text: str) -> str:
        return f'<span class="badge-danger">{html.escape(text)}</span>'

    def _badge_warning(text: str) -> str:
        return f'<span class="badge-warning">{html.escape(text)}</span>'

    def _risk_pill(risk: str) -> str:
        risk_upper = (risk or "").upper()
        css = {"HIGH": "sev-HIGH", "MEDIUM": "sev-MEDIUM", "LOW": "sev-LOW"}.get(risk_upper, "sev-INFO")
        return f'<span class="sev-pill {css}">{html.escape(risk_upper)}</span>'

    def _fmt_aud(val) -> str:
        if val is None:
            return "—"
        try:
            return f"${float(val):,.0f}"
        except (TypeError, ValueError):
            return str(val)

    def _fmt_pct(val) -> str:
        if val is None:
            return "—"
        try:
            return f"{float(val):.0f}%"
        except (TypeError, ValueError):
            return str(val)

    def _bool_true(val) -> bool:
        return str(val).lower() not in ("false", "0", "none", "")

    def _render_borrower_section(data: dict, label_prefix: str = "") -> None:
        """Shared renderer for a Borrower profile dict (used for main borrower and guarantors)."""
        borrower    = data.get("borrower") or {}
        loans       = data.get("loans") or []
        jurisdiction = data.get("jurisdiction") or {}
        industry    = data.get("industry") or {}
        account_count = data.get("account_count") or 0
        avg_balance = data.get("avg_balance")
        officers    = data.get("officers") or []
        suspicious  = data.get("suspicious_transactions") or []

        b_id      = borrower.get("borrower_id") or ""
        b_credit  = borrower.get("credit_score")
        b_risk    = borrower.get("risk_rating") or ""
        b_revenue = borrower.get("annual_revenue")
        b_type_val = (borrower.get("entity_subtype") or borrower.get("type") or "").replace("_", " ").title()

        header = label_prefix or html.escape(borrower.get("name") or b_id)
        if label_prefix and b_id:
            header += f' <span style="color:var(--text-secondary);font-size:12px;">({html.escape(b_id)})</span>'

        metrics_html = f"""<div class="profile-metrics">
  <div class="profile-metric">
    <div class="profile-metric-value" style="font-size:0.9rem;">{header}</div>
    <div class="profile-metric-label">Name</div>
  </div>
  <div class="profile-metric">
    <div class="profile-metric-value" style="font-size:0.9rem;">{html.escape(b_type_val) if b_type_val else "—"}</div>
    <div class="profile-metric-label">Type</div>
  </div>
  <div class="profile-metric">
    <div class="profile-metric-value">{b_credit or "—"}</div>
    <div class="profile-metric-label">Credit Score</div>
  </div>
  <div class="profile-metric">
    <div class="profile-metric-value">{_fmt_aud(b_revenue) if b_revenue and float(b_revenue or 0) > 0 else "—"}</div>
    <div class="profile-metric-label">Annual Revenue</div>
  </div>
  <div class="profile-metric">
    <div class="profile-metric-value">{_risk_pill(b_risk) if b_risk else "—"}</div>
    <div class="profile-metric-label">Risk Rating</div>
  </div>
</div>"""

        rows_html = ""

        if loans:
            loan_parts = []
            for l in loans[:5]:
                l_id     = html.escape(l.get("loan_id") or "?")
                l_amt    = _fmt_aud(l.get("amount"))
                l_status = html.escape((l.get("status") or "").replace("_", " ").title())
                l_lvr    = _fmt_pct(l.get("lvr"))
                loan_parts.append(
                    f'<code style="font-size:11px;">{l_id}</code> {l_amt}'
                    f' LVR {l_lvr}'
                    f' <span style="color:var(--text-secondary);">{l_status}</span>'
                )
            if len(loans) > 5:
                loan_parts.append(f'<span style="color:var(--text-secondary);">+ {len(loans)-5} more</span>')
            rows_html += f'<div class="profile-label">Loans</div><div class="profile-value">{"<br>".join(loan_parts)}</div>'

        if jurisdiction:
            j_name = html.escape(jurisdiction.get("name") or jurisdiction.get("jurisdiction_id") or "—")
            j_aml  = (jurisdiction.get("aml_risk_rating") or "").upper()
            j_val  = j_name + (f' &nbsp; AML: {_risk_pill(j_aml)}' if j_aml else "")
            rows_html += f'<div class="profile-label">Jurisdiction</div><div class="profile-value">{j_val}</div>'

        if industry:
            i_name = html.escape(industry.get("name") or industry.get("industry_id") or "—")
            i_risk = (industry.get("risk_level") or "").upper()
            i_val  = i_name + (f' &nbsp; Risk: {_risk_pill(i_risk)}' if i_risk else "")
            rows_html += f'<div class="profile-label">Industry</div><div class="profile-value">{i_val}</div>'

        acc_val = f"{account_count} account{'s' if account_count != 1 else ''}"
        if avg_balance is not None:
            acc_val += f" &nbsp; Avg balance: {_fmt_aud(avg_balance)}/mo"
        rows_html += f'<div class="profile-label">Accounts</div><div class="profile-value">{acc_val}</div>'

        if officers:
            off_parts = []
            for o in officers:
                o_name = html.escape(o.get("name") or o.get("officer_id") or "?")
                o_str  = o_name
                if _bool_true(o.get("is_pep")):
                    o_str += _badge_danger("PEP")
                if _bool_true(o.get("sanctions_match")):
                    o_str += _badge_danger("SANCTIONS")
                off_parts.append(o_str)
            rows_html += f'<div class="profile-label">Officers</div><div class="profile-value">{" &nbsp;·&nbsp; ".join(off_parts)}</div>'

        st.markdown(
            metrics_html + f'<div class="profile-grid">{rows_html}</div>',
            unsafe_allow_html=True,
        )

        if suspicious:
            n = len(suspicious)
            txn_html = (
                f'<div class="profile-suspicious">'
                f'<div style="font-size:12px;font-weight:700;color:var(--sev-MEDIUM-fg);margin-bottom:0.35rem;">'
                f'⚠ {n} Suspicious Transaction{"s" if n > 1 else ""}</div>'
            )
            for t in suspicious:
                t_id   = html.escape(t.get("transaction_id") or "")
                t_amt  = _fmt_aud(t.get("amount"))
                t_ccy  = html.escape(t.get("currency") or "AUD")
                t_date = html.escape(str(t.get("date") or ""))
                t_type = html.escape((t.get("type") or "").replace("_", " "))
                t_desc = html.escape((t.get("description") or "")[:60])
                txn_html += (
                    f'<div style="font-size:12px;color:var(--text-primary);padding:2px 0;">'
                    f'<code style="font-size:11px;">{t_id}</code> &nbsp; {t_amt} {t_ccy}'
                    f' &nbsp; <span style="color:var(--text-secondary);">{t_date} · {t_type}</span>'
                    f' &nbsp; {t_desc}</div>'
                )
            txn_html += "</div>"
            st.markdown(txn_html, unsafe_allow_html=True)

    for eid, data in profile.items():
        etype = data.get("entity_type", "")

        if etype == "LoanApplication":
            loan        = data.get("loan") or {}
            borrower    = data.get("borrower") or {}
            collateral  = data.get("collateral") or {}
            guarantors  = data.get("guarantors") or []
            jurisdiction = data.get("jurisdiction") or {}
            industry    = data.get("industry") or {}
            account_count = data.get("account_count") or 0
            avg_balance = data.get("avg_balance")
            officers    = data.get("officers") or []
            suspicious  = data.get("suspicious_transactions") or []

            loan_type   = html.escape((loan.get("loan_type") or "").replace("_", " ").title())
            loan_purpose = html.escape((loan.get("purpose") or "").replace("_", " ").title())
            status      = html.escape((loan.get("status") or "—").replace("_", " ").title())
            rate        = loan.get("interest_rate_indicative")
            term        = loan.get("term_months")

            metrics_html = f"""<div class="profile-metrics">
  <div class="profile-metric">
    <div class="profile-metric-value">{_fmt_aud(loan.get("amount"))}</div>
    <div class="profile-metric-label">Loan Amount</div>
  </div>
  <div class="profile-metric">
    <div class="profile-metric-value">{_fmt_pct(loan.get("lvr"))}</div>
    <div class="profile-metric-label">LVR</div>
  </div>
  <div class="profile-metric">
    <div class="profile-metric-value">{f"{float(rate):.2f}%" if rate is not None else "—"}</div>
    <div class="profile-metric-label">Indicative Rate</div>
  </div>
  <div class="profile-metric">
    <div class="profile-metric-value">{f"{term} mo" if term else "—"}</div>
    <div class="profile-metric-label">Term</div>
  </div>
  <div class="profile-metric">
    <div class="profile-metric-value" style="font-size:0.9rem;">{status}</div>
    <div class="profile-metric-label">Status</div>
  </div>
</div>"""

            rows_html = ""

            if loan_type or loan_purpose:
                type_val = " &nbsp;·&nbsp; ".join(filter(None, [loan_type, loan_purpose]))
                rows_html += f'<div class="profile-label">Type</div><div class="profile-value">{type_val}</div>'

            if borrower:
                b_name   = html.escape(borrower.get("name") or "—")
                b_type   = html.escape((borrower.get("entity_subtype") or borrower.get("type") or "").replace("_", " "))
                b_credit = borrower.get("credit_score")
                b_risk   = borrower.get("risk_rating") or ""
                b_rev    = borrower.get("annual_revenue")
                b_val    = b_name
                if b_type:
                    b_val += f' <span style="color:var(--text-secondary);font-size:12px;">({b_type})</span>'
                if b_credit:
                    b_val += f' &nbsp; Credit: <strong>{b_credit}</strong>'
                if b_risk:
                    b_val += f' &nbsp; Risk: {_risk_pill(b_risk)}'
                if b_rev and float(b_rev or 0) > 0:
                    b_val += f' &nbsp; Revenue: {_fmt_aud(b_rev)}'
                rows_html += f'<div class="profile-label">Borrower</div><div class="profile-value">{b_val}</div>'

            if collateral:
                c_desc  = html.escape((collateral.get("description") or collateral.get("type") or "—")[:80])
                c_val   = f"{c_desc}"
                c_amt   = collateral.get("estimated_value")
                if c_amt:
                    c_val += f' &nbsp; {_fmt_aud(c_amt)}'
                c_date  = collateral.get("valuation_date")
                if c_date:
                    c_val += f' <span style="color:var(--text-secondary);font-size:12px;">valued {html.escape(str(c_date))}</span>'
                if _bool_true(collateral.get("encumbered")):
                    c_val += _badge_warning("ENCUMBERED")
                rows_html += f'<div class="profile-label">Collateral</div><div class="profile-value">{c_val}</div>'

            if guarantors:
                g_parts = []
                for g in guarantors:
                    g_name = html.escape(g.get("name") or g.get("borrower_id") or "?")
                    g_credit = g.get("credit_score")
                    g_parts.append(g_name + (f" (credit: {g_credit})" if g_credit else ""))
                rows_html += f'<div class="profile-label">Guarantors</div><div class="profile-value">{" &nbsp;·&nbsp; ".join(g_parts)}</div>'

            if jurisdiction:
                j_name = html.escape(jurisdiction.get("name") or jurisdiction.get("jurisdiction_id") or "—")
                j_aml  = (jurisdiction.get("aml_risk_rating") or "").upper()
                j_val  = j_name + (f' &nbsp; AML: {_risk_pill(j_aml)}' if j_aml else "")
                rows_html += f'<div class="profile-label">Jurisdiction</div><div class="profile-value">{j_val}</div>'

            if industry:
                i_name = html.escape(industry.get("name") or industry.get("industry_id") or "—")
                i_risk = (industry.get("risk_level") or "").upper()
                i_val  = i_name + (f' &nbsp; Risk: {_risk_pill(i_risk)}' if i_risk else "")
                rows_html += f'<div class="profile-label">Industry</div><div class="profile-value">{i_val}</div>'

            acc_val = f"{account_count} account{'s' if account_count != 1 else ''}"
            if avg_balance is not None:
                acc_val += f" &nbsp; Avg balance: {_fmt_aud(avg_balance)}/mo"
            rows_html += f'<div class="profile-label">Accounts</div><div class="profile-value">{acc_val}</div>'

            if officers:
                off_parts = []
                for o in officers:
                    o_name = html.escape(o.get("name") or o.get("officer_id") or "?")
                    o_str  = o_name
                    if _bool_true(o.get("is_pep")):
                        o_str += _badge_danger("PEP")
                    if _bool_true(o.get("sanctions_match")):
                        o_str += _badge_danger("SANCTIONS")
                    off_parts.append(o_str)
                rows_html += f'<div class="profile-label">Officers</div><div class="profile-value">{" &nbsp;·&nbsp; ".join(off_parts)}</div>'

            st.markdown(
                metrics_html + f'<div class="profile-grid">{rows_html}</div>',
                unsafe_allow_html=True,
            )

            # Borrower suspicious transactions (label distinguishes from guarantor)
            if suspicious:
                n = len(suspicious)
                txn_html = (
                    f'<div class="profile-suspicious">'
                    f'<div style="font-size:12px;font-weight:700;color:var(--sev-MEDIUM-fg);margin-bottom:0.35rem;">'
                    f'⚠ {n} Suspicious Transaction{"s" if n > 1 else ""} on Borrower Accounts</div>'
                )
                for t in suspicious:
                    t_id   = html.escape(t.get("transaction_id") or "")
                    t_amt  = _fmt_aud(t.get("amount"))
                    t_ccy  = html.escape(t.get("currency") or "AUD")
                    t_date = html.escape(str(t.get("date") or ""))
                    t_type = html.escape((t.get("type") or "").replace("_", " "))
                    t_desc = html.escape((t.get("description") or "")[:60])
                    txn_html += (
                        f'<div style="font-size:12px;color:var(--text-primary);padding:2px 0;">'
                        f'<code style="font-size:11px;">{t_id}</code> &nbsp; {t_amt} {t_ccy}'
                        f' &nbsp; <span style="color:var(--text-secondary);">{t_date} · {t_type}</span>'
                        f' &nbsp; {t_desc}</div>'
                    )
                txn_html += "</div>"
                st.markdown(txn_html, unsafe_allow_html=True)

            # Guarantor profiles
            for g_data in (data.get("guarantor_profiles") or []):
                g_name    = (g_data.get("borrower") or {}).get("name") or "Guarantor"
                g_id      = (g_data.get("borrower") or {}).get("borrower_id") or ""
                g_id_html = (
                    f'&nbsp;<span style="font-weight:400">({html.escape(g_id)})</span>'
                    if g_id else ""
                )
                st.markdown(
                    f'<div style="border-top:1px solid var(--border);margin:0.75rem 0 0.5rem 0;'
                    f'padding-top:0.5rem;font-size:11px;font-weight:600;text-transform:uppercase;'
                    f'letter-spacing:.06em;color:var(--text-secondary);">'
                    f'Guarantor: {html.escape(g_name)}{g_id_html}</div>',
                    unsafe_allow_html=True,
                )
                _render_borrower_section(g_data)

        elif etype == "Borrower":
            _render_borrower_section(data)


def _render_finding_graph(finding: dict, chart_key: str = "finding_graph") -> None:
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
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=chart_key)


def _render_findings_chart(findings: list[dict], chart_key: str = "findings_sev_chart") -> None:
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
        key=chart_key,
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
            _render_finding_graph(selected, chart_key=f"{chart_key}_evidence")


def _render_evidence_graph(cited_sections: list[dict], cited_chunks: list[dict], chart_key: str = "evidence_graph") -> None:
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
        nonlocal edge_x, edge_y
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
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=chart_key)


def _render_routing_graph(routing: dict, chart_key: str = "routing_graph") -> None:
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
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=chart_key)


def _render_evidence(cited_sections: list[dict], cited_chunks: list[dict], chart_key: str = "evidence_graph") -> None:
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
                score    = c.get("similarity_score")
                if isinstance(score, (int, float)):
                    score_badge = f'<span style="font-size:11px;color:var(--accent);">score: {score:.3f}</span>'
                else:
                    score_badge = '<span style="font-size:11px;color:var(--text-secondary);font-style:italic;">cited</span>'
                excerpt  = html.escape((c.get("text_excerpt") or "")[:200])
                html_parts += f"""
<div class="card" style="padding:0.5rem 0.75rem;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
    <code style="font-size:11px;">{chunk_id}</code>
    {score_badge}
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
        _render_evidence_graph(cited_sections, cited_chunks, chart_key=chart_key)


def _render_error(error_str: str) -> None:
    """Render a user-friendly error message, translating known exception types."""
    s = (error_str or "").lower()
    if "ratelimit" in s or "rate_limit" in s or "rate limit" in s or "429" in s:
        msg = "The AI service is rate-limited. Please wait a moment and try again."
    elif "authentication" in s or "401" in s or "invalid x-api-key" in s:
        msg = "API key is invalid or missing. Check your `.env` file."
    elif ("serviceunavaila" in s or "neo4j" in s) and ("connect" in s or "unavailable" in s):
        msg = "Cannot reach the Neo4j database. Check your connection settings."
    elif "timeout" in s:
        msg = "The request timed out. Try a more specific question."
    else:
        msg = error_str[:300]
    st.error(msg)


def render_response(resp, elapsed_s: float | None = None) -> None:
    """Render an InvestigationResponse in the Streamlit UI."""
    _verdict_badge(resp.verdict, resp.confidence, routing=resp.routing)

    with st.expander("Routing", expanded=False):
        _render_routing(resp.routing, chart_key=f"routing_graph_{resp.session_id}")

    if resp.cypher_used:
        with st.expander(f"Cypher used ({len(resp.cypher_used)} queries)", expanded=False):
            for i, c in enumerate(resp.cypher_used, 1):
                q = c.get("cypher", c) if isinstance(c, dict) else c
                st.code(q, language="cypher")

    st.markdown('<div class="section-label">Analysis</div>', unsafe_allow_html=True)
    st.markdown(resp.answer)

    _entity_ids = tuple(resp.routing.get("entity_ids") or [])
    if _entity_ids:
        _profile = _fetch_entity_profile(_entity_ids)
        if _profile:
            with st.expander("Entity Profile", expanded=False):
                _render_entity_profile(_profile)

    if resp.cited_sections or resp.cited_chunks:
        with st.expander("Evidence", expanded=False):
            _render_evidence(resp.cited_sections or [], resp.cited_chunks or [], chart_key=f"evidence_graph_{resp.session_id}")

    if resp.findings:
        _sev_emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢", "INFO": "🔵"}
        _sev_css   = {"HIGH": "var(--sev-HIGH-fg)", "MEDIUM": "var(--sev-MEDIUM-fg)",
                      "LOW": "var(--sev-LOW-fg)", "INFO": "var(--sev-INFO-fg)"}
        top_sev = min(resp.findings, key=lambda f: SEV_ORDER.get(f.get("severity") or "INFO", 3)).get("severity") or "INFO"
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
                _render_findings_chart(resp.findings, chart_key=f"findings_sev_chart_{resp.session_id}")

    if resp.recommended_next_steps:
        with st.expander("Recommended next steps", expanded=True):
            steps_html = ""
            for i, step in enumerate(resp.recommended_next_steps, 1):
                step_escaped = html.escape(step)
                step_html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', step_escaped)
                steps_html += f"""
<div class="step-card">
  <div class="step-num">{i}</div>
  <div style="font-size:14px;color:var(--text-primary);line-height:1.5;">{step_html}</div>
</div>
"""
            st.markdown(steps_html, unsafe_allow_html=True)

    _aid_list = getattr(resp, "assessment_ids", None) or (
        [resp.assessment_id] if resp.assessment_id else []
    )
    footer_parts: list[str] = []
    if _aid_list:
        ids_html = " &nbsp;·&nbsp; ".join(
            f"<code>{html.escape(aid)}</code>" for aid in _aid_list
        )
        footer_parts.append(f'Assessment{"s" if len(_aid_list) > 1 else ""} stored: {ids_html}')
    if elapsed_s is not None:
        footer_parts.append(f'⏱ {elapsed_s:.1f}s')
    if footer_parts:
        st.markdown(
            f'<div class="assess-footer">{" &nbsp;&nbsp;·&nbsp;&nbsp; ".join(footer_parts)}</div>',
            unsafe_allow_html=True,
        )


# ── Page layout ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="LoanGuard AI",
    page_icon="🔍",
    layout="wide",
)

_inject_css()

st.title("LoanGuard AI")
st.markdown("**Intelligent loan compliance monitoring and risk investigation powered by Neo4j · Claude Model · OpenAI embeddings**")
st.caption("Multi-agent pipeline: Orchestrator → ComplianceAgent + InvestigationAgent")

# Initialise on first load only — skip spinner/banner on subsequent reruns
if "agent_ready" not in st.session_state:
    with st.spinner("Connecting to Neo4j and loading agents…"):
        try:
            _get_orchestrator()
            st.session_state["agent_ready"] = True
        except Exception as e:
            st.error(f"Initialisation failed: {e}")
            st.stop()
orchestrator = _get_orchestrator()  # returns cached resource instantly

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

if st.session_state.pop("_clear_input", False):
    st.session_state.pop("question_input", None)

with st.form("question_form", clear_on_submit=False):
    col_input, col_btn = st.columns([8, 1])
    with col_input:
        question = st.text_input(
            "Ask a compliance or investigation question",
            placeholder="e.g. Is LOAN-0002 compliant with APG-223?",
            label_visibility="collapsed",
            key="question_input",
        )
    with col_btn:
        ask = st.form_submit_button("Ask", type="primary", use_container_width=True)

if st.button("Clear chat", type="secondary"):
    st.session_state.history = []
    st.rerun()

# ── Run question ──────────────────────────────────────────────────────────────
auto_submit = st.session_state.pop("auto_submit", False)
submit_question = question if ask else (st.session_state.get("question_input", "") if auto_submit else "")
if submit_question.strip():
    st.session_state.history.append({"role": "user", "content": submit_question.strip()})
    with st.spinner("Thinking…"):
        _start = time.time()
        try:
            resp = orchestrator.run(submit_question.strip())
            _elapsed = round(time.time() - _start, 1)
            logging.getLogger(__name__).info(
                "Question completed in %.1fs: %s", _elapsed, submit_question.strip()[:80]
            )
            st.session_state.history.append({"role": "assistant", "content": resp, "elapsed_s": _elapsed})
        except Exception as e:
            _elapsed = round(time.time() - _start, 1)
            logging.getLogger(__name__).info(
                "Question failed after %.1fs: %s", _elapsed, submit_question.strip()[:80]
            )
            st.session_state.history.append({"role": "assistant", "content": None, "error": str(e)})
    st.session_state["_clear_input"] = True
    st.rerun()

# ── Render chat history (newest on top) ──────────────────────────────────────
_history = st.session_state.history
_pairs: list[tuple] = []
for _i in range(0, len(_history), 2):
    _user_msg = _history[_i]
    _asst_msg = _history[_i + 1] if _i + 1 < len(_history) else None
    _pairs.append((_user_msg, _asst_msg))

for _user_msg, _asst_msg in reversed(_pairs):
    with st.chat_message("user"):
        st.markdown(_user_msg["content"])
    if _asst_msg is not None:
        with st.chat_message("assistant"):
            if _asst_msg.get("error"):
                _render_error(_asst_msg["error"])
            else:
                render_response(_asst_msg["content"], elapsed_s=_asst_msg.get("elapsed_s"))
