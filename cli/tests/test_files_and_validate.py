import json

from openborder import validate
from openborder.sources.datadog.files import load_directory


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


def test_loader_classifies_and_reports_what_it_skips(tmp_path):
    write(tmp_path / "dash.json", {"title": "D", "widgets": []})
    write(tmp_path / "sub" / "mon.json", {"name": "M", "type": "query alert", "query": "q"})
    write(tmp_path / "wrapped.json", {"definition": {"name": "W", "type": "query alert", "query": "q"}})
    write(tmp_path / "many.json", [{"name": "A", "type": "metric alert", "query": "q"}, {"title": "E", "widgets": []}])
    write(tmp_path / "pipeline.json", {"name": "log pipeline", "filter": {}})
    write(tmp_path / "broken.json", "{not json")
    loaded = load_directory(tmp_path)
    assert len(loaded.dashboards) == 2 and len(loaded.monitors) == 3
    reasons = dict(loaded.skipped)
    assert reasons["pipeline.json"] == "not a Datadog dashboard or monitor"
    assert reasons["broken.json"].startswith("unreadable JSON")


def test_loader_order_is_deterministic(tmp_path):
    for name in ("b", "a", "c"):
        write(tmp_path / f"{name}.json", {"title": name, "widgets": []})
    assert [d["title"] for d in load_directory(tmp_path).dashboards] == ["a", "b", "c"]


def good_dashboard():
    query = {"kind": "TimeSeriesQuery", "spec": {"plugin": {"kind": "PrometheusTimeSeriesQuery", "spec": {"query": "up"}}}}
    return {
        "kind": "Dashboard",
        "metadata": {"name": "d"},
        "spec": {
            "panels": {"p1": {"kind": "Panel", "spec": {"plugin": {"kind": "TimeSeriesChart", "spec": {}}, "queries": [query]}}},
            "layouts": [{"kind": "Grid", "spec": {"items": [{"x": 0, "y": 0, "width": 24, "height": 6, "content": {"$ref": "#/spec/panels/p1"}}]}}],
        },
    }


def test_valid_dashboard_has_no_errors():
    assert validate.structure_errors(good_dashboard()) == []


def test_dashboard_structure_errors():
    body = good_dashboard()
    body["spec"]["layouts"][0]["spec"]["items"][0]["content"]["$ref"] = "#/spec/panels/missing"
    body["spec"]["layouts"][0]["spec"]["items"][0]["width"] = 30
    errors = validate.structure_errors(body)
    assert any("missing panel 'missing'" in e for e in errors)
    assert any("does not fit" in e for e in errors)
    assert any("not placed in any layout" in e for e in errors)

    body = good_dashboard()
    body["spec"]["panels"]["p1"]["spec"]["queries"][0]["spec"]["plugin"]["spec"]["query"] = ""
    assert any("empty query" in e for e in validate.structure_errors(body))


def test_rule_validation():
    ok = {"name": "n", "expression": "up > $__threshold", "thresholds": {"failed": 1}, "interval": "1m", "enabled": True}
    assert validate.rule_errors(ok) == []
    assert any("no thresholds" in e for e in validate.rule_errors({"name": "n", "expression": "up > $__threshold"}))
    assert validate.rule_errors({"name": "n", "expression": "up", "bogus": 1})  # unknown field is rejected
    assert validate.rule_errors({"expression": "up"})  # name is required
