"""Metric and tag mappings (data, not code). One YAML file per integration."""

from dataclasses import dataclass, field
from importlib import resources

import yaml


@dataclass(frozen=True)
class MetricMapping:
    otel: str | None
    transform: str = "none"  # none | rate
    matchers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Mapping:
    tags: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, MetricMapping] = field(default_factory=dict)


def load_mappings() -> Mapping:
    """Merge every bundled mapping file (sorted by name, so the result is deterministic)."""
    tags: dict[str, str] = {}
    metrics: dict[str, MetricMapping] = {}
    root = resources.files("openborder.mappings.datadog")
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".yaml"):
            continue
        data = yaml.safe_load(entry.read_text(encoding="utf-8"))
        tags.update(data.get("tags") or {})
        for name, spec in (data.get("metrics") or {}).items():
            metrics[name] = MetricMapping(
                otel=spec.get("otel"),
                transform=spec.get("transform", "none"),
                matchers=tuple(sorted((spec.get("matchers") or {}).items())),
            )
    return Mapping(tags=tags, metrics=metrics)
