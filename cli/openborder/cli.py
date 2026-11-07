import typer
from rich.console import Console

from openborder import __version__

app = typer.Typer(
    help="OpenBorder: migrate Datadog dashboards and monitors to Dash0, deterministically.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _not_implemented(command: str) -> None:
    console.print(f"[yellow]`{command}` is not implemented yet.[/yellow]")
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the OpenBorder version."""
    console.print(__version__)


@app.command()
def discover() -> None:
    """List dashboards and monitors in the source Datadog org."""
    _not_implemented("discover")


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
