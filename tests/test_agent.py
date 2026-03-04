"""
Unit tests for src/agent/compliance_agent.py

Anthropic client is fully mocked — no API calls made.
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call

from src.agent.compliance_agent import ComplianceAgent


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
def mock_neo4j_conn():
    return MagicMock()


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
    def test_returns_text_on_end_turn(self, mock_neo4j_conn, mock_anthropic_client):
        mock_anthropic_client.messages.create.return_value = _make_text_response(
            "No compliance issues found."
        )
        agent = ComplianceAgent(neo4j_conn=mock_neo4j_conn)
        result = agent.run("Are there any compliance flags?")
        assert result == "No compliance issues found."

    def test_tool_use_loop_then_end_turn(self, mock_neo4j_conn, mock_anthropic_client):
        """Agent should call a tool, inject result, then return final text."""
        tool_response = _make_tool_use_response(
            tool_name="get_compliance_flags",
            tool_input={"severity": "HIGH"},
        )
        final_response = _make_text_response("Found 2 HIGH severity flags.")

        mock_anthropic_client.messages.create.side_effect = [tool_response, final_response]

        # Mock the execute_tool function
        with patch("src.agent.compliance_agent.execute_tool") as mock_execute:
            mock_execute.return_value = json.dumps([{"flag_id": "F-001", "severity": "HIGH"}])
            agent = ComplianceAgent(neo4j_conn=mock_neo4j_conn)
            result = agent.run("Show me high severity compliance flags.")

        assert result == "Found 2 HIGH severity flags."
        assert mock_anthropic_client.messages.create.call_count == 2
        mock_execute.assert_called_once_with("get_compliance_flags", {"severity": "HIGH"}, mock_neo4j_conn)

    def test_multiple_tool_calls_in_one_turn(self, mock_neo4j_conn, mock_anthropic_client):
        """Agent should handle multiple tool_use blocks in a single response."""
        block1 = MagicMock()
        block1.type = "tool_use"
        block1.name = "get_loan_accounts"
        block1.input = {}
        block1.id = "tu_001"

        block2 = MagicMock()
        block2.type = "tool_use"
        block2.name = "get_compliance_flags"
        block2.input = {}
        block2.id = "tu_002"

        multi_tool_response = MagicMock()
        multi_tool_response.stop_reason = "tool_use"
        multi_tool_response.content = [block1, block2]

        final_response = _make_text_response("Analysis complete.")
        mock_anthropic_client.messages.create.side_effect = [multi_tool_response, final_response]

        with patch("src.agent.compliance_agent.execute_tool") as mock_execute:
            mock_execute.return_value = json.dumps([])
            agent = ComplianceAgent(neo4j_conn=mock_neo4j_conn)
            result = agent.run("Give me a full compliance overview.")

        assert result == "Analysis complete."
        assert mock_execute.call_count == 2

    def test_returns_last_text_on_max_iterations(self, mock_neo4j_conn, mock_anthropic_client):
        """Agent should not loop infinitely — stop at MAX_ITERATIONS."""
        tool_response = _make_tool_use_response("get_loan_accounts", {})
        # Always return tool_use to exhaust iterations
        mock_anthropic_client.messages.create.return_value = tool_response

        with patch("src.agent.compliance_agent.execute_tool") as mock_execute:
            mock_execute.return_value = json.dumps([])
            with patch("src.agent.compliance_agent.MAX_ITERATIONS", 3):
                agent = ComplianceAgent(neo4j_conn=mock_neo4j_conn)
                agent.run("Loop me forever.")

        assert mock_anthropic_client.messages.create.call_count == 3


# ---------------------------------------------------------------------------
# Helper method
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_extracts_first_text_block(self):
        block = MagicMock()
        block.text = "Hello compliance world"
        response = MagicMock()
        response.content = [block]
        assert ComplianceAgent._extract_text(response) == "Hello compliance world"

    def test_returns_fallback_if_no_text(self):
        block = MagicMock(spec=[])  # no .text attribute
        response = MagicMock()
        response.content = [block]
        result = ComplianceAgent._extract_text(response)
        assert "No text response" in result
