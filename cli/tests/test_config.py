import pytest
from typer.testing import CliRunner

from openborder import config
from openborder.cli import app

runner = CliRunner()


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setenv("OPENBORDER_ENV_FILE", str(path))
    for key in config.KEYS:
        monkeypatch.delenv(key, raising=False)
    return path


def test_update_creates_file_and_roundtrips(env_file):
    config.update_env_file({"DD_API_KEY": "abc123", "DD_SITE": "datadoghq.eu"})
    assert config.read_env_file() == {"DD_API_KEY": "abc123", "DD_SITE": "datadoghq.eu"}


def test_update_preserves_comments_and_other_lines(env_file):
    env_file.write_text("# mine\nOTHER=1\nDD_SITE=old\n", encoding="utf-8")
    config.update_env_file({"DD_SITE": "new", "DD_API_KEY": "k"})
    assert env_file.read_text(encoding="utf-8") == "# mine\nOTHER=1\nDD_SITE=new\nDD_API_KEY=k\n"


def test_values_with_spaces_or_hash_are_quoted_and_roundtrip(env_file):
    config.update_env_file({"DD_APP_KEY": "a b#c"})
    assert config.read_env_file()["DD_APP_KEY"] == "a b#c"


def test_multiline_value_rejected(env_file):
    with pytest.raises(ValueError):
        config.update_env_file({"DD_API_KEY": "a\nb"})


def test_env_var_overrides_file_and_default_applies(env_file, monkeypatch):
    config.update_env_file({"DD_SITE": "from-file"})
    assert config.resolve("DD_SITE") == ("from-file", "file")
    monkeypatch.setenv("DD_SITE", "from-env")
    assert config.resolve("DD_SITE") == ("from-env", "env")
    assert config.resolve("DASH0_DATASET") == ("default", "default")
    assert config.resolve("DD_API_KEY") == (None, "unset")


def test_mask_never_reveals_short_values():
    assert config.mask("abcdefghijkl") == "****ijkl"
    assert config.mask("short") == "****"


def test_init_saves_values_and_enter_keeps_existing(env_file):
    # order: DD_API_KEY, DD_APP_KEY, DD_SITE, DASH0_AUTH_TOKEN, DASH0_API_URL, DASH0_DATASET
    first = runner.invoke(app, ["init"], input="apikey123456\nappkey123456\n\ntoken123456\n\n\n")
    assert first.exit_code == 0, first.output
    assert config.read_env_file() == {
        "DD_API_KEY": "apikey123456",
        "DD_APP_KEY": "appkey123456",
        "DD_SITE": "datadoghq.eu",
        "DASH0_AUTH_TOKEN": "token123456",
        "DASH0_API_URL": "https://api.eu-west-1.aws.dash0.com",
        "DASH0_DATASET": "default",
    }
    second = runner.invoke(app, ["init"], input="\n\ndatadoghq.com\n\n\n\n")
    assert second.exit_code == 0, second.output
    saved = config.read_env_file()
    assert saved["DD_API_KEY"] == "apikey123456"
    assert saved["DD_SITE"] == "datadoghq.com"


def test_config_show_masks_secrets(env_file):
    config.update_env_file({"DD_API_KEY": "supersecretvalue"})
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "supersecretvalue" not in result.output
    assert "alue" in result.output


def test_config_set_and_unknown_key(env_file):
    ok = runner.invoke(app, ["config", "set", "dd_site", "datadoghq.com"])
    assert ok.exit_code == 0
    assert config.read_env_file()["DD_SITE"] == "datadoghq.com"
    bad = runner.invoke(app, ["config", "set", "NOPE", "x"])
    assert bad.exit_code == 2
