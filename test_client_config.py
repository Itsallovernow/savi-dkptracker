"""Unit tests for client_config module."""

import os
import tempfile

from client_config import ClientConfig, load_config, TEMPLATE_INI


def test_load_config_creates_template_when_missing(tmp_path, capsys):
    """If INI file doesn't exist, create template and return local_only config."""
    ini_path = str(tmp_path / "dkp_client.ini")
    assert not os.path.exists(ini_path)

    config = load_config(ini_path)

    # Template file should now exist
    assert os.path.exists(ini_path)
    with open(ini_path, encoding="utf-8") as f:
        content = f.read()
    assert "api_url" in content
    assert "api_key" in content

    # Config should be local-only
    assert config.local_only is True

    # Should print instructions
    captured = capsys.readouterr()
    assert "template configuration file" in captured.out
    assert "API URL" in captured.out


def test_load_config_full_valid(tmp_path):
    """All fields present and valid returns a fully populated config."""
    ini_path = str(tmp_path / "dkp_client.ini")
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write("""\
[server]
api_url = https://dkp.example.com
api_key = my-secret-key-12345

[client]
log_directory = C:\\EverQuest\\Logs
poll_interval = 2.0
retry_interval = 60.0
""")

    config = load_config(ini_path)

    assert config.api_url == "https://dkp.example.com"
    assert config.api_key == "my-secret-key-12345"
    assert config.log_directory == "C:\\EverQuest\\Logs"
    assert config.poll_interval == 2.0
    assert config.retry_interval == 60.0
    assert config.local_only is False


def test_load_config_missing_api_url(tmp_path, capsys):
    """Missing api_url triggers local-only mode."""
    ini_path = str(tmp_path / "dkp_client.ini")
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write("""\
[server]
api_url = 
api_key = my-key

[client]
log_directory = C:\\Logs
""")

    config = load_config(ini_path)

    assert config.local_only is True
    captured = capsys.readouterr()
    assert "api_url" in captured.out


def test_load_config_missing_multiple_fields(tmp_path, capsys):
    """Multiple missing fields are all reported."""
    ini_path = str(tmp_path / "dkp_client.ini")
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write("""\
[server]
api_url = 
api_key = 

[client]
log_directory = 
""")

    config = load_config(ini_path)

    assert config.local_only is True
    captured = capsys.readouterr()
    assert "api_url" in captured.out
    assert "api_key" in captured.out
    assert "log_directory" in captured.out


def test_load_config_defaults_for_optional_fields(tmp_path):
    """Optional fields use defaults when not specified."""
    ini_path = str(tmp_path / "dkp_client.ini")
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write("""\
[server]
api_url = https://dkp.example.com
api_key = key123

[client]
log_directory = C:\\Logs
""")

    config = load_config(ini_path)

    assert config.poll_interval == 1.0
    assert config.retry_interval == 30.0
    assert config.local_only is False


def test_load_config_invalid_numeric_uses_defaults(tmp_path):
    """Invalid numeric values fall back to defaults."""
    ini_path = str(tmp_path / "dkp_client.ini")
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write("""\
[server]
api_url = https://dkp.example.com
api_key = key123

[client]
log_directory = C:\\Logs
poll_interval = not_a_number
retry_interval = also_bad
""")

    config = load_config(ini_path)

    assert config.poll_interval == 1.0
    assert config.retry_interval == 30.0
    assert config.local_only is False


def test_load_config_missing_sections(tmp_path, capsys):
    """INI file with no sections still works, returns local-only."""
    ini_path = str(tmp_path / "dkp_client.ini")
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write("")  # Empty file

    config = load_config(ini_path)

    assert config.local_only is True
    captured = capsys.readouterr()
    assert "api_url" in captured.out
    assert "api_key" in captured.out
    assert "log_directory" in captured.out


def test_clientconfig_dataclass_defaults():
    """ClientConfig default values are correct."""
    config = ClientConfig()
    assert config.api_url == ""
    assert config.api_key == ""
    assert config.log_directory == ""
    assert config.poll_interval == 1.0
    assert config.retry_interval == 30.0
    assert config.local_only is False
