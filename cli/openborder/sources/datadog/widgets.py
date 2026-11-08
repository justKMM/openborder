"""Datadog dashboard JSON -> IR Dashboard (widgets, groups, layout, template variables)."""

import dataclasses

from openborder.ids import slug
from openborder.ir import (
    Aggregate,
    Dashboard,
    Finding,
    GridPos,
    Panel,
    PanelQuery,
    Section,
    Tier,
    TraceStep,
    Variable,
    worst,
)
from openborder.mappings import Mapping
from openborder.sources.datadog import query_parser as qp
from openborder.sources.datadog.lower import Ctx, Recorder, _label, translate

EXACT, EQUIVALENT, APPROXIMATE, MANUAL = Tier.EXACT, Tier.EQUIVALENT, Tier.APPROXIMATE, Tier.MANUAL

PERSES_COLUMNS = 24
DD_COLUMNS = 12
HEIGHT_SCALE = 3  # Perses grid rows per Datadog row; calibrate against Dash0's rendering
MIN_HEIGHT = 3

_AGGREGATOR_TO_CALCULATION = {"avg": "mean", "max": "max", "min": "min", "last": "last", "sum": "sum"}
_UNITS = {"%": "percent", "secs": "seconds", "millisecond": "milliseconds", "second": "seconds", "byte": "bytes"}
_METRIC_WIDGETS = {"timeseries", "query_value"}


# ---- helpers -----------------------------------------------------------------------------


def _split_queries(text: str) -> list[str]:
    """Legacy `q` strings may hold several comma-separated queries."""
    parts, depth, current = [], 0, []
    for ch in text:
        depth += ch in "{("
        depth -= ch in "})"
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def collect_queries(widget_def: dict) -> list[str]:
    """Every query-like string in a widget, for quoting in placeholders and descriptions."""
    out: list[str] = []
    requests = widget_def.get("requests")
    for r in requests if isinstance(requests, list) else []:
        if r.get("q"):
            out.append(r["q"])
        out += [q["query"] for q in r.get("queries", []) if q.get("query")]
        # a formula that is only a query name (`query1`) says nothing the query itself doesn't
        out += [
            f["formula"]
            for f in r.get("formulas", [])
            if f.get("formula") and f["formula"] not in out and not f["formula"].isidentifier()
        ]
        if isinstance(r.get("query"), dict) and r["query"].get("query_string"):
            out.append(r["query"]["query_string"])
    return out


def _scaled(layout: dict | None, scale: float, auto_y: int) -> GridPos:
    if not layout:
        return GridPos(0, auto_y, PERSES_COLUMNS, MIN_HEIGHT * 3)
    x0 = round(layout.get("x", 0) * scale)
    x1 = round((layout.get("x", 0) + layout.get("width", DD_COLUMNS)) * scale)
    x1 = min(x1, PERSES_COLUMNS)
    return GridPos(
        x=x0,
        y=int(layout.get("y", 0)) * HEIGHT_SCALE,
        w=max(1, x1 - x0),
        h=max(MIN_HEIGHT, int(layout.get("height", 2)) * HEIGHT_SCALE),
    )


def _placeholder(key, title, grid, widget_type, rule_id, reason, rec: Recorder, definition: dict, extra=()) -> Panel:
    if not any(f.rule_id == rule_id and f.message == reason for f in rec.findings):
        rec.step(rule_id, MANUAL, widget_type, "placeholder", reason)
    queries = collect_queries(definition)
    lines = [f"**Not migrated: {title or widget_type}**", "", f"Datadog widget type `{widget_type}`. {reason}"]
    if queries:
        lines += ["", "Original query:"] + [f"- `{q}`" for q in queries[:5]]
    lines += ["", "The full original definition is kept in `plan.json`."]
    return Panel(
        key=key,
        title=title or widget_type,
        kind="Markdown",
        grid=grid,
        options={"text": "\n".join(lines)},
        findings=tuple(rec.findings),
        trace=tuple(rec.trace),
        original=definition,
        source_queries=tuple(queries),
        placeholder=True,
    )


# ---- metric widgets ----------------------------------------------------------------------


def _unit(name: str | None) -> str | None:
    return _UNITS.get(name) if name else None


def _legend(expr, alias: str | None) -> str | None:
    if isinstance(expr, Aggregate) and expr.by:
        return " - ".join("{{" + label + "}}" for label in expr.by)
    return alias or None


