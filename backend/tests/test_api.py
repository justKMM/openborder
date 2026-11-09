"""The HTTP surface: what the frontend calls. The engine itself is covered by cli/tests; these check the wiring,
the credential handling, the error shape and that nothing is imported without an explicit confirmation."""

import json
import os
import time
from urllib.parse import quote

import pytest
from conftest import DD, FIXTURES, TOKEN, dash0_headers

from openborder import __version__

GOLDEN = FIXTURES.parent.parent / "golden" / "redis"


def error(response, status: int, code: str) -> str:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"} and body["error"]["code"] == code, body
    return body["error"]["message"]


# ---- health and the error shape -----------------------------------------------------------------


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["version"] == __version__ and isinstance(body["docker_installed"], bool)


def test_unknown_routes_and_methods_use_the_same_error_shape(client):
    error(client.get("/api/nope"), 404, "not_found")
    error(client.put("/api/health"), 405, "method_not_allowed")


def test_validation_errors_say_what_is_wrong_and_never_echo_the_input(client):
    response = client.post("/api/plans", json={"source": "upload", "files": [], "api_key": "SECRET-VALUE"})
    message = error(response, 422, "invalid_request")
    assert "api_key" in message and "SECRET-VALUE" not in response.text  # a misspelt field is refused, not echoed
    assert "files" in error(client.post("/api/plans", json={"source": "upload"}), 422, "invalid_request")


# ---- credentials --------------------------------------------------------------------------------


def test_discover_needs_datadog_credentials_and_ignores_the_servers_own(client, datadog, monkeypatch):
    monkeypatch.setenv("DD_API_KEY", "server-key")
    monkeypatch.setenv("DD_APP_KEY", "server-app")
    error(client.get("/api/discover"), 400, "missing_credentials")
    assert datadog.created_with == []  # the server's environment is never used for a caller


def test_only_one_datadog_key_is_refused(client, datadog):
    message = error(client.get("/api/discover", headers={"X-Datadog-Api-Key": "k"}), 400, "missing_credentials")
    assert "X-Datadog-App-Key" in message


@pytest.mark.parametrize("site", ["evil.example.com", "datadoghq.eu.evil.com", "localhost", "169.254.169.254"])
def test_only_real_datadog_sites_are_called(client, datadog, site):
    error(client.get("/api/discover", headers={**DD, "X-Datadog-Site": site}), 400, "invalid_datadog_site")
    assert datadog.created_with == []  # no outgoing request on the caller's behalf


@pytest.mark.parametrize(
    "given, used", [(None, "datadoghq.eu"), ("app.datadoghq.eu", "datadoghq.eu"), ("US5.datadoghq.com", "us5.datadoghq.com")]
)
def test_datadog_sites_are_normalised(client, datadog, given, used):
    headers = {**DD, **({"X-Datadog-Site": given} if given else {})}
    assert client.get("/api/discover", headers=headers).status_code == 200
    assert datadog.created_with == [("dd-api-secret", "dd-app-secret", used)]


# ---- discover -----------------------------------------------------------------------------------


def test_discover_lists_and_filters(client, datadog):
    body = client.get("/api/discover", headers=DD).json()
    assert body["site"] == "datadoghq.eu"
    assert [m["name"] for m in body["monitors"]] == ["A", "b"]
    only = client.get("/api/discover", headers=DD, params={"name": "redis", "tag": "integration:redisdb"}).json()
    assert [d["id"] for d in only["dashboards"]] == ["aaa-111"] and only["monitors"] == []
    assert "monitors:integration:redisdb" in datadog.calls


def test_rejected_datadog_credentials_are_a_clear_error_that_does_not_leak_them(client, datadog):
    response = client.get("/api/discover", headers={**DD, "X-Datadog-Api-Key": "bad"})
    message = error(response, 502, "datadog_error")
    assert "403" in message and "bad" not in response.text.replace("rejected", "")


# ---- plans --------------------------------------------------------------------------------------


