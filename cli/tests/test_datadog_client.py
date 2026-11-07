import pytest

from openborder.report import filter_by_name
from openborder.sources.datadog.client import explain_http_error, normalize_site, paginate


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("datadoghq.eu", "datadoghq.eu"),
        ("app.datadoghq.eu", "datadoghq.eu"),
        ("https://app.datadoghq.eu/", "datadoghq.eu"),
        ("api.datadoghq.com", "datadoghq.com"),
        (" US3.datadoghq.com ", "us3.datadoghq.com"),
    ],
)
def test_normalize_site(raw, expected):
    assert normalize_site(raw) == expected


def test_auth_errors_point_to_init_and_include_site():
    for status in (401, 403):
        message = explain_http_error(status, ["Forbidden"], "datadoghq.eu")
        assert "datadoghq.eu" in message and "Forbidden" in message and "init" in message


def test_rate_limit_and_generic_errors():
    assert "429" in explain_http_error(429, [], "datadoghq.eu")
    assert "HTTP 500" in explain_http_error(500, ["boom"], "datadoghq.eu")


def _pages(items: list[dict]):
    def fetch(start: int, count: int) -> list[dict]:
        return items[start : start + count]

    return fetch


def test_paginate_collects_every_page():
    items = [{"id": str(i)} for i in range(250)]
    assert paginate(_pages(items), 100) == items


def test_paginate_exact_multiple_of_page_size_terminates():
    items = [{"id": str(i)} for i in range(200)]
    assert paginate(_pages(items), 100) == items


def test_paginate_empty():
    assert paginate(_pages([]), 100) == []


def test_paginate_stops_if_endpoint_ignores_offset():
    page = [{"id": str(i)} for i in range(100)]
    assert paginate(lambda start, count: page, 100) == page


def test_filter_by_name_is_case_insensitive_and_optional():
    items = [{"title": "Redis - Overview"}, {"title": "Postgres"}]
    assert filter_by_name(items, "title", "redis") == [items[0]]
    assert filter_by_name(items, "title", None) == items
