"""IR Dashboard -> Dash0 dashboard import body (Perses dashboard format).

Format per https://www.dash0.com/docs/dash0/dashboards/reference-dashboard-source-format
Unverified against a live import: variable plugin spec, TimeSeriesChart/StatChart option names.
"""

from openborder.ids import slug, stable_id
from openborder.ir import Dashboard, Panel, Tier, Variable
from openborder.targets.promql import render

DEFAULT_DURATION = "1h"


def _description(panel: Panel) -> str:
    notes = [f"{f.tier.symbol} {f.message}" for f in panel.findings if f.tier > Tier.EXACT]
    if not notes or panel.placeholder:
        return ""
    lines = notes + [f"Datadog query: {q}" for q in panel.source_queries]
    return "\n".join(lines)


def _query(expr, legend: str | None) -> dict:
    spec = {"query": render(expr)}
    if legend:
        spec["seriesNameFormat"] = legend
    return {"kind": "TimeSeriesQuery", "spec": {"plugin": {"kind": "PrometheusTimeSeriesQuery", "spec": spec}}}


def _plugin_spec(panel: Panel) -> dict:
    o = panel.options
    if panel.kind == "Markdown":
        return {"text": o["text"]}
    fmt = {}
    if o.get("unit"):
        fmt["unit"] = o["unit"]
    if panel.kind == "StatChart":
        if "decimals" in o:
            fmt["decimalPlaces"] = o["decimals"]
        spec = {"calculation": o["calculation"]}
        if fmt:
            spec["format"] = fmt
        if o.get("sparkline"):
            spec["sparkline"] = {}
        return spec
    visual = {"display": "bar"} if o.get("display") == "bar" else {"display": "line"}
    if o.get("display") == "area":
        visual["areaOpacity"] = 0.55
    spec = {"visual": visual}
    if o.get("legend"):
        spec["legend"] = {"position": "bottom"}
    axis = {}
    if fmt:
        axis["format"] = fmt
    if "y_min" in o:
        axis["min"] = o["y_min"]
    if axis:
        spec["yAxis"] = axis
    return spec


def _panel(panel: Panel) -> dict:
    display = {"name": panel.title}
    if description := _description(panel):
        display["description"] = description
    body = {"display": display, "plugin": {"kind": panel.kind, "spec": _plugin_spec(panel)}}
    if panel.kind != "Markdown":
        body["queries"] = [_query(q.expr, q.legend) for q in panel.queries]
    return {"kind": "Panel", "spec": body}


def _variable(v: Variable) -> dict:
    return {
        "kind": "ListVariable",
        "spec": {
            "name": v.name,
            "display": {"name": v.name},
            "allowAllValue": True,
            "customAllValue": ".*",
            "allowMultiple": True,
            "defaultValue": v.default,
            "plugin": {"kind": "PrometheusLabelValuesVariable", "spec": {"labelName": v.label}},
        },
    }


def build(dashboard: Dashboard) -> dict:
    panels, layouts = {}, []
    for section in dashboard.sections:
        items = []
        for p in section.panels:
            panels[p.key] = _panel(p)
            items.append(
                {
                    "x": p.grid.x,
                    "y": p.grid.y,
                    "width": p.grid.w,
                    "height": p.grid.h,
                    "content": {"$ref": f"#/spec/panels/{p.key}"},
                }
            )
        spec: dict = {"items": items}
        if section.title:
            spec["display"] = {"title": section.title, "collapse": {"open": True}}
        layouts.append({"kind": "Grid", "spec": spec})
    display = {"name": dashboard.title}
    if dashboard.description:
        display["description"] = dashboard.description
    return {
        "kind": "Dashboard",
        "metadata": {
            "name": slug(dashboard.title, "dashboard"),
            "dash0Extensions": {"id": stable_id("dashboard", dashboard.source_id)},
        },
        "spec": {
            "display": display,
            "duration": DEFAULT_DURATION,
            "variables": [_variable(v) for v in dashboard.variables],
            "panels": panels,
            "layouts": layouts,
        },
    }
