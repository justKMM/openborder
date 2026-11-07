import typer
from rich.console import Console
from rich.table import Table

from openborder import __version__, config, report
from openborder.sources.datadog.client import DatadogClient, DatadogError

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


@app.command()
def plan() -> None:
    """Translate dashboards and monitors and write reviewable artifacts to ./migration."""
    _not_implemented("plan")


@app.command()
def apply() -> None:
    """Import a reviewed plan into Dash0."""
    _not_implemented("apply")


@app.command()
def explain() -> None:
    """Show the rule-by-rule trace for one query, widget, or monitor."""
    _not_implemented("explain")
