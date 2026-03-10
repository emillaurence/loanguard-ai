"""
Unit tests for src/agent/compliance_agent.py

Anthropic client is fully mocked — no API calls made.
The new ComplianceAgent takes (tools, execute_tool_fn) as constructor args
and returns a ComplianceResult dataclass from .run().
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call

from src.agent.compliance_agent import ComplianceAgent
from src.agent.utils import extract_text
from src.mcp.schema import ComplianceResult


# ---------------------------------------------------------------------------
# Helpers to build mock Claude responses
# ---------------------------------------------------------------------------


def _make_text_response(text: str) -> MagicMock:
    """Simulate a Claude end_turn response with a single text block."""
    block = MagicMock()
    block.type = "text"
    block.text = text

    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def _make_tool_use_response(tool_name: str, tool_input: dict, tool_id: str = "tu_001") -> MagicMock:
    """Simulate a Claude tool_use response."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    block.id = tool_id

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_execute_tool():
    """A callable that stands in for the execute_tool dispatcher."""
    fn = MagicMock(return_value={"rows": []})
    return fn


@pytest.fixture
def mock_anthropic_client():
    with patch("src.agent.compliance_agent.anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        yield mock_client


# ---------------------------------------------------------------------------
# Basic agent behaviour
# ---------------------------------------------------------------------------


class TestComplianceAgentRun:
    def test_returns_result_on_end_turn(self, mock_execute_tool, mock_anthropic_client):
        mock_anthropic_client.messages.create.return_value = _make_text_response(
            "No compliance issues found.\n"
            "VERDICT: COMPLIANT\nCONFIDENCE: 0.9\n"
            "REQUIREMENTS CHECKED: none\nTHRESHOLDS BREACHED: none\n"
        )
        agent = ComplianceAgent(tools=[], execute_tool_fn=mock_execute_tool)
        result = agent.run("Are there any compliance flags?")
        assert isinstance(result, ComplianceResult)
        assert result.verdict == "COMPLIANT"
        assert result.confidence == 0.9

    def test_tool_use_loop_then_end_turn(self, mock_execute_tool, mock_anthropic_client):
        """Agent should call a tool, inject result, then return final text."""
        tool_response = _make_tool_use_response(
            tool_name="read-neo4j-cypher",
            tool_input={"query": "MATCH (l:LoanApplication) RETURN l LIMIT 1"},
        )
        final_response = _make_text_response(
            "Found 2 HIGH severity flags.\n"
            "VERDICT: NON_COMPLIANT\nCONFIDENCE: 0.85\n"
            "REQUIREMENTS CHECKED: APG-223-REQ-040\nTHRESHOLDS BREACHED: APG-223-THR-008\n"
        )
        mock_anthropic_client.messages.create.side_effect = [tool_response, final_response]

        agent = ComplianceAgent(tools=[], execute_tool_fn=mock_execute_tool)
        result = agent.run("Show me high severity compliance flags.")

        assert isinstance(result, ComplianceResult)
        assert result.verdict == "NON_COMPLIANT"
        assert mock_anthropic_client.messages.create.call_count == 2
        mock_execute_tool.assert_called_once_with(
            "read-neo4j-cypher",
            {"query": "MATCH (l:LoanApplication) RETURN l LIMIT 1"},
        )

    def test_multiple_tool_calls_in_one_turn(self, mock_execute_tool, mock_anthropic_client):
        """Agent should handle multiple tool_use blocks in a single response."""
        block1 = MagicMock()
        block1.type = "tool_use"
        block1.name = "read-neo4j-cypher"
        block1.input = {"query": "MATCH (b:Borrower) RETURN b LIMIT 5"}
        block1.id = "tu_001"

        block2 = MagicMock()
        block2.type = "tool_use"
        block2.name = "traverse_compliance_path"
        block2.input = {"entity_id": "LOAN-0002", "entity_type": "LoanApplication"}
        block2.id = "tu_002"

        multi_tool_response = MagicMock()
        multi_tool_response.stop_reason = "tool_use"
        multi_tool_response.content = [block1, block2]

        final_response = _make_text_response(
            "Analysis complete.\nVERDICT: REQUIRES_REVIEW\nCONFIDENCE: 0.75\n"
            "REQUIREMENTS CHECKED: none\nTHRESHOLDS BREACHED: none\n"
        )
        mock_anthropic_client.messages.create.side_effect = [multi_tool_response, final_response]

        agent = ComplianceAgent(tools=[], execute_tool_fn=mock_execute_tool)
        result = agent.run("Give me a full compliance overview.")

        assert isinstance(result, ComplianceResult)
        assert result.verdict == "REQUIRES_REVIEW"
        assert mock_execute_tool.call_count == 2

    def test_returns_result_on_max_iterations(self, mock_execute_tool, mock_anthropic_client):
        """Agent should not loop infinitely — stop at MAX_ITERATIONS."""
        tool_response = _make_tool_use_response(
            "read-neo4j-cypher", {"query": "MATCH (n) RETURN n LIMIT 1"}
        )
        mock_anthropic_client.messages.create.return_value = tool_response

        with patch("src.agent.compliance_agent.MAX_ITERATIONS", 3):
            agent = ComplianceAgent(tools=[], execute_tool_fn=mock_execute_tool)
            result = agent.run("Loop me forever.")

        assert mock_anthropic_client.messages.create.call_count == 3
        assert isinstance(result, ComplianceResult)

    def test_cypher_queries_tracked(self, mock_execute_tool, mock_anthropic_client):
        """Cypher queries from read-neo4j-cypher calls should be recorded."""
        cypher = "MATCH (l:LoanApplication {loan_id: 'LOAN-0002'}) RETURN l.lvr"
        tool_response = _make_tool_use_response(
            "read-neo4j-cypher", {"query": cypher}
        )
        final_response = _make_text_response(
            "Done.\nVERDICT: COMPLIANT\nCONFIDENCE: 0.8\n"
            "REQUIREMENTS CHECKED: none\nTHRESHOLDS BREACHED: none\n"
        )
        mock_anthropic_client.messages.create.side_effect = [tool_response, final_response]

        agent = ComplianceAgent(tools=[], execute_tool_fn=mock_execute_tool)
        result = agent.run("Check LVR.")

        assert cypher in result.cypher_used


# ---------------------------------------------------------------------------
# Helper methods
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_extracts_first_text_block(self):
        block = MagicMock()
        block.text = "Hello compliance world"
        response = MagicMock()
        response.content = [block]
        assert extract_text(response) == "Hello compliance world"

    def test_returns_empty_if_no_text(self):
        block = MagicMock(spec=[])  # no .text attribute
        response = MagicMock()
        response.content = [block]
        result = extract_text(response)
        assert result == ""


class TestParseResult:
    def test_parses_verdict_and_confidence(self):
        text = (
            "Assessment complete.\n"
            "VERDICT: NON_COMPLIANT\n"
            "CONFIDENCE: 0.92\n"
            "REQUIREMENTS CHECKED: APG-223-REQ-040, APG-223-REQ-015\n"
            "THRESHOLDS BREACHED: APG-223-THR-008\n"
            "RECOMMENDED NEXT STEPS:\n1. Escalate to credit risk.\n2. Request LMI policy.\n"
        )
        result = ComplianceAgent._parse_result(text, ["MATCH (n) RETURN n"])
        assert result.verdict == "NON_COMPLIANT"
        assert result.confidence == 0.92
        assert "APG-223-REQ-040" in result.requirement_ids
        assert "APG-223-THR-008" in [t["threshold_id"] for t in result.threshold_breaches]
        assert len(result.cypher_used) == 1

    def test_defaults_on_missing_fields(self):
        result = ComplianceAgent._parse_result("No structured output.", [])
        assert result.verdict == "INFORMATIONAL"
        assert result.confidence == 0.5
        assert result.requirement_ids == []
        assert result.threshold_breaches == []
