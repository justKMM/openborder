import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openborder import core
from openborder.cli import app
from openborder.explain import ExplainError, explain_monitor_query, explain_query, find_in_plan
from openborder.sources.datadog.files import load_directory

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures" / "datadog"


def flat(result) -> str:
    return " ".join(result.output.split())


@pytest.fixture(scope="module")
def document():
    loaded = load_directory(FIXTURES)
    plan = core.plan(loaded.dashboards, loaded.monitors)
    return json.loads(core.render_plan_json(plan, {"kind": "datadog", "mode": "files"}, {"structure": {}}))


@pytest.fixture
def plan_dir(tmp_path):
    out = tmp_path / "plan"
    assert runner.invoke(app, ["plan", "--from-dir", str(FIXTURES), "--out", str(out), "--no-promtool"]).exit_code == 0
    return out


# ---- explaining a raw query ------------------------------------------------------------


def test_query_explanation_lists_every_rule_in_order():
    e = explain_query("avg:redis.stats.keyspace_hits{*}", {})
    assert e["promql"] == ['avg(rate({otel_metric_name="redis.keyspace.hits"}[$__rate_interval]))']
    assert [s["rule_id"] for s in e["trace"]] == ["mapping.metric", "mapping.transform.rate", "dd.space-agg"]
    assert e["tier"] == "equivalent"
    assert [f["rule_id"] for f in e["findings"]] == ["mapping.transform.rate"]  # exact steps are not findings


def test_variables_are_supplied_with_name_equals_prefix():
    e = explain_query("avg:redis.mem.used{$scope,$host}", {"scope": None, "host": "host"})
    assert e["promql"] == ['avg({otel_metric_name="redis.memory.used", host_name=~"^($host)$"})']
    assert e["tier"] == "exact"


def test_an_unknown_variable_is_a_manual_explanation_not_a_crash():
    e = explain_query("avg:redis.mem.used{$host}", {})
    assert e["tier"] == "manual" and e["promql"] == []
    assert e["findings"][0]["rule_id"] == "dd.var.unknown"


def test_monitor_query_explanation_includes_thresholds_and_the_window_rule():
    e = explain_monitor_query("avg(last_5m):avg:redis.mem.used{*} > 90")
    assert e["promql"] == ['avg(avg_over_time({otel_metric_name="redis.memory.used"}[5m])) > $__threshold']
    assert e["thresholds"] == {"failed": 90}
    assert "dd.monitor.window.pushdown" in [s["rule_id"] for s in e["trace"]]


# ---- explaining something from a plan ---------------------------------------------------


def test_find_a_panel_by_title(document):
    [e] = find_in_plan(document, "Evictions")
    assert e["tier"] == "approximate" and e["kind"] == "TimeSeriesChart panel"
    assert "per_minute(query2)" in e["original"] and any("sum:redis.keys.evicted" in q for q in e["original"])
    assert "dd.fn.per-unit.gauge" in [s["rule_id"] for s in e["trace"]]
    assert e["promql"] and e["promql"][0].startswith("60 * sum by (host_name) (rate(")


def test_find_a_placeholder_says_so(document):
    [e] = find_in_plan(document, "Slowlog query rates")
    assert e["kind"] == "placeholder panel" and e["tier"] == "manual" and e["promql"] == []


def test_find_a_monitor_by_title(document):
    [e] = find_in_plan(document, "high memory")
    assert e["kind"] == "monitor" and e["thresholds"] == {"degraded": 70, "failed": 90} and e["enabled"] is True


def test_find_by_panel_key_and_case_insensitively(document):
    assert len(find_in_plan(document, "panel-03")) == 1
    assert find_in_plan(document, "EVICTIONS")


def test_no_match_lists_what_is_available(document):
    with pytest.raises(ExplainError, match="Try one of"):
        find_in_plan(document, "nonexistent thing")


def test_too_many_matches_asks_to_narrow_down(document):
    with pytest.raises(ExplainError, match="narrow it down"):
        find_in_plan(document, "panel-")


# ---- the command -----------------------------------------------------------------------


def test_cli_query_mode_prints_the_trace_and_the_promql():
    result = runner.invoke(app, ["explain", "--query", "avg:redis.stats.keyspace_hits{*}"])
    assert result.exit_code == 0, result.output
    text = flat(result)
    assert "mapping.transform.rate" in text and "otel_metric_name" in text and "equivalent" in text


def test_cli_var_option_and_bad_var():
    ok = runner.invoke(app, ["explain", "-q", "avg:redis.mem.used{$host}", "--var", "host=host"])
    assert ok.exit_code == 0 and "^($host)$" in flat(ok)
    bad = runner.invoke(app, ["explain", "-q", "avg:redis.mem.used{*}", "--var", "oops"])
    assert bad.exit_code == 2 and "NAME=TAGPREFIX" in flat(bad)


def test_cli_monitor_mode():
    result = runner.invoke(app, ["explain", "--monitor", "-q", "avg(last_5m):avg:redis.mem.used{*} > 90"])
    assert result.exit_code == 0 and "failed 90" in flat(result)


def test_cli_find_reads_the_plan_folder(plan_dir):
    result = runner.invoke(app, ["explain", "--find", "evictions", "--plan", str(plan_dir)])
    assert result.exit_code == 0, result.output
    assert "dd.fn.per-unit.gauge" in flat(result) and "approximate" in flat(result)


def test_cli_requires_exactly_one_of_query_and_find(plan_dir):
    for args in ([], ["--query", "x", "--find", "y"]):
        result = runner.invoke(app, ["explain", *args])
        assert result.exit_code == 2 and "exactly one of" in flat(result)


def test_cli_find_without_a_plan_says_to_run_plan(tmp_path):
    result = runner.invoke(app, ["explain", "--find", "x", "--plan", str(tmp_path)])
    assert result.exit_code == 1 and "no plan.json" in flat(result)


def test_cli_json_output_is_machine_readable(plan_dir):
    result = runner.invoke(app, ["explain", "--find", "evictions", "--plan", str(plan_dir), "--json"])
    assert result.exit_code == 0
    explanations = json.loads(result.output)["explanations"]
    assert explanations[0]["tier"] == "approximate" and explanations[0]["trace"]
