"""
Neo4j AuraDB connection manager.

Loads credentials from environment variables:
  NEO4J_URI       - AuraDB connection URI (neo4j+s://...)
  NEO4J_USERNAME  - Database username (default: neo4j)
  NEO4J_PASSWORD  - Database password
"""

import os
import logging
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError

logger = logging.getLogger(__name__)


class Neo4jConnection:
    """
    Manages a connection to a Neo4j AuraDB instance.

    Usage (context manager):
        with Neo4jConnection() as conn:
            results = conn.run_query("MATCH (n) RETURN count(n) AS total")

    Usage (manual):
        conn = Neo4jConnection()
        conn.connect()
        results = conn.run_query(cypher, params)
        conn.close()
    """

    def __init__(
        self,
        uri: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._uri = uri or os.getenv("NEO4J_URI")
        self._username = username or os.getenv("NEO4J_USERNAME", "neo4j")
        self._password = password or os.getenv("NEO4J_PASSWORD")
        self._driver = None

        if not all([self._uri, self._username, self._password]):
            raise ValueError(
                "Neo4j credentials are incomplete. "
                "Set NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD in your .env file."
            )

    def connect(self) -> "Neo4jConnection":
        """Open the driver and verify connectivity."""
        try:
            self._driver = GraphDatabase.driver(
                self._uri,
                auth=(self._username, self._password),
            )
            self._driver.verify_connectivity()
            logger.info("Connected to Neo4j at %s", self._uri)
        except AuthError as e:
            raise RuntimeError(f"Neo4j authentication failed: {e}") from e
        except ServiceUnavailable as e:
            raise RuntimeError(f"Neo4j instance unreachable at {self._uri}: {e}") from e
        return self

    def close(self) -> None:
        """Close the driver and release resources."""
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("Neo4j connection closed.")

    def run_query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict]:
        """
        Execute a Cypher query and return results as a list of dicts.

        Args:
            cypher: Cypher query string.
            params: Optional parameter dictionary for parameterised queries.

        Returns:
            List of result records as plain dicts.
        """
        if self._driver is None:
            raise RuntimeError("Call connect() before run_query().")

        params = params or {}
        with self._driver.session() as session:
            result = session.run(cypher, params)
            return [record.data() for record in result]

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "Neo4jConnection":
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
