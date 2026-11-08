import pytest

from openborder.sources.datadog import query_parser as qp


def test_simple_query_with_variables_and_group_by():
    q = qp.parse_expression("avg:redis.mem.used{$scope,$host} by {host,name}")
    assert q == qp.Query(
        "avg", "redis.mem.used", (qp.TagVar("scope"), qp.TagVar("host")), ("host", "name"), ()
    )


def test_filters_all_forms():
    q = qp.parse_expression("sum:m.x{*, env:prod, !host:a-1, host:web-*, role IN (a, b), az NOT IN (c), key:$k}")
    assert q.filters == (
        qp.TagFilter("env", ("prod",)),
        qp.TagFilter("host", ("a-1",), negate=True),
        qp.TagFilter("host", ("web-*",)),
        qp.TagFilter("role", ("a", "b")),
        qp.TagFilter("az", ("c",), negate=True),
        qp.TagFilter("key", ("$k",)),
    )


def test_unsupported_filters_are_kept_raw_not_dropped():
    q = qp.parse_expression("avg:m.x{a:1 OR b:2, production}")
    assert q.filters == (qp.RawFilter("a:1 OR b:2"), qp.RawFilter("production"))


def test_method_chain():
    q = qp.parse_expression("avg:m.x{*}.rollup(max, 60).fill(zero).as_count()")
    assert q.methods == (
        qp.Method("rollup", ("max", 60.0)),
        qp.Method("fill", ("zero",)),
        qp.Method("as_count", ()),
    )


def test_formula_precedence_and_functions():
    node = qp.parse_expression("(query1 / (query1 + query2)) * 100")
    assert node == qp.Bin(
        "*",
        qp.Bin("/", qp.Ref("query1"), qp.Bin("+", qp.Ref("query1"), qp.Ref("query2"))),
        qp.Num(100.0),
    )
    top = qp.parse_expression("top(query1 / 1000, 10, 'mean', 'desc')")
    assert isinstance(top, qp.Func) and top.name == "top"
    assert top.args[1:] == (qp.Num(10.0), qp.Str("mean"), qp.Str("desc"))


def test_arithmetic_between_queries_and_unary_minus():
    node = qp.parse_expression("100 * avg:a.b{*} / avg:c.d{*}")
    assert isinstance(node, qp.Bin) and node.op == "/"
    assert qp.parse_expression("-query1") == qp.Neg(qp.Ref("query1"))


def test_monitor_query():
    mq = qp.parse_monitor_query("avg(last_5m):100 * avg:redis.mem.used{*} / avg:redis.mem.maxmemory{*} > 90")
    assert (mq.time_agg, mq.window, mq.comparator, mq.threshold) == ("avg", "5m", ">", 90.0)
    assert mq.body.op == "/"


@pytest.mark.parametrize(
    "text",
    [
        "avg:m.x{",  # unterminated filter
        "avg:m.x{*} by",  # dangling by
        "avg:m.x{*}.rollup(",  # unterminated method
        "query1 +",  # dangling operator
        "avg:m.x{*} extra",  # trailing junk
        "avg:m.x{*} @",  # illegal character
    ],
)
def test_malformed_queries_raise_parse_error(text):
    with pytest.raises(qp.ParseError):
        qp.parse_expression(text)


@pytest.mark.parametrize(
    "text",
    [
        "change(avg(last_5m),last_5m):avg:m.x{*} > 50",
        "avg(last_5m):avg:m.x{*}",  # no threshold
        "logs(\"status:error\").index(\"*\").rollup(\"count\").last(\"5m\") > 4",
    ],
)
def test_unsupported_monitor_queries_raise_parse_error(text):
    with pytest.raises(qp.ParseError):
        qp.parse_monitor_query(text)
