"""
Orchestrator — routes user questions to specialist agents and synthesises responses.

Flow:
  1. route(question) → routing plan (intent classification via Claude, MODEL_FAST)
  2. Dispatch to ComplianceAgent and/or InvestigationAgent — parallel when both needed
  3. Synthesis merges outputs → InvestigationResponse (MODEL_MAIN)

The orchestrator holds references to both MCP tool lists and the shared
execute_tool dispatcher, injecting them into each specialist agent.

Model: MODEL_FAST for routing, MODEL_MAIN for synthesis  (see src/agent/config.py)
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.agent.compliance_agent import ComplianceAgent
from src.agent.config import (
    MODEL, MODEL_FAST, make_anthropic_client,
    ROUTING_MAX_TOKENS, SYNTHESIS_MAX_TOKENS,
    CACHE_CONTROL_EPHEMERAL, TEMPERATURE,
)
from src.agent.investigation_agent import InvestigationAgent
from src.agent.utils import call_claude_with_retry
from src.document.utils import strip_fences
from src.mcp.schema import GRAPH_SCHEMA_HINT, InvestigationResponse, SEV_ORDER, THRESHOLD_TO_PATTERN, VERDICT_PRIORITY

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer 3 Cypher queries (dispatched via execute_tool, kept here for visibility)
# ---------------------------------------------------------------------------

_FINDINGS_QUERY = (
    "MATCH (a:Assessment)-[:HAS_FINDING]->(f:Finding) "
    "WHERE a.assessment_id IN $ids "
    "RETURN a.assessment_id AS assessment_id, "
    "       a.regulation_id AS regulation_id, "
    "       a.verdict AS verdict, "
    "       a.confidence AS confidence, "
    "       f.finding_id AS finding_id, "
    "       f.finding_type AS finding_type, "
    "       f.severity AS severity, "
    "       f.description AS description, "
    "       f.pattern_name AS pattern_name "
    "ORDER BY "
    "  CASE f.severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 "
    "  WHEN 'LOW' THEN 2 ELSE 3 END "
    "LIMIT 200"
)

_ASSESSMENT_META_QUERY = (
    "MATCH (a:Assessment) "
    "WHERE a.assessment_id IN $ids "
    "RETURN a.assessment_id AS assessment_id, "
    "       a.regulation_id AS regulation_id, "
    "       a.verdict AS verdict, "
    "       a.confidence AS confidence "
    "LIMIT 50"
)

# ---------------------------------------------------------------------------
# Routing prompt
# ---------------------------------------------------------------------------

ROUTING_SYSTEM = """You are a financial compliance routing agent.

Classify the user's question and return ONLY a JSON object (no markdown fences):
{
  "intents": ["compliance"|"investigation"|"anomaly"|"exploration"|"evidence"],
  "entity_ids": ["LOAN-xxxx"|"BRW-xxxx"|"ACC-xxxx"|"TXN-xxxx"],
  "entity_types": ["LoanApplication"|"Borrower"|"BankAccount"|"Transaction"],
  "regulations": ["APS-112"|"APG-223"|"APS-220"],
  "run_anomaly_check": true|false,
  "needs_compliance_agent": true|false,
  "needs_investigation_agent": true|false
}

Rules:
- intents may have multiple values if the question spans topics.
- entity_ids: extract any IDs mentioned (e.g. LOAN-0002, BRW-0001).
- run_anomaly_check=true when question mentions suspicious, anomaly, fraud,
  unusual, structuring, circular, or asks to 'find' patterns.
- needs_compliance_agent=true for compliance, regulation, threshold questions.
- needs_investigation_agent=true for investigation, connections, network,
  suspicious, review, or exploration questions.
- If unclear, set both needs_* to true.
"""

# ---------------------------------------------------------------------------
# Threshold ID → (human description, severity) for enriching findings panel.
# Mirrors the key thresholds in GRAPH_SCHEMA_HINT and ComplianceAgent system prompt.
# ---------------------------------------------------------------------------

_THRESHOLD_META: dict[str, tuple[str, str]] = {
    "APG-223-THR-003": (
        "Serviceability buffer must be >= 3.0 percentage points above the loan rate.",
        "HIGH",
    ),
    "APG-223-THR-006": (
        "Non-salary income (rental, self-employed) must be haircut by >= 20% in serviceability calculations.",
        "MEDIUM",
    ),
    "APG-223-THR-008": (
        "LVR >= 90% (including capitalised LMI) requires senior management review with Board oversight.",
        "HIGH",
    ),
    "APS-112-THR-031": (
        "Commercial property must apply a >= 40% haircut to assessed value when calculating LVR.",
        "HIGH",
    ),
    "APS-112-THR-032": (
        "Lenders Mortgage Insurance must cover >= 40% of the loan loss to qualify for capital relief.",
        "HIGH",
    ),
}


# ---------------------------------------------------------------------------
# Synthesis prompt
# ---------------------------------------------------------------------------

SYNTHESIS_SYSTEM = """You are a senior financial compliance analyst.

