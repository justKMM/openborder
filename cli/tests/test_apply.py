import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openborder.cli import app
from openborder.dash0_client import Dash0Client, Dash0Error, explain_http_error

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures" / "datadog"
TOKEN = "tok-secret-123"


@pytest.fixture
def target(fake_dash0, monkeypatch):
    monkeypatch.setenv("DASH0_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("DASH0_API_URL", fake_dash0.url)
    monkeypatch.setenv("DASH0_DATASET", "default")
    return fake_dash0


@pytest.fixture
def plan_dir(tmp_path):
    out = tmp_path / "plan"
    result = runner.invoke(app, ["plan", "--from-dir", str(FIXTURES), "--out", str(out), "--no-promtool"])
    assert result.exit_code == 0, result.output
    return out


def apply(plan_dir, *args, **kwargs):
    result = runner.invoke(app, ["apply", str(plan_dir), *args], **kwargs)
    result.flat = " ".join(result.output.split())  # Rich wraps long lines; compare without the wrapping
    return result


# ---- the happy path -----------------------------------------------------------------------


def test_apply_sends_exactly_the_planned_files_to_the_right_endpoints(target, plan_dir):
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 0, result.output
    dashboard, rules = target.requests
    assert dashboard["path"] == "/api/import/dashboard" and dashboard["query"] == "dataset=default"
    assert rules["path"] == "/api/import/check-rules" and rules["query"] == "dataset=default"
    assert dashboard["auth"] == rules["auth"] == f"Bearer {TOKEN}"
    assert dashboard["body"] == json.loads((plan_dir / "perses" / "redis-overview.json").read_text(encoding="utf-8"))
    assert rules["body"] == json.loads((plan_dir / "check-rules.json").read_text(encoding="utf-8"))
    assert "Apply complete" in result.flat


def test_apply_json_records_ids_and_never_the_token(target, plan_dir):
    apply(plan_dir, "--auto-approve")
    text = (plan_dir / "apply.json").read_text(encoding="utf-8")
    assert TOKEN not in text
    result = json.loads(text)
    assert result["dashboards"][0]["status"] == "imported"
    assert result["dashboards"][0]["id"] == "e5f39f83-a8e4-5492-ae31-1b4e400dc0be"
    assert result["check_rules"] == {"status": "imported", "sent": 1, "created": 1}
    assert TOKEN not in apply(plan_dir, "--auto-approve").flat


def test_applying_twice_sends_identical_ids_so_dash0_overwrites(target, plan_dir):
    apply(plan_dir, "--auto-approve")
    apply(plan_dir, "--auto-approve")
    first, second = target.requests[:2], target.requests[2:]
    assert [r["body"] for r in first] == [r["body"] for r in second]


def test_overwritten_rules_are_reported(target, plan_dir):
    target.responses["/api/import/check-rules"] = (200, {"created": 0})
    result = apply(plan_dir, "--auto-approve")
    assert "overwrote existing rules" in result.flat


# ---- confirmation and dry run -------------------------------------------------------------


def test_dry_run_shows_the_plan_and_sends_nothing(target, plan_dir):
    result = apply(plan_dir, "--dry-run")
    assert result.exit_code == 0 and target.requests == []
    assert "Redis - Overview" in result.flat and "Dry run" in result.flat
    assert "High memory consumption" in result.flat and "enabled" in result.flat
    assert not (plan_dir / "apply.json").exists()


def test_anything_but_yes_cancels(target, plan_dir):
    for answer in ("no\n", "\n", "y\n", ""):
        result = apply(plan_dir, input=answer)
        assert result.exit_code == 1 and "Cancelled" in result.flat
    assert target.requests == []


def test_typing_yes_imports(target, plan_dir):
    result = apply(plan_dir, input="yes\n")
    assert result.exit_code == 0 and len(target.requests) == 2


# ---- the plan folder is the single source of truth ----------------------------------------


def test_hand_edited_artifact_is_imported_with_a_warning(target, plan_dir):
    path = plan_dir / "perses" / "redis-overview.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["spec"]["panels"]["panel-04"]["spec"]["display"]["name"] = "Renamed by a human"
    path.write_text(json.dumps(body), encoding="utf-8")
    result = apply(plan_dir, "--auto-approve")
    assert "edited by hand" in result.flat
    assert target.requests[0]["body"]["spec"]["panels"]["panel-04"]["spec"]["display"]["name"] == "Renamed by a human"


def test_a_broken_edit_is_refused_before_anything_is_sent(target, plan_dir):
    path = plan_dir / "perses" / "redis-overview.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["spec"]["layouts"][1]["spec"]["items"][0]["content"]["$ref"] = "#/spec/panels/does-not-exist"
    path.write_text(json.dumps(body), encoding="utf-8")
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1 and "not a valid dashboard" in result.flat and target.requests == []


def test_an_invalid_edited_rule_is_refused(target, plan_dir):
    path = plan_dir / "check-rules.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["items"][0]["bogus_field"] = 1
    path.write_text(json.dumps(body), encoding="utf-8")
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1 and "invalid rules" in result.flat and target.requests == []


def test_a_plan_that_failed_validation_is_refused(target, plan_dir):
    document = json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))
    document["validation"]["promtool"]["errors"] = {"some panel": 'group "g1", rule 1: parse error'}
    (plan_dir / "plan.json").write_text(json.dumps(document), encoding="utf-8")
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1 and "did not pass validation" in result.flat and target.requests == []


def test_missing_plan_json_says_to_run_plan(target, tmp_path):
    result = apply(tmp_path)
    assert result.exit_code == 1 and "no plan.json" in result.flat and "plan" in result.flat


