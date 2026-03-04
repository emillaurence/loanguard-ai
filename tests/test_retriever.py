"""
Unit tests for src/retriever/graphrag.py

Both Anthropic client and Neo4j connection are fully mocked.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.retriever.graphrag import GraphRAGRetriever


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_neo4j_conn():
    return MagicMock()


@pytest.fixture
def mock_anthropic_client():
    with patch("src.retriever.graphrag.anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        yield mock_client


# ---------------------------------------------------------------------------
# nl_to_cypher
# ---------------------------------------------------------------------------


class TestNlToCypher:
    def test_returns_cypher_string(self, mock_neo4j_conn, mock_anthropic_client):
        block = MagicMock()
        block.text = "MATCH (l:LoanAccount) RETURN l.account_id LIMIT 10"
        mock_anthropic_client.messages.create.return_value = MagicMock(content=[block])

        retriever = GraphRAGRetriever(neo4j_conn=mock_neo4j_conn)
        cypher = retriever.nl_to_cypher("Show me all loan accounts")

        assert "MATCH" in cypher
        assert "LoanAccount" in cypher

    def test_strips_whitespace_from_cypher(self, mock_neo4j_conn, mock_anthropic_client):
        block = MagicMock()
        block.text = "  MATCH (n) RETURN n LIMIT 5  \n"
        mock_anthropic_client.messages.create.return_value = MagicMock(content=[block])

        retriever = GraphRAGRetriever(neo4j_conn=mock_neo4j_conn)
        cypher = retriever.nl_to_cypher("anything")

        assert not cypher.startswith(" ")
        assert not cypher.endswith("\n")


# ---------------------------------------------------------------------------
# retrieve
# ---------------------------------------------------------------------------


class TestRetrieve:
    def test_returns_results_from_neo4j(self, mock_neo4j_conn, mock_anthropic_client):
        # Mock Claude returning Cypher
        cypher_block = MagicMock()
        cypher_block.text = "MATCH (l:LoanAccount) RETURN l LIMIT 5"
        mock_anthropic_client.messages.create.return_value = MagicMock(content=[cypher_block])

        # Mock Neo4j returning records
        mock_neo4j_conn.run_query.return_value = [
            {"account_id": "LA-001", "balance": 450000},
            {"account_id": "LA-002", "balance": 1200000},
        ]

        retriever = GraphRAGRetriever(neo4j_conn=mock_neo4j_conn)
        results = retriever.retrieve("Show me all accounts")

        assert len(results) == 2
        assert results[0]["account_id"] == "LA-001"

    def test_returns_empty_list_on_neo4j_error(self, mock_neo4j_conn, mock_anthropic_client):
        cypher_block = MagicMock()
        cypher_block.text = "MATCH (n) RETURN n"
        mock_anthropic_client.messages.create.return_value = MagicMock(content=[cypher_block])

        mock_neo4j_conn.run_query.side_effect = Exception("Connection error")

        retriever = GraphRAGRetriever(neo4j_conn=mock_neo4j_conn)
        results = retriever.retrieve("Show me data")

        assert results == []


# ---------------------------------------------------------------------------
# format_context_for_claude
# ---------------------------------------------------------------------------


class TestFormatContext:
    def test_formats_results_with_index(self, mock_neo4j_conn, mock_anthropic_client):
        retriever = GraphRAGRetriever(neo4j_conn=mock_neo4j_conn)
        results = [{"account_id": "LA-001"}, {"account_id": "LA-002"}]
        context = retriever.format_context_for_claude(results)

        assert "Retrieved 2 record(s)" in context
        assert "[1]" in context
        assert "[2]" in context
        assert "LA-001" in context

    def test_handles_empty_results(self, mock_neo4j_conn, mock_anthropic_client):
        retriever = GraphRAGRetriever(neo4j_conn=mock_neo4j_conn)
        context = retriever.format_context_for_claude([])
        assert "No relevant records" in context


# ---------------------------------------------------------------------------
# retrieve_and_format (integration of retrieve + format)
# ---------------------------------------------------------------------------


class TestRetrieveAndFormat:
    def test_end_to_end(self, mock_neo4j_conn, mock_anthropic_client):
        cypher_block = MagicMock()
        cypher_block.text = "MATCH (f:ComplianceFlag) RETURN f LIMIT 10"
        mock_anthropic_client.messages.create.return_value = MagicMock(content=[cypher_block])

        mock_neo4j_conn.run_query.return_value = [
            {"flag_id": "F-001", "severity": "HIGH", "reason": "Suspicious transaction pattern"}
        ]

        retriever = GraphRAGRetriever(neo4j_conn=mock_neo4j_conn)
        context = retriever.retrieve_and_format("Show high severity flags")

        assert "F-001" in context
        assert "HIGH" in context
