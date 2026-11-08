"""The Redis mapping, checked against what a real OpenTelemetry Collector's redisreceiver emitted.

tests/fixtures/otel/redisreceiver.json was captured with demo/otel-collector.local.yaml (collector 0.165.0,
redis:7-alpine). This guards the mapping YAML against typos and against a wrong transform.
"""

import json
from pathlib import Path

import pytest

from openborder.mappings import load_mappings

SNAPSHOT = json.loads((Path(__file__).parent / "fixtures" / "otel" / "redisreceiver.json").read_text(encoding="utf-8"))["metrics"]
MAPPING = load_mappings()
MAPPED = {name: m for name, m in MAPPING.metrics.items() if m.otel}


def test_the_snapshot_is_the_real_thing():
    assert len(SNAPSHOT) == 30 and "redis.maxmemory" in SNAPSHOT  # the optional metric, enabled in the demo


@pytest.mark.parametrize("datadog_name", sorted(MAPPED))
def test_every_mapped_metric_exists_in_the_collector_output(datadog_name):
    assert MAPPED[datadog_name].otel in SNAPSHOT, f"{datadog_name} -> {MAPPED[datadog_name].otel} is not emitted by redisreceiver"


@pytest.mark.parametrize("datadog_name", sorted(n for n, m in MAPPED.items() if m.transform == "rate"))
def test_rate_is_only_applied_to_monotonic_cumulative_sums(datadog_name):
    metric = SNAPSHOT[MAPPED[datadog_name].otel]
    assert metric["type"] == "Sum" and metric.get("monotonic") is True, (
        f"{datadog_name}: rate() of a {metric['type']} would not mean what Datadog's rate means"
    )


@pytest.mark.parametrize("datadog_name", sorted(n for n, m in MAPPED.items() if m.matchers))
def test_matcher_values_occur_on_the_real_series(datadog_name):
    attributes = SNAPSHOT[MAPPED[datadog_name].otel]["attributes"]
    for label, value in MAPPING.metrics[datadog_name].matchers:
        assert value in attributes.get(label, []), f"{datadog_name}: {label}={value!r} never occurs; real values: {attributes.get(label)}"

