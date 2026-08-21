"""Property-based tests for client configuration local-only mode.

Property 5: Missing config fields enable local-only mode
Validates: Requirements 4.4

For any combination of missing or empty required config fields (api_url, api_key,
log_directory), the DKP_Client SHALL enter local-only mode (console bid tracking
works, no cloud sync attempted) and report which fields are missing.
"""

import configparser
import io
import os
import sys
import tempfile
from unittest.mock import patch

import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

from client_config import load_config, ClientConfig


# Strategy that produces either an empty/whitespace-only string or omits the field entirely
_empty_value = st.sampled_from(["", "   ", "\t", "\n"])

# Strategy for a non-empty valid value
_valid_url = st.just("https://dkp.example.com")
_valid_key = st.just("abc123secretkey")
_valid_dir = st.just("C:\\EverQuest\\Logs")


def _field_strategy(must_be_missing: bool):
    """Return a strategy that is empty if must_be_missing, otherwise a valid value placeholder."""
    if must_be_missing:
        return _empty_value
    # For non-missing fields, we still want valid values
    return None  # Sentinel — handled in the composite strategy


@st.composite
def missing_config_strategy(draw):
    """Generate INI file content where at least one required field is missing or empty.

    Returns a tuple of (ini_content_string, set_of_missing_field_names).
    """
    # Decide which fields are missing — at least one must be missing
    api_url_missing = draw(st.booleans())
    api_key_missing = draw(st.booleans())
    log_dir_missing = draw(st.booleans())

    # Ensure at least one is missing
    assume(api_url_missing or api_key_missing or log_dir_missing)

    # Generate values
    if api_url_missing:
        api_url_val = draw(_empty_value)
    else:
        api_url_val = "https://dkp.example.com"

    if api_key_missing:
        api_key_val = draw(_empty_value)
    else:
        api_key_val = "abc123secretkey"

    if log_dir_missing:
        log_dir_val = draw(_empty_value)
    else:
        log_dir_val = "C:\\EverQuest\\Logs"

    # Optionally include valid optional fields
    poll_interval = draw(st.sampled_from(["1.0", "2.5", "0.5"]))
    retry_interval = draw(st.sampled_from(["30.0", "60.0", "15.0"]))

    # Decide whether to include sections/keys or omit them entirely
    # For missing fields, sometimes omit the key entirely vs leaving it empty
    omit_key_entirely = draw(st.booleans())

    # Build INI content
    lines = ["[server]\n"]
    if api_url_missing and omit_key_entirely:
        pass  # Don't include the key at all
    else:
        lines.append(f"api_url = {api_url_val}\n")

    omit_key_entirely_2 = draw(st.booleans())
    if api_key_missing and omit_key_entirely_2:
        pass
    else:
        lines.append(f"api_key = {api_key_val}\n")

    lines.append("\n[client]\n")

    omit_key_entirely_3 = draw(st.booleans())
    if log_dir_missing and omit_key_entirely_3:
        pass
    else:
        lines.append(f"log_directory = {log_dir_val}\n")

    lines.append(f"poll_interval = {poll_interval}\n")
    lines.append(f"retry_interval = {retry_interval}\n")

    ini_content = "".join(lines)

    # Track which fields are actually missing
    missing_fields = set()
    if api_url_missing:
        missing_fields.add("api_url")
    if api_key_missing:
        missing_fields.add("api_key")
    if log_dir_missing:
        missing_fields.add("log_directory")

    return ini_content, missing_fields


class TestProperty5MissingConfigFieldsLocalOnly:
    """Property 5: Missing config fields enable local-only mode.

    **Validates: Requirements 4.4**
    """

    @given(data=missing_config_strategy())
    @settings(max_examples=100)
    def test_missing_fields_enable_local_only(self, data):
        """For any combination of missing/empty required fields, local_only must be True."""
        ini_content, expected_missing = data

        # Write INI to a temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ini", delete=False, encoding="utf-8"
        ) as f:
            f.write(ini_content)
            ini_path = f.name

        try:
            # Suppress print output during test
            with patch("builtins.print"):
                config = load_config(ini_path)

            # Property: local_only must be True when any required field is missing
            assert config.local_only is True, (
                f"Expected local_only=True when fields {expected_missing} are missing, "
                f"got local_only={config.local_only}"
            )
        finally:
            os.unlink(ini_path)

    @given(data=missing_config_strategy())
    @settings(max_examples=100)
    def test_missing_fields_are_reported(self, data):
        """For any combination of missing/empty required fields, each missing field is reported."""
        ini_content, expected_missing = data

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ini", delete=False, encoding="utf-8"
        ) as f:
            f.write(ini_content)
            ini_path = f.name

        try:
            # Capture print output
            captured = io.StringIO()
            with patch("builtins.print", side_effect=lambda *args, **kwargs: captured.write(" ".join(str(a) for a in args) + "\n")):
                config = load_config(ini_path)

            output = captured.getvalue()

            # Property: each missing field name must appear in the error output
            for field_name in expected_missing:
                assert field_name in output, (
                    f"Expected missing field '{field_name}' to be reported in output, "
                    f"but output was: {output}"
                )
        finally:
            os.unlink(ini_path)

    @given(data=missing_config_strategy())
    @settings(max_examples=100)
    def test_config_still_returns_valid_object(self, data):
        """Even in local-only mode, a valid ClientConfig object is returned."""
        ini_content, expected_missing = data

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ini", delete=False, encoding="utf-8"
        ) as f:
            f.write(ini_content)
            ini_path = f.name

        try:
            config = load_config(ini_path)

            # Property: result is always a ClientConfig instance
            assert isinstance(config, ClientConfig)

            # Property: poll_interval and retry_interval have valid defaults
            assert config.poll_interval > 0
            assert config.retry_interval > 0
        finally:
            os.unlink(ini_path)

    @given(
        poll_interval=st.sampled_from(["1.0", "2.0", "0.5"]),
        retry_interval=st.sampled_from(["30.0", "60.0", "10.0"]),
    )
    @settings(max_examples=50)
    def test_all_fields_present_not_local_only(self, poll_interval, retry_interval):
        """Inverse property: when ALL required fields are present, local_only is False."""
        ini_content = (
            "[server]\n"
            "api_url = https://dkp.example.com\n"
            "api_key = valid_key_123\n"
            "\n"
            "[client]\n"
            "log_directory = C:\\EverQuest\\Logs\n"
            f"poll_interval = {poll_interval}\n"
            f"retry_interval = {retry_interval}\n"
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ini", delete=False, encoding="utf-8"
        ) as f:
            f.write(ini_content)
            ini_path = f.name

        try:
            config = load_config(ini_path)

            # Inverse property: all fields present means NOT local-only
            assert config.local_only is False, (
                f"Expected local_only=False when all fields are present, "
                f"got local_only={config.local_only}"
            )
        finally:
            os.unlink(ini_path)
