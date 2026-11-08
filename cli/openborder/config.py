"""Credentials and settings, stored in a gitignored .env file.

Shell-side only: nothing reachable from openborder.core may import this module.
Real environment variables take precedence over the file, so `export` still works.
"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Setting:
    key: str
    secret: bool
    default: str | None
    hint: str


SETTINGS: list[Setting] = [
    Setting("DD_API_KEY", True, None, "Datadog: Organization Settings > API Keys"),
    Setting("DD_APP_KEY", True, None, "Datadog: Organization Settings > Application Keys"),
    Setting("DD_SITE", False, "datadoghq.eu", "your Datadog site, e.g. datadoghq.eu"),
    Setting("DASH0_AUTH_TOKEN", True, None, "Dash0: Settings > Auth Tokens"),
    Setting(
        "DASH0_API_URL",
        False,
        "https://api.eu-west-1.aws.dash0.com",
        "must match your Dash0 org's region",
    ),
    Setting("DASH0_DATASET", False, "default", "Dash0 dataset slug"),
]
KEYS = [s.key for s in SETTINGS]

_HEADER = "# OpenBorder credentials. Never commit this file (it is gitignored).\n"


def env_file_path() -> Path:
    override = os.environ.get("OPENBORDER_ENV_FILE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / ".env"


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read_env_file(path: Path | None = None) -> dict[str, str]:
    path = path or env_file_path()
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = _unquote(value.strip())
    return values


def _format(key: str, value: str) -> str:
    if any(c in value for c in " #\"'"):
        value = '"' + value.replace('"', "'") + '"'
    return f"{key}={value}"


def update_env_file(updates: dict[str, str], path: Path | None = None) -> Path:
    """Set the given keys, keeping comments and unrelated lines in place."""
    path = path or env_file_path()
    for key, value in updates.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"{key}: value must be a single line")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else [_HEADER.rstrip()]
    remaining = dict(updates)
    for i, line in enumerate(lines):
        key = line.partition("=")[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in remaining:
            lines[i] = _format(key, remaining.pop(key))
    lines.extend(_format(k, v) for k, v in remaining.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)  # best effort; no-op for most purposes on Windows
    except OSError:
        pass
    return path


def resolve(key: str) -> tuple[str | None, str]:
    """Return (value, source) where source is 'env', 'file', 'default' or 'unset'."""
    if os.environ.get(key):
        return os.environ[key], "env"
    from_file = read_env_file().get(key)
    if from_file:
        return from_file, "file"
    default = next(s.default for s in SETTINGS if s.key == key)
    if default is not None:
        return default, "default"
    return None, "unset"


def load_settings() -> dict[str, str]:
    """All settings that have a value (env > file > default). For the clients to use."""
    out: dict[str, str] = {}
    for s in SETTINGS:
        value, _ = resolve(s.key)
        if value is not None:
            out[s.key] = value
    return out


def mask(value: str) -> str:
    return "****" + value[-4:] if len(value) > 8 else "****"
