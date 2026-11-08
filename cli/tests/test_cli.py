from pathlib import Path

from typer.testing import CliRunner

from openborder import __version__
from openborder.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_unimplemented_commands_fail_loudly():
    for command in ["apply", "explain"]:
        result = runner.invoke(app, [command])
        assert result.exit_code == 1
        assert "not implemented" in result.output


def test_plan_and_discover_without_credentials_explain_what_to_do():
    for command in (["plan"], ["discover"]):
        result = runner.invoke(app, command)
        assert result.exit_code == 1
        assert "init" in result.output and "DD_API_KEY" in result.output


def test_plan_from_dir_end_to_end_writes_artifacts(tmp_path):
    fixtures = Path(__file__).parent / "fixtures" / "datadog"
    out = tmp_path / "out"
    result = runner.invoke(app, ["plan", "--from-dir", str(fixtures), "--out", str(out), "--no-promtool"])
    assert result.exit_code == 0, result.output
    assert "Coverage: 32/36" in result.output
    assert (out / "plan.json").exists() and (out / "check-rules.json").exists()
    assert (out / "perses" / "redis-overview.json").exists()


def test_plan_filters_and_empty_selection(tmp_path):
    fixtures = Path(__file__).parent / "fixtures" / "datadog"
    only_monitors = runner.invoke(
        app, ["plan", "--from-dir", str(fixtures), "--out", str(tmp_path / "m"), "--no-promtool", "--no-dashboards"]
    )
    assert only_monitors.exit_code == 0 and "Redis - Overview" not in only_monitors.output
    nothing = runner.invoke(app, ["plan", "--from-dir", str(fixtures), "--out", str(tmp_path / "n"), "--name", "zzz"])
    assert nothing.exit_code == 1 and "Nothing to plan" in nothing.output
    missing = runner.invoke(app, ["plan", "--from-dir", str(tmp_path / "nope")])
    assert missing.exit_code == 1 and "not a folder" in missing.output
