"""Test doubles shared by the CLI and backend tests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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


class FakeDatadog:
    """Stands in for DatadogClient, which always talks to https://api.<site> and so cannot target a local server.

    Install it with `monkeypatch.setattr(service, "DatadogClient", FakeDatadog)`. `calls` records what was asked and
    `created_with` the credentials each client was built with; an api_key of "bad" fails like a rejected key.
    """

    calls: list[str] = []
    created_with: list[tuple] = []
    summaries = [{"id": "aaa-111", "title": "Redis - Overview"}, {"id": "bbb-222", "title": "Other"}]

    def __init__(self, api_key, app_key, site="datadoghq.eu"):
        from openborder.sources.datadog.client import DatadogError

        if api_key == "bad":
            raise DatadogError("Datadog rejected the credentials with HTTP 403.")
        self.site = site
        FakeDatadog.created_with.append((api_key, app_key, site))

    @classmethod
    def reset(cls):
        cls.calls, cls.created_with = [], []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def list_dashboards(self):
        FakeDatadog.calls.append("list_dashboards")
        return self.summaries

    def get_dashboard(self, dashboard_id):
        FakeDatadog.calls.append(f"get:{dashboard_id}")
        return {"id": dashboard_id, "title": dashboard_id, "widgets": []}

    def list_monitors(self, monitor_tags=None):
        FakeDatadog.calls.append(f"monitors:{monitor_tags}")
        return [{"id": 2, "name": "b", "type": "metric alert", "tags": ["z"]}, {"id": 1, "name": "A", "type": "query alert"}]
