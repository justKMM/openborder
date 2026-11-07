from typer.testing import CliRunner

from openborder import __version__
from openborder.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_unimplemented_commands_fail_loudly():
    for command in ["plan", "apply", "explain"]:
        result = runner.invoke(app, [command])
        assert result.exit_code == 1
        assert "not implemented" in result.output
