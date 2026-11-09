"""Load Datadog dashboards and monitors from exported JSON files (offline ingestion)."""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Loaded:
    dashboards: list[dict] = field(default_factory=list)
    monitors: list[dict] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (file, reason)


def is_dashboard(obj) -> bool:
    return isinstance(obj, dict) and "widgets" in obj and "title" in obj


def is_monitor(obj) -> bool:
    if not isinstance(obj, dict) or "widgets" in obj:
        return False
    d = obj.get("definition") if isinstance(obj.get("definition"), dict) else obj
    return "query" in d and "type" in d


def _classify(label: str, obj, loaded: Loaded) -> None:
    for item in obj if isinstance(obj, list) else [obj]:
        if is_dashboard(item):
            loaded.dashboards.append(item)
        elif is_monitor(item):
            loaded.monitors.append(item)
        else:
            loaded.skipped.append((label, "not a Datadog dashboard or monitor"))


def load_documents(documents: list[tuple[str, object]]) -> Loaded:
    """Sort already-parsed JSON documents, given as (label, content), into dashboards and monitors (pure)."""
    loaded = Loaded()
    for label, obj in documents:
        _classify(label, obj, loaded)
    return loaded


def load_directory(root: Path) -> Loaded:
    """Recursively read *.json under `root`. Files that are not dashboards or monitors are reported, not ignored."""
    loaded = Loaded()
    for path in sorted(root.rglob("*.json")):
        rel = path.relative_to(root).as_posix()
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            loaded.skipped.append((rel, f"unreadable JSON: {e}"))
            continue
        _classify(rel, obj, loaded)
    return loaded