def test_creating_a_plan_from_uploaded_exports(client, redis_files):
    response = client.post("/api/plans", json={"source": "upload", "files": redis_files, "promtool": False})
    assert response.status_code == 201
    body = response.json()
    plan = body["plan"]
    assert body["failed"] is False and len(body["id"]) >= 16
    assert plan["source"] == {"kind": "datadog", "mode": "upload"}
    assert plan["summary"]["dashboards"] == 1 and plan["summary"]["monitors"] == 1
    assert plan["summary"]["panels"]["manual"] == 4  # 32 of 36 panels migrated
    assert sum(plan["summary"]["panels"].values()) == 36
    dashboard = next(i for i in plan["items"] if i["kind"] == "dashboard")
    assert dashboard["panels"][0]["trace"] and "original" in dashboard["panels"][0]  # what the review screen draws
    assert client.get(f"/api/plans/{body['id']}").json() == body


def test_the_api_produces_exactly_the_artifacts_the_cli_golden_files_hold(client, plan_id):
    for relative in ("perses/redis-overview.json", "check-rules.json"):
        served = client.get(f"/api/plans/{plan_id}/artifacts/{relative}")
        assert served.headers["content-type"].startswith("application/json")
        assert served.content == (GOLDEN / relative).read_bytes().replace(b"\r\n", b"\n")
    assert client.get(f"/api/plans/{plan_id}/artifacts/migration.log").headers["content-type"].startswith("text/plain")


def test_creating_a_plan_from_datadog_uses_the_callers_credentials(client, datadog):
    body = client.post("/api/plans", headers=DD, json={"dashboard_ids": ["bbb-222"], "include_monitors": False, "promtool": False}).json()
    assert body["plan"]["source"] == {"kind": "datadog", "mode": "api", "site": "datadoghq.eu"}
    assert [i["title"] for i in body["plan"]["items"]] == ["bbb-222"]
    assert datadog.created_with == [("dd-api-secret", "dd-app-secret", "datadoghq.eu")]
    assert "dd-api-secret" not in json.dumps(body) and "dd-app-secret" not in json.dumps(body)


def test_a_datadog_plan_without_credentials_is_refused(client, datadog):
    error(client.post("/api/plans", json={"promtool": False}), 400, "missing_credentials")


def test_unknown_dashboard_ids_are_refused_without_the_cli_hint(client, datadog):
    message = error(client.post("/api/plans", headers=DD, json={"dashboard_ids": ["nope"]}), 400, "bad_request")
    assert "nope" in message and "discover" not in message


def test_nothing_selected_is_an_error_and_leaves_nothing_behind(client, redis_files, tmp_path):
    message = error(client.post("/api/plans", json={"source": "upload", "files": redis_files, "name": "zzz"}), 400, "bad_request")
    assert "Nothing to plan" in message and "discover" not in message
    assert not list((tmp_path / "plans").glob("*"))


def test_unreadable_uploads_are_reported_not_ignored(client, redis_files):
    files = redis_files + [{"name": "notes.json", "content": {"hello": "world"}}]
    skipped = client.post("/api/plans", json={"source": "upload", "files": files, "promtool": False}).json()["plan"]["summary"]["skipped"]
    assert skipped == [{"what": "notes.json", "why": "not a Datadog dashboard or monitor"}]


def test_a_plan_that_failed_validation_is_still_returned_for_review(client, redis_files, monkeypatch):
    from openborder import service

    bad = service.promtool.PromtoolResult(ran=True, checked=1, errors={"monitor x": "parse error"})
    monkeypatch.setattr(service.promtool, "check", lambda expressions: bad)
    created = client.post("/api/plans", json={"source": "upload", "files": redis_files}).json()
    assert created["failed"] is True and created["plan"]["validation"]["promtool"]["errors"] == {"monitor x": "parse error"}
    assert client.get(f"/api/plans/{created['id']}").json()["failed"] is True


