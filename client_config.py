"""Client configuration module for DKP Bid Tracker.

Reads configuration from an INI file (dkp_client.ini) located beside the executable.
Supports local-only mode when required fields are missing.

Requirements: 4.1, 4.2, 4.3, 4.4
"""

import configparser
import os
from dataclasses import dataclass, field


TEMPLATE_INI = """\
[server]
# The URL of your guild's DKP backend API (e.g., https://dkp.example.com)
api_url = 

# Your guild's API key (bearer token) for authenticating uploads
api_key = 

[client]
# Path to the EverQuest log directory (e.g., C:\\EverQuest\\Logs)
log_directory = 

# Seconds between log file polls (default: 1.0)
poll_interval = 1.0

# Seconds between offline queue retry attempts (default: 30.0)
retry_interval = 30.0
"""


@dataclass
class ClientConfig:
    """Configuration for the DKP client."""

    api_url: str = ""
    api_key: str = ""
    log_directory: str = ""
    poll_interval: float = 1.0
    retry_interval: float = 30.0
    local_only: bool = field(default=False, repr=False)


def load_config(ini_path: str) -> ClientConfig:
    """Load client configuration from an INI file.

    If the INI file does not exist, creates a template with placeholder values
    and prints instructions for the officer.

    If required fields (api_url, api_key, log_directory) are missing or empty,
    prints an error identifying the missing fields and returns a config with
    local_only=True.

    Args:
        ini_path: Path to the INI configuration file.

    Returns:
        A ClientConfig instance. Check local_only flag to determine if cloud
        sync should be disabled.
    """
    if not os.path.exists(ini_path):
        _create_template(ini_path)
        print(f"[CONFIG] Created template configuration file: {ini_path}")
        print("[CONFIG] Please edit the file and fill in your guild's API URL and API key.")
        print("[CONFIG] Restart the client after editing the configuration.")
        return ClientConfig(local_only=True)

    parser = configparser.ConfigParser()
    parser.read(ini_path, encoding="utf-8")

    # Read values from INI, using defaults where appropriate
    api_url = parser.get("server", "api_url", fallback="").strip()
    api_key = parser.get("server", "api_key", fallback="").strip()
    log_directory = parser.get("client", "log_directory", fallback="").strip()

    poll_interval_str = parser.get("client", "poll_interval", fallback="1.0").strip()
    retry_interval_str = parser.get("client", "retry_interval", fallback="30.0").strip()

    # Parse numeric fields with safe defaults
    try:
        poll_interval = float(poll_interval_str)
    except (ValueError, TypeError):
        poll_interval = 1.0

    try:
        retry_interval = float(retry_interval_str)
    except (ValueError, TypeError):
        retry_interval = 30.0

    # Check required fields
    missing_fields = []
    if not api_url:
        missing_fields.append("api_url")
    if not api_key:
        missing_fields.append("api_key")
    if not log_directory:
        missing_fields.append("log_directory")

    local_only = False
    if missing_fields:
        local_only = True
        fields_str = ", ".join(missing_fields)
        print(f"[CONFIG] ERROR: Required fields missing or empty: {fields_str}")
        print("[CONFIG] Running in local-only mode (no cloud sync).")
        print(f"[CONFIG] Edit {ini_path} to provide the missing values.")

    return ClientConfig(
        api_url=api_url,
        api_key=api_key,
        log_directory=log_directory,
        poll_interval=poll_interval,
        retry_interval=retry_interval,
        local_only=local_only,
    )


def _create_template(ini_path: str) -> None:
    """Write the template INI file to disk."""
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write(TEMPLATE_INI)
