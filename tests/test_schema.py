"""
Unit tests for src/mcp/schema.py

Covers:
  - Verdict and Severity StrEnum: string equality, completeness
  - AnomalyPattern dataclass: required fields, defaults, attribute access
  - ANOMALY_REGISTRY: all entries are AnomalyPattern instances, valid id_keys,
    non-empty cypher/description, unique id_key per pattern, severity validity
  - PATTERN_HINTS: non-empty, contains all pattern names
"""

import pytest
from src.mcp.schema import (
    Verdict,
    Severity,
    AnomalyPattern,
    ANOMALY_REGISTRY,
    PATTERN_HINTS,
)


# ---------------------------------------------------------------------------
# Verdict StrEnum
# ---------------------------------------------------------------------------

class TestVerdict:
    def test_values_equal_strings(self):
        assert Verdict.COMPLIANT == "COMPLIANT"
        assert Verdict.NON_COMPLIANT == "NON_COMPLIANT"
        assert Verdict.REQUIRES_REVIEW == "REQUIRES_REVIEW"
        assert Verdict.ANOMALY_DETECTED == "ANOMALY_DETECTED"
        assert Verdict.INFORMATIONAL == "INFORMATIONAL"

    def test_usable_as_dict_key_with_string(self):
        priority = {
            "NON_COMPLIANT": 4,
            "REQUIRES_REVIEW": 3,
            "ANOMALY_DETECTED": 2,
            "COMPLIANT": 1,
            "INFORMATIONAL": 0,
        }
        assert priority[Verdict.NON_COMPLIANT] == 4
        assert priority[Verdict.INFORMATIONAL] == 0

    def test_all_five_members_present(self):
        members = {v.value for v in Verdict}
        assert members == {
            "COMPLIANT", "NON_COMPLIANT", "REQUIRES_REVIEW",
            "ANOMALY_DETECTED", "INFORMATIONAL",
        }


# ---------------------------------------------------------------------------
# Severity StrEnum
# ---------------------------------------------------------------------------

class TestSeverity:
    def test_values_equal_strings(self):
        assert Severity.HIGH == "HIGH"
        assert Severity.MEDIUM == "MEDIUM"
        assert Severity.LOW == "LOW"
        assert Severity.INFO == "INFO"

    def test_usable_in_sort_key_with_string(self):
        order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
        assert order[Severity.HIGH] == 0
        assert order[Severity.INFO] == 3

    def test_all_four_members_present(self):
        members = {s.value for s in Severity}
        assert members == {"HIGH", "MEDIUM", "LOW", "INFO"}


# ---------------------------------------------------------------------------
# AnomalyPattern dataclass
# ---------------------------------------------------------------------------

class TestAnomalyPattern:
    def test_required_fields(self):
        p = AnomalyPattern(
            description="Test pattern",
            severity=Severity.HIGH,
            cypher="MATCH (n) RETURN n",
            id_key="borrower_id",
        )
        assert p.description == "Test pattern"
        assert p.severity == "HIGH"
        assert p.cypher == "MATCH (n) RETURN n"
        assert p.id_key == "borrower_id"

    def test_defaults(self):
        p = AnomalyPattern(
            description="x", severity=Severity.LOW, cypher="y", id_key="z"
        )
        assert p.params == {}
        assert p.threshold_id == ""

    def test_optional_fields(self):
        p = AnomalyPattern(
            description="x",
            severity=Severity.MEDIUM,
            cypher="y",
            id_key="loan_id",
            params={"min_lvr": 90},
            threshold_id="APG-223-THR-008",
        )
        assert p.params == {"min_lvr": 90}
        assert p.threshold_id == "APG-223-THR-008"

    def test_severity_string_compatibility(self):
        p = AnomalyPattern(description="x", severity="HIGH", cypher="y", id_key="z")
        assert p.severity == Severity.HIGH


# ---------------------------------------------------------------------------
# ANOMALY_REGISTRY
# ---------------------------------------------------------------------------

EXPECTED_PATTERNS = {
    "transaction_structuring",
    "high_lvr_loans",
    "high_risk_industry",
    "layered_ownership",
    "high_risk_jurisdiction",
    "guarantor_concentration",
    "director_concentration",
    "cross_border_opacity",
}

VALID_SEVERITIES = {"HIGH", "MEDIUM", "LOW"}


