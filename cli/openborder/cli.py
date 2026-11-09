import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from openborder import __version__, config, report, service
from openborder import explain as explainer
from openborder.apply import PlanError, load_plan
from openborder.dash0_client import Dash0Client, Dash0Error
from openborder.sources.datadog.client import DatadogError
from openborder.sources.datadog.files import load_directory

app = typer.Typer(
    help="OpenBorder: migrate Datadog dashboards and monitors to Dash0, deterministically.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="View or change stored credentials and settings.", no_args_is_help=True)
app.add_typer(config_app, name="config")
console = Console()


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
    as_json: bool = typer.Option(False, "--json", help="Print the result as JSON instead of tables."),
) -> None:
    """List dashboards and monitors in the source Datadog org (read-only)."""
    settings = config.load_settings()
    missing = [k for k in ("DD_API_KEY", "DD_APP_KEY") if k not in settings]
    if missing:
        console.print(f"[red]Missing {', '.join(missing)}.[/red] Run `python -m openborder init` first.")
        raise typer.Exit(code=1)
    try:
        found = service.run_discover(settings["DD_API_KEY"], settings["DD_APP_KEY"], settings["DD_SITE"], name, tag)
    except DatadogError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(service.discovery_document(found), indent=2, ensure_ascii=False))
        return
    report.print_discovery(console, found.dashboards, found.monitors, found.site)


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
    try:
        if from_dir is not None:
            if not from_dir.is_dir():
                raise _fail(f"--from-dir {from_dir} is not a folder.")
            loaded = load_directory(from_dir)
            inputs = service.Inputs(
                loaded.dashboards, loaded.monitors, loaded.skipped, {"kind": "datadog", "mode": "files", "path": str(from_dir)}
            )
        else:
            settings = config.load_settings()
            missing = [k for k in ("DD_API_KEY", "DD_APP_KEY") if k not in settings]
            if missing:
                raise _fail(f"Missing {', '.join(missing)}. Run `python -m openborder init`, or use --from-dir.")
            inputs = service.fetch_from_datadog(
                settings["DD_API_KEY"],
                settings["DD_APP_KEY"],
                settings["DD_SITE"],
                dashboard_ids=dashboard,
                monitor_tag=monitor_tag,
                dashboards=not no_dashboards,
                monitors=not no_monitors,
            )
        inputs = service.select_inputs(
            inputs, name=name, monitor_tag=monitor_tag, dashboards=not no_dashboards, monitors=not no_monitors
        )
    except DatadogError as e:
        raise _fail(str(e))
    except service.ServiceError as e:
        raise _fail(f"{e} {e.hint}".strip())

    run = service.run_plan(
        inputs, out, enable_approximate=enable_approximate, promtool_check=promtool_check, status=console.status
    )
    report.print_plan(console, run.plan, out, run.validation)
    console.print(f"Artifacts written to [bold]{out}[/bold]  ·  review them, then run `python -m openborder apply {out}`")
    if run.failed:
        raise typer.Exit(code=1)


