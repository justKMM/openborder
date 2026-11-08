"""Datadog AST -> IR expressions. The only place (with the mapping YAML) where semantics are decided.

Every rule has a fixed rule_id and a fixed tier (see docs/COMPATIBILITY.md). A rule records a TraceStep
always, and a Finding whenever its tier is above EXACT. Anything we cannot translate raises Unsupported,
which becomes a MANUAL finding for that query only.
"""

import re
from dataclasses import dataclass, field

from openborder.ir import (
    Aggregate,
    BinOp,
    Call,
    Expr,
    Finding,
    Matcher,
    Number,
    RangeFn,
    Selector,
    Subquery,
    Tier,
    TraceStep,
    Translation,
)
from openborder.mappings import Mapping
from openborder.sources.datadog import query_parser as qp
from openborder.targets.promql import render

EXACT, EQUIVALENT, APPROXIMATE, MANUAL = Tier.EXACT, Tier.EQUIVALENT, Tier.APPROXIMATE, Tier.MANUAL


class Unsupported(Exception):
    def __init__(self, rule_id: str, message: str):
        super().__init__(message)
        self.rule_id = rule_id
        self.message = message


class Recorder:
    def __init__(self, ref: str):
        self.ref = ref
        self.findings: list[Finding] = []
        self.trace: list[TraceStep] = []

    def step(self, rule_id: str, tier: Tier, before: str, after: str, message: str | None = None) -> None:
        self.trace.append(TraceStep(rule_id, tier, before, after))
        if tier > EXACT:
            finding = Finding(tier, rule_id, message or f"{before} -> {after}", self.ref)
            if finding not in self.findings:
                self.findings.append(finding)

    def result(self, expr: Expr | None, original: str) -> Translation:
        if any(f.tier == MANUAL for f in self.findings):
            expr = None
        return Translation(expr, tuple(self.findings), tuple(self.trace), original)


@dataclass
class Ctx:
    mapping: Mapping
    variables: dict[str, str | None]  # Datadog template variable name -> tag prefix (None = free-form)
    var_defaults: dict[str, str] = field(default_factory=dict)  # template variable name -> default value
    dropped_vars: set[str] = field(default_factory=set)  # free-form variables dropped by any query
    rate_window: str = "$__rate_interval"
    interval: str = "$__interval"
    range: str | None = "$__range"
    named: dict[str, Expr] = field(default_factory=dict)  # formula `queryN` -> translated expression


def translate(text: str, ctx: Ctx, ref: str) -> Translation:
    """Translate one Datadog query or formula. Never raises: problems become findings."""
    rec = Recorder(ref)
    expr = None
    try:
        expr = lower_node(qp.parse_expression(text), ctx, rec)
    except qp.ParseError as e:
        rec.step("dd.query.parse-error", MANUAL, text, "", f"could not parse the query: {e}")
    except Unsupported as e:
        rec.step(e.rule_id, MANUAL, text, "", e.message)
    return rec.result(expr, text)


# ---- nodes -------------------------------------------------------------------------------


def lower_node(node, ctx: Ctx, rec: Recorder) -> Expr:
    if isinstance(node, qp.Num):
        return Number(node.value)
    if isinstance(node, qp.Neg):
        inner = lower_node(node.arg, ctx, rec)
        return Number(-inner.value) if isinstance(inner, Number) else BinOp("-", Number(0), inner)
    if isinstance(node, qp.Ref):
        if node.name not in ctx.named:
            raise Unsupported("dd.formula.ref-unavailable", f"formula refers to '{node.name}', which is not an available query")
        return ctx.named[node.name]
    if isinstance(node, qp.Query):
        return lower_query(node, ctx, rec)
    if isinstance(node, qp.Bin):
        return lower_bin(node, ctx, rec)
    if isinstance(node, qp.Func):
        return lower_func(node, ctx, rec)
    raise Unsupported("dd.query.unexpected", f"unexpected {type(node).__name__} in an expression")


# ---- metric queries ----------------------------------------------------------------------

