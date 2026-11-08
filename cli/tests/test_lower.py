"""One test row per translation rule: Datadog input, exact PromQL output, fixed tier, rule id.

The rows double as the evidence behind docs/COMPATIBILITY.md.
"""

import pytest

from openborder.ir import Tier
from openborder.mappings import load_mappings
from openborder.sources.datadog.lower import Ctx, translate
from openborder.targets.promql import render

EXACT, EQUIVALENT, APPROXIMATE, MANUAL = Tier.EXACT, Tier.EQUIVALENT, Tier.APPROXIMATE, Tier.MANUAL
MAPPING = load_mappings()

USED = '{otel_metric_name="redis.memory.used"}'
HITS = '{otel_metric_name="redis.keyspace.hits"}'
EVICTED = '{otel_metric_name="redis.keys.evicted"}'


def run(text, named=None, variables=None, defaults=None, **ctx_kwargs):
    ctx = Ctx(
        MAPPING,
        {"scope": None, "host": "host"} if variables is None else variables,
        var_defaults={"scope": "*", "host": "*"} if defaults is None else defaults,
        **ctx_kwargs,
    )
    for name, query in (named or {}).items():
        t = translate(query, ctx, name)
        assert t.expr is not None, f"setup query {name} failed: {t.findings}"
        ctx.named[name] = t.expr
    return translate(text, ctx, "test"), ctx


# (datadog, expected promql, tier, rule id that must be in the trace)
QUERY_CASES = [
    ("avg:redis.mem.used{*}", f"avg({USED})", EXACT, "mapping.metric"),
    ("sum:redis.keys{*}", 'sum({otel_metric_name="redis.db.keys"})', EXACT, "dd.space-agg"),
    ("avg:redis.mem.used{*} by {host}", f"avg by (host_name) ({USED})", EXACT, "mapping.tag"),
    ("avg:redis.mem.used{env:prod}", f'avg({{otel_metric_name="redis.memory.used", env="prod"}})', APPROXIMATE, "mapping.unmapped-tag"),
    ("avg:redis.mem.used{$scope,$host}", f'avg({{otel_metric_name="redis.memory.used", host_name=~"$host"}})', EXACT, "dd.var.prefixed"),
    ("avg:redis.mem.used{host:web-1}", f'avg({{otel_metric_name="redis.memory.used", host_name="web-1"}})', EXACT, "dd.filter.eq"),
    ("avg:redis.mem.used{!host:web-1}", f'avg({{otel_metric_name="redis.memory.used", host_name!="web-1"}})', EXACT, "dd.filter.eq"),
    ("avg:redis.mem.used{host:web-*}", f'avg({{otel_metric_name="redis.memory.used", host_name=~"web-.*"}})', EXACT, "dd.filter.wildcard"),
    ("avg:redis.mem.used{host:a.b*}", f'avg({{otel_metric_name="redis.memory.used", host_name=~"a\\\\.b.*"}})', EXACT, "dd.filter.wildcard"),
    ("avg:redis.mem.used{host IN (a, b)}", f'avg({{otel_metric_name="redis.memory.used", host_name=~"a|b"}})', EXACT, "dd.filter.in"),
    ("avg:redis.mem.used{host NOT IN (a, b)}", f'avg({{otel_metric_name="redis.memory.used", host_name!~"a|b"}})', EXACT, "dd.filter.in"),
    ("avg:redis.mem.used{host:Web-1}", f'avg({{otel_metric_name="redis.memory.used", host_name="Web-1"}})', EQUIVALENT, "dd.filter.case"),
    ("avg:redis.mem.used{*}.rollup(max, 60)", 'avg(max_over_time({otel_metric_name="redis.memory.used"}[60s]))', EXACT, "dd.rollup"),
    ("avg:redis.mem.used{*}.rollup(60)", 'avg(avg_over_time({otel_metric_name="redis.memory.used"}[60s]))', EXACT, "dd.rollup"),
    ("avg:redis.mem.used{*}.rollup(sum)", 'avg(sum_over_time({otel_metric_name="redis.memory.used"}[$__interval]))', EQUIVALENT, "dd.rollup.auto"),
    ("avg:redis.mem.used{*}.fill(null)", f"avg({USED})", EXACT, "dd.fill.null"),
    ("avg:redis.mem.used{*}.fill(zero)", f"avg({USED})", APPROXIMATE, "dd.fill.other"),
    # Datadog per-second rates (RATE_KEYS) -> rate() of the OTel cumulative counter
    ("avg:redis.stats.keyspace_hits{*}", f"avg(rate({HITS}[$__rate_interval]))", EQUIVALENT, "mapping.transform.rate"),
    (
        "avg:redis.cpu.sys{*}",
        'avg(rate({otel_metric_name="redis.cpu.time", state="sys"}[$__rate_interval]))',
        EQUIVALENT,
        "mapping.transform.rate",
    ),
    ("avg:redis.stats.keyspace_hits{*}.as_rate()", f"avg(rate({HITS}[$__rate_interval]))", EQUIVALENT, "dd.as-rate"),
    ("sum:redis.stats.keyspace_hits{*}.as_count()", f"sum(increase({HITS}[$__interval]))", EQUIVALENT, "dd.as-count"),
    ("avg:redis.mem.used{*}.as_count()", f"avg({USED})", APPROXIMATE, "dd.as-count"),
    (
        "avg:redis.stats.keyspace_hits{*}.rollup(avg, 60)",
        f"avg(avg_over_time(rate({HITS}[$__rate_interval])[60s:]))",
        APPROXIMATE,
        "dd.rollup.on-rate",
    ),
    # no OTel counterpart
    (
        "avg:redis.info.latency_ms{*}",
        'avg({otel_metric_name="redis.info.latency_ms"})',
        APPROXIMATE,
        "mapping.unmapped-metric",
    ),
]