def test_an_unknown_or_malformed_plan_id_is_not_found(client):
    for bad_id in ("x" * 22, "short", "..%2F..%2Fetc", "a b"):
        for method, path in (("get", ""), ("delete", ""), ("get", "/apply"), ("get", "/artifacts/plan.json")):
            assert client.request(method, f"/api/plans/{bad_id}{path}").status_code == 404, (method, bad_id, path)
    error(client.post(f"/api/plans/{'x' * 22}/explain", json={"find": "a"}), 404, "plan_not_found")


def test_plans_can_be_deleted(client, plan_id):
    assert client.delete(f"/api/plans/{plan_id}").status_code == 204
    error(client.get(f"/api/plans/{plan_id}"), 404, "plan_not_found")
    error(client.delete(f"/api/plans/{plan_id}"), 404, "plan_not_found")


def test_old_plans_expire_when_a_new_one_is_made(client, redis_files, plan_id, tmp_path):
    folder = tmp_path / "plans" / plan_id
    old = time.time() - 48 * 3600
    for path in (folder, folder / "plan.json"):
        os.utime(path, (old, old))
    client.post("/api/plans", json={"source": "upload", "files": redis_files, "promtool": False})
    error(client.get(f"/api/plans/{plan_id}"), 404, "plan_not_found")


def test_artifacts_cannot_escape_the_plan_folder(client, plan_id, redis_files, tmp_path):
    secret = tmp_path / "plans" / "secret.txt"  # beside the plan folders, outside every plan
    secret.write_text("FILE-CONTENT-OUTSIDE-THE-PLAN", encoding="utf-8")
    other_id = client.post("/api/plans", json={"source": "upload", "files": redis_files, "promtool": False}).json()["id"]
    attempts = [
        "..%2Fsecret.txt",
        "%2E%2E%2Fsecret.txt",
        "perses%2F..%2F..%2Fsecret.txt",
        quote(secret.as_posix(), safe=""),  # an absolute path
        f"..%2F{other_id}%2Fplan.json",  # another customer's plan
    ]
    for path in attempts:
        response = client.get(f"/api/plans/{plan_id}/artifacts/{path}")
        assert response.status_code == 404, path
        assert "FILE-CONTENT-OUTSIDE" not in response.text and '"items"' not in response.text, path
    for fine in ("plan.json", "perses/redis-overview.json", "check-rules.json"):  # the guard is not just refusing everything
        assert client.get(f"/api/plans/{plan_id}/artifacts/{fine}").status_code == 200, fine
    for missing in ("perses", "nope.json"):  # a folder and an unknown file
        assert client.get(f"/api/plans/{plan_id}/artifacts/{missing}").status_code == 404, missing


# ---- explain ------------------------------------------------------------------------------------


def test_explain_a_panel_of_a_plan(client, plan_id):
    explanations = client.post(f"/api/plans/{plan_id}/explain", json={"find": "evictions"}).json()["explanations"]
    assert explanations and all(e["trace"] and e["tier"] for e in explanations)
    assert explanations[0]["promql"] and "redis.keys.evicted" in explanations[0]["promql"][0]


def test_explain_finds_nothing_with_a_hint(client, plan_id):
    assert "Nothing in the plan matches" in error(client.post(f"/api/plans/{plan_id}/explain", json={"find": "zzzz"}), 400, "explain_error")


def test_explain_a_query_without_any_plan(client):
    body = client.post("/api/explain", json={"query": "avg:redis.stats.keyspace_hits{$host}", "variables": {"host": "host"}}).json()
    [e] = body["explanations"]
    assert e["tier"] == "equivalent" and "redis.keyspace.hits" in e["promql"][0] and "rate(" in e["promql"][0]
    assert [step["rule_id"] for step in e["trace"]][0] == "mapping.metric"


def test_explain_a_monitor_query(client):
    [e] = client.post("/api/explain", json={"query": "avg(last_5m):avg:redis.mem.used{*} > 90", "monitor": True}).json()["explanations"]
    assert e["kind"] == "monitor" and e["thresholds"] == {"failed": 90}


