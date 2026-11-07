import sys

# Windows consoles and pipes often default to a legacy codepage (cp1252) that can't
# encode the tier symbols (✓ ≈ ~ ✗) or arrows. Never crash on output; degrade instead.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from openborder.cli import app  # noqa: E402

app(prog_name="openborder")