@pytest.mark.parametrize("text, promql, tier, rule_id", QUERY_CASES, ids=[c[0] for c in QUERY_CASES])
def test_query_rules(text, promql, tier, rule_id):
    t, _ = run(text)
    assert t.expr is not None, t.findings
    assert render(t.expr) == promql
    assert t.tier == tier
    assert rule_id in [s.rule_id for s in t.trace]


# ---- semantics that must never regress ---------------------------------------------------


def test_raw_totals_are_not_wrapped_in_rate():
    """Datadog submits evicted/expired/rejected as raw running totals (GAUGE_KEYS), so applying rate()
    would silently change what the chart means."""
    for metric in ("redis.keys.evicted", "redis.keys.expired", "redis.net.rejected"):
        t, _ = run(f"sum:{metric}{{*}}")
        assert "rate(" not in render(t.expr), metric
        assert t.tier == EXACT


def test_rates_are_wrapped_in_rate_before_aggregating():
    t, _ = run("sum:redis.net.commands{*}")
    assert render(t.expr) == 'sum(rate({otel_metric_name="redis.commands.processed"}[$__rate_interval]))'


def test_free_scope_variable_is_exact_at_default_but_approximate_otherwise():
    default, ctx = run("avg:redis.mem.used{$scope}")
    assert default.tier == EXACT and ctx.dropped_vars == {"scope"}
    custom, _ = run("avg:redis.mem.used{$scope}", defaults={"scope": "env:prod", "host": "*"})
    assert custom.tier == APPROXIMATE


def test_monitor_context_uses_the_window_instead_of_dashboard_variables():
    t, _ = run("avg:redis.stats.keyspace_hits{*}", rate_window="5m", interval="1m", range=None)
    assert render(t.expr) == f"avg(rate({HITS}[5m]))"


# ---- formulas ----------------------------------------------------------------------------

Q1 = "avg:redis.mem.used{*} by {host}"
Q2 = "max:redis.mem.maxmemory{*} by {host}"
MAX = '{otel_metric_name="redis.maxmemory"}'


def test_formula_matching_groupings_is_exact():
    t, _ = run("(query1 / query2) * 100", named={"query1": Q1, "query2": Q2})
    assert render(t.expr) == f"avg by (host_name) ({USED}) / max by (host_name) ({MAX}) * 100"
    assert t.tier == EXACT


