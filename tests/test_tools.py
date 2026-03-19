"""
Unit tests for src/mcp/tools_impl.py — detect_graph_anomalies entity_id scoping.

Neo4jConnection is fully mocked — no database credentials required.
"""

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_conn(rows=None):
    """Return a mock Neo4jConnection whose run_query captures call args."""
    conn = MagicMock()
    conn.run_query.return_value = rows or []
    return conn


# ---------------------------------------------------------------------------
# detect_graph_anomalies — entity_id scoping
# ---------------------------------------------------------------------------

class TestDetectGraphAnomaliesEntityScoping:
    """Verify that entity_id causes the Cypher to be scoped to the given entity."""

    def _run(self, pattern_names, entity_id, mock_conn):
        with patch("src.mcp.tools_impl._get_conn", return_value=mock_conn):
            from src.mcp.tools_impl import detect_graph_anomalies
            return detect_graph_anomalies(pattern_names, entity_id=entity_id)

    def test_transaction_structuring_scoped_to_account(self):
        conn = _make_mock_conn()
        self._run(["transaction_structuring"], "ACC-0610", conn)

        cypher, params = conn.run_query.call_args[0]
        assert "{account_id: $eid}" in cypher
        assert params.get("eid") == "ACC-0610"

    def test_layered_ownership_scoped_to_borrower(self):
        conn = _make_mock_conn()
        self._run(["layered_ownership"], "BRW-0582", conn)

        cypher, params = conn.run_query.call_args[0]
        assert "{borrower_id: $eid}" in cypher
        assert params.get("eid") == "BRW-0582"

    def test_high_lvr_loans_scoped_to_loan(self):
        conn = _make_mock_conn()
        self._run(["high_lvr_loans"], "LOAN-0001", conn)

        cypher, params = conn.run_query.call_args[0]
        assert "{loan_id: $eid}" in cypher
        assert params.get("eid") == "LOAN-0001"

    def test_no_entity_id_runs_globally(self):
        conn = _make_mock_conn()
        self._run(["transaction_structuring"], "", conn)

        cypher, params = conn.run_query.call_args[0]
        assert "{account_id: $eid}" not in cypher
        assert "eid" not in params

    def test_unknown_pattern_returns_error(self):
        conn = _make_mock_conn()
        result = self._run(["nonexistent_pattern"], "", conn)

        assert "error" in result
        assert "nonexistent_pattern" in result["error"]
        conn.run_query.assert_not_called()

    def test_returns_correct_structure(self):
        conn = _make_mock_conn(rows=[])
        result = self._run(["transaction_structuring"], "ACC-0610", conn)

        assert "patterns_run" in result
        assert "total_findings" in result
        assert "results" in result
        assert result["patterns_run"] == 1
