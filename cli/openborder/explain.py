"""`explain`: show how one query, panel or monitor was translated, rule by rule.

Builds plain dicts (one "explanation" each) so the CLI can print them for people or dump them as JSON.
Shell-side module; never imported by openborder.core.
"""

from openborder.ir import AlertRule, Translation
from openborder.mappings import load_mappings
from openborder.sources.datadog.lower import Ctx, translate
from openborder.sources.datadog.monitors import convert_monitor
from openborder.sources.datadog.widgets import collect_queries
from openborder.targets.promql import render


class ExplainError(Exception):
    """Nothing to explain, or ambiguous input; the message says what to do."""


def _steps(trace) -> list[dict]:
    return [{"rule_id": s.rule_id, "tier": s.tier.label, "before": s.before, "after": s.after} for s in trace]


def _findings(findings) -> list[dict]:
    return [{"tier": f.tier.label, "rule_id": f.rule_id, "message": f.message} for f in findings if f.tier > 0]


def explain_query(text: str, variables: dict[str, str | None]) -> dict:
    """Translate one Datadog query or formula with the bundled mappings and show every rule that ran."""
    ctx = Ctx(load_mappings(), variables, var_defaults={name: "*" for name in variables})
    translation: Translation = translate(text, ctx, "query")
    return {
        "title": "Query",
        "kind": "query",
        "tier": translation.tier.label,
        "original": [text],
        "promql": [render(translation.expr)] if translation.expr is not None else [],
        "trace": _steps(translation.trace),
        "findings": _findings(translation.findings),
    }


def explain_monitor_query(text: str) -> dict:
    """Translate a monitor query (`avg(last_5m):... > 90`) into a check rule and show how."""
    rule: AlertRule = convert_monitor(
        {"name": "(monitor query)", "type": "query alert", "query": text, "options": {}}, load_mappings()
    )
    return {
        "title": "Monitor query",
        "kind": "monitor",
        "tier": rule.tier.label,
        "original": [text],
        "promql": [render(rule.expr)] if rule.expr is not None else [],
        "thresholds": rule.thresholds,
        "trace": _steps(rule.trace),
        "findings": _findings(rule.findings),
    }


def _from_panel(dashboard: dict, panel: dict) -> dict:
    return {
        "title": f"{dashboard['title']} / {panel['title'] or panel['key']}  ({panel['key']})",
        "kind": "placeholder panel" if panel["placeholder"] else f"{panel['kind']} panel",
        "tier": panel["tier"],
        "original": collect_queries(panel["original"]),
        "promql": [q["promql"] for q in panel["queries"]],
        "trace": panel["trace"],
        "findings": [{k: f[k] for k in ("tier", "rule_id", "message")} for f in panel["findings"] if f["tier"] != "exact"],
    }


def _from_monitor(item: dict) -> dict:
    original = item["original"].get("definition", item["original"])
    return {
        "title": item["title"],
        "kind": "monitor",
        "tier": item["tier"],
        "original": [original.get("query", "")],
        "promql": [item["promql"]] if item["promql"] else [],
        "thresholds": item["thresholds"],
        "enabled": item["enabled"],
        "trace": item["trace"],
        "findings": [{k: f[k] for k in ("tier", "rule_id", "message")} for f in item["findings"] if f["tier"] != "exact"],
    }


def find_in_plan(document: dict, text: str, limit: int = 8) -> list[dict]:
    """Panels (by title or key) and monitors (by title) of a plan.json whose name contains `text`."""
    needle = text.lower()
    found: list[dict] = []
    for item in document.get("items", []):
        if item["kind"] == "dashboard":
            for panel in item["panels"]:
                if needle in (panel["title"] or "").lower() or needle in panel["key"].lower():
                    found.append(_from_panel(item, panel))
        elif needle in item["title"].lower():
            found.append(_from_monitor(item))
    if not found:
        titles = sorted(
            {p["title"] for i in document.get("items", []) if i["kind"] == "dashboard" for p in i["panels"] if p["title"]}
            | {i["title"] for i in document.get("items", []) if i["kind"] == "monitor"}
        )
        hint = "; ".join(titles[:10]) + (" …" if len(titles) > 10 else "")
        raise ExplainError(f"Nothing in the plan matches {text!r}. Try one of: {hint}")
    if len(found) > limit:
        names = "; ".join(e["title"] for e in found[:limit])
        raise ExplainError(f"{len(found)} items match {text!r}; narrow it down (or use a panel key like panel-12). First matches: {names}")
    return found