def test_manual_monitors_are_listed_as_not_imported(target, tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "svc.json").write_text(
        json.dumps({"name": "Redis is down", "type": "service check", "query": '"redis.can_connect".over("*").last(2)', "options": {}}),
        encoding="utf-8",
    )
    out = tmp_path / "plan"
    assert runner.invoke(app, ["plan", "--from-dir", str(source), "--out", str(out), "--no-promtool"]).exit_code == 0
    result = apply(out, "--auto-approve")
    assert result.exit_code == 1 and "nothing to import" in result.flat  # a plan with only manual items
    assert target.requests == []


# ---- failures from Dash0 ------------------------------------------------------------------


def test_a_rejected_rule_batch_says_nothing_was_written_but_dashboards_still_go_through(target, plan_dir):
    target.responses["/api/import/check-rules"] = (400, {"message": "expression is invalid"})
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1
    assert "Nothing from this request was written" in result.flat and "expression is invalid" in result.flat
    assert [r["path"] for r in target.requests] == ["/api/import/dashboard", "/api/import/check-rules"]
    saved = json.loads((plan_dir / "apply.json").read_text(encoding="utf-8"))
    assert saved["dashboards"][0]["status"] == "imported" and saved["check_rules"]["status"] == "failed"


def test_a_rejected_dashboard_does_not_stop_the_rules(target, plan_dir):
    target.responses["/api/import/dashboard"] = (422, {"message": "unknown panel kind"})
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1 and "unknown panel kind" in result.flat
    assert [r["path"] for r in target.requests] == ["/api/import/dashboard", "/api/import/check-rules"]


def test_bad_token_points_at_the_config(target, plan_dir):
    target.responses["/api/import/dashboard"] = (401, {"message": "unauthorized"})
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1 and "auth token" in result.flat and "config show" in result.flat


def test_missing_token_is_reported_before_anything_else(plan_dir, monkeypatch):
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1 and "DASH0_AUTH_TOKEN" in result.flat


def test_plain_http_to_a_remote_host_is_refused_so_the_token_never_leaks(plan_dir, monkeypatch):
    monkeypatch.setenv("DASH0_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("DASH0_API_URL", "http://api.example.com")
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1 and "https://" in result.flat and TOKEN not in result.flat


def test_invalid_dataset_is_reported(plan_dir, monkeypatch):
    monkeypatch.setenv("DASH0_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("DASH0_API_URL", "https://api.example.com")
    monkeypatch.setenv("DASH0_DATASET", "no")
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1 and "dataset" in result.flat


def test_unreachable_server_is_a_clear_error(plan_dir, monkeypatch):
    monkeypatch.setenv("DASH0_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("DASH0_API_URL", "http://127.0.0.1:1")  # nothing listens here
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1 and "Could not reach" in result.flat


# ---- pure pieces ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, expected",
    [(401, "auth token"), (403, "permission"), (404, "DASH0_API_URL"), (400, "invalid"), (413, "too large"), (429, "rate limit"), (500, "HTTP 500")],
)
def test_error_messages(status, expected):
    assert expected in explain_http_error(status, "boom", "dashboard", "default")


def test_atomic_errors_say_nothing_was_written():
    assert "Nothing from this request was written" in explain_http_error(400, "", "check rules", "default", atomic=True)
    assert "Nothing from this request was written" not in explain_http_error(400, "", "dashboard", "default")


def test_client_validates_url_and_dataset():
    with pytest.raises(Dash0Error):
        Dash0Client("t", "http://api.example.com")
    with pytest.raises(Dash0Error):
        Dash0Client("t", "https://api.example.com", dataset="x")
    Dash0Client("t", "http://localhost:8080").__exit__()


# ---- regressions found by running `apply` for real ---------------------------------------


def test_a_plan_written_by_an_older_version_is_refused_not_partially_imported(target, plan_dir):
    document = json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))
    document["version"] = 1
    (plan_dir / "plan.json").write_text(json.dumps(document), encoding="utf-8")
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1 and "version" in result.flat and "plan" in result.flat and target.requests == []


def test_rules_that_plan_marked_importable_can_never_be_silently_skipped(target, plan_dir):
    """A plan.json without its check-rules entry used to import only the dashboard and say 'Apply complete'."""
    document = json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))
    document["check_rules"] = None
    (plan_dir / "plan.json").write_text(json.dumps(document), encoding="utf-8")
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1 and "incomplete" in result.flat and target.requests == []


def test_a_rule_count_that_disagrees_with_plan_json_is_refused(target, plan_dir):
    path = plan_dir / "check-rules.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["items"] = []
    path.write_text(json.dumps(body), encoding="utf-8")
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 1 and "holds 0" in result.flat and target.requests == []


def test_crlf_line_endings_from_git_autocrlf_are_not_a_hand_edit(target, plan_dir):
    for path in (plan_dir / "perses" / "redis-overview.json", plan_dir / "check-rules.json"):
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    result = apply(plan_dir, "--auto-approve")
    assert result.exit_code == 0, result.flat
    assert "edited by hand" not in result.flat and len(target.requests) == 2


def test_a_real_edit_is_still_detected_alongside_crlf(target, plan_dir):
    path = plan_dir / "perses" / "redis-overview.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["spec"]["display"]["name"] = "Edited"
    path.write_bytes(json.dumps(body, indent=2).replace("\n", "\r\n").encode("utf-8"))
    assert "edited by hand" in apply(plan_dir, "--auto-approve").flat
