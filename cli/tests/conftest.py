import os
from pathlib import Path

import pytest

TESTS = Path(__file__).parent


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
