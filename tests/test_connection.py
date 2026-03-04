"""
Unit tests for src/graph/connection.py

Neo4j driver is fully mocked — no AuraDB instance required.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.graph.connection import Neo4jConnection


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def env_vars(monkeypatch):
    """Inject required environment variables."""
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://test.databases.neo4j.io")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-password")


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestNeo4jConnectionInit:
    def test_loads_credentials_from_env(self, env_vars):
        conn = Neo4jConnection()
        assert conn._uri == "neo4j+s://test.databases.neo4j.io"
        assert conn._username == "neo4j"
        assert conn._password == "test-password"

    def test_accepts_explicit_credentials(self):
        conn = Neo4jConnection(
            uri="neo4j+s://custom.io",
            username="admin",
            password="secret",
        )
        assert conn._uri == "neo4j+s://custom.io"
        assert conn._username == "admin"

    def test_raises_if_credentials_missing(self, monkeypatch):
        monkeypatch.delenv("NEO4J_URI", raising=False)
        monkeypatch.delenv("NEO4J_USERNAME", raising=False)
        monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
        with pytest.raises(ValueError, match="credentials are incomplete"):
            Neo4jConnection()


# ---------------------------------------------------------------------------
# connect() / close()
# ---------------------------------------------------------------------------


class TestNeo4jConnectionLifecycle:
    @patch("src.graph.connection.GraphDatabase.driver")
    def test_connect_creates_driver_and_verifies(self, mock_driver_cls, env_vars):
        mock_driver = MagicMock()
        mock_driver_cls.return_value = mock_driver

        conn = Neo4jConnection()
        result = conn.connect()

        mock_driver_cls.assert_called_once()
        mock_driver.verify_connectivity.assert_called_once()
        assert result is conn  # fluent interface

    @patch("src.graph.connection.GraphDatabase.driver")
    def test_close_shuts_down_driver(self, mock_driver_cls, env_vars):
        mock_driver = MagicMock()
        mock_driver_cls.return_value = mock_driver

        conn = Neo4jConnection()
        conn.connect()
        conn.close()

        mock_driver.close.assert_called_once()
        assert conn._driver is None

    @patch("src.graph.connection.GraphDatabase.driver")
    def test_context_manager(self, mock_driver_cls, env_vars):
        mock_driver = MagicMock()
        mock_driver_cls.return_value = mock_driver

        with Neo4jConnection() as conn:
            assert conn._driver is not None

        mock_driver.close.assert_called_once()


# ---------------------------------------------------------------------------
# run_query()
# ---------------------------------------------------------------------------


class TestRunQuery:
    @patch("src.graph.connection.GraphDatabase.driver")
    def test_run_query_returns_list_of_dicts(self, mock_driver_cls, env_vars):
        # Mock the session and result
        mock_record = MagicMock()
        mock_record.data.return_value = {"account_id": "LA-001", "balance": 450000}

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.run.return_value = [mock_record]

        mock_driver = MagicMock()
        mock_driver.session.return_value = mock_session
        mock_driver_cls.return_value = mock_driver

        conn = Neo4jConnection()
        conn.connect()
        results = conn.run_query("MATCH (l:LoanAccount) RETURN l.account_id AS account_id")

        assert results == [{"account_id": "LA-001", "balance": 450000}]

    @patch("src.graph.connection.GraphDatabase.driver")
    def test_run_query_raises_if_not_connected(self, mock_driver_cls, env_vars):
        mock_driver_cls.return_value = MagicMock()
        conn = Neo4jConnection()  # no connect()
        with pytest.raises(RuntimeError, match="connect\\(\\)"):
            conn.run_query("MATCH (n) RETURN n")
