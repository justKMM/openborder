import pytest

from openborder import core
from openborder.ir import Tier
from openborder.mappings import load_mappings
from openborder.sources.datadog.monitors import convert_message, convert_monitor
from openborder.targets.promql import render

MAPPING = load_mappings()


def monitor(default_query, **overrides):
    definition = {
        "name": "Test monitor",
        "type": "query alert",
        "query": default_query,
        "message": "something is wrong",  # no templates, so message conversion adds no finding
        "tags": ["team:db", "integration:redisdb"],
        "options": {"thresholds": {"critical": 90, "warning": 70}},
    }
    definition.update(overrides)
    return {"id": 4242, **definition}


def convert(default_query, enable_approximate=False, **overrides):
    return convert_monitor(monitor(default_query, **overrides), MAPPING, enable_approximate)


def test_single_selector_window_is_pushed_down_exactly():
    rule = convert("avg(last_5m):avg:redis.mem.used{*} > 90")
    assert render(rule.expr) == 'avg(avg_over_time({otel_metric_name="redis.memory.used"}[5m])) > $__threshold'
    assert rule.tier == Tier.EXACT and rule.enabled
    assert rule.thresholds == {"failed": 90, "degraded": 70}


def test_mixed_aggregators_use_a_subquery_and_are_equivalent():
    rule = convert("max(last_10m):avg:redis.mem.used{*} > 90")
    assert render(rule.expr) == 'max_over_time(avg({otel_metric_name="redis.memory.used"})[10m:1m]) > $__threshold'
    assert rule.tier == Tier.EQUIVALENT and rule.enabled


def test_rate_metrics_use_the_monitor_window_not_a_dashboard_variable():
    rule = convert("avg(last_5m):avg:redis.stats.keyspace_misses{*} > 100")
    assert "$__rate_interval" not in render(rule.expr)
    assert "[5m]" in render(rule.expr)


def test_approximate_alerts_are_imported_disabled_unless_asked():
    q = "avg(last_5m):avg:redis.info.latency_ms{*} > 100"  # no OTel equivalent -> approximate
    assert convert(q).tier == Tier.APPROXIMATE and not convert(q).enabled
    assert convert(q, enable_approximate=True).enabled


def test_below_threshold_monitors_are_approximate_until_dash0_ordering_is_verified():
    rule = convert("avg(last_5m):avg:redis.mem.used{*} < 10")
    assert rule.tier == Tier.APPROXIMATE and not rule.enabled
    assert "dd.monitor.comparator.below" in [f.rule_id for f in rule.findings]


def test_notify_no_data_is_approximate_because_it_is_not_migrated():
    rule = convert("avg(last_5m):avg:redis.mem.used{*} > 90", options={"thresholds": {"critical": 90}, "notify_no_data": True})
    assert rule.tier == Tier.APPROXIMATE and not rule.enabled


@pytest.mark.parametrize(
    "overrides, rule_id",
    [
        ({"type": "service check", "query": '"redis.can_connect".over("*").by("host").last(2).count_by_status()'}, "dd.monitor.unsupported"),
        ({"type": "log alert", "query": 'logs("status:error").index("*").rollup("count").last("5m") > 4'}, "dd.monitor.unsupported"),
        ({"query": "avg(last_5m):avg:redis.mem.used{*} == 90"}, "dd.monitor.comparator"),
        ({"query": "change(avg(last_5m),last_5m):avg:redis.mem.used{*} > 50"}, "dd.monitor.unsupported-query"),
        ({"query": "avg(last_5m):anomalies(avg:redis.mem.used{*}, 'basic', 2) >= 1"}, "dd.fn.unsupported"),
    ],
)
def test_unsupported_monitors_are_manual_and_not_exported(overrides, rule_id):
    data = monitor("avg(last_5m):avg:redis.mem.used{*} > 90", **overrides)
    rule = convert_monitor(data, MAPPING)
    assert rule.tier == Tier.MANUAL and rule.expr is None and not rule.enabled
    assert rule_id in [f.rule_id for f in rule.findings]
    assert rule.original == data
    assert "check-rules.json" not in core.plan([], [data]).artifacts