def _metric_panel(key, title, grid, definition, base_ctx: Ctx, ref: str) -> Panel:
    widget_type = definition["type"]
    rec = Recorder(ref)
    requests = definition.get("requests")
    if not isinstance(requests, list) or not requests:
        return _placeholder(key, title, grid, widget_type, "dd.widget.shape", "the widget has no query requests we can read", rec, definition)

    translations = []
    panel_queries: list[PanelQuery] = []
    aggregators: list[str] = []
    n_named = 0
    unit = None
    for r in requests:
        ctx = dataclasses.replace(base_ctx, named={})
        if r.get("queries") or r.get("formulas"):
            queries = r.get("queries", [])
            if any(q.get("data_source", "metrics") != "metrics" for q in queries):
                sources = sorted({q.get("data_source") for q in queries})
                return _placeholder(key, title, grid, widget_type, "dd.datasource.unsupported", f"data source(s) {sources} are not metrics and have no Dash0 metric-query equivalent", rec, definition)
            n_named = max(n_named, len(queries))
            for q in queries:
                t = translate(q["query"], ctx, f"{ref}/{q['name']}")
                translations.append(t)
                aggregators.append(q.get("aggregator") or "avg")
                if t.expr is not None:
                    ctx.named[q["name"]] = t.expr
            formulas = r.get("formulas") or [{"formula": q["name"]} for q in queries]
            for f in formulas:
                t = translate(f["formula"], ctx, f"{ref}/formula")
                translations.append(t)
                if t.expr is not None:
                    panel_queries.append(PanelQuery(t.expr, _legend(t.expr, f.get("alias"))))
                unit = unit or (f.get("number_format", {}).get("unit", {}) or {}).get("unit_name")
        elif r.get("q"):
            for text in _split_queries(r["q"]):
                t = translate(text, ctx, f"{ref}/q")
                translations.append(t)
                if t.expr is not None:
                    panel_queries.append(PanelQuery(t.expr, _legend(t.expr, None)))
        else:
            return _placeholder(key, title, grid, widget_type, "dd.widget.shape", "the request has neither `queries` nor `q`", rec, definition)

    findings = list(rec.findings)
    trace: list[TraceStep] = list(rec.trace)
    for t in translations:
        findings += [f for f in t.findings if f not in findings]
        trace += t.trace
    manual = [f for f in findings if f.tier == MANUAL]
    if manual:
        rec2 = Recorder(ref)
        rec2.findings = findings
        rec2.trace = trace
        return _placeholder(key, title, grid, widget_type, manual[0].rule_id, manual[0].message, rec2, definition)

    def note(rule_id: str, tier: Tier, message: str) -> None:
        f = Finding(tier, rule_id, message, ref)
        findings.append(f)
        trace.append(TraceStep(rule_id, tier, widget_type, widget_type))

    dropped = []
    if definition.get("markers"):
        dropped.append(f"{len(definition['markers'])} marker line(s)")
    if any(r.get("conditional_formats") for r in requests):
        dropped.append("conditional colour formats")
    if dropped:
        note("dd.widget.styling-dropped", EQUIVALENT, "not migrated: " + ", ".join(dropped))

    options: dict = {}
    if widget_type == "timeseries":
        options["display"] = {"area": "area", "bars": "bar"}.get(requests[0].get("display_type", "line"), "line")
        options["legend"] = bool(definition.get("show_legend"))
        if (definition.get("yaxis") or {}).get("include_zero"):
            options["y_min"] = 0
        options["unit"] = _unit(unit)
        kind = "TimeSeriesChart"
        trace.append(TraceStep("dd.widget.timeseries", EXACT, "timeseries", kind))
    else:
        kind = "StatChart"
        distinct = sorted(set(aggregators))
        options["calculation"] = _AGGREGATOR_TO_CALCULATION.get(distinct[0] if distinct else "avg", "mean")
        options["unit"] = _unit(definition.get("custom_unit") or unit)
        if "precision" in definition:
            options["decimals"] = definition["precision"]
        options["sparkline"] = "timeseries_background" in definition
        if len(distinct) > 1:
            note(
                "dd.widget.query-value.mixed-aggregators",
                APPROXIMATE,
                f"the queries use different aggregators {distinct}; only '{distinct[0]}' is applied to the whole value",
            )
        if n_named > 1:
            note(
                "dd.widget.query-value.formula",
                APPROXIMATE,
                "Datadog aggregates each query over the time range first and then evaluates the formula (a ratio of "
                "averages); the migrated panel evaluates the formula at every step and then averages (an average of "
                "ratios), so the value can differ slightly",
            )
        else:
            note(
                "dd.widget.query-value",
                EQUIVALENT,
                "Datadog aggregates over the time range; the migrated panel applies the same calculation to the "
                "returned series, so sampling can differ slightly",
            )
    return Panel(
        key=key,
        title=title,
        kind=kind,
        grid=grid,
        queries=tuple(panel_queries),
        options=options,
        findings=tuple(findings),
        trace=tuple(trace),
        original=definition,
        source_queries=tuple(collect_queries(definition)),
    )


# ---- one widget --------------------------------------------------------------------------


