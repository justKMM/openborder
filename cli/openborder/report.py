"""Terminal output. Everything the user reads goes through here."""

from rich.console import Console
from rich.table import Table


def filter_by_name(items: list[dict], key: str, text: str | None) -> list[dict]:
    if not text:
        return items
    needle = text.lower()
    return [item for item in items if needle in str(item.get(key, "")).lower()]


def print_discovery(console: Console, dashboards: list[dict], monitors: list[dict], site: str) -> None:
    dashboards = sorted(dashboards, key=lambda d: (str(d.get("title", "")).lower(), str(d.get("id"))))
    monitors = sorted(monitors, key=lambda m: (str(m.get("name", "")).lower(), m.get("id", 0)))

    table = Table(title=f"Dashboards ({len(dashboards)})", title_justify="left", header_style="bold")
    for column in ("ID", "Title", "Layout", "Modified"):
        table.add_column(column, overflow="fold")
    for d in dashboards:
        table.add_row(
            str(d.get("id", "")),
            str(d.get("title", "")),
            str(d.get("layout_type", "")),
            str(d.get("modified_at", ""))[:10],
        )
    console.print(table)
    if not dashboards:
        console.print(
            "[dim]None found. Datadog's API only lists custom or cloned dashboards, not its built-in ones: "
            "import or clone one into your org first.[/dim]"
        )

    console.print()
    table = Table(title=f"Monitors ({len(monitors)})", title_justify="left", header_style="bold")
    for column in ("ID", "Name", "Type", "Tags"):
        table.add_column(column, overflow="fold")
    for m in monitors:
        table.add_row(
            str(m.get("id", "")),
            str(m.get("name", "")),
            str(m.get("type", "")),
            ", ".join(m.get("tags") or []),
        )
    console.print(table)

    console.print(f"\n[dim]Source: {site} · {len(dashboards)} dashboards · {len(monitors)} monitors[/dim]")