_SPACE_AGGS = {"avg", "sum", "min", "max"}
_ROLLUP_FNS = {
    "avg": "avg_over_time",
    "min": "min_over_time",
    "max": "max_over_time",
    "sum": "sum_over_time",
    "count": "count_over_time",
}
_REGEX_SPECIAL = set(".+?()|[]{}^$\\")


def _regex_value(value: str) -> str:
    return "".join(".*" if ch == "*" else ("\\" + ch if ch in _REGEX_SPECIAL else ch) for ch in value)


def _label(tag: str, ctx: Ctx, rec: Recorder) -> str:
    mapped = ctx.mapping.tags.get(tag)
    if mapped:
        if mapped != tag:
            rec.step("mapping.tag", EXACT, tag, mapped)
        return mapped
    safe = re.sub(r"[^A-Za-z0-9_]", "_", tag)
    rec.step(
        "mapping.unmapped-tag",
        APPROXIMATE,
        tag,
        safe,
        f"tag '{tag}' has no mapping; it is used as label '{safe}', which may not exist on the OpenTelemetry series",
    )
    return safe


def _matchers(filters, ctx: Ctx, rec: Recorder) -> list[Matcher]:
    out: list[Matcher] = []
    for f in filters:
        if isinstance(f, qp.TagVar):
            if f.name not in ctx.variables:
                raise Unsupported("dd.var.unknown", f"filter uses ${f.name}, which is not a template variable of this dashboard")
            prefix = ctx.variables[f.name]
            if prefix is None:
                ctx.dropped_vars.add(f.name)
                if ctx.var_defaults.get(f.name, "*") == "*":
                    # Identical results under the default; the lost ability to filter by an arbitrary
                    # tag is reported once for the dashboard (dd.var.free-scope), not on every query.
                    rec.step("dd.var.free-scope", EXACT, f"${f.name}", "(dropped; default is *)")
                else:
                    rec.step(
                        "dd.var.free-scope",
                        APPROXIMATE,
                        f"${f.name}",
                        "(dropped)",
                        f"${f.name} is a free-form scope variable with a non-default value "
                        f"({ctx.var_defaults[f.name]!r}) and no label to map to; the filter was dropped, "
                        "so this panel shows more series than it did in Datadog",
                    )
            else:
                label = _label(prefix, ctx, rec)
                out.append(Matcher(label, "=~", f"${f.name}"))
                rec.step("dd.var.prefixed", EXACT, f"${f.name}", f'{label}=~"${f.name}"')
        elif isinstance(f, qp.TagFilter):
            label = _label(f.key, ctx, rec)
            if any(v.startswith("$") for v in f.values):
                if len(f.values) != 1:
                    raise Unsupported("dd.filter.unsupported", f"filter on '{f.key}' mixes variables and values")
                name = f.values[0][1:]
                if name not in ctx.variables:
                    raise Unsupported("dd.var.unknown", f"filter uses ${name}, which is not a template variable of this dashboard")
                out.append(Matcher(label, "!~" if f.negate else "=~", f"${name}"))
                rec.step("dd.var.prefixed", EXACT, f"{f.key}:${name}", f'{label}=~"${name}"')
                continue
            for v in f.values:
                if v != v.lower():
                    rec.step(
                        "dd.filter.case",
                        EQUIVALENT,
                        v,
                        v,
                        f"Datadog lowercases tag values but OpenTelemetry attribute values keep their case; "
                        f"'{v}' only matches if the attribute is exactly that",
                    )
            if len(f.values) == 1 and "*" not in f.values[0]:
                op = "!=" if f.negate else "="
                out.append(Matcher(label, op, f.values[0]))
                rec.step("dd.filter.eq", EXACT, f"{f.key}:{f.values[0]}", f'{label}{op}"{f.values[0]}"')
            else:
                op = "!~" if f.negate else "=~"
                pattern = "|".join(_regex_value(v) for v in f.values)
                out.append(Matcher(label, op, pattern))
                rule = "dd.filter.in" if len(f.values) > 1 else "dd.filter.wildcard"
                rec.step(rule, EXACT, f"{f.key}:{','.join(f.values)}", f'{label}{op}"{pattern}"')
        else:
            raise Unsupported(
                "dd.filter.unsupported", f"filter '{f.text}' (an OR/AND expression or a bare tag) cannot be expressed as label matchers"
            )
    return out


