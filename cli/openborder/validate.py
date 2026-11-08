"""Pure validation of generated artifacts (no I/O). PromQL syntax checking lives in promtool.py."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

PERSES_COLUMNS = 24
MAX_RULES_PER_REQUEST = 1000  # Dash0 bulk import limit


# ---- dashboards --------------------------------------------------------------------------


def structure_errors(body: dict) -> list[str]:
    errors: list[str] = []
    if body.get("kind") != "Dashboard":
        errors.append("kind must be 'Dashboard'")
    if not (body.get("metadata") or {}).get("name"):
        errors.append("metadata.name is required")
    spec = body.get("spec") or {}
    panels = spec.get("panels") or {}
    referenced = set()
    for i, layout in enumerate(spec.get("layouts") or []):
        for item in (layout.get("spec") or {}).get("items", []):
            ref = (item.get("content") or {}).get("$ref", "")
            key = ref.rsplit("/", 1)[-1]
            referenced.add(key)
            if key not in panels:
                errors.append(f"layout {i} references missing panel '{key}'")
            if item.get("x", 0) < 0 or item.get("x", 0) + item.get("width", 0) > PERSES_COLUMNS:
                errors.append(f"panel '{key}' does not fit the {PERSES_COLUMNS}-column grid")
    for key, panel in panels.items():
        if key not in referenced:
            errors.append(f"panel '{key}' is not placed in any layout")
        plugin = (panel.get("spec") or {}).get("plugin") or {}
        if not plugin.get("kind"):
            errors.append(f"panel '{key}' has no plugin kind")
        if plugin.get("kind") not in (None, "Markdown"):
            queries = (panel["spec"].get("queries")) or []
            if not queries:
                errors.append(f"panel '{key}' has no queries")
            for q in queries:
                if not q["spec"]["plugin"]["spec"].get("query"):
                    errors.append(f"panel '{key}' has an empty query")
    return errors


# ---- check rules -------------------------------------------------------------------------


class _Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    degraded: float | None = None
    failed: float | None = None


class _CheckRule(BaseModel):
    """The documented PrometheusAlertRule fields. extra='forbid' catches typos before Dash0 does."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str | None = None
    name: str
    expression: str
    thresholds: _Thresholds | None = None
    interval: str | None = None
    for_: str | None = Field(default=None, alias="for")
    keep_firing_for: str | None = None
    labels: dict[str, str] | None = None
    annotations: dict[str, str] | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _threshold_needed(self):
        if "$__threshold" in self.expression and not (self.thresholds and (self.thresholds.failed is not None or self.thresholds.degraded is not None)):
            raise ValueError("expression uses $__threshold but no thresholds are set")
        return self


def rule_errors(body: dict) -> list[str]:
    try:
        _CheckRule.model_validate(body)
    except ValidationError as e:
        return [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
    return []
