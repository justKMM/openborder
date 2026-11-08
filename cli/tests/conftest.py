import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

TESTS = Path(__file__).parent


class FakeDash0:
    """A real HTTP server standing in for Dash0, so the actual client code runs over a real socket.

    `requests` records every call; `responses` maps a path to (status, payload) to override the default
    behaviour (dashboards are echoed back, check rules answer {"created": n}).
    """

    def __init__(self):
        self.requests: list[dict] = []
        self.responses: dict[str, tuple[int, object]] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(raw) if raw else None
                path, _, query = self.path.partition("?")
                outer.requests.append({"path": path, "query": query, "auth": self.headers.get("Authorization"), "body": body})
                status, payload = outer.responses.get(path) or outer._default(path, body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def _default(path, body):
        if path == "/api/import/check-rules":
            return 200, {"created": len(body["items"])}
        return 200, body

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_dash0():
    server = FakeDash0()
    yield server
    server.close()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No test may ever touch real credentials, call a real API, or write into the working tree.

    Credentials come from a temp .env that does not exist, real DD_/DASH0_ variables are cleared, and the
    working directory is a temp dir (so a stray `plan` writes its ./migration there).
    """
    monkeypatch.setenv("OPENBORDER_ENV_FILE", str(tmp_path / "no-such.env"))
    for key in ("DD_API_KEY", "DD_APP_KEY", "DD_SITE", "DASH0_AUTH_TOKEN", "DASH0_API_URL", "DASH0_DATASET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def assert_golden():
    """Compare bytes to a golden file. Rewrite them with UPDATE_GOLDEN=1, then review the git diff."""

    def check(relative: str, content: bytes) -> None:
        path = TESTS / "golden" / relative
        if os.environ.get("UPDATE_GOLDEN"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            return
        assert path.exists(), f"missing golden file {relative}; create it with UPDATE_GOLDEN=1 and review it"
        expected = path.read_bytes().replace(b"\r\n", b"\n")  # tolerate git autocrlf on Windows
        assert expected == content, f"{relative} differs from the golden file; if intended, rerun with UPDATE_GOLDEN=1 and review the diff"

    return check