def lower_query(q: qp.Query, ctx: Ctx, rec: Recorder) -> Expr:
    entry = ctx.mapping.metrics.get(q.metric)
    if entry and entry.otel:
        metric, fixed = entry.otel, [Matcher(k, "=", v) for k, v in entry.matchers]
        rec.step("mapping.metric", EXACT, q.metric, metric)
    else:
        metric, fixed = q.metric, []
        rec.step(
            "mapping.unmapped-metric",
            APPROXIMATE,
            q.metric,
            q.metric,
            f"'{q.metric}' has no known OpenTelemetry equivalent; it is queried under its Datadog name "
            "and will probably show no data until it is mapped",
        )
    selector = Selector(metric, tuple(fixed + _matchers(q.filters, ctx, rec)))
    transform = entry.transform if entry and entry.otel else "none"
    base: Expr = selector
    if transform == "rate":
        base = RangeFn("rate", selector, ctx.rate_window)
        rec.step(
            "mapping.transform.rate",
            EQUIVALENT,
            q.metric,
            render(base),
            "Datadog's agent reports a per-second rate computed between check runs; PromQL rate() over the window "
            "is the same quantity but is smoothed over a different window",
        )
    base = _apply_methods(base, q.methods, transform, ctx, rec)

    if q.space_agg not in _SPACE_AGGS:
        raise Unsupported("dd.space-agg.unsupported", f"space aggregator '{q.space_agg}:' has no PromQL translation")
    by = tuple(_label(t, ctx, rec) for t in q.group_by)
    rec.step("dd.space-agg", EXACT, q.space_agg, q.space_agg)
    return Aggregate(q.space_agg, by, base)


def _apply_methods(base: Expr, methods, transform: str, ctx: Ctx, rec: Recorder) -> Expr:
    for m in methods:
        if m.name == "rollup":
            base = _rollup(base, m.args, ctx, rec)
        elif m.name == "as_rate":
            if transform == "rate":
                rec.step("dd.as-rate", EQUIVALENT, ".as_rate()", "(already a rate)", ".as_rate() on a metric that is already a rate is a no-op")
            elif isinstance(base, Selector):
                base = RangeFn("rate", base, ctx.rate_window)
                rec.step(
                    "dd.as-rate",
                    APPROXIMATE,
                    ".as_rate()",
                    render(base),
                    ".as_rate() on a metric that Datadog reports as a gauge was translated to rate(); the meaning may differ",
                )
            else:
                raise Unsupported("dd.as-rate", ".as_rate() after other time functions has no translation")
        elif m.name == "as_count":
            if isinstance(base, RangeFn) and base.fn == "rate":
                base = RangeFn("increase", base.arg, ctx.interval)
                rec.step("dd.as-count", EQUIVALENT, ".as_count()", render(base), "increase() extrapolates and may not be an integer; Datadog counts are integers")
            else:
                rec.step("dd.as-count", APPROXIMATE, ".as_count()", "(ignored)", ".as_count() on a gauge was ignored")
        elif m.name == "fill":
            if not m.args or m.args[0] == "null":
                rec.step("dd.fill.null", EXACT, ".fill(null)", "(default)")
            else:
                rec.step("dd.fill.other", APPROXIMATE, f".fill({m.args[0]})", "(ignored)", f".fill({m.args[0]}) has no PromQL equivalent; gaps are left as they are")
        else:
            raise Unsupported("dd.method.unsupported", f".{m.name}() has no translation")
    return base


