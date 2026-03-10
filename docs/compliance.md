# APRA Compliance System

LoanGuard AI evaluates loan applications and borrowers against three APRA prudential standards using a threshold-type system that distinguishes enforceable limits from monitoring triggers and reference values.

---

## Covered Regulations

| Regulation | Document type | Enforceable | Purpose |
|---|---|---|---|
| **APS-112** — Capital Adequacy: Standardised Approach to Credit Risk | Prudential Standard | Yes | Minimum capital requirements for credit risk; LVR bands, risk weights, LMI coverage requirements |
| **APG-223** — Residential Mortgage Lending | Practice Guide | No | Serviceability assessment standards, income haircut expectations, LVR monitoring thresholds |
| **APS-220** — Credit Risk Management | Prudential Standard | Yes | Credit risk governance, portfolio concentration limits, provisioning requirements |

Prudential Standards (APS) create binding obligations on ADIs. Practice Guides (APG) communicate APRA's expectations — non-compliance with APG is not a direct breach but creates regulatory risk and may indicate non-compliance with associated APS obligations.

---

## Threshold Type System

Every `Threshold` node carries a `threshold_type` field that controls how `evaluate_thresholds` interprets it.

### `minimum`

The entity's measured value must meet or equal the threshold value. Failing to meet the minimum is a compliance breach.

**Evaluation:** `PASS` if `actual >= limit`, `BREACH` otherwise.

**Example:** `APG-223-THR-001` — serviceability interest rate buffer must be >= 3.0 percentage points. A buffer of 2.5pp → BREACH → NON_COMPLIANT verdict.

**Count in dataset:** 40 thresholds

### `maximum`

The entity's measured value must not exceed the threshold value. Exceeding it is a compliance breach.

**Evaluation:** `PASS` if `actual <= limit`, `BREACH` otherwise.

**Example:** `APS-112-THR-087` — single asset concentration must be <= 25.0 percent. Exceeding 25% → BREACH → NON_COMPLIANT verdict.

**Count in dataset:** 18 thresholds

### `trigger`

The threshold fires a monitoring concern when the condition is met. A trigger does not constitute a breach by itself — it escalates the verdict to `REQUIRES_REVIEW` and signals that senior management review (or board oversight) is required.

**Evaluation:** `TRIGGER` if condition is met, `PASS` if not.

**Example:** `APG-223-THR-005` — LVR >= 90.0 percent requires senior management review. An LVR of 92% → TRIGGER → REQUIRES_REVIEW verdict.

**Count in dataset:** 9 thresholds

### `informational`

An ADI-level reference value used in calculations, not a per-entity pass/fail gate. These are always excluded from verdict logic.

**Evaluation:** `N/A` (always).

**Example:** `APG-223-THR-002` — `credit_card_revolving_debt_repayment_rate == 3.0 percent per month`. This is an example of a suitably prudent approach, not a binding per-loan limit.

**Example:** APS-112 risk weight lookup values — these are inputs to capital calculations at ADI level, not thresholds tested against individual loan properties.

**Count in dataset:** 177 threshold entries (informational is the most common type)

---

## APG-223 Thresholds

These five thresholds are evaluated per loan application:

| Threshold ID | Metric | Operator | Value | Type | Entity field | Condition |
|---|---|---|---|---|---|---|
| `APG-223-THR-001` | `serviceability_interest_rate_buffer` | `>=` | 3.0 percent | minimum | `serviceability_assessment_rate - interest_rate_indicative` | Always applies |
| `APG-223-THR-002` | `credit_card_revolving_debt_repayment_rate` | `==` | 3.0 percent | informational | N/A | Always N/A — reference only |
| `APG-223-THR-003` | `income_haircut_non_salary` | `>=` | 20.0 percent | minimum | `non_salary_income_haircut_pct` | Only when `income_type != 'salary'` |
| `APG-223-THR-004` | `income_haircut_rental` | `>=` | 20.0 percent | minimum | `rental_income_haircut_pct` | Only when `rental_income_gross` is present |
| `APG-223-THR-005` | `LVR` | `>=` | 90.0 percent | trigger | `lvr` | Always applies |

---

## Conditional Threshold Evaluation

Two APG-223 thresholds are only applicable when certain entity data is present.

### THR-003: Non-salary income haircut

`income_haircut_non_salary >= 20.0 percent` applies only when `income_type != 'salary'`.

