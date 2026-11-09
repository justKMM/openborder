"""service.py: the functions the CLI and the web backend share. They return data and never print or exit."""

import json
from pathlib import Path

import pytest
from fakes import FakeDatadog

from openborder import service
from openborder.apply import load_plan
from openborder.dash0_client import Dash0Client
from openborder.sources.datadog.files import load_directory, load_documents

FIXTURES = Path(__file__).parent / "fixtures" / "datadog"
TOKEN = "tok-secret-123"
FILE_SOURCE = {"kind": "datadog", "mode": "files", "path": "fixtures"}


def file_inputs(source=FILE_SOURCE) -> service.Inputs:
    loaded = load_directory(FIXTURES)
    return service.Inputs(loaded.dashboards, loaded.monitors, loaded.skipped, source)


@pytest.fixture
def datadog(monkeypatch):
    FakeDatadog.reset()
    monkeypatch.setattr(service, "DatadogClient", FakeDatadog)
    return FakeDatadog


# ---- discover -----------------------------------------------------------------------------------


def test_discovery_document_is_trimmed_and_sorted(datadog):
    found = service.run_discover("k", "a", "datadoghq.eu")
    document = service.discovery_document(found)
    assert document["site"] == "datadoghq.eu"
    assert [m["name"] for m in document["monitors"]] == ["A", "b"]  # case-insensitive order
    assert document["monitors"][0]["tags"] == []
    assert set(document["dashboards"][0]) == {"id", "title", "description", "layout_type", "modified_at"}


def test_discover_filters_by_name_and_passes_the_tag_to_datadog(datadog):
    found = service.run_discover("k", "a", "datadoghq.eu", name="redis", tag="integration:redisdb")
    assert [d["id"] for d in found.dashboards] == ["aaa-111"] and found.monitors == []
    assert "monitors:integration:redisdb" in datadog.calls


# ---- plan: selecting inputs ---------------------------------------------------------------------


def test_fetch_from_datadog_gets_full_definitions_and_records_the_source(datadog):
    inputs = service.fetch_from_datadog("k", "a", "datadoghq.eu", dashboard_ids=["bbb-222"])
    assert [d["id"] for d in inputs.dashboards] == ["bbb-222"]
    assert inputs.source == {"kind": "datadog", "mode": "api", "site": "datadoghq.eu"}  # never any credentials
    assert "get:aaa-111" not in datadog.calls


def test_fetch_from_datadog_can_skip_a_kind_without_calling_for_it(datadog):
    service.fetch_from_datadog("k", "a", "datadoghq.eu", dashboards=False)
    assert datadog.calls == ["monitors:None"]
    datadog.calls.clear()
    service.fetch_from_datadog("k", "a", "datadoghq.eu", monitors=False)
    assert not any(c.startswith("monitors") for c in datadog.calls)


def test_unknown_dashboard_ids_are_refused_with_a_cli_hint(datadog):
    with pytest.raises(service.ServiceError) as caught:
        service.fetch_from_datadog("k", "a", "datadoghq.eu", dashboard_ids=["nope", "bbb-222"])
    assert "nope" in str(caught.value) and "bbb-222" not in str(caught.value)
    assert "discover" in caught.value.hint and "discover" not in str(caught.value)


def test_select_inputs_filters_by_name_tag_and_kind():
    inputs = file_inputs()
    assert inputs.dashboards and inputs.monitors
    assert service.select_inputs(inputs, dashboards=False).dashboards == []
    assert service.select_inputs(inputs, monitors=False).monitors == []
    assert service.select_inputs(inputs, name="redis - overview").monitors == []
    assert service.select_inputs(inputs, monitor_tag="integration:redisdb").monitors  # exact tag match on files
    assert service.select_inputs(inputs, name="memory").dashboards == []


def test_select_inputs_with_nothing_left_says_so_and_only_the_api_hints_at_discover():
    with pytest.raises(service.ServiceError, match="Nothing to plan") as from_files:
        service.select_inputs(file_inputs(), name="zzz")
    assert from_files.value.hint == ""
    api = service.Inputs([], [], [], {"kind": "datadog", "mode": "api", "site": "datadoghq.eu"})
    with pytest.raises(service.ServiceError) as from_api:
        service.select_inputs(api)
    assert "discover" in from_api.value.hint


def test_the_api_already_filtered_monitors_by_tag_so_select_does_not_do_it_again():
    api = service.Inputs([], [{"name": "m", "type": "query alert", "query": "x", "tags": ["a:b"]}], [], {"mode": "api"})
    assert service.select_inputs(api, monitor_tag="a:b,c:d").monitors == api.monitors


