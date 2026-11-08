"""Deterministic identifiers. Never random: a repeated import must overwrite, not duplicate."""

import re
import uuid

_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/justKMM/openborder")


def stable_id(kind: str, source_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"datadog:{kind}:{source_id}"))


def slug(text: str, fallback: str = "item") -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or fallback
