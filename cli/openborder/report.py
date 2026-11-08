"""Terminal output. Everything the user reads goes through here."""

from collections import Counter

from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from openborder.ir import Tier
from openborder.targets.promql import render

_COLOR = {Tier.EXACT: "green", Tier.EQUIVALENT: "cyan", Tier.APPROXIMATE: "yellow", Tier.MANUAL: "red"}


def _tag(tier: Tier) -> str:
    return f"[{_COLOR[tier]}]{tier.symbol}[/{_COLOR[tier]}]"


def _short(text: str, width: int = 96) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _panel_detail(panel) -> str:
    if panel.placeholder:
        reasons = [f.message for f in panel.findings if f.tier == Tier.MANUAL]
        return reasons[0] if reasons else "placeholder"
    if panel.tier > Tier.EXACT:
        notes = [f for f in panel.findings if f.tier == panel.tier]
        return notes[0].message if notes else ""
    if panel.queries:
        return render(panel.queries[0].expr)
    return ""


def print_plan(console: Console, plan, out_dir, validation: dict) -> None:
    for dash in plan.dashboards:
        counts = Counter(p.tier for p in dash.panels)
        migrated = sum(n for t, n in counts.items() if t != Tier.MANUAL)
        total = len(dash.panels)
        tree = Tree(f"[bold]{dash.title}[/bold]  →  Perses dashboard", guide_style="dim")
        for section in dash.sections:
            branch = tree.add(f"[bold dim]{section.title or '(ungrouped)'}[/bold dim]")
            for p in section.panels:
                detail = _panel_detail(p)
                branch.add(f"{_tag(p.tier)} {p.title or p.kind}  [dim]{_short(detail)}[/dim]")
        console.print(tree)
        if total:
            console.print(
                f"  Coverage: [bold]{migrated}/{total}[/bold] panels migrated ({round(100 * migrated / total)}%)  "
                + " · ".join(f"{_tag(t)} {counts.get(t, 0)}" for t in Tier)
            )
        else:
            console.print("  [dim]This dashboard has no panels.[/dim]")
        for f in dash.findings:
            console.print(f"  {_tag(f.tier)} [dim]{_short(f.message, 140)}[/dim]")
        console.print()

    if plan.rules:
        tree = Tree("[bold]Monitors[/bold]  →  Dash0 check rules", guide_style="dim")
        for rule in plan.rules:
            if rule.expr is None:
                state = "[red]not imported[/red]"
            elif rule.enabled:
                state = "[green]enabled[/green]"
            else:
                state = "[yellow]imported DISABLED[/yellow] (approximate; use --enable-approximate to arm)"
            levels = " · ".join(f"{k} {v:g}" for k, v in sorted(rule.thresholds.items(), key=lambda kv: kv[1]))
            node = tree.add(f"{_tag(rule.tier)} {rule.name}  [dim]{levels}[/dim]  {state}")
            for f in rule.findings:
                if f.tier > Tier.EXACT:
                    node.add(f"{_tag(f.tier)} [dim]{_short(f.message, 140)}[/dim]")
        console.print(tree)
        armed = sum(1 for r in plan.rules if r.enabled and r.expr is not None)
        console.print(f"  Armed: [bold]{armed}/{len(plan.rules)}[/bold]\n")

    findings = []
    for dash in plan.dashboards:
        findings += list(dash.findings) + [f for p in dash.panels for f in p.findings]
    for rule in plan.rules:
        findings += list(rule.findings)
    findings = [f for f in findings if f.tier > Tier.EXACT]
    if findings:
        by_rule: dict[str, list] = {}
        for f in findings:
            by_rule.setdefault(f.rule_id, []).append(f)
        table = Table(title="Findings by rule", title_justify="left", header_style="bold")
        for column in ("", "Rule", "Count", "What it means"):
            table.add_column(column, overflow="fold")
        for rule_id, group in sorted(by_rule.items(), key=lambda kv: (-max(f.tier for f in kv[1]), kv[0])):
            tier = max(f.tier for f in group)
            table.add_row(_tag(tier), rule_id, str(len(group)), _short(group[0].message, 110))
        console.print(table)
        console.print()

    for what, why in plan.skipped:
        console.print(f"[red]✗ skipped {what}[/red] [dim]{_short(why, 140)}[/dim]")
    for label, errs in plan.structure_errors.items():
        for e in errs:
            console.print(f"[red]✗ invalid {label}:[/red] {e}")
    promtool = validation.get("promtool", {})
    if promtool.get("ran"):
        bad = promtool.get("errors", {})
        for label, message in bad.items():
            console.print(f"[red]✗ promtool rejected {label}:[/red] [dim]{_short(message, 160)}[/dim]")
        if not bad:
            console.print(f"[green]✓ promtool accepted all {promtool.get('checked', 0)} PromQL expressions[/green]")
    elif promtool:
        console.print(f"[yellow]PromQL syntax was not checked with promtool: {promtool.get('reason')}[/yellow]")


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