def test_formula_with_mismatched_groupings_is_approximate_and_explicit():
    t, _ = run("query1 / query2", named={"query1": Q1, "query2": "max:redis.mem.maxmemory{*}"})
    assert render(t.expr) == f"avg by (host_name) ({USED}) / on () group_left max({MAX})"
    assert t.tier == APPROXIMATE
    assert "dd.arith.mismatched" in [f.rule_id for f in t.findings]


def test_formula_parentheses_are_preserved_where_precedence_requires():
    t, _ = run("query1 / (query2 + 1)", named={"query1": "avg:redis.mem.used{*}", "query2": "avg:redis.mem.maxmemory{*}"})
    assert render(t.expr) == f"avg({USED}) / (avg({MAX}) + 1)"


def test_scalar_arithmetic_and_math_functions():
    t, _ = run("log10(query1) * 2", named={"query1": "avg:redis.mem.used{*}"})
    assert render(t.expr) == f"log10(avg({USED})) * 2"
    assert t.tier == EXACT


def test_per_minute_on_a_rate_just_scales():
    t, _ = run("per_minute(query1)", named={"query1": "sum:redis.net.commands{*}"})
    assert render(t.expr).startswith("60 * sum(rate(")
    # scaling a rate is exact; the rate() itself is reported (as equivalent) on the query it belongs to,
    # and a panel's tier is the worst over all its queries and formulas
    assert t.tier == EXACT
    _, ctx = run("sum:redis.net.commands{*}")
    assert run("sum:redis.net.commands{*}")[0].tier == EQUIVALENT


def test_per_minute_on_a_raw_total_becomes_rate_and_is_approximate():
    t, _ = run("per_minute(query1)", named={"query1": "sum:redis.keys.evicted{*} by {host}"})
    assert render(t.expr) == f"60 * sum by (host_name) (rate({EVICTED}[$__rate_interval]))"
    assert t.tier == APPROXIMATE


def test_diff_uses_offset_on_every_selector():
    t, _ = run("diff(query1)", named={"query1": "sum:redis.net.rejected{*}"})
    assert render(t.expr) == (
        'sum({otel_metric_name="redis.connections.rejected"}) - '
        'sum({otel_metric_name="redis.connections.rejected"} offset $__interval)'
    )
    assert t.tier == APPROXIMATE


def test_top_ranks_over_the_time_range():
    t, _ = run("top(query1 / 1000, 10, 'mean', 'desc')", named={"query1": "sum:redis.net.clients{*} by {host}"})
    assert render(t.expr) == (
        'topk(10, avg_over_time((sum by (host_name) ({otel_metric_name="redis.clients.connected"}) / 1000)[$__range:]))'
    )
    assert t.tier == APPROXIMATE


def test_top_is_unavailable_in_monitors():
    t, _ = run("top(query1, 5, 'mean', 'desc')", named={"query1": "avg:redis.mem.used{*}"}, range=None)
    assert t.tier == MANUAL and t.expr is None


# ---- manual: unsupported constructs become findings, never exceptions ---------------------


@pytest.mark.parametrize(
    "text, rule_id",
    [
        ("p95:redis.mem.used{*}", "dd.space-agg.unsupported"),
        ("avg:redis.mem.used{*}.anomalies()", "dd.method.unsupported"),
        ("anomalies(avg:redis.mem.used{*}, 'basic', 2)", "dd.fn.unsupported"),
        ("avg:redis.mem.used{a:1 OR b:2}", "dd.filter.unsupported"),
        ("avg:redis.mem.used{production}", "dd.filter.unsupported"),
        ("avg:redis.mem.used{$nope}", "dd.var.unknown"),
        ("avg:redis.mem.used{*}.rollup(median, 60)", "dd.rollup.unsupported-agg"),
        ("avg:redis.mem.used{", "dd.query.parse-error"),
        ("query9 * 2", "dd.formula.ref-unavailable"),
        ("top(avg:redis.mem.used{*}, 5, 'area', 'desc')", "dd.fn.top"),
    ],
)
def test_unsupported_constructs_are_manual_findings(text, rule_id):
    t, _ = run(text)
    assert t.tier == MANUAL
    assert t.expr is None
    assert rule_id in [f.rule_id for f in t.findings]