def test_an_undeclared_variable_is_reported_not_guessed(client):
    [e] = client.post("/api/explain", json={"query": "avg:redis.mem.used{$host}"}).json()["explanations"]
    assert "dd.var.unknown" in [f["rule_id"] for f in e["findings"]]


# ---- apply: the only endpoint that writes to a customer's account -------------------------------


def apply(client, plan_id, fake, body=None, **headers):
    return client.post(f"/api/plans/{plan_id}/apply", headers=dash0_headers(fake, **headers), json=body if body is not None else {"confirm": True})


def test_apply_needs_a_dash0_token_and_ignores_the_servers_own(client, plan_id, monkeypatch):
    monkeypatch.setenv("DASH0_AUTH_TOKEN", "server-token")
    error(client.post(f"/api/plans/{plan_id}/apply", json={"confirm": True}), 400, "missing_credentials")


@pytest.mark.parametrize("url", ["https://evil.example.com", "http://api.eu-west-1.aws.dash0.com", "https://dash0.com.evil.com", "https://evil.com/.dash0.com", "http://127.0.0.1:9"])
def test_the_token_is_only_ever_sent_to_dash0(client, plan_id, url):
    response = client.post(f"/api/plans/{plan_id}/apply", headers={"X-Dash0-Token": TOKEN, "X-Dash0-Api-Url": url}, json={"confirm": True})
    error(response, 400, "invalid_dash0_url")
    assert TOKEN not in response.text


def test_a_real_dash0_address_is_accepted_and_a_bad_dataset_is_refused(client, plan_id):
    headers = {"X-Dash0-Token": TOKEN, "X-Dash0-Api-Url": "https://api.europe-west4.gcp.dash0.com", "X-Dash0-Dataset": "x"}
    error(client.post(f"/api/plans/{plan_id}/apply", headers=headers, json={"dry_run": True}), 400, "invalid_dash0_settings")
    ok = client.post(f"/api/plans/{plan_id}/apply", headers={**headers, "X-Dash0-Dataset": "default"}, json={"dry_run": True})
    assert ok.status_code == 200 and ok.json()["summary"]["target"]["api_url"] == "https://api.europe-west4.gcp.dash0.com"


def test_dry_run_describes_the_import_and_sends_nothing(client, plan_id, fake_dash0):
    body = apply(client, plan_id, fake_dash0, {"dry_run": True}).json()
    assert body["dry_run"] is True and body["results"] is None and body["failures"] == 0
    assert body["summary"]["target"] == {"api_url": fake_dash0.url, "dataset": "default"}
    assert body["summary"]["dashboards"][0]["title"] == "Redis - Overview"
    assert body["summary"]["check_rules"][0]["thresholds"] == {"degraded": 70, "failed": 90}
    assert fake_dash0.requests == []


def test_nothing_is_imported_without_an_explicit_confirmation(client, plan_id, fake_dash0):
    for body in ({}, {"confirm": False}, {"dry_run": False}):
        error(apply(client, plan_id, fake_dash0, body), 400, "confirmation_required")
    assert fake_dash0.requests == []


def test_a_confirmed_apply_imports_exactly_the_planned_files(client, plan_id, fake_dash0):
    response = apply(client, plan_id, fake_dash0)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dry_run"] is False and body["failures"] == 0
    assert body["results"]["dashboards"][0]["status"] == "imported"
    assert body["results"]["dashboards"][0]["id"] == "e5f39f83-a8e4-5492-ae31-1b4e400dc0be"  # a uuid5 of the Datadog id: stable
    assert body["results"]["check_rules"] == {"status": "imported", "sent": 1, "created": 1}
    dashboard, rules = fake_dash0.requests
    assert dashboard["path"] == "/api/import/dashboard" and rules["path"] == "/api/import/check-rules"
    assert dashboard["auth"] == rules["auth"] == f"Bearer {TOKEN}"
    assert dashboard["query"] == rules["query"] == "dataset=default"
    assert dashboard["body"] == json.loads((GOLDEN / "perses" / "redis-overview.json").read_text(encoding="utf-8"))
    assert rules["body"] == json.loads((GOLDEN / "check-rules.json").read_text(encoding="utf-8"))