def _rollup(base: Expr, args: tuple, ctx: Ctx, rec: Recorder) -> Expr:
    agg, seconds = "avg", None
    for a in args:
        if isinstance(a, str):
            agg = a.lower()
        else:
            seconds = int(a)
    fn = _ROLLUP_FNS.get(agg)
    if fn is None:
        raise Unsupported("dd.rollup.unsupported-agg", f".rollup({agg}) has no PromQL translation")
    window = f"{seconds}s" if seconds else ctx.interval
    if isinstance(base, Selector):
        out: Expr = RangeFn(fn, base, window)
        if seconds:
            rec.step("dd.rollup", EXACT, f".rollup({agg}, {seconds})", render(out))
        else:
            rec.step("dd.rollup.auto", EQUIVALENT, f".rollup({agg})", render(out), "no rollup interval given; the dashboard's automatic interval is used")
        return out
    out = RangeFn(fn, Subquery(base, window))
    rec.step(
        "dd.rollup.on-rate",
        APPROXIMATE,
        f".rollup({agg})",
        render(out),
        "rollup applied to a rate series is evaluated as a PromQL subquery, which resamples the rate",
    )
    return out


# ---- arithmetic --------------------------------------------------------------------------


def _labels(e: Expr) -> frozenset | None:
    """Label set of a vector expression; None for scalars."""
    if isinstance(e, Number):
        return None
    if isinstance(e, Aggregate):
        return frozenset(e.by)
    if isinstance(e, BinOp):
        if e.on is not None:
            return frozenset(e.on)
        left = _labels(e.lhs)
        return left if left is not None else _labels(e.rhs)
    if isinstance(e, Call):
        return _labels(e.args[0])
    return frozenset({"*"})


def lower_bin(node: qp.Bin, ctx: Ctx, rec: Recorder) -> Expr:
    lhs, rhs = lower_node(node.lhs, ctx, rec), lower_node(node.rhs, ctx, rec)
    la, lb = _labels(lhs), _labels(rhs)
    if la is None or lb is None:
        rec.step("dd.arith.scalar", EXACT, node.op, node.op)
        return BinOp(node.op, lhs, rhs)
    if la == lb:
        rec.step("dd.arith.vector", EXACT, node.op, node.op)
        return BinOp(node.op, lhs, rhs)
    on = tuple(sorted(la & lb))
    group = "group_left" if la > lb else "group_right" if lb > la else None
    rec.step(
        "dd.arith.mismatched",
        APPROXIMATE,
        node.op,
        f"{node.op} on ({', '.join(on)})",
        f"operands are grouped by different tags ({sorted(la)} vs {sorted(lb)}); matched on ({', '.join(on)}), "
        "which may combine series differently from Datadog",
    )
    return BinOp(node.op, lhs, rhs, on=on, group=group)


# ---- functions ---------------------------------------------------------------------------

_MATH = {"abs": "abs", "log2": "log2", "log10": "log10", "sqrt": "sqrt", "exp": "exp"}
_PER_UNIT = {"per_second": 1, "per_minute": 60, "per_hour": 3600}
_TOP_AGGS = {
    "mean": "avg_over_time",
    "avg": "avg_over_time",
    "max": "max_over_time",
    "min": "min_over_time",
    "sum": "sum_over_time",
    "last": None,
}


def map_selectors(e: Expr, f, into_ranges: bool = False) -> Expr:
    """Rebuild `e`, replacing selectors with f(selector). Selectors already inside a range function
    are left alone unless `into_ranges` is set."""

    def go(x: Expr) -> Expr:
        if isinstance(x, Selector):
            return f(x)
        if isinstance(x, RangeFn):
            if isinstance(x.arg, Selector):
                return RangeFn(x.fn, f(x.arg), x.window) if into_ranges else x
            return RangeFn(x.fn, go(x.arg), x.window)
        if isinstance(x, Subquery):
            return Subquery(go(x.arg), x.range, x.step)
        if isinstance(x, Aggregate):
            return Aggregate(x.op, x.by, go(x.arg), x.param)
        if isinstance(x, BinOp):
            return BinOp(x.op, go(x.lhs), go(x.rhs), x.on, x.group)
        if isinstance(x, Call):
            return Call(x.fn, tuple(go(a) for a in x.args))
        return x

    return go(e)


