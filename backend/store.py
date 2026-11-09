"""Where plans live: one folder per plan, in exactly the layout the CLI's `plan` writes, so `apply` can verify it.

A plan holds a customer's dashboards, so plans expire: every new plan first deletes the ones that were last
touched more than OPENBORDER_PLAN_TTL_HOURS (default 24) ago. Plain filesystem code; it knows nothing about OpenBorder.
"""

import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path

_PLAN_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def data_dir() -> Path:
    return Path(os.environ.get("OPENBORDER_DATA_DIR") or Path(tempfile.gettempdir()) / "openborder-plans")


def _ttl_seconds() -> float:
    return float(os.environ.get("OPENBORDER_PLAN_TTL_HOURS") or 24) * 3600


def _last_touched(folder: Path) -> float:
    return max((p.stat().st_mtime for p in (folder, folder / "plan.json", folder / "apply.json") if p.exists()), default=0.0)


def prune() -> None:
    root = data_dir()
    if not root.is_dir():
        return
    cutoff = time.time() - _ttl_seconds()
    for folder in root.iterdir():
        if folder.is_dir() and _PLAN_ID.match(folder.name) and _last_touched(folder) < cutoff:
            shutil.rmtree(folder, ignore_errors=True)


def create() -> tuple[str, Path]:
    """A fresh, empty plan folder. The id is random on purpose: it is the only thing protecting the plan."""
    plan_id = secrets.token_urlsafe(16)
    folder = data_dir() / plan_id
    folder.mkdir(parents=True)
    return plan_id, folder


def get(plan_id: str) -> Path | None:
    """The folder of an existing plan; None for an unknown id or one that is not shaped like ours (never a path)."""
    if not _PLAN_ID.match(plan_id):
        return None
    folder = data_dir() / plan_id
    return folder if (folder / "plan.json").is_file() else None


def delete(plan_id: str) -> bool:
    if not _PLAN_ID.match(plan_id):
        return False
    folder = data_dir() / plan_id
    existed = folder.is_dir()
    shutil.rmtree(folder, ignore_errors=True)
    return existed
