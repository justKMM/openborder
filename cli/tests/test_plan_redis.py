"""The real Redis assets from integrations-core, end to end (offline)."""

import json
from pathlib import Path

import pytest

from openborder import core
from openborder.ir import Tier
from openborder.sources.datadog.files import load_directory

FIXTURES = Path(__file__).parent / "fixtures" / "datadog"


@pytest.fixture(scope="module")
def loaded():
    return load_directory(FIXTURES)


@pytest.fixture(scope="module")
def result(loaded):
    return core.plan(loaded.dashboards, loaded.monitors, skipped=loaded.skipped)


def panel(result, title, nth=0):
    return [p for p in result.panels if p.title == title][nth]


def test_loads_exactly_the_two_assets(loaded):
    assert len(loaded.dashboards) == 1 and len(loaded.monitors) == 1
    assert loaded.skipped == []


def test_every_widget_is_accounted_for(result, loaded):
    def count(widgets):
        return sum(count(w["definition"]["widgets"]) if w["definition"]["type"] == "group" else 1 for w in widgets)

    assert len(result.panels) == count(loaded.dashboards[0]["widgets"]) == 36
    assert all(p.original for p in result.panels), "the original widget must always be kept"


def test_tier_distribution_is_stable(result):
    # Reviewed by hand against the Datadog source; a change here means translation behaviour changed.
    assert result.panel_counts() == {"exact": 13, "equivalent": 11, "approximate": 8, "manual": 4}


@pytest.mark.parametrize(
    "title, nth, tier",
    [
        ("Total allocated memory", 0, Tier.EXACT),  # plain gauge, renamed
        ("Evictions", 0, Tier.APPROXIMATE),  # per_minute() of a raw total
        ("Rejected connections", 0, Tier.APPROXIMATE),  # diff()
        ("Commands per second", 0, Tier.EQUIVALENT),  # rate-type metric
        ("Average CPU usage", 0, Tier.EQUIVALENT),
        ("Hit rate", 0, Tier.APPROXIMATE),  # ratio of averages vs average of ratios
        ("Latency by Host", 0, Tier.APPROXIMATE),  # no OTel equivalent for INFO latency
        ("Slowlog query rates", 0, Tier.MANUAL),  # toplist
        ("Key length distribution", 0, Tier.MANUAL),
        ("Error Logs", 0, Tier.MANUAL),
    ],
)
def test_panel_tiers(result, title, nth, tier):
    assert panel(result, title, nth).tier == tier


def test_manual_widgets_become_placeholders_that_quote_the_original(result):
    p = panel(result, "Slowlog query rates")
    assert p.placeholder and p.kind == "Markdown" and not p.queries
    assert "redis.slowlog.micros.count" in p.options["text"]
    logs = panel(result, "Error Logs")
    assert "source:redis" in logs.options["text"]


def test_the_two_dashboard_level_findings(result):
    rules = {f.rule_id for f in result.dashboards[0].findings}
    assert rules == {"dd.layout.groups-stacked", "dd.var.free-scope"}


def test_generated_artifacts_are_structurally_valid(result):
    assert result.structure_errors == {}
    assert set(result.artifacts) == {"perses/redis-overview.json", "check-rules.json", "migration.log"}


def test_the_monitor_becomes_an_enabled_check_rule(result):
    rule = result.rules[0]
    assert rule.tier == Tier.EQUIVALENT and rule.enabled
    body = json.loads(result.artifacts["check-rules.json"])["items"][0]
    assert body["thresholds"] == {"degraded": 70, "failed": 90}
    assert body["expression"].endswith("> $__threshold")
    assert "{{ $value }}" in body["annotations"]["description"]


def test_artifacts_are_deterministic(loaded, result):
    again = core.plan(loaded.dashboards, loaded.monitors, skipped=loaded.skipped)
    assert again.artifacts == result.artifacts


def test_ids_are_stable_so_reimport_overwrites(result):
    dashboard = json.loads(result.artifacts["perses/redis-overview.json"])
    assert dashboard["metadata"]["dash0Extensions"]["id"] == "e5f39f83-a8e4-5492-ae31-1b4e400dc0be"
    assert json.loads(result.artifacts["check-rules.json"])["items"][0]["id"] == "83da09f7-8dc7-5e34-970c-89726f0bdcf0"


def test_plan_json_keeps_originals_and_summary(result):
    document = json.loads(core.render_plan_json(result, {"kind": "datadog", "mode": "files"}, {"structure": {}}))
    assert document["summary"]["panels"]["manual"] == 4
    assert document["summary"]["monitors_enabled"] == 1
    monitor = next(i for i in document["items"] if i["kind"] == "monitor")
    assert monitor["original"]["definition"]["query"].startswith("avg(last_5m)")
    dashboard = next(i for i in document["items"] if i["kind"] == "dashboard")
    assert all("original" in p and p["trace"] is not None for p in dashboard["panels"])


@pytest.mark.parametrize("artifact", ["perses/redis-overview.json", "check-rules.json", "migration.log"])
def test_golden_artifacts(result, assert_golden, artifact):
    assert_golden(f"redis/{artifact}", result.artifacts[artifact])