def _is_rate(e: Expr) -> bool:
    """True when every selector in `e` is already wrapped in rate()/increase()."""
    plain, rated = [], []

    def find(x: Expr) -> None:
        if isinstance(x, Selector):
            plain.append(x)
        elif isinstance(x, RangeFn):
            if isinstance(x.arg, Selector):
                if x.fn in ("rate", "increase"):
                    rated.append(x)
            else:
                find(x.arg)
        elif isinstance(x, (Subquery, Aggregate)):
            find(x.arg)
        elif isinstance(x, BinOp):
            find(x.lhs)
            find(x.rhs)
        elif isinstance(x, Call):
            for a in x.args:
                find(a)

    find(e)
    return bool(rated) and not plain


def _arity(node: qp.Func, n: int) -> None:
    if len(node.args) != n:
        raise Unsupported("dd.fn.arity", f"{node.name}() expects {n} argument(s), got {len(node.args)}")


def lower_func(node: qp.Func, ctx: Ctx, rec: Recorder) -> Expr:
    name = node.name
    if name in _MATH:
        _arity(node, 1)
        out = Call(_MATH[name], (lower_node(node.args[0], ctx, rec),))
        rec.step("dd.fn.math", EXACT, name, _MATH[name])
        return out

    if name in _PER_UNIT:
        _arity(node, 1)
        inner = lower_node(node.args[0], ctx, rec)
        factor = _PER_UNIT[name]
        if _is_rate(inner):
            rec.step("dd.fn.per-unit", EXACT, name, f"x {factor}" if factor != 1 else "x")
        else:
            inner = map_selectors(inner, lambda s: RangeFn("rate", s, ctx.rate_window))
            rec.step(
                "dd.fn.per-unit.gauge",
                APPROXIMATE,
                name,
                "rate()",
                f"{name}() on a series that is not already a rate was translated to a per-second PromQL rate() "
                "(of the cumulative value); Datadog's exact normalisation for this series type is not reproduced",
            )
        return inner if factor == 1 else BinOp("*", Number(factor), inner)

    if name == "diff":
        _arity(node, 1)
        inner = lower_node(node.args[0], ctx, rec)
        shifted = map_selectors(inner, lambda s: Selector(s.metric, s.matchers, ctx.interval), into_ranges=True)
        out = BinOp("-", inner, shifted)
        rec.step(
            "dd.fn.diff",
            APPROXIMATE,
            "diff()",
            render(out),
            f"diff() is the difference to the previous point; translated as the value minus the value one "
            f"interval ({ctx.interval}) earlier, which can differ at the edges of the window",
        )
        return out

    if name == "top":
        _arity(node, 4)
        n, agg, order = node.args[1], node.args[2], node.args[3]
        if not (isinstance(n, qp.Num) and isinstance(agg, qp.Str) and isinstance(order, qp.Str)):
            raise Unsupported("dd.fn.top", "top() with non-literal arguments has no translation")
        if agg.value not in _TOP_AGGS or order.value not in ("asc", "desc"):
            raise Unsupported("dd.fn.top", f"top(..., '{agg.value}', '{order.value}') has no translation")
        if ctx.range is None:
            raise Unsupported("dd.fn.top", "top() needs the dashboard time range and is not available in monitors")
        inner = lower_node(node.args[0], ctx, rec)
        fn = _TOP_AGGS[agg.value]
        ranked: Expr = inner if fn is None else RangeFn(fn, Subquery(inner, ctx.range))
        out = Aggregate("topk" if order.value == "desc" else "bottomk", (), ranked, Number(n.value))
        rec.step(
            "dd.fn.top",
            APPROXIMATE,
            "top()",
            render(out),
            "Datadog picks the top series once over the whole time range; PromQL re-ranks at every step over a "
            "trailing window, so the set of series shown can differ",
        )
        return out

    raise Unsupported("dd.fn.unsupported", f"{name}() has no PromQL translation")
