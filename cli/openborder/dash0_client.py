"""Thin Dash0 API client for the two import endpoints. No translation logic belongs here."""

import re
from urllib.parse import urlparse

import httpx

_DATASET = re.compile(r"^[a-zA-Z0-9_-]{3,26}$")  # Dash0's documented dataset slug pattern
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


class Dash0Error(Exception):
    """A problem talking to Dash0, with a message that is fit to show the user."""


def explain_http_error(status: int, body: str, what: str, dataset: str, atomic: bool = False) -> str:
    detail = " ".join(body.split())[:600]
    suffix = f" Dash0 said: {detail}" if detail else ""
    nothing = " Nothing from this request was written." if atomic else ""
    if status == 401:
        return f"Dash0 rejected the auth token (HTTP 401). Check DASH0_AUTH_TOKEN with `python -m openborder config show`.{suffix}"
    if status == 403:
        return (
            f"Dash0 refused the request (HTTP 403): the token may lack permission to import {what} "
            f"into dataset '{dataset}'.{suffix}"
        )
    if status == 404:
        return (
            f"Dash0 returned HTTP 404 for the {what} import. DASH0_API_URL must be your organisation's API "
            f"endpoint for its region.{suffix}"
        )
    if status in (400, 422):
        return f"Dash0 rejected the {what} as invalid (HTTP {status}).{nothing}{suffix}"
    if status == 413:
        return f"Dash0 says the {what} request is too large (HTTP 413).{nothing}{suffix}"
    if status == 429:
        return "Dash0 rate limit reached (HTTP 429). Wait a minute and run `apply` again."
    return f"Dash0 returned HTTP {status} for the {what} import.{nothing}{suffix}"


class Dash0Client:
    def __init__(self, token: str, api_url: str, dataset: str = "default", timeout: float = 60.0):
        parsed = urlparse(api_url)
        if parsed.scheme != "https" and parsed.hostname not in _LOCAL_HOSTS:
            raise Dash0Error(f"DASH0_API_URL must start with https:// (got {api_url!r}); the auth token is never sent over plain http.")
        if not parsed.hostname:
            raise Dash0Error(f"DASH0_API_URL {api_url!r} is not a valid URL.")
        if not _DATASET.match(dataset):
            raise Dash0Error(f"DASH0_DATASET {dataset!r} is not a valid dataset slug (3-26 letters, digits, '_' or '-').")
        self.api_url = api_url.rstrip("/")
        self.dataset = dataset
        self._http = httpx.Client(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
        )

    def __enter__(self) -> "Dash0Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, body: dict, what: str, atomic: bool = False) -> dict:
        try:
            response = self._http.post(path, params={"dataset": self.dataset}, json=body)
        except httpx.TransportError as e:
            raise Dash0Error(f"Could not reach {self.api_url} ({type(e).__name__}). Check your network connection and DASH0_API_URL.") from e
        if response.status_code != 200:
            raise Dash0Error(explain_http_error(response.status_code, response.text, what, self.dataset, atomic))
        try:
            return response.json()
        except ValueError:
            return {}

    def import_dashboard(self, body: dict) -> dict:
        """POST /api/import/dashboard. With metadata.dash0Extensions.id set, an existing dashboard is overwritten."""
        return self._post("/api/import/dashboard", body, "dashboard")

    def import_check_rules(self, items: list[dict]) -> dict:
        """POST /api/import/check-rules: atomic bulk import (one invalid item rejects the whole batch)."""
        return self._post("/api/import/check-rules", {"items": items}, "check rules", atomic=True)