def _widget_panel(widget: dict, key: str, grid: GridPos, ctx: Ctx, dash_ref: str) -> Panel:
    definition = widget["definition"]
    widget_type = definition.get("type", "unknown")
    title = definition.get("title") or ""
    ref = f"{dash_ref}/{key}"
    rec = Recorder(ref)
    try:
        if widget_type == "note":
            rec.step("dd.widget.note", EXACT, "note", "Markdown")
            return Panel(
                key=key, title=title or "Note", kind="Markdown", grid=grid,
                options={"text": definition.get("content", "")},
                trace=tuple(rec.trace), original=definition,
            )
        if widget_type in _METRIC_WIDGETS:
            return _metric_panel(key, title, grid, definition, ctx, ref)
        if widget_type == "toplist":
            reason = "Dash0 has no ranked bar/table panel we can target yet; recreate it as a table or bar chart"
            return _placeholder(key, title, grid, widget_type, "dd.widget.toplist", reason, rec, definition)
        if widget_type in ("list_stream", "log_stream", "event_stream"):
            reason = "log and event streams are not metric queries; Dash0 shows logs in its own views"
            return _placeholder(key, title, grid, widget_type, "dd.widget.stream", reason, rec, definition)
        if widget_type in ("distribution", "heatmap"):
            reason = "distribution/heatmap widgets need Datadog distribution metrics, which have no translation yet"
            return _placeholder(key, title, grid, widget_type, "dd.widget.distribution", reason, rec, definition)
        return _placeholder(key, title, grid, widget_type, "dd.widget.unsupported", f"widget type '{widget_type}' is not supported", rec, definition)
    except Exception as e:  # one bad widget must not take down the dashboard
        rec = Recorder(ref)
        return _placeholder(key, title, grid, widget_type, "internal-error", f"unexpected error while translating: {type(e).__name__}: {e}", rec, definition)


# ---- dashboard ---------------------------------------------------------------------------


def convert_dashboard(data: dict, mapping: Mapping) -> Dashboard:
    title = data.get("title") or "Untitled dashboard"
    source_id = str(data.get("id") or slug(title, "dashboard"))
    dash_ref = f"dashboard:{title}"

    template_vars = data.get("template_variables") or []
    prefixes = {v["name"]: v.get("prefix") or None for v in template_vars}
    defaults = {v["name"]: v.get("default") or "*" for v in template_vars}
    ctx = Ctx(mapping, prefixes, var_defaults=defaults)

    variables = []
    for v in template_vars:
        if not prefixes[v["name"]]:
            continue
        rec = Recorder(f"{dash_ref}/variable:{v['name']}")
        label = _label(prefixes[v["name"]], ctx, rec)
        default = "$__all" if defaults[v["name"]] == "*" else defaults[v["name"]]
        variables.append(Variable(v["name"], label, default, tuple(rec.findings)))

    dashboard_findings: list[Finding] = []
    layout_type = data.get("layout_type", "ordered")
    if layout_type == "free":
        dashboard_findings.append(Finding(EQUIVALENT, "dd.layout.free", "free-form layout was snapped to the Dash0 grid; positions are approximate", dash_ref))

    counter = 0
    sections: list[Section] = []
    loose: list[dict] = []
    side_by_side = False

    def make_panels(widgets: list[dict], scale: float) -> tuple[Panel, ...]:
        nonlocal counter
        panels, auto_y = [], 0
        for w in widgets:
            counter += 1
            grid = _scaled(w.get("layout"), scale, auto_y)
            auto_y = grid.y + grid.h
            panels.append(_widget_panel(w, f"panel-{counter:02d}", grid, ctx, dash_ref))
        return tuple(panels)

    def flush_loose() -> None:
        nonlocal loose
        if loose:
            sections.append(Section(None, make_panels(loose, PERSES_COLUMNS / DD_COLUMNS)))
            loose = []

    for w in data.get("widgets", []):
        definition = w["definition"]
        if definition.get("type") != "group":
            loose.append(w)
            continue
        flush_loose()
        width = (w.get("layout") or {}).get("width") or DD_COLUMNS
        if width < DD_COLUMNS:
            side_by_side = True
        shown_title = definition.get("title") if definition.get("show_title", True) else None
        sections.append(Section(shown_title or None, make_panels(definition.get("widgets", []), PERSES_COLUMNS / width)))
    flush_loose()

    if side_by_side:
        dashboard_findings.append(
            Finding(EQUIVALENT, "dd.layout.groups-stacked", "Datadog placed some groups side by side; the migrated dashboard stacks every group vertically", dash_ref)
        )
    for name in sorted(ctx.dropped_vars):
        dashboard_findings.append(
            Finding(
                EQUIVALENT,
                "dd.var.free-scope",
                f"template variable ${name} is a free-form tag filter with no OpenTelemetry label to map to; "
                f"panels are identical under its default value, but filtering by ${name} is not available",
                dash_ref,
            )
        )
    return Dashboard(
        source_id=source_id,
        title=title,
        description=data.get("description") or "",
        variables=tuple(variables),
        sections=tuple(sections),
        findings=tuple(dashboard_findings),
        original=data,
    )