@app.command()
def apply(
    plan_dir: Path = typer.Argument(Path("migration"), help="The folder written by `plan`."),
    auto_approve: bool = typer.Option(False, "--auto-approve", "--yes", "-y", help="Import without asking for confirmation."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be imported and where; send nothing."),
) -> None:
    """Import a reviewed plan into Dash0. This is the only command that writes to Dash0."""
    try:
        loaded = load_plan(plan_dir)
    except PlanError as e:
        raise _fail(str(e))
    settings = config.load_settings()
    if "DASH0_AUTH_TOKEN" not in settings:
        raise _fail("Missing DASH0_AUTH_TOKEN. Run `python -m openborder init`.")
    try:
        client = Dash0Client(settings["DASH0_AUTH_TOKEN"], settings["DASH0_API_URL"], settings["DASH0_DATASET"])
    except Dash0Error as e:
        raise _fail(str(e))

    report.print_apply_summary(console, loaded, client.api_url, client.dataset)
    if dry_run:
        client.close()
        console.print("\n[bold]Dry run:[/bold] nothing was sent.")
        raise typer.Exit(code=0)
    if not auto_approve:
        console.print("\nDash0 will create these items, or overwrite them if they were imported before.")
        try:
            answer = typer.prompt("Type 'yes' to import", default="no", show_default=False)
        except typer.Abort:
            answer = "no"
        if answer.strip().lower() != "yes":
            client.close()
            console.print("[yellow]Cancelled. Nothing was imported.[/yellow]")
            raise typer.Exit(code=1)

    def show(kind: str, entry: dict) -> None:
        if kind == "dashboard" and entry["status"] == "imported":
            console.print(f"[green]✓[/green] dashboard [bold]{entry['title']}[/bold]  [dim]{entry['id']}[/dim]")
        elif kind == "dashboard":
            console.print(f"[red]✗ dashboard {entry['title']}[/red]\n  {entry['error']}")
        elif entry["status"] == "imported":
            created, sent = entry["created"], entry["sent"]
            note = ""
            if isinstance(created, int) and created < sent:
                note = f" ({created} newly created, {sent - created} overwrote existing rules)"
            console.print(f"[green]✓[/green] {sent} check rule(s) imported{note}")
        else:
            console.print(f"[red]✗ check rules[/red]\n  {entry['error']}")

    console.print()
    outcome = service.run_apply(loaded, client, plan_dir, on_result=show)
    if outcome.failures:
        console.print(
            f"\n[red]Apply finished with {outcome.failures} failure(s).[/red] Items that succeeded are in Dash0. "
            "Fix the cause and run `apply` again: every item has a stable ID, so Dash0 overwrites instead of duplicating."
        )
        raise typer.Exit(code=1)
    console.print(f"\n[green]Apply complete[/green] in dataset '{client.dataset}'. Results are in {plan_dir / 'apply.json'}.")


@app.command()
def explain(
    query: str | None = typer.Option(None, "--query", "-q", help="A Datadog metric query or formula to translate and trace."),
    monitor: bool = typer.Option(False, "--monitor", help="Treat --query as a monitor query, e.g. 'avg(last_5m):avg:redis.mem.used{*} > 90'."),
    var: list[str] = typer.Option(
        [], "--var", help="Template variable for --query as NAME=TAGPREFIX; leave the prefix empty for a free-form one (--var scope= --var host=host)."
    ),
    find: str | None = typer.Option(None, "--find", "-f", help="Explain the panel or monitor of a plan whose title or key contains this text."),
    plan_dir: Path = typer.Option(Path("migration"), "--plan", help="The plan folder to search with --find."),
    as_json: bool = typer.Option(False, "--json", help="Print the explanation as JSON."),
) -> None:
    """Show how a query, panel or monitor was translated, rule by rule. Every step is a named rule; no LLM is involved."""
    if (query is None) == (find is None):
        raise _fail(
            "Give exactly one of --query or --find.\n"
            "  explain --query 'avg:redis.stats.keyspace_hits{*}'\n"
            "  explain --find evictions   (searches ./migration, or --plan DIR)",
            code=2,
        )
    try:
        if query is not None and monitor:
            explanations = [explainer.explain_monitor_query(query)]
        elif query is not None:
            variables: dict[str, str | None] = {}
            for item in var:
                var_name, separator, prefix = item.partition("=")
                if not separator or not var_name:
                    raise _fail(f"--var must look like NAME=TAGPREFIX, got {item!r}.", code=2)
                variables[var_name] = prefix or None
            explanations = [explainer.explain_query(query, variables)]
        else:
            plan_path = plan_dir / "plan.json"
            if not plan_path.is_file():
                raise _fail(f"{plan_dir} has no plan.json. Run `python -m openborder plan --out {plan_dir}` first.")
            try:
                document = json.loads(plan_path.read_text(encoding="utf-8"))
            except ValueError as e:
                raise _fail(f"{plan_path} is not valid JSON: {e}")
            explanations = explainer.find_in_plan(document, find)
    except explainer.ExplainError as e:
        raise _fail(str(e))
    if as_json:
        typer.echo(json.dumps({"explanations": explanations}, indent=2, ensure_ascii=False))
        return
    for explanation in explanations:
        report.print_explanation(console, explanation)