def test_the_token_never_appears_in_a_response_or_a_stored_file(client, plan_id, fake_dash0, tmp_path):
    texts = [apply(client, plan_id, fake_dash0).text, client.get(f"/api/plans/{plan_id}/apply").text, client.get(f"/api/plans/{plan_id}").text]
    texts += [p.read_text(encoding="utf-8") for p in (tmp_path / "plans").rglob("*") if p.is_file()]
    assert all(TOKEN not in text for text in texts)


def test_applying_twice_overwrites_in_place(client, plan_id, fake_dash0):
    first = apply(client, plan_id, fake_dash0).json()["results"]
    second = apply(client, plan_id, fake_dash0).json()["results"]
    assert first["dashboards"][0]["id"] == second["dashboards"][0]["id"]
    sent_ids = [r["body"]["metadata"]["dash0Extensions"]["id"] for r in fake_dash0.requests if r["path"].endswith("dashboard")]
    assert len(sent_ids) == 2 and sent_ids[0] == sent_ids[1]


def test_the_result_of_the_last_apply_can_be_read_back(client, plan_id, fake_dash0):
    error(client.get(f"/api/plans/{plan_id}/apply"), 404, "not_applied")
    results = apply(client, plan_id, fake_dash0).json()["results"]
    assert client.get(f"/api/plans/{plan_id}/apply").json() == results


def test_a_rejection_by_dash0_is_a_partial_result_not_a_server_error(client, plan_id, fake_dash0):
    fake_dash0.responses["/api/import/dashboard"] = (400, {"error": "unknown panel kind"})
    response = apply(client, plan_id, fake_dash0)
    body = response.json()
    assert response.status_code == 200 and body["failures"] == 1
    assert "unknown panel kind" in body["results"]["dashboards"][0]["error"]
    assert body["results"]["check_rules"]["status"] == "imported"  # a rejected dashboard does not stop the rules


def test_a_plan_that_failed_validation_cannot_be_applied(client, redis_files, fake_dash0, monkeypatch):
    from openborder import service

    bad = service.promtool.PromtoolResult(ran=True, checked=1, errors={"monitor x": "parse error"})
    monkeypatch.setattr(service.promtool, "check", lambda expressions: bad)
    failed_id = client.post("/api/plans", json={"source": "upload", "files": redis_files}).json()["id"]
    assert "did not pass validation" in error(apply(client, failed_id, fake_dash0), 409, "plan_not_applicable")
    assert fake_dash0.requests == []


def test_an_artifact_edited_after_review_is_still_checked_before_sending(client, plan_id, fake_dash0, tmp_path):
    artifact = tmp_path / "plans" / plan_id / "perses" / "redis-overview.json"
    broken = json.loads(artifact.read_text(encoding="utf-8"))
    broken["kind"] = "NotADashboard"
    artifact.write_text(json.dumps(broken), encoding="utf-8")
    error(apply(client, plan_id, fake_dash0), 409, "plan_not_applicable")
    assert fake_dash0.requests == []


# ---- browser access -----------------------------------------------------------------------------


def test_the_dev_frontend_may_call_the_api_with_credential_headers(client):
    preflight = client.options(
        "/api/plans",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-datadog-api-key,x-dash0-token"},
    )
    assert preflight.status_code == 200 and preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
    allowed = preflight.headers["access-control-allow-headers"].lower()
    assert "x-datadog-api-key" in allowed and "x-dash0-token" in allowed


def test_other_origins_get_no_cors_permission_and_the_list_is_configurable(client, monkeypatch):
    other = client.get("/api/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in other.headers
    monkeypatch.setenv("OPENBORDER_CORS_ORIGINS", "https://app.example.com")
    from fastapi.testclient import TestClient
    from main import create_app

    configured = TestClient(create_app()).get("/api/health", headers={"Origin": "https://app.example.com"})
    assert configured.headers["access-control-allow-origin"] == "https://app.example.com"
