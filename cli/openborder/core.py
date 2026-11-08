"""Pure entry point: raw Datadog JSON in, artifacts and findings out.

No file, environment, network or Docker access here (see CONTRIBUTING.md). The CLI does the I/O.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field

from openborder.ids import slug
from openborder.ir import AlertRule, Dashboard, Finding, Tier
from openborder.mappings import load_mappings
from openborder.sources.datadog.monitors import convert_monitor
from openborder.sources.datadog.widgets import convert_dashboard
from openborder.targets import dash0_rules, perses
from openborder.targets.promql import render
from openborder import validate

PLAN_VERSION = 2  # 2: plan.json records the check-rules.json hash and count


@dataclass(frozen=True)
class PlanOptions:
    enable_approximate: bool = False


@dataclass
class Plan:
    dashboards: list[Dashboard]
    rules: list[AlertRule]
    skipped: list[tuple[str, str]]  # (what, why): inputs we could not process at all
    artifacts: dict[str, bytes]  # relative path -> content (plan.json is added by render_plan_json)
    dashboard_artifacts: dict[str, str]  # source_id -> artifact path
    structure_errors: dict[str, list[str]] = field(default_factory=dict)

    @property
    def panels(self):
        return [p for d in self.dashboards for p in d.panels]

    def panel_counts(self) -> dict[str, int]:
        counts = Counter(p.tier.label for p in self.panels)
        return {t.label: counts.get(t.label, 0) for t in Tier}


def dump_json(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _finding(f: Finding) -> dict:
    return {"tier": f.tier.label, "rule_id": f.rule_id, "message": f.message, "source_ref": f.source_ref}


def plan(dashboards: list[dict], monitors: list[dict], options: PlanOptions = PlanOptions(), skipped=()) -> Plan:
    mapping = load_mappings()
    skipped_list = list(skipped)
    artifacts: dict[str, bytes] = {}
    dashboard_artifacts: dict[str, str] = {}
    structure_errors: dict[str, list[str]] = {}

    converted: list[Dashboard] = []
    used_paths: set[str] = set()
    for raw in dashboards:
        try:
            dash = convert_dashboard(raw, mapping)
            body = perses.build(dash)
        except Exception as e:  # a malformed dashboard must not stop the others
            skipped_list.append((f"dashboard:{raw.get('title', '?')}", f"{type(e).__name__}: {e}"))
            continue
        base = slug(dash.title, "dashboard")
        path, n = f"perses/{base}.json", 2
        while path in used_paths:
            path, n = f"perses/{base}-{n}.json", n + 1
        used_paths.add(path)
        artifacts[path] = dump_json(body)
        dashboard_artifacts[dash.source_id] = path
        converted.append(dash)
        if errors := validate.structure_errors(body):
            structure_errors[dash.title] = errors

    rules: list[AlertRule] = []
    bodies: list[dict] = []
    for raw in monitors:
        try:
            rule = convert_monitor(raw, mapping, options.enable_approximate)
            body = dash0_rules.build(rule) if rule.expr is not None else None
        except Exception as e:
            d = raw.get("definition") if isinstance(raw.get("definition"), dict) else raw
            skipped_list.append((f"monitor:{d.get('name', '?')}", f"{type(e).__name__}: {e}"))
            continue
        rules.append(rule)
        if body is not None:
            bodies.append(body)
            if errors := validate.rule_errors(body):
                structure_errors[rule.name] = errors
    if bodies:
        artifacts["check-rules.json"] = dump_json({"items": bodies})

    artifacts["migration.log"] = _log(converted, rules, skipped_list)
    return Plan(converted, rules, skipped_list, artifacts, dashboard_artifacts, structure_errors)


def _log(dashboards: list[Dashboard], rules: list[AlertRule], skipped) -> bytes:
    lines = []
    for dash in dashboards:
        findings = list(dash.findings) + [f for v in dash.variables for f in v.findings]
        findings += [f for p in dash.panels for f in p.findings]
        lines += [_finding(f) for f in findings if f.tier > Tier.EXACT]
    for rule in rules:
        lines += [_finding(f) for f in rule.findings if f.tier > Tier.EXACT]
    for what, why in skipped:
        lines.append({"tier": "manual", "rule_id": "input.skipped", "message": why, "source_ref": what})
    return ("".join(json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n" for line in lines)).encode("utf-8")


def _panel_json(p) -> dict:
    return {
        "key": p.key,
        "title": p.title,
        "kind": p.kind,
        "tier": p.tier.label,
        "placeholder": p.placeholder,
        "queries": [{"promql": render(q.expr), "legend": q.legend} for q in p.queries],
        "findings": [_finding(f) for f in p.findings],
        "trace": [
            {"rule_id": s.rule_id, "tier": s.tier.label, "before": s.before, "after": s.after} for s in p.trace
        ],
        "original": p.original,
    }


def render_plan_json(plan_: Plan, source: dict, validation: dict) -> bytes:
    """plan.json: what `apply` imports, plus everything needed to review and explain the plan."""
    items = []
    for dash in plan_.dashboards:
        path = plan_.dashboard_artifacts[dash.source_id]
        items.append(
            {
                "kind": "dashboard",
                "source_ref": f"datadog:dashboard:{dash.source_id}",
                "title": dash.title,
                "artifact": path,
                "sha256": hashlib.sha256(plan_.artifacts[path]).hexdigest(),
                "tier": max((p.tier for p in dash.panels), default=Tier.EXACT).label,
                "findings": [_finding(f) for f in dash.findings] + [_finding(f) for v in dash.variables for f in v.findings],
                "panels": [_panel_json(p) for p in dash.panels],
            }
        )
    for rule in plan_.rules:
        items.append(
            {
                "kind": "monitor",
                "source_ref": f"datadog:monitor:{rule.source_id}",
                "title": rule.name,
                "artifact": "check-rules.json" if rule.expr is not None else None,
                "tier": rule.tier.label,
                "enabled": rule.enabled and rule.expr is not None,
                "promql": render(rule.expr) if rule.expr is not None else None,
                "thresholds": rule.thresholds,
                "findings": [_finding(f) for f in rule.findings],
                "trace": [
                    {"rule_id": s.rule_id, "tier": s.tier.label, "before": s.before, "after": s.after} for s in rule.trace
                ],
                "original": rule.original,
            }
        )
    check_rules = None
    if "check-rules.json" in plan_.artifacts:
        content = plan_.artifacts["check-rules.json"]
        check_rules = {
            "artifact": "check-rules.json",
            "sha256": hashlib.sha256(content).hexdigest(),
            "count": len(json.loads(content)["items"]),
        }
    document = {
        "version": PLAN_VERSION,
        "source": source,
        "check_rules": check_rules,
        "summary": {
            "dashboards": len(plan_.dashboards),
            "panels": plan_.panel_counts(),
            "monitors": len(plan_.rules),
            "monitors_enabled": sum(1 for r in plan_.rules if r.enabled and r.expr is not None),
            "skipped": [{"what": w, "why": y} for w, y in plan_.skipped],
        },
        "validation": validation,
        "items": items,
    }
    return dump_json(document)
