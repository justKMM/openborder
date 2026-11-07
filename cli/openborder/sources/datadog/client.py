"""Thin Datadog API client. Read-only. No translation logic belongs here."""

from collections.abc import Callable

import httpx

DASHBOARD_PAGE_SIZE = 100  # Datadog's default and the unit for the `start` offset


class DatadogError(Exception):
    """A problem talking to Datadog, with a message that is fit to show the user."""


def normalize_site(site: str) -> str:
    """Accept `datadoghq.eu`, `app.datadoghq.eu`, or a full URL; return the bare site."""
    site = site.strip().lower()
    for scheme in ("https://", "http://"):
        if site.startswith(scheme):
            site = site[len(scheme):]
    site = site.split("/")[0]
    for prefix in ("app.", "api."):
        if site.startswith(prefix):
            site = site[len(prefix):]
    return site


def explain_http_error(status: int, errors: list[str], site: str) -> str:
    detail = f" ({'; '.join(errors)})" if errors else ""
    if status in (401, 403):
        return (
            f"Datadog rejected the credentials with HTTP {status} on {site}{detail}. "
            "Check DD_API_KEY, DD_APP_KEY and DD_SITE: keys only work on the site they were "
            "created on, and the application key needs read access to dashboards and monitors. "
            "Fix them with `python -m openborder init`."
        )
    if status == 429:
        return "Datadog rate limit reached (HTTP 429). Wait a minute and try again."
    return f"Datadog returned HTTP {status}{detail}."


def paginate(fetch_page: Callable[[int, int], list[dict]], page_size: int) -> list[dict]:
    """Collect all items from an offset-paginated endpoint.

    Stops on a short page, or when a page adds nothing new (guards against an
    endpoint that ignores the offset and would otherwise loop forever).
    """
    items: list[dict] = []
    seen: set[str] = set()
    start = 0
    while True:
        page = fetch_page(start, page_size)
        new = [item for item in page if str(item.get("id")) not in seen]
        if not new:
            break
        seen.update(str(item.get("id")) for item in new)
        items.extend(new)
        if len(page) < page_size:
            break
        start += page_size
    return items


class DatadogClient:
    def __init__(self, api_key: str, app_key: str, site: str = "datadoghq.eu", timeout: float = 30.0):
        self.site = normalize_site(site)
        self._http = httpx.Client(
            base_url=f"https://api.{self.site}",
            headers={
                "DD-API-KEY": api_key,
                "DD-APPLICATION-KEY": app_key,
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    def __enter__(self) -> "DatadogClient":
        return self

    def __exit__(self, *exc) -> None:
        self._http.close()

    def _get(self, path: str, params: dict | None = None):
        try:
            response = self._http.get(path, params=params)
        except httpx.TransportError as e:
            raise DatadogError(
                f"Could not reach api.{self.site} ({type(e).__name__}). "
                "Check your network connection and DD_SITE."
            ) from e
        if response.status_code != 200:
            try:
                errors = [str(e) for e in response.json().get("errors", [])]
            except (ValueError, AttributeError):
                errors = []
            raise DatadogError(explain_http_error(response.status_code, errors, self.site))
        return response.json()

    def list_dashboards(self) -> list[dict]:
        """Dashboard summaries (id, title, layout_type, ...). Custom and cloned dashboards only."""

        def fetch(start: int, count: int) -> list[dict]:
            body = self._get("/api/v1/dashboard", {"count": count, "start": start})
            return body.get("dashboards", [])

        return paginate(fetch, DASHBOARD_PAGE_SIZE)

    def list_monitors(self, monitor_tags: str | None = None) -> list[dict]:
        """All monitors. With no `page` parameter Datadog returns every match in one response."""
        params = {"monitor_tags": monitor_tags} if monitor_tags else None
        return self._get("/api/v1/monitor", params)
