"""IR AlertRule -> Dash0 check rule (PrometheusAlertRule) import body.

Fields per https://www.dash0.com/docs/api-reference/operations/post/api/import/check-rule
"""

from openborder.ids import stable_id
from openborder.ir import AlertRule
from openborder.targets.promql import render


def _number(value: float):
    return int(value) if value == int(value) else value


def build(rule: AlertRule) -> dict:
    """Only call for rules with an expression (not MANUAL)."""
    return {
        "id": stable_id("monitor", rule.source_id),
        "name": rule.name,
        "expression": render(rule.expr),
        "thresholds": {k: _number(v) for k, v in sorted(rule.thresholds.items())},
        "interval": rule.interval,
        "labels": dict(sorted(rule.labels.items())),
        "annotations": dict(sorted(rule.annotations.items())),
        "enabled": rule.enabled,
    }
