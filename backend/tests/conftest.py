import json
from pathlib import Path

import pytest
from fakes import FakeDash0, FakeDatadog
from fastapi.testclient import TestClient

from openborder import service

CLI_TESTS = Path(__file__).resolve().parents[2] / "cli" / "tests"
FIXTURES = CLI_TESTS / "fixtures" / "datadog"
DD = {"X-Datadog-Api-Key": "dd-api-secret", "X-Datadog-App-Key": "dd-app-secret"}
TOKEN = "dash0-token-secret"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No test may touch real credentials, a real API or the working tree; plans live in a temp folder."""
    monkeypatch.setenv("OPENBORDER_DATA_DIR", str(tmp_path / "plans"))
    monkeypatch.setenv("OPENBORDER_ENV_FILE", str(tmp_path / "no-such.env"))
    for key in ("DD_API_KEY", "DD_APP_KEY", "DD_SITE", "DASH0_AUTH_TOKEN", "DASH0_API_URL", "DASH0_DATASET"):
        monkeypatch.delenv(key, raising=False)
    for key in ("OPENBORDER_ALLOW_ANY_DASH0_URL", "OPENBORDER_PLAN_TTL_HOURS", "OPENBORDER_CORS_ORIGINS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def client():
    from main import create_app

    return TestClient(create_app())


@pytest.fixture
def datadog(monkeypatch):
    FakeDatadog.reset()
    monkeypatch.setattr(service, "DatadogClient", FakeDatadog)
    return FakeDatadog


@pytest.fixture
def fake_dash0(monkeypatch):
    monkeypatch.setenv("OPENBORDER_ALLOW_ANY_DASH0_URL", "1")  # the stand-in is http://127.0.0.1
    server = FakeDash0()
    yield server
    server.close()


@pytest.fixture
def redis_files() -> list[dict]:
    """The real Redis dashboard and monitor of integrations-core, as the frontend would upload them."""
    return [
        {"name": path.name, "content": json.loads(path.read_text(encoding="utf-8"))}
        for path in sorted(FIXTURES.rglob("*.json"))
    ]


@pytest.fixture
def plan_id(client, redis_files) -> str:
    response = client.post("/api/plans", json={"source": "upload", "files": redis_files, "promtool": False})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def dash0_headers(fake: FakeDash0, **extra) -> dict:
    return {"X-Dash0-Token": TOKEN, "X-Dash0-Api-Url": fake.url, **extra}