The ComplianceAgent excludes this threshold from the `evaluate_thresholds` call when the loan's `income_type` is `salary`. For loans with `income_type` of `self_employed`, `mixed`, or `rental`, the threshold is evaluated against `non_salary_income_haircut_pct`.

If `non_salary_income_haircut_pct` is null for a non-salary loan, `evaluate_thresholds` returns `status=unknown` — treated as REQUIRES_REVIEW.

### THR-004: Rental income haircut

`income_haircut_rental >= 20.0 percent` applies only when `rental_income_gross` is present on the loan application.

If `rental_income_gross` is null (the loan has no rental income component), this threshold is N/A for that loan. If `rental_income_gross` is present but `rental_income_haircut_pct` is null, `status=unknown` is returned.

---

## Verdict Derivation

The ComplianceAgent applies a priority-ordered rule set to derive a single verdict from all threshold evaluation results.

### Priority table

| Condition (evaluated in this order) | Verdict |
|---|---|
| One or more thresholds result in `BREACH` | `NON_COMPLIANT` |
| One or more thresholds result in `TRIGGER`, and no `BREACH` | `REQUIRES_REVIEW` |
| All applicable thresholds result in `PASS`, and no `TRIGGER` | `COMPLIANT` |
| A material entity-level threshold has `status=unknown` | `REQUIRES_REVIEW` |
| All thresholds are `N/A` (informational only) | `INFORMATIONAL` |

`N/A` results are always excluded from verdict consideration.

### VERDICT_PRIORITY constants

The Orchestrator uses `VERDICT_PRIORITY` from `src/mcp/schema.py` to aggregate the worst-case verdict across multiple assessments (when an entity is assessed against multiple regulations):

```python
VERDICT_PRIORITY: dict[str, int] = {
    "NON_COMPLIANT":    4,
    "REQUIRES_REVIEW":  3,
    "ANOMALY_DETECTED": 2,
    "COMPLIANT":        1,
    "INFORMATIONAL":    0,
}
```

The verdict with the highest priority value wins. This ensures that a NON_COMPLIANT result under APS-112 is not overridden by a COMPLIANT result under APG-223.

---

## evaluate_thresholds Algorithm

Step-by-step evaluation in `src/mcp/tools_impl.py`:

1. Fetch entity compliance values from Neo4j using `get_entity_compliance_values(conn, entity_id, entity_type)`
2. For each threshold dict in the input list:
   a. Look up the entity field name from `_METRIC_TO_FIELD` (maps metric name to entity property name)
   b. If `actual` is `None` or `limit` is `None` or the operator is unrecognised: return `status=unknown`
   c. If `threshold_type == 'informational'`: return `status=N/A`, skip operator evaluation
   d. If `threshold_type == 'trigger'`: evaluate `actual OP limit`; return `TRIGGER` if True, `PASS` if False
   e. If `threshold_type == 'minimum'` or `'maximum'`: evaluate `actual OP limit`; return `PASS` if True, `BREACH` if False
3. Return the full evaluation list plus summary counts and breach/trigger ID lists

The operator mapping:

```python
_OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">":  lambda a, b: a > b,
    "<":  lambda a, b: a < b,
    "==": lambda a, b: a == b,
}
```

---

## Compliance Distribution in Sample Data

APG-223 compliance verdicts across all 466 loan applications:

| Verdict | Count | Share |
|---|---|---|
| `COMPLIANT` | 366 | 79% |
| `NON_COMPLIANT` | 39 | 8% |
| `REQUIRES_REVIEW` | 61 | 13% |

---

## Common Breach Patterns

### Serviceability buffer below 3 percentage points (THR-001 breach)

The most common breach pattern. Occurs when:
```
serviceability_assessment_rate - interest_rate_indicative < 3.0
```

This produces a `BREACH` result → `NON_COMPLIANT` verdict. The margin field shows how far below the minimum the loan falls.

### LVR >= 90% (THR-005 trigger)

The second most common non-compliant outcome. Occurs when:
```
lvr >= 90.0
```

This produces a `TRIGGER` result → `REQUIRES_REVIEW` verdict (assuming no concurrent BREACH). Per APG-223, LVRs above 90% (including capitalised LMI) require senior management review with Board oversight. The loan is not automatically rejected — it enters a review state.

Loans with both a serviceability breach and high LVR will be `NON_COMPLIANT` (BREACH takes priority over TRIGGER).
