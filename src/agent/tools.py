"""
Claude tool definitions for the compliance agent.

Each tool maps to a graph query helper in src/graph/queries.py.
The execute_tool() dispatcher routes Claude tool_use calls to the
correct query function and returns a JSON-serialisable result.

# TODO: Extend with additional tools as the graph schema evolves.
"""

from __future__ import annotations
import json
import logging
from typing import Any, TYPE_CHECKING

from src.graph import (
    get_loans_by_risk as get_loan_accounts,
    get_transactions_for_account,
    get_requirements_for_loan_type as get_apra_obligations,
    get_assessments_for_entity as get_compliance_assessments,
    get_assessments_for_entity as get_compliance_flags,
)

if TYPE_CHECKING:
    from src.graph.connection import Neo4jConnection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool Definitions (Anthropic tool_use schema)
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "get_loan_accounts",
        "description": (
            "Retrieve loan account records from the Neo4j knowledge graph. "
            "Returns account IDs, customer IDs, balances, product types, statuses, "
            "and risk ratings. Use this to identify accounts under review."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of accounts to return (default 100).",
                    "default": 100,
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_transactions_for_account",
        "description": (
            "Retrieve recent transactions linked to a specific loan account. "
            "Includes transaction amounts, types, counterparties, timestamps, "
            "and a suspicious flag. Use this to investigate transaction patterns."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "The loan account ID to retrieve transactions for.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of transactions to return (default 50).",
                    "default": 50,
                },
            },
            "required": ["account_id"],
        },
    },
    {
        "name": "get_apra_obligations",
        "description": (
            "Retrieve APRA prudential standard obligations from the regulatory layer "
            "of the knowledge graph. Optionally filter by entity type (e.g. 'ADI'). "
            "Returns obligation IDs, descriptions, severity levels, and the "
            "regulation they belong to."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": (
                        "Optional. Filter obligations by entity type, e.g. 'ADI', "
                        "'insurer', 'superannuation'. Omit to return all obligations."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_compliance_assessments",
        "description": (
            "Retrieve compliance assessment records from the runtime assessment layer. "
            "Optionally filter by loan account ID. Returns outcomes, scores, and "
            "linked obligation IDs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": (
                        "Optional. Loan account ID to filter assessments. "
                        "Omit to return all recent assessments."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_compliance_flags",
        "description": (
            "Retrieve open compliance flags raised against loan accounts. "
            "Optionally filter by severity (HIGH, MEDIUM, LOW). "
            "Use this to surface accounts with active compliance issues."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["HIGH", "MEDIUM", "LOW"],
                    "description": "Optional. Filter flags by severity level.",
                }
            },
            "required": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool Dispatcher
# ---------------------------------------------------------------------------

def execute_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    neo4j_conn: "Neo4jConnection",
) -> str:
    """
    Dispatch a Claude tool_use call to the appropriate graph query.

    Args:
        tool_name:  The name field from the Claude tool_use block.
        tool_input: The input dict from the Claude tool_use block.
        neo4j_conn: An active Neo4jConnection instance.

    Returns:
        JSON-encoded string of query results, ready to be sent back to Claude
        as a tool_result content block.
    """
    logger.info("Executing tool: %s | input: %s", tool_name, tool_input)

    try:
        if tool_name == "get_loan_accounts":
            results = get_loan_accounts(
                neo4j_conn,
                limit=tool_input.get("limit", 100),
            )

        elif tool_name == "get_transactions_for_account":
            results = get_transactions_for_account(
                neo4j_conn,
                account_id=tool_input["account_id"],
                limit=tool_input.get("limit", 50),
            )

        elif tool_name == "get_apra_obligations":
            results = get_apra_obligations(
                neo4j_conn,
                entity_type=tool_input.get("entity_type"),
            )

        elif tool_name == "get_compliance_assessments":
            results = get_compliance_assessments(
                neo4j_conn,
                account_id=tool_input.get("account_id"),
            )

        elif tool_name == "get_compliance_flags":
            results = get_compliance_flags(
                neo4j_conn,
                severity=tool_input.get("severity"),
            )

        else:
            # TODO: Register new tools here as the graph schema grows.
            logger.warning("Unknown tool requested: %s", tool_name)
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        return json.dumps(results, default=str)

    except Exception as e:
        logger.error("Tool execution failed for %s: %s", tool_name, e)
        return json.dumps({"error": str(e)})
