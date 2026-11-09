"""discover, plan and apply as plain functions that return data: nothing here prints, prompts or exits.

Shared by the CLI (cli.py) and the web backend (backend/). Shell-side module: it reads and writes files,
calls Docker and talks to the network, so openborder.core must never import it. Credentials arrive as
arguments; this module never reads the environment, so a multi-user caller can pass each user's own.

Problems the user can fix raise ServiceError (or the existing DatadogError, Dash0Error and PlanError);
the caller decides how to show them.
"""

import json
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path

from openborder import core, promtool
from openborder.apply import Loaded
from openborder.dash0_client import Dash0Client, Dash0Error
from openborder.sources.datadog.client import DatadogClient
from openborder.targets.promql import render


class ServiceError(Exception):
    """A request the user can fix. `hint` is a command-line follow-up that the CLI appends; the backend ignores it."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def filter_by_name(items: list[dict], key: str, text: str | None) -> list[dict]:
    if not text:
        return items
    needle = text.lower()
    return [item for item in items if needle in str(item.get(key, "")).lower()]


# ---- discover -----------------------------------------------------------------------------------


@dataclass
class Discovery:
    site: str
    dashboards: list[dict]  # Datadog summaries (id, title, layout_type, ...)
    monitors: list[dict]


def run_discover(api_key: str, app_key: str, site: str, name: str | None = None, tag: str | None = None) -> Discovery:
    """List dashboards and monitors in the source Datadog org (read-only). Raises DatadogError."""
    with DatadogClient(api_key, app_key, site) as dd:
        dashboards = dd.list_dashboards()
        monitors = dd.list_monitors(monitor_tags=tag)
        resolved_site = dd.site
    return Discovery(
        resolved_site, filter_by_name(dashboards, "title", name), filter_by_name(monitors, "name", name)
    )


def discovery_document(found: Discovery) -> dict:
    """The sorted, trimmed form of a Discovery: what `discover --json` prints and the backend returns."""
    return {
        "site": found.site,
        "dashboards": [
            {k: d.get(k) for k in ("id", "title", "description", "layout_type", "modified_at")}
            for d in sorted(found.dashboards, key=lambda d: (str(d.get("title", "")).lower(), str(d.get("id"))))
        ],
        "monitors": [
            {"id": m.get("id"), "name": m.get("name"), "type": m.get("type"), "tags": m.get("tags") or []}
            for m in sorted(found.monitors, key=lambda m: (str(m.get("name", "")).lower(), m.get("id", 0)))
        ],
    }


# ---- plan ---------------------------------------------------------------------------------------


@dataclass
class Inputs:
    dashboards: list[dict]  # raw Datadog dashboard JSON
    monitors: list[dict]  # raw Datadog monitor JSON
    skipped: list[tuple[str, str]]  # (what, why): inputs that could not be read at all
    source: dict  # recorded in plan.json; must never hold credentials


def fetch_from_datadog(
    api_key: str,
    app_key: str,
    site: str,
    *,
    dashboard_ids: Sequence[str] = (),
    monitor_tag: str | None = None,
    dashboards: bool = True,
    monitors: bool = True,
) -> Inputs:
    """Pull dashboards (full definitions) and monitors from the Datadog API. Raises DatadogError, ServiceError."""
    found_dashboards: list[dict] = []
    found_monitors: list[dict] = []
    with DatadogClient(api_key, app_key, site) as dd:
        if dashboards:
            summaries = dd.list_dashboards()
            if dashboard_ids:
                unknown = sorted(set(dashboard_ids) - {s["id"] for s in summaries})
                if unknown:
                    raise ServiceError(
                        f"No such dashboard ID(s) in {dd.site}: {', '.join(unknown)}.", hint="Run `discover` to list them."
                    )
                summaries = [s for s in summaries if s["id"] in dashboard_ids]
            found_dashboards = [dd.get_dashboard(s["id"]) for s in summaries]
        if monitors:
            found_monitors = dd.list_monitors(monitor_tags=monitor_tag)
        source = {"kind": "datadog", "mode": "api", "site": dd.site}
    return Inputs(found_dashboards, found_monitors, [], source)


def select_inputs(
    inputs: Inputs,
    *,
    name: str | None = None,
    monitor_tag: str | None = None,
    dashboards: bool = True,
    monitors: bool = True,
) -> Inputs:
    """Narrow the inputs down. Raises ServiceError when nothing is left to plan."""
    selected_dashboards = inputs.dashboards if dashboards else []
    selected_monitors = inputs.monitors if monitors else []
    if name:
        selected_dashboards = filter_by_name(selected_dashboards, "title", name)
        selected_monitors = [
            m for m in selected_monitors if name.lower() in str((m.get("definition") or m).get("name", "")).lower()
        ]
    if monitor_tag and inputs.source.get("mode") != "api":  # the API already filtered by tag server-side
        selected_monitors = [
            m for m in selected_monitors if monitor_tag in ((m.get("definition") or m).get("tags") or []) + (m.get("tags") or [])
        ]
    if not selected_dashboards and not selected_monitors:
        raise ServiceError(
            "Nothing to plan: no dashboards or monitors matched.",
            hint="Run `discover` to see what the org contains." if inputs.source.get("mode") == "api" else "",
        )
    return replace(inputs, dashboards=selected_dashboards, monitors=selected_monitors)


def plan_failed(document: dict) -> bool:
    """A plan.json that recorded structure or promtool errors. Its artifacts exist, but `apply` will refuse them."""
    validation = document.get("validation") or {}
    return bool(validation.get("structure")) or bool((validation.get("promtool") or {}).get("errors"))


@dataclass
class PlanRun:
    plan: core.Plan
    validation: dict
    document: dict  # the plan.json that was written

    @property
    def failed(self) -> bool:
        return plan_failed(self.document)


def run_plan(
    inputs: Inputs,
    out: Path,
    *,
    enable_approximate: bool = False,
    promtool_check: bool = True,
    status: Callable[[str], AbstractContextManager] | None = None,
) -> PlanRun:
    """Translate, validate and write the plan folder `out` (nothing is imported).

    `status("message")` should return a context manager shown while the slow promtool step runs
    (the CLI passes `console.status`).
    """
    result = core.plan(
        inputs.dashboards, inputs.monitors, core.PlanOptions(enable_approximate=enable_approximate), skipped=inputs.skipped
    )

    validation: dict = {"structure": result.structure_errors}
    if promtool_check:
        expressions = {}
        for dash in result.dashboards:
            for p in dash.panels:
                for i, q in enumerate(p.queries):
                    expressions[f"{dash.title} / {p.title or p.key} [{i + 1}]"] = render(q.expr)
        for rule in result.rules:
            if rule.expr is not None:
                expressions[f"monitor {rule.name}"] = render(rule.expr)
        with (status or (lambda _: nullcontext()))("Checking PromQL with promtool (Docker)..."):
            outcome = promtool.check(expressions)
        validation["promtool"] = {"ran": outcome.ran, "reason": outcome.reason, "checked": outcome.checked, "errors": outcome.errors}
    else:
        validation["promtool"] = {"ran": False, "reason": "skipped (--no-promtool)"}

    out.mkdir(parents=True, exist_ok=True)
    perses_dir = out / "perses"
    if (out / "plan.json").exists() and perses_dir.is_dir():  # our own earlier output: drop stale dashboards
        for old in perses_dir.glob("*.json"):
            old.unlink()
    if not result.rules and (out / "check-rules.json").exists() and (out / "plan.json").exists():
        (out / "check-rules.json").unlink()
    for relative, content in result.artifacts.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    plan_json = core.render_plan_json(result, inputs.source, validation)
    (out / "plan.json").write_bytes(plan_json)
    return PlanRun(result, validation, json.loads(plan_json))


# ---- apply --------------------------------------------------------------------------------------


def apply_summary(loaded: Loaded, api_url: str, dataset: str) -> dict:
    """What `apply` would send and where, as data: the pre-import confirmation screen."""
    return {
        "target": {"api_url": api_url, "dataset": dataset},
        "dashboards": [
            {"title": d.title, "artifact": d.artifact, "tier": d.tier, "panels": d.panels, "placeholders": d.placeholders, "edited": d.edited}
            for d in loaded.dashboards
        ],
        "check_rules": [
            {
                "name": r["name"],
                "tier": r["annotations"].get("openborder_tier", "exact"),
                "enabled": r.get("enabled", True),
                "thresholds": r.get("thresholds", {}),
            }
            for r in loaded.rules
        ],
        "check_rules_edited": loaded.rules_edited,
        "not_imported": list(loaded.not_imported),
    }


@dataclass
class ApplyOutcome:
    results: dict  # exactly what is written to apply.json
    failures: int


def run_apply(
    loaded: Loaded,
    client: Dash0Client,
    plan_dir: Path,
    on_result: Callable[[str, dict], None] | None = None,
) -> ApplyOutcome:
    """Import a loaded plan: one request per dashboard, then one atomic request for all check rules.

    Failures are collected, not raised: a rejected dashboard does not stop the rules. Closes `client`, writes
    apply.json into `plan_dir` (never credentials) and calls `on_result("dashboard" | "check_rules", entry)`
    after each request so a caller can show progress.
    """
    results: dict = {"target": {"api_url": client.api_url, "dataset": client.dataset}, "dashboards": [], "check_rules": None}
    failures = 0

    def note(kind: str, entry: dict) -> None:
        if on_result:
            on_result(kind, entry)

    with client:
        for d in loaded.dashboards:
            try:
                response = client.import_dashboard(d.body)
                fallback = d.body["metadata"]["dash0Extensions"]["id"]
                dash0_id = ((response.get("metadata") or {}).get("dash0Extensions") or {}).get("id") or fallback
                entry = {"title": d.title, "artifact": d.artifact, "status": "imported", "id": dash0_id}
            except Dash0Error as e:
                failures += 1
                entry = {"title": d.title, "artifact": d.artifact, "status": "failed", "error": str(e)}
            results["dashboards"].append(entry)
            note("dashboard", entry)
        if loaded.rules:
            try:
                response = client.import_check_rules(loaded.rules)
                results["check_rules"] = {"status": "imported", "sent": len(loaded.rules), "created": response.get("created")}
            except Dash0Error as e:
                failures += 1
                results["check_rules"] = {"status": "failed", "sent": len(loaded.rules), "error": str(e)}
            note("check_rules", results["check_rules"])

    (plan_dir / "apply.json").write_text(json.dumps(results, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return ApplyOutcome(results, failures)