def test_uploaded_documents_are_sorted_like_exported_files():
    loaded = load_directory(FIXTURES)
    documents = [
        ("dash.json", loaded.dashboards[0]),
        ("mon.json", loaded.monitors[0]),
        ("junk.json", {"hello": 1}),
        ("both.json", [loaded.dashboards[0], 5]),  # a list is split; the 5 is reported, not ignored
    ]
    result = load_documents(documents)
    assert len(result.dashboards) == 2 and len(result.monitors) == 1
    assert [label for label, _ in result.skipped] == ["junk.json", "both.json"]


# ---- plan: running ------------------------------------------------------------------------------


def test_run_plan_writes_the_folder_and_returns_the_document(tmp_path):
    run = service.run_plan(file_inputs(), tmp_path / "out", promtool_check=False)
    out = tmp_path / "out"
    assert not run.failed
    assert json.loads((out / "plan.json").read_bytes()) == run.document
    assert run.document["summary"]["dashboards"] == 1 and run.document["summary"]["monitors"] == 1
    assert (out / "perses" / "redis-overview.json").exists() and (out / "check-rules.json").exists()
    assert run.validation["promtool"] == {"ran": False, "reason": "skipped (--no-promtool)"}


def test_run_plan_is_deterministic_and_replaces_stale_dashboards(tmp_path):
    out = tmp_path / "out"
    service.run_plan(file_inputs(), out, promtool_check=False)
    first = {p.name: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    (out / "perses" / "stale.json").write_text("{}", encoding="utf-8")
    service.run_plan(file_inputs(), out, promtool_check=False)
    assert {p.name: p.read_bytes() for p in out.rglob("*") if p.is_file()} == first


def test_run_plan_shows_a_status_while_promtool_runs(tmp_path, monkeypatch):
    seen = []

    class Status:
        def __init__(self, message):
            seen.append(message)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(service.promtool, "check", lambda expressions: service.promtool.PromtoolResult(ran=True, checked=len(expressions)))
    run = service.run_plan(file_inputs(), tmp_path / "out", status=Status)
    assert seen and "promtool" in seen[0] and run.validation["promtool"]["ran"] is True


def test_a_promtool_error_marks_the_run_failed_but_still_writes_the_plan(tmp_path, monkeypatch):
    bad = service.promtool.PromtoolResult(ran=True, checked=1, errors={"monitor x": "parse error"})
    monkeypatch.setattr(service.promtool, "check", lambda expressions: bad)
    run = service.run_plan(file_inputs(), tmp_path / "out")
    assert run.failed and (tmp_path / "out" / "plan.json").exists()


def test_plan_json_never_holds_credentials(tmp_path):
    run = service.run_plan(
        file_inputs({"kind": "datadog", "mode": "api", "site": "datadoghq.eu"}), tmp_path / "out", promtool_check=False
    )
    assert run.document["source"] == {"kind": "datadog", "mode": "api", "site": "datadoghq.eu"}


# ---- apply --------------------------------------------------------------------------------------


@pytest.fixture
def planned(tmp_path):
    out = tmp_path / "plan"
    service.run_plan(file_inputs(), out, promtool_check=False)
    return out


def test_apply_summary_describes_the_import_without_sending_anything(planned, fake_dash0):
    summary = service.apply_summary(load_plan(planned), fake_dash0.url, "default")
    assert summary["target"] == {"api_url": fake_dash0.url, "dataset": "default"}
    dashboard = summary["dashboards"][0]
    assert dashboard["title"] == "Redis - Overview" and dashboard["panels"] == 36 and dashboard["placeholders"] == 4
    rule = summary["check_rules"][0]
    assert rule["enabled"] is True and rule["thresholds"] == {"degraded": 70, "failed": 90}
    assert summary["not_imported"] == [] and fake_dash0.requests == []


def test_run_apply_imports_reports_each_result_and_writes_apply_json(planned, fake_dash0):
    seen = []
    outcome = service.run_apply(
        load_plan(planned), Dash0Client(TOKEN, fake_dash0.url), planned, on_result=lambda kind, entry: seen.append((kind, entry["status"]))
    )
    assert outcome.failures == 0 and seen == [("dashboard", "imported"), ("check_rules", "imported")]
    assert [r["path"] for r in fake_dash0.requests] == ["/api/import/dashboard", "/api/import/check-rules"]
    assert json.loads((planned / "apply.json").read_text(encoding="utf-8")) == outcome.results
    assert TOKEN not in (planned / "apply.json").read_text(encoding="utf-8").replace(fake_dash0.url, "")


def test_run_apply_collects_failures_instead_of_raising(planned, fake_dash0):
    fake_dash0.responses["/api/import/dashboard"] = (400, {"error": "unknown panel kind"})
    outcome = service.run_apply(load_plan(planned), Dash0Client(TOKEN, fake_dash0.url), planned)
    assert outcome.failures == 1
    assert outcome.results["dashboards"][0]["status"] == "failed" and "unknown panel kind" in outcome.results["dashboards"][0]["error"]
    assert outcome.results["check_rules"]["status"] == "imported"  # a rejected dashboard does not stop the rules
