"""Per-request credentials. They arrive as headers, serve that one request and are never stored or logged.

Headers, not the body or the URL: they cannot end up in a validation error, an access log or a browser history.
The backend makes outgoing requests on a stranger's behalf, so the hosts it may call are restricted (no SSRF).
"""

import os
import re
from typing import Annotated, NamedTuple
from urllib.parse import urlparse

from fastapi import Depends, Header
from openborder import config
from openborder.sources.datadog.client import normalize_site

from errors import ApiError

# Datadog's public sites: datadoghq.com, datadoghq.eu, us3/us5/ap1/ap2.datadoghq.com, ddog-gov.com.
_DATADOG_SITE = re.compile(r"^(?:[a-z0-9]+\.)?(?:datadoghq\.(?:com|eu)|ddog-gov\.com)$")
# Dash0's API hosts look like api.eu-west-1.aws.dash0.com. OPENBORDER_ALLOW_ANY_DASH0_URL=1 lifts the restriction (tests, local stand-ins).
_DASH0_HOST_SUFFIX = ".dash0.com"


class DatadogCreds(NamedTuple):
    api_key: str
    app_key: str
    site: str


class Dash0Creds(NamedTuple):
    token: str
    api_url: str
    dataset: str


def _default(key: str) -> str:
    return next(s.default for s in config.SETTINGS if s.key == key)


def optional_datadog(
    api_key: Annotated[str | None, Header(alias="X-Datadog-Api-Key")] = None,
    app_key: Annotated[str | None, Header(alias="X-Datadog-App-Key")] = None,
    site: Annotated[str | None, Header(alias="X-Datadog-Site")] = None,
) -> DatadogCreds | None:
    """None when no key was sent; an error when only one of the two was."""
    if not api_key and not app_key:
        return None
    if not (api_key and app_key):
        raise ApiError(400, "missing_credentials", "Send both the X-Datadog-Api-Key and the X-Datadog-App-Key header.")
    resolved = normalize_site(site or _default("DD_SITE"))
    if not _DATADOG_SITE.match(resolved):
        raise ApiError(400, "invalid_datadog_site", f"{site!r} is not a Datadog site, for example datadoghq.eu or us5.datadoghq.com.")
    return DatadogCreds(api_key, app_key, resolved)


def required_datadog(creds: Annotated[DatadogCreds | None, Depends(optional_datadog)]) -> DatadogCreds:
    if creds is None:
        raise ApiError(
            400,
            "missing_credentials",
            "Send your Datadog credentials in the X-Datadog-Api-Key and X-Datadog-App-Key headers (X-Datadog-Site is optional).",
        )
    return creds


def dash0(
    token: Annotated[str | None, Header(alias="X-Dash0-Token")] = None,
    api_url: Annotated[str | None, Header(alias="X-Dash0-Api-Url")] = None,
    dataset: Annotated[str | None, Header(alias="X-Dash0-Dataset")] = None,
) -> Dash0Creds:
    if not token:
        raise ApiError(
            400,
            "missing_credentials",
            "Send your Dash0 auth token in the X-Dash0-Token header (X-Dash0-Api-Url and X-Dash0-Dataset are optional).",
        )
    api_url = (api_url or _default("DASH0_API_URL")).strip()
    parsed = urlparse(api_url)
    allowed = parsed.scheme == "https" and (parsed.hostname or "").endswith(_DASH0_HOST_SUFFIX)
    if not allowed and not os.environ.get("OPENBORDER_ALLOW_ANY_DASH0_URL"):
        raise ApiError(
            400,
            "invalid_dash0_url",
            f"{api_url!r} is not a Dash0 API address; it should look like https://api.eu-west-1.aws.dash0.com.",
        )
    return Dash0Creds(token, api_url, dataset or _default("DASH0_DATASET"))