You will receive outputs from specialist agents (compliance, investigation,
anomaly detection). Synthesise them into a single clear response suitable
for a compliance officer or investigator.

Your response must include:
1. A direct answer to the original question (2–4 sentences).
2. A clearly delimited section of 3–5 concrete recommended next steps.

Do NOT reproduce or summarise the findings list — findings are displayed
separately in the UI.

Structure your response exactly like this:
<answer text here>

RECOMMENDED NEXT STEPS:
1. <step>
2. <step>
3. <step>

CITATION RULE: Only cite regulation IDs that appear in the AVAILABLE REGULATIONS
list provided in the context (e.g. APG-223, APS-112, APS-220). Use the bare ID
only — no qualifiers.

Be concise. Use plain language.
"""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """
    Top-level orchestrator for the interactive investigation notebook.

    Usage:
        orchestrator = Orchestrator(tools=combined_tools, execute_tool_fn=dispatcher)
        response = orchestrator.run("Is LOAN-0002 compliant with APG-223?")
    """

    def __init__(
        self,
        tools: list[dict],
        execute_tool_fn: Any,
        model: str = MODEL,
    ) -> None:
        self.tools = tools
        self.execute_tool = execute_tool_fn
        self.model = model
        self.client = make_anthropic_client()
        self._compliance_agent = ComplianceAgent(tools, execute_tool_fn, model)
        self._investigation_agent = InvestigationAgent(tools, execute_tool_fn, model)
        self._graph_regulation_ids: list[str] = self._fetch_regulation_ids()

    def run(self, question: str) -> InvestigationResponse:
        """
        Route a user question through the multi-agent pipeline.

        Args:
            question: Natural-language compliance or investigation question.

        Returns:
            InvestigationResponse — structured result for the chat UI.
        """
        session_id = str(uuid.uuid4())[:8]
        logger.info("[%s] Orchestrator: %s", session_id, question)

        # Step 1: Route
        routing = self._route(question)
        logger.info("[%s] Routing: %s", session_id, routing)

        # Step 2: Dispatch to specialist agents (parallel when both are needed)
        compliance_result = None
        investigation_result = None

        needs_compliance   = routing.get("needs_compliance_agent", False)
        needs_investigation = routing.get("needs_investigation_agent", False)

        if needs_compliance and needs_investigation:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {
                    executor.submit(self._compliance_agent.run, question): "compliance",
                    executor.submit(self._investigation_agent.run, question): "investigation",
                }
                for future in as_completed(futures):
                    label = futures[future]
                    try:
                        result = future.result()
                        if label == "compliance":
                            compliance_result = result
                            logger.info("[%s] Compliance verdict: %s", session_id, result.verdict)
                        else:
                            investigation_result = result
                            logger.info("[%s] Investigation complete", session_id)
                    except Exception as e:
                        logger.error("[%s] %s agent failed: %s", session_id, label, e)
        else:
            if needs_compliance:
                try:
                    compliance_result = self._compliance_agent.run(question)
                    logger.info("[%s] Compliance verdict: %s", session_id, compliance_result.verdict)
                except Exception as e:
                    logger.error("[%s] ComplianceAgent failed: %s", session_id, e)
            if needs_investigation:
                try:
                    investigation_result = self._investigation_agent.run(question)
                    logger.info("[%s] Investigation complete", session_id)
                except Exception as e:
                    logger.error("[%s] InvestigationAgent failed: %s", session_id, e)

        # Step 3: Synthesise
        response = self._synthesise(
            session_id=session_id,
            question=question,
            routing=routing,
            compliance_result=compliance_result,
            investigation_result=investigation_result,
        )
        return response

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_regulation_ids(self) -> list[str]:
        """Query the graph for all Regulation node IDs at startup."""
        try:
            result = self.execute_tool(
                "read-neo4j-cypher",
                {"query": "MATCH (r:Regulation) RETURN r.regulation_id AS id ORDER BY r.regulation_id LIMIT 50"},
            )
            rows = result.get("rows", [])
            ids = [row["id"] for row in rows if row.get("id")]
            logger.info("Graph regulations found: %s", ids)
            return ids
        except Exception as e:
            logger.warning("Could not fetch regulation IDs from graph: %s", e)
            return []

    def _fetch_assessment_findings(
        self, assessment_ids: list[str]
    ) -> tuple[list[dict], str, float]:
        """
        Fetch all Finding nodes for the given assessment IDs from Neo4j.

        Returns (findings, aggregated_verdict, aggregated_confidence).
        Verdict is the worst-case across all assessments:
          NON_COMPLIANT > REQUIRES_REVIEW > ANOMALY_DETECTED > COMPLIANT > INFORMATIONAL
        """
        try:
            # Fetch findings across all assessments
            findings_result = self.execute_tool(
                "read-neo4j-cypher",
                {"query": _FINDINGS_QUERY, "params": {"ids": assessment_ids}},
            )

            # Fetch per-assessment verdicts/confidence for aggregation
            meta_result = self.execute_tool(
                "read-neo4j-cypher",
                {"query": _ASSESSMENT_META_QUERY, "params": {"ids": assessment_ids}},
            )

            rows = findings_result.get("rows", [])
            meta_rows = meta_result.get("rows", [])

            findings: list[dict] = []
            for row in rows:
                findings.append({
                    "finding_type":  row.get("finding_type", "information"),
                    "severity":      row.get("severity", "INFO"),
                    "description":   row.get("description", ""),
                    "pattern_name":  row.get("pattern_name"),
                    "regulation_id": row.get("regulation_id", ""),
                    "assessment_id": row.get("assessment_id", ""),
                })

            # Aggregate verdict (worst-case) and confidence (average)
            best_verdict = "INFORMATIONAL"
            confidences: list[float] = []
            for m in meta_rows:
                v = (m.get("verdict") or "INFORMATIONAL").upper()
                if VERDICT_PRIORITY.get(v, 0) > VERDICT_PRIORITY.get(best_verdict, 0):
                    best_verdict = v
                if m.get("confidence") is not None:
                    confidences.append(float(m["confidence"]))

            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.5
            logger.info(
                "Fetched %d findings from %d assessments; aggregated verdict=%s confidence=%.2f",
                len(findings), len(assessment_ids), best_verdict, avg_confidence,
            )
            return findings, best_verdict, avg_confidence

        except Exception as e:
            logger.warning("Could not fetch assessment findings from graph: %s", e)
            return [], "INFORMATIONAL", 0.5

    def _route(self, question: str) -> dict:
        """Classify question intent using a single Claude call."""
        resp = call_claude_with_retry(
            self.client,
            label="routing",
            model=MODEL_FAST,
            max_tokens=ROUTING_MAX_TOKENS,
            system=[{"type": "text", "text": ROUTING_SYSTEM,
                     "cache_control": CACHE_CONTROL_EPHEMERAL}],
            messages=[{"role": "user", "content": question}],
            temperature=TEMPERATURE,
        )
        text = strip_fences(resp.content[0].text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Routing returned non-JSON: %s", text)
            return {
                "intents": ["investigation"],
                "entity_ids": [],
                "entity_types": [],
                "regulations": [],
                "run_anomaly_check": False,
                "needs_compliance_agent": True,
                "needs_investigation_agent": True,
            }

    def _synthesise(
        self,
        session_id: str,
        question: str,
        routing: dict,
        compliance_result: Any | None,
        investigation_result: Any | None,
    ) -> InvestigationResponse:
        """Merge specialist outputs into a single InvestigationResponse."""

        # Build context for synthesis
        reg_list = ", ".join(self._graph_regulation_ids) if self._graph_regulation_ids else "unknown"
        context_parts: list[str] = [
            f"AVAILABLE REGULATIONS (these are the only regulations in the knowledge graph): {reg_list}\n",
            f"Original question: {question}\n",
        ]
        all_cypher: list[dict] = []
        all_findings: list[dict] = []
        verdict = "INFORMATIONAL"
        confidence = 0.5
        cited_sections: list[dict] = []
        cited_chunks: list[dict] = []

        # Resolve entity/regulation context for graph enrichment (needed by both agents)
        _ent_id   = (routing.get("entity_ids")   or [""])[0]
        _ent_type = (routing.get("entity_types")  or [""])[0]
        _reg_id   = (routing.get("regulations")   or [""])[0]

        if compliance_result:
            for cypher in compliance_result.cypher_used:
                all_cypher.append({"tool": "read-neo4j-cypher", "cypher": cypher})

            # Preferred: fetch all findings from Neo4j using the persisted assessment IDs.
            # This captures findings from every persist_assessment call (one per regulation).
            # Falls back to in-memory persisted_findings, then threshold_breaches.
            if compliance_result.assessment_ids:
                neo4j_findings, verdict, confidence = self._fetch_assessment_findings(
                    compliance_result.assessment_ids
                )
                if neo4j_findings:
                    for f in neo4j_findings:
                        f.setdefault("entity_id",   _ent_id)
                        f.setdefault("entity_type", _ent_type)
                    all_findings.extend(neo4j_findings)
                else:
                    # Neo4j fetch returned nothing (e.g. graph unavailable) — fall back
                    verdict = compliance_result.verdict
                    confidence = compliance_result.confidence
                    all_findings.extend(compliance_result.persisted_findings)
            elif compliance_result.persisted_findings:
                verdict = compliance_result.verdict
                confidence = compliance_result.confidence
                breaches = compliance_result.threshold_breaches or []
                for i, f in enumerate(compliance_result.persisted_findings):
                    enriched = dict(f)
                    enriched.setdefault("entity_id",   _ent_id)
                    enriched.setdefault("entity_type", _ent_type)
                    enriched.setdefault("regulation_id", _reg_id)
                    if i < len(breaches):
                        enriched.setdefault("threshold_id", breaches[i].get("threshold_id", ""))
                    all_findings.append(enriched)
            else:
                verdict = compliance_result.verdict
                confidence = compliance_result.confidence
                for breach in compliance_result.threshold_breaches:
                    tid = breach.get("threshold_id", "unknown")
                    description, severity = _THRESHOLD_META.get(
                        tid,
                        (f"Threshold breached: {tid}", "HIGH"),
                    )
                    all_findings.append({
                        "finding_type":  "compliance_breach",
                        "severity":      severity,
                        "description":   description,
                        "pattern_name":  None,
                        "entity_id":     _ent_id,
                        "entity_type":   _ent_type,
                        "regulation_id": _reg_id,
                        "threshold_id":  tid,
                    })

            # Sort findings HIGH → MEDIUM → LOW → INFO for synthesis context
            all_findings.sort(key=lambda f: SEV_ORDER.get(f.get("severity", "INFO"), 3))

            findings_lines = "\n".join(
                f"  [{f.get('severity', 'INFO')}] {f.get('description', '')}"
                for f in all_findings
            )
            context_parts.append(
                f"Compliance agent result:\n"
                f"  Verdict: {verdict}\n"
                f"  Confidence: {confidence}\n"
                f"  Assessments: {compliance_result.assessment_ids or [compliance_result.assessment_id]}\n"
                f"  Requirements checked: {compliance_result.requirement_ids}\n"
                f"  Threshold breaches: {compliance_result.threshold_breaches}\n"
                f"  Reasoning steps: {compliance_result.reasoning_steps}\n"
                f"FINDINGS (use these exactly — do not re-assess severity):\n{findings_lines}\n"
            )

            # Walk each assessment back to its cited sections and chunks via trace_evidence.
            # Deduplicate by ID so repeated section/chunk references collapse.
            _seen_sec_ids: set[str] = set()
            _seen_chk_ids: set[str] = set()
            for _aid in (compliance_result.assessment_ids or []):
                try:
                    _ev = self.execute_tool("trace_evidence", {"assessment_id": _aid})
                    for _sec in _ev.get("cited_sections") or []:
                        _sid = _sec.get("section_id")
                        if _sid and _sid not in _seen_sec_ids:
                            _seen_sec_ids.add(_sid)
                            cited_sections.append(_sec)
                    for _chk in _ev.get("cited_chunks") or []:
                        _cid = _chk.get("chunk_id")
                        if _cid and _cid not in _seen_chk_ids:
                            _seen_chk_ids.add(_cid)
                            cited_chunks.append(_chk)
                except Exception as _e:
                    logger.warning("trace_evidence failed for %s: %s", _aid, _e)

        if investigation_result:
            context_parts.append(
                f"Investigation agent result:\n"
                f"  Entity: {investigation_result.entity_id}\n"
                f"  Risk signals: {investigation_result.risk_signals}\n"
                f"  Connections: {investigation_result.connections}\n"
            )
            for i, cypher in enumerate(investigation_result.cypher_used):
                all_cypher.append({"tool": "read-neo4j-cypher", "cypher": cypher})
            _inv_ent_id   = investigation_result.entity_id or _ent_id
            _inv_ent_type = investigation_result.entity_type or _ent_type
            for signal in investigation_result.risk_signals:
                severity = "HIGH" if "[HIGH]" in signal else "MEDIUM" if "[MEDIUM]" in signal else "LOW"
                pat_m = re.search(r"pattern=([a-z_]+):", signal, re.IGNORECASE)
                pattern_name = pat_m.group(1) if pat_m and pat_m.group(1).lower() != "none" else None
                description = re.sub(r"pattern=[a-z_]+:\s*", "", signal, flags=re.IGNORECASE).strip()
                all_findings.append({
                    "finding_type":  "risk_signal",
                    "severity":      severity,
                    "description":   description,
                    "pattern_name":  pattern_name,
                    "entity_id":     _inv_ent_id,
                    "entity_type":   _inv_ent_type,
                    "regulation_id": _reg_id,
                })
            for pat in investigation_result.anomaly_patterns:
                all_findings.append({
                    "finding_type":  "anomaly_pattern",
                    "severity":      pat.get("severity", "MEDIUM"),
                    "description":   pat.get("description", ""),
                    "pattern_name":  pat.get("pattern_name"),
                    "entity_id":     _inv_ent_id,
                    "entity_type":   _inv_ent_type,
                    "regulation_id": _reg_id,
                })
            # Re-sort after adding investigation findings
            all_findings.sort(key=lambda f: SEV_ORDER.get(f.get("severity", "INFO"), 3))
            inv_findings_lines = "\n".join(
                f"  [{f.get('severity', 'INFO')}] {f.get('description', '')}"
                for f in all_findings
                if f.get("finding_type") == "risk_signal"
            )
            context_parts.append(
                f"FINDINGS from investigation (use these exactly):\n{inv_findings_lines}\n"
            )

        # Enrich findings that have a threshold link but no pattern_name yet.
        # Covers compliance_breach (has threshold_id field), Neo4j-fetched findings,
        # and risk_signal findings where the description text cites a threshold ID.
        _threshold_re = re.compile(r"\b((?:APG|APS)-\d+-THR-\d+)\b")
        for _f in all_findings:
            if _f.get("pattern_name"):
                continue
            _tid = _f.get("threshold_id") or ""
            if not _tid:
                _m = _threshold_re.search(_f.get("description", ""))
                _tid = _m.group(1) if _m else ""
            if _tid:
                _f["pattern_name"] = THRESHOLD_TO_PATTERN.get(_tid)

        if not compliance_result and not investigation_result:
            return InvestigationResponse(
                session_id=session_id,
                question=question,
                answer="Unable to process the question. Please try again with a specific entity ID.",
                verdict="INFORMATIONAL",
                routing=routing,
            )

        # Final synthesis via Claude
        synthesis_response = call_claude_with_retry(
            self.client,
            label="synthesis",
            model=self.model,
            max_tokens=SYNTHESIS_MAX_TOKENS,
            system=[{"type": "text", "text": SYNTHESIS_SYSTEM,
                     "cache_control": CACHE_CONTROL_EPHEMERAL}],
            messages=[{"role": "user", "content": "\n".join(context_parts)}],
            temperature=TEMPERATURE,
        )
        answer = synthesis_response.content[0].text.strip()

        # Split answer from recommended next steps at the delimiter
        _STEPS_DELIMITER = "RECOMMENDED NEXT STEPS:"
        if _STEPS_DELIMITER in answer:
            _answer_part, _steps_part = answer.split(_STEPS_DELIMITER, 1)
            answer = _answer_part.strip()
            steps = re.findall(r"^\d+\.\s+(.+)$", _steps_part, re.MULTILINE)
        else:
            steps = re.findall(r"^\d+\.\s+(.+)$", answer, re.MULTILINE)

        return InvestigationResponse(
            session_id=session_id,
            question=question,
            answer=answer,
            verdict=verdict,
            confidence=confidence,
            routing=routing,
            findings=all_findings,
            cypher_used=all_cypher,
            evidence=[],
            cited_sections=cited_sections,
            cited_chunks=cited_chunks,
            recommended_next_steps=steps[:5],
            assessment_id=compliance_result.assessment_id if compliance_result else None,
            assessment_ids=compliance_result.assessment_ids if compliance_result else [],
        )