class TestAnomalyRegistry:
    def test_all_expected_patterns_present(self):
        assert set(ANOMALY_REGISTRY.keys()) == EXPECTED_PATTERNS

    def test_all_entries_are_anomaly_pattern_instances(self):
        for name, pattern in ANOMALY_REGISTRY.items():
            assert isinstance(pattern, AnomalyPattern), (
                f"Registry entry '{name}' is {type(pattern)}, expected AnomalyPattern"
            )

    def test_all_entries_have_non_empty_description(self):
        for name, pattern in ANOMALY_REGISTRY.items():
            assert pattern.description.strip(), f"'{name}' has empty description"

    def test_all_entries_have_non_empty_cypher(self):
        for name, pattern in ANOMALY_REGISTRY.items():
            assert pattern.cypher.strip(), f"'{name}' has empty cypher"

    def test_all_entries_have_non_empty_id_key(self):
        for name, pattern in ANOMALY_REGISTRY.items():
            assert pattern.id_key.strip(), f"'{name}' has empty id_key"

    def test_all_severities_are_valid(self):
        for name, pattern in ANOMALY_REGISTRY.items():
            assert pattern.severity in VALID_SEVERITIES, (
                f"'{name}' has invalid severity '{pattern.severity}'"
            )

    def test_transaction_structuring_id_key(self):
        assert ANOMALY_REGISTRY["transaction_structuring"].id_key == "target_account"

    def test_high_lvr_loans_id_key(self):
        assert ANOMALY_REGISTRY["high_lvr_loans"].id_key == "loan_id"

    def test_borrower_patterns_id_key(self):
        for name in ("high_risk_industry", "high_risk_jurisdiction", "guarantor_concentration"):
            assert ANOMALY_REGISTRY[name].id_key == "borrower_id", (
                f"'{name}' expected id_key='borrower_id'"
            )

    def test_layered_ownership_id_key(self):
        assert ANOMALY_REGISTRY["layered_ownership"].id_key == "ultimate_owner_id"

    def test_high_lvr_threshold_id(self):
        assert ANOMALY_REGISTRY["high_lvr_loans"].threshold_id == "APG-223-THR-008"

    def test_cypher_contains_return(self):
        for name, pattern in ANOMALY_REGISTRY.items():
            assert "RETURN" in pattern.cypher.upper(), (
                f"'{name}' cypher missing RETURN clause"
            )

    def test_cypher_contains_limit_or_order(self):
        # All patterns should either LIMIT or ORDER to avoid unbounded results
        for name, pattern in ANOMALY_REGISTRY.items():
            cypher_upper = pattern.cypher.upper()
            assert "LIMIT" in cypher_upper or "ORDER" in cypher_upper, (
                f"'{name}' cypher has no LIMIT or ORDER BY"
            )

    def test_all_entries_have_entity_scoping_fields(self):
        # Either all three scoping fields are set, or all three are empty (global-only pattern).
        for name, pattern in ANOMALY_REGISTRY.items():
            fields = (pattern.entity_label, pattern.entity_node_alias, pattern.entity_id_field)
            all_set   = all(f.strip() for f in fields)
            all_empty = not any(f.strip() for f in fields)
            assert all_set or all_empty, (
                f"'{name}' has partial entity scoping fields — set all three or leave all empty"
            )

    def test_entity_scoping_spot_checks(self):
        ts = ANOMALY_REGISTRY["transaction_structuring"]
        assert ts.entity_label == "BankAccount"
        assert ts.entity_node_alias == "target"
        assert ts.entity_id_field == "account_id"

        lo = ANOMALY_REGISTRY["layered_ownership"]
        assert lo.entity_label == "Borrower"
        assert lo.entity_node_alias == "owner"
        assert lo.entity_id_field == "borrower_id"

    def test_entity_node_alias_present_in_cypher(self):
        # The alias must appear in the Cypher so the generic filter can inject it.
        # Global-only patterns (empty alias) are skipped.
        for name, pattern in ANOMALY_REGISTRY.items():
            if not pattern.entity_node_alias:
                continue  # global-only pattern — no node injection
            needle = f"({pattern.entity_node_alias}:{pattern.entity_label})"
            assert needle in pattern.cypher, (
                f"'{name}' cypher missing expected node pattern '{needle}'"
            )


# ---------------------------------------------------------------------------
# PATTERN_HINTS
# ---------------------------------------------------------------------------

class TestPatternHints:
    def test_non_empty(self):
        assert PATTERN_HINTS.strip()

    def test_contains_all_pattern_names(self):
        for name in EXPECTED_PATTERNS:
            assert name in PATTERN_HINTS, (
                f"PATTERN_HINTS missing pattern '{name}'"
            )

    def test_one_line_per_pattern(self):
        lines = [l for l in PATTERN_HINTS.splitlines() if l.strip()]
        assert len(lines) == len(ANOMALY_REGISTRY)
