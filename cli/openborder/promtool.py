"""PromQL syntax check via `promtool` from the prom/prometheus Docker image.

Shells out to Docker, so it may only be called from the CLI, never from openborder.core.
"""

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class PromtoolResult:
    ran: bool
    reason: str = ""
    checked: int = 0
    errors: dict[str, str] = field(default_factory=dict)  # label -> message


_SUBSTITUTIONS = [
    (re.compile(r"\$__(rate_interval|interval)"), "5m"),
    (re.compile(r"\$__range"), "1h"),
    (re.compile(r"\$__threshold"), "0"),
]


def _for_promtool(expr: str) -> str:
    for pattern, value in _SUBSTITUTIONS:
        expr = pattern.sub(value, expr)
    return expr


def check(expressions: dict[str, str]) -> PromtoolResult:
    """Syntax-check PromQL with `promtool check rules` from the prom/prometheus image."""
    if not expressions:
        return PromtoolResult(ran=True, checked=0)
    if shutil.which("docker") is None:
        return PromtoolResult(ran=False, reason="Docker is not installed or not on PATH")
    labels = list(expressions)
    groups = [
        {"name": f"g{i}", "rules": [{"alert": f"r{i}", "expr": _for_promtool(expressions[label])}]}
        for i, label in enumerate(labels)
    ]
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "rules.yml").write_text(yaml.safe_dump({"groups": groups}, sort_keys=False), encoding="utf-8")
        try:
            proc = subprocess.run(
                ["docker", "run", "--rm", "-v", f"{tmp}:/work", "--entrypoint", "promtool",
                 "prom/prometheus:latest", "check", "rules", "/work/rules.yml"],
                capture_output=True, text=True, timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return PromtoolResult(ran=False, reason=f"could not run Docker: {e}")
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        return PromtoolResult(ran=True, checked=len(labels))
    errors = {}
    for match in re.finditer(r'group "g(\d+)".*?(?:\n|$)', output):
        errors[labels[int(match.group(1))]] = match.group(0).strip()
    if not errors:
        return PromtoolResult(ran=False, reason="promtool failed without a readable result: " + output.strip()[-300:])
    return PromtoolResult(ran=True, checked=len(labels), errors=errors)
