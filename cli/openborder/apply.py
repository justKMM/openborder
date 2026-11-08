"""Load a plan folder for `apply`: read what `plan` wrote, verify it, and nothing else.

`apply` never re-translates. It imports exactly the files listed in plan.json, so what the user reviewed
is what gets imported. Shell-side module (reads files); never imported by openborder.core.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from openborder import validate

MAX_DASHBOARD_BYTES = 256 * 1024  # Dash0: 256 KB for every import endpoint except bulk check rules
MAX_RULES_BYTES = 2 * 1024 * 1024  # Dash0: 2 MB for the bulk check-rules import
MAX_RULES = 1000
SUPPORTED_PLAN_VERSION = 2


class PlanError(Exception):
    """The plan folder cannot be applied; the message says why and what to do."""


@dataclass
class DashboardImport:
    title: str
    artifact: str
    tier: str
    body: dict
    edited: bool  # the file no longer matches the hash recorded by `plan`
    panels: int = 0
    placeholders: int = 0  # widgets that could not be translated and became placeholder panels


@dataclass
class Loaded:
    directory: Path
    dashboards: list[DashboardImport] = field(default_factory=list)
    rules: list[dict] = field(default_factory=list)
    rules_edited: bool = False
    rules_size: int = 0
    not_imported: list[str] = field(default_factory=list)  # monitors with no equivalent (tier manual)


def _digest(raw: bytes) -> str:
    """SHA-256 ignoring CRLF vs LF: git's autocrlf rewrites line endings, which is not a hand edit."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _read_json(path: Path, what: str):
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise PlanError(f"Cannot read {what} ({path}): {e}. Run `plan` again.") from e
    try:
        return raw, json.loads(raw)
    except ValueError as e:
        raise PlanError(f"{what} ({path}) is not valid JSON: {e}") from e


def load_plan(directory: Path) -> Loaded:
    plan_path = directory / "plan.json"
    if not plan_path.is_file():
        raise PlanError(f"{directory} has no plan.json. Run `python -m openborder plan --out {directory}` first.")
    _, document = _read_json(plan_path, "plan.json")
    if document.get("version") != SUPPORTED_PLAN_VERSION:
        raise PlanError(f"plan.json has version {document.get('version')!r}; this OpenBorder reads version {SUPPORTED_PLAN_VERSION}. Run `plan` again.")

    validation = document.get("validation") or {}
    problems = []
    for label, errors in (validation.get("structure") or {}).items():
        problems += [f"{label}: {e}" for e in errors]
    for label, message in ((validation.get("promtool") or {}).get("errors") or {}).items():
        problems.append(f"promtool rejected {label}: {message}")
    if problems:
        raise PlanError("The plan did not pass validation, so nothing will be imported:\n  " + "\n  ".join(problems))

    loaded = Loaded(directory)
    for item in document.get("items", []):
        if item["kind"] == "dashboard":
            raw, body = _read_json(directory / item["artifact"], f"dashboard artifact {item['artifact']}")
            errors = validate.structure_errors(body)
            if errors:
                raise PlanError(f"{item['artifact']} is not a valid dashboard (was it edited?):\n  " + "\n  ".join(errors))
            if len(raw) > MAX_DASHBOARD_BYTES:
                raise PlanError(f"{item['artifact']} is {len(raw) // 1024} KB; Dash0's dashboard import accepts at most {MAX_DASHBOARD_BYTES // 1024} KB.")
            loaded.dashboards.append(
                DashboardImport(
                    title=item["title"],
                    artifact=item["artifact"],
                    tier=item["tier"],
                    body=body,
                    edited=_digest(raw) != item["sha256"],
                    panels=len(item.get("panels", [])),
                    placeholders=sum(1 for p in item.get("panels", []) if p.get("placeholder")),
                )
            )
        elif item["kind"] == "monitor" and item.get("artifact") is None:
            loaded.not_imported.append(item["title"])

    # Every monitor that plan marked importable must arrive, or we would silently drop alerts.
    expected_rules = sum(1 for i in document.get("items", []) if i["kind"] == "monitor" and i.get("artifact"))
    rules_meta = document.get("check_rules")
    if expected_rules and not rules_meta:
        raise PlanError(f"plan.json lists {expected_rules} importable monitor(s) but has no check-rules entry, so it is incomplete. Run `plan` again.")
    if rules_meta:
        raw, body = _read_json(directory / rules_meta["artifact"], "check-rules.json")
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise PlanError("check-rules.json must be an object with an `items` list.")
        errors = [f"{i.get('name', '?')}: {e}" for i in items for e in validate.rule_errors(i)]
        if errors:
            raise PlanError("check-rules.json contains invalid rules (was it edited?):\n  " + "\n  ".join(errors))
        if len(items) > validate.MAX_RULES_PER_REQUEST:
            raise PlanError(f"{len(items)} check rules; Dash0 accepts at most {MAX_RULES} per request.")
        if len(raw) > MAX_RULES_BYTES:
            raise PlanError(f"check-rules.json is {len(raw) // 1024} KB; Dash0 accepts at most {MAX_RULES_BYTES // 1024 // 1024} MB.")
        if len(items) != expected_rules:
            raise PlanError(f"plan.json lists {expected_rules} importable monitor(s) but check-rules.json holds {len(items)}. Run `plan` again.")
        loaded.rules = items
        loaded.rules_size = len(raw)
        loaded.rules_edited = _digest(raw) != rules_meta["sha256"]

    if not loaded.dashboards and not loaded.rules:
        raise PlanError("The plan contains nothing to import (no dashboards and no importable check rules).")
    return loaded
