"""
Orchestrator — routes user questions to specialist agents and synthesises responses.

Flow:
  1. route(question) → routing plan (intent classification via Claude)
  2. Dispatch to ComplianceAgent and/or InvestigationAgent in parallel (future)
  3. AnswerSynthesisAgent merges outputs → InvestigationResponse

The orchestrator holds references to both MCP tool lists and the shared
execute_tool dispatcher, injecting them into each specialist agent.

Model: claude-sonnet-4-6  temperature=0
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import anthropic

from src.agent.compliance_agent import ComplianceAgent
from src.agent.investigation_agent import InvestigationAgent
from src.mcp.schema import GRAPH_SCHEMA_HINT, InvestigationResponse

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

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
2. 3–5 concrete recommended next steps as a numbered list.

Do NOT reproduce or summarise the findings list — findings are displayed
separately in the UI. Focus only on the answer and next steps.

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
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
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

    def _route(self, question: str) -> dict:
        """Classify question intent using a single Claude call."""
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=512,
            system=[{"type": "text", "text": ROUTING_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": question}],
            temperature=0,
        )
        text = resp.content[0].text.strip()
        try:
            # Strip any accidental markdown fences
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
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

        if compliance_result:
            verdict = compliance_result.verdict
            confidence = compliance_result.confidence
            for cypher in compliance_result.cypher_used:
                all_cypher.append({"tool": "read-neo4j-cypher", "cypher": cypher})

            # Resolve entity/regulation context for graph enrichment
            _ent_id   = (routing.get("entity_ids")   or [""])[0]
            _ent_type = (routing.get("entity_types")  or [""])[0]
            _reg_id   = (routing.get("regulations")   or [""])[0]

            # Primary source: findings Claude persisted to Layer 3 (rich descriptions,
            # agent-assessed severities). Fall back to threshold_breaches conversion
            # only if persist_assessment was never called or returned no findings.
            if compliance_result.persisted_findings:
                breaches = compliance_result.threshold_breaches or []
                for i, f in enumerate(compliance_result.persisted_findings):
                    enriched = dict(f)
                    enriched.setdefault("entity_id",   _ent_id)
                    enriched.setdefault("entity_type", _ent_type)
                    enriched.setdefault("regulation_id", _reg_id)
                    # Pair threshold breach by position (best-effort)
                    if i < len(breaches):
                        enriched.setdefault("threshold_id", breaches[i].get("threshold_id", ""))
                    all_findings.append(enriched)
            else:
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
            _sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
            all_findings.sort(key=lambda f: _sev_order.get(f.get("severity", "INFO"), 3))

            findings_lines = "\n".join(
                f"  [{f.get('severity', 'INFO')}] {f.get('description', '')}"
                for f in all_findings
            )
            context_parts.append(
                f"Compliance agent result:\n"
                f"  Verdict: {compliance_result.verdict}\n"
                f"  Confidence: {compliance_result.confidence}\n"
                f"  Requirements checked: {compliance_result.requirement_ids}\n"
                f"  Threshold breaches: {compliance_result.threshold_breaches}\n"
                f"  Reasoning steps: {compliance_result.reasoning_steps}\n"
                f"FINDINGS (use these exactly — do not re-assess severity):\n{findings_lines}\n"
            )

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
                all_findings.append({
                    "finding_type":  "risk_signal",
                    "severity":      severity,
                    "description":   signal,
                    "pattern_name":  None,
                    "entity_id":     _inv_ent_id,
                    "entity_type":   _inv_ent_type,
                    "regulation_id": _reg_id,
                })
            # Re-sort after adding investigation findings
            _sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
            all_findings.sort(key=lambda f: _sev_order.get(f.get("severity", "INFO"), 3))
            inv_findings_lines = "\n".join(
                f"  [{f.get('severity', 'INFO')}] {f.get('description', '')}"
                for f in all_findings
                if f.get("finding_type") == "risk_signal"
            )
            context_parts.append(
                f"FINDINGS from investigation (use these exactly):\n{inv_findings_lines}\n"
            )

        if not compliance_result and not investigation_result:
            return InvestigationResponse(
                session_id=session_id,
                question=question,
                answer="Unable to process the question. Please try again with a specific entity ID.",
                verdict="INFORMATIONAL",
                routing=routing,
            )

        # Final synthesis via Claude
        synthesis_response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=[{"type": "text", "text": SYNTHESIS_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": "\n".join(context_parts)}],
            temperature=0,
        )
        answer = synthesis_response.content[0].text.strip()

        # Extract recommended next steps from answer
        import re
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
            cited_chunks=[],
            recommended_next_steps=steps[:5],
            assessment_id=compliance_result.assessment_id if compliance_result else None,
        )