def test_labels_and_annotations_carry_tags_priority_and_provenance():
    rule = convert("avg(last_5m):avg:redis.mem.used{*} > 90", priority=2)
    assert rule.labels == {"team": "db", "integration": "redisdb", "priority": "2"}
    assert rule.annotations["openborder_source"] == "datadog:monitor:4242"
    assert rule.annotations["openborder_original_query"].startswith("avg(last_5m)")
    assert rule.annotations["summary"] == "Test monitor"


def test_exported_file_format_and_api_format_are_both_accepted():
    flat = convert_monitor(monitor("avg(last_5m):avg:redis.mem.used{*} > 90"), MAPPING)
    wrapped = convert_monitor(
        {"title": "T", "tags": ["integration:redis"], "definition": monitor("avg(last_5m):avg:redis.mem.used{*} > 90")}, MAPPING
    )
    assert render(flat.expr) == render(wrapped.expr)


# ---- message templates ----------------------------------------------------------------


def test_message_conversion_removes_mentions_and_reports_them():
    text, notes, mentions = convert_message("CPU high {{value}} @team-ops @oncall@example.com")
    assert "@" not in text and "{{ $value }}" in text
    assert mentions == ["@oncall@example.com", "@team-ops"]


def test_message_conversion_flattens_severity_blocks_and_drops_recovery_blocks():
    text, notes, _ = convert_message("{{#is_alert}}A{{/is_alert}}{{#is_warning}}W{{/is_warning}}{{#is_recovery}}R{{/is_recovery}}")
    assert text == "AW"
    assert any("flattened" in n for n in notes) and any("recovery" in n for n in notes)


def test_message_conversion_flags_variables_it_cannot_convert():
    _, notes, _ = convert_message("on {{host.name}} threshold {{threshold}} {{tag.env}}")
    leftovers = next(n for n in notes if n.startswith("left unconverted"))
    assert "{{threshold}}" in leftovers and "{{tag.env}}" in leftovers and "host.name" not in leftovers


# ---- names with templates, and forecast monitors ---------------------------------------


@pytest.mark.parametrize(
    "name, static, summary",
    [
        ("Disk latency is high for host {{host.name}} on device {{device.name}}", "Disk latency is high",
         "Disk latency is high for host {{ $labels.host_name }} on device {{device.name}}"),
        ("Memory space is low for host {{host.name}}", "Memory space is low", "Memory space is low for host {{ $labels.host_name }}"),
        ("[Redis] High memory consumption", "[Redis] High memory consumption", "[Redis] High memory consumption"),
    ],
)
def test_check_names_never_contain_templates_but_the_summary_keeps_them(name, static, summary):
    rule = convert("avg(last_5m):avg:redis.mem.used{*} > 90", name=name)
    assert rule.name == static and rule.annotations["summary"] == summary
    assert ("dd.monitor.name" in [f.rule_id for f in rule.findings]) == (static != name)


def test_a_name_that_is_only_a_template_is_kept_rather_than_emptied():
    assert convert("avg(last_5m):avg:redis.mem.used{*} > 90", name="{{host.name}}").name == "{{host.name}}"


def test_forecast_monitors_are_named_as_such_not_blamed_on_syntax():
    query = 'max(next_3d):forecast((1 - avg:system.disk.free{*} / avg:system.disk.total{*}) * 100, "seasonal", 1) >= 99'
    rule = convert(query)
    assert rule.tier == Tier.MANUAL and rule.expr is None
    reason = next(f.message for f in rule.findings if f.rule_id == "dd.monitor.unsupported-query")
    assert "forecast monitors have no Prometheus equivalent" in reason
