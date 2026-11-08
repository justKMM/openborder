from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from openborder import __version__, config, core, promtool, report
from openborder.sources.datadog.client import DatadogClient, DatadogError
from openborder.sources.datadog.files import load_directory
from openborder.targets.promql import render

app = typer.Typer(
    help="OpenBorder: migrate Datadog dashboards and monitors to Dash0, deterministically.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="View or change stored credentials and settings.", no_args_is_help=True)
app.add_typer(config_app, name="config")
console = Console()


def _not_implemented(command: str) -> None:
    console.print(f"[yellow]`{command}` is not implemented yet.[/yellow]")
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the OpenBorder version."""
    console.print(__version__)


@app.command()
def init() -> None:
    """Prompt for credentials and settings and save them, so you don't have to export them.

    Safe to re-run to update values: press Enter to keep the current one.
    """
    current = config.read_env_file()
    console.print(f"Saving to [bold]{config.env_file_path()}[/bold] (gitignored). Enter keeps the current value.\n")
    updates: dict[str, str] = {}
    for s in config.SETTINGS:
        have = current.get(s.key)
        console.print(f"[dim]{s.hint}[/dim]")
        if s.secret:
            label = f"{s.key} [keep {config.mask(have)}]" if have else s.key
            value = typer.prompt(label, default="", show_default=False, hide_input=True)
        else:
            value = typer.prompt(s.key, default=have or s.default or "", show_default=bool(have or s.default))
        value = value.strip()
        if value:
            updates[s.key] = value
        elif not have:
            console.print(f"[yellow]  {s.key} left unset[/yellow]")
    if updates:
        config.update_env_file(updates)
    console.print("\n[green]Saved.[/green] Run `python -m openborder config show` to review.")


@config_app.command("show")
def config_show() -> None:
    """Show current settings (secrets are masked) and where each value comes from."""
    table = Table(show_header=True, header_style="bold")
    for column in ("Key", "Value", "Source"):
        table.add_column(column)
    for s in config.SETTINGS:
        value, source = config.resolve(s.key)
        shown = "[red]not set[/red]" if value is None else (config.mask(value) if s.secret else value)
        table.add_row(s.key, shown, source)
    console.print(table)
    console.print(f"[dim]File: {config.env_file_path()}  ·  environment variables override the file[/dim]")


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """Set one value, e.g. `config set DD_SITE datadoghq.com`."""
    key = key.upper()
    if key not in config.KEYS:
        console.print(f"[red]Unknown key {key}.[/red] Valid keys: {', '.join(config.KEYS)}")
        raise typer.Exit(code=2)
    try:
        path = config.update_env_file({key: value})
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=2)
    console.print(f"Saved {key} to {path}")


@app.command()
def discover(
    name: str | None = typer.Option(None, "--name", "-n", help="Only items whose name contains this text."),
    tag: str | None = typer.Option(
        None, "--tag", "-t", help="Only monitors with this tag, e.g. integration:redisdb. Dashboards aren't tagged."
    ),
) -> None:
    """List dashboards and monitors in the source Datadog org (read-only)."""
    settings = config.load_settings()
    missing = [k for k in ("DD_API_KEY", "DD_APP_KEY") if k not in settings]
    if missing:
        console.print(f"[red]Missing {', '.join(missing)}.[/red] Run `python -m openborder init` first.")
        raise typer.Exit(code=1)
    try:
        with DatadogClient(settings["DD_API_KEY"], settings["DD_APP_KEY"], settings["DD_SITE"]) as dd:
            dashboards = dd.list_dashboards()
            monitors = dd.list_monitors(monitor_tags=tag)
            site = dd.site
    except DatadogError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    report.print_discovery(
        console,
        report.filter_by_name(dashboards, "title", name),
        report.filter_by_name(monitors, "name", name),
        site,
    )


def _fail(message: str, code: int = 1) -> typer.Exit:
    console.print(f"[red]{message}[/red]")
    return typer.Exit(code=code)


@app.command()
def plan(
    from_dir: Path | None = typer.Option(
        None, "--from-dir", help="Read exported Datadog JSON from this folder instead of calling the API."
    ),
    dashboard: list[str] = typer.Option([], "--dashboard", "-d", help="Datadog dashboard ID (repeatable). Default: all."),
    monitor_tag: str | None = typer.Option(None, "--monitor-tag", help="Only monitors with this tag, e.g. integration:redisdb."),
    name: str | None = typer.Option(None, "--name", "-n", help="Only dashboards/monitors whose name contains this text."),
    no_dashboards: bool = typer.Option(False, "--no-dashboards", help="Skip dashboards."),
    no_monitors: bool = typer.Option(False, "--no-monitors", help="Skip monitors."),
    out: Path = typer.Option(Path("migration"), "--out", "-o", help="Where to write the plan."),
    enable_approximate: bool = typer.Option(
        False, "--enable-approximate", help="Import approximate (~) alerts enabled instead of disabled."
    ),
    promtool_check: bool = typer.Option(True, "--promtool/--no-promtool", help="Syntax-check PromQL with promtool (needs Docker)."),
) -> None:
    """Translate dashboards and monitors and write reviewable artifacts (nothing is imported)."""
    if from_dir is not None:
        if not from_dir.is_dir():
            raise _fail(f"--from-dir {from_dir} is not a folder.")
        loaded = load_directory(from_dir)
        dashboards, monitors, skipped = loaded.dashboards, loaded.monitors, loaded.skipped
        source = {"kind": "datadog", "mode": "files", "path": str(from_dir)}
        site = None
    else:
        settings = config.load_settings()
        missing = [k for k in ("DD_API_KEY", "DD_APP_KEY") if k not in settings]
        if missing:
            raise _fail(f"Missing {', '.join(missing)}. Run `python -m openborder init`, or use --from-dir.")
        dashboards, monitors, skipped = [], [], []
        try:
            with DatadogClient(settings["DD_API_KEY"], settings["DD_APP_KEY"], settings["DD_SITE"]) as dd:
                site = dd.site
                if not no_dashboards:
                    summaries = dd.list_dashboards()
                    if dashboard:
                        unknown = sorted(set(dashboard) - {s["id"] for s in summaries})
                        if unknown:
                            raise _fail(f"No such dashboard ID(s) in {site}: {', '.join(unknown)}. Run `discover` to list them.")
                        summaries = [s for s in summaries if s["id"] in dashboard]
                    dashboards = [dd.get_dashboard(s["id"]) for s in summaries]
                if not no_monitors:
                    monitors = dd.list_monitors(monitor_tags=monitor_tag)
        except DatadogError as e:
            raise _fail(str(e))
        source = {"kind": "datadog", "mode": "api", "site": site}

    if no_dashboards:
        dashboards = []
    if no_monitors:
        monitors = []
    if name:
        dashboards = report.filter_by_name(dashboards, "title", name)
        monitors = [m for m in monitors if name.lower() in str((m.get("definition") or m).get("name", "")).lower()]
    if monitor_tag and from_dir is not None:
        monitors = [m for m in monitors if monitor_tag in ((m.get("definition") or m).get("tags") or []) + (m.get("tags") or [])]
    if not dashboards and not monitors:
        raise _fail("Nothing to plan: no dashboards or monitors matched." + (" Run `discover` to see what the org contains." if from_dir is None else ""))

    result = core.plan(dashboards, monitors, core.PlanOptions(enable_approximate=enable_approximate), skipped=skipped)

    validation: dict = {"structure": result.structure_errors}
    if promtool_check:
        expressions = {}
        for dash in result.dashboards:
            for p in dash.panels:
                for i, q in enumerate(p.queries):
                    expressions[f"{dash.title} / {p.title or p.key} [{i + 1}]"] = render(q.expr)
        for rule in result.rules:
            if rule.expr is not None:
                expressions[f"monitor {rule.name}"] = render(rule.expr)
        with console.status("Checking PromQL with promtool (Docker)..."):
            outcome = promtool.check(expressions)
        validation["promtool"] = {"ran": outcome.ran, "reason": outcome.reason, "checked": outcome.checked, "errors": outcome.errors}
    else:
        validation["promtool"] = {"ran": False, "reason": "skipped (--no-promtool)"}

    out.mkdir(parents=True, exist_ok=True)
    perses_dir = out / "perses"
    if (out / "plan.json").exists() and perses_dir.is_dir():  # our own earlier output: drop stale dashboards
        for old in perses_dir.glob("*.json"):
            old.unlink()
    if not result.rules and (out / "check-rules.json").exists() and (out / "plan.json").exists():
        (out / "check-rules.json").unlink()
    for relative, content in result.artifacts.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (out / "plan.json").write_bytes(core.render_plan_json(result, source, validation))

    report.print_plan(console, result, out, validation)
    console.print(f"Artifacts written to [bold]{out}[/bold]  ·  review them, then run `python -m openborder apply {out}`")
    failed = bool(result.structure_errors) or bool(validation["promtool"].get("errors"))
    if failed:
        raise typer.Exit(code=1)


@app.command()
def apply() -> None:
    """Import a reviewed plan into Dash0."""
    _not_implemented("apply")


@app.command()
def explain() -> None:
    """Show the rule-by-rule trace for one query, widget, or monitor."""
    _not_implemented("explain")
