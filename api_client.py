"""
api_client.py — HTTP client for pushing AuctionRecords to the Backend API.

Provides push_record() which POSTs a serialized AuctionRecord to the
/auctions endpoint with bearer token authentication. Raises typed
exceptions so the offline queue can decide how to handle each failure.

Requirements: 2.1, 2.3, 2.4, 2.5
"""

import requests

from models import AuctionRecord
from offline_queue import PushRetryableError, PushUnauthorizedError, PushValidationError

# Default timeout for HTTP requests (seconds)
DEFAULT_TIMEOUT = 10.0


def push_record(
    record: AuctionRecord,
    api_url: str,
    api_key: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """
    POST an AuctionRecord to the backend API.

    Args:
        record: The AuctionRecord to push.
        api_url: Base URL of the backend API (e.g., "https://dkp.example.com").
        api_key: Bearer token for authentication.
        timeout: HTTP request timeout in seconds (default 10.0).

    Returns:
        True on successful push (HTTP 201 or 200).

    Raises:
        PushUnauthorizedError: If the server returns HTTP 401 (bad API key).
        PushValidationError: If the server returns HTTP 422 (invalid record).
        PushRetryableError: On timeout, connection error, or HTTP 5xx.
    """
    url = f"{api_url.rstrip('/')}/auctions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = record.to_dict()

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout:
        raise PushRetryableError("Request timed out")
    except requests.exceptions.ConnectionError:
        raise PushRetryableError("Connection error — server unreachable")
    except requests.exceptions.RequestException as e:
        raise PushRetryableError(f"Request failed: {e}")

    # HTTP 201 Created or 200 OK (duplicate acknowledged) — success
    if response.status_code in (200, 201):
        return True

    # HTTP 401 Unauthorized — bad API key, non-retryable
    if response.status_code == 401:
        raise PushUnauthorizedError("API returned 401 Unauthorized — check your API key")

    # HTTP 422 Unprocessable Entity — record rejected, non-retryable
    if response.status_code == 422:
        try:
            body = response.json()
            details = body.get("details", [])
            if details:
                detail = "; ".join(details)
            else:
                detail = body.get("message", body.get("error", response.text))
        except (ValueError, AttributeError):
            detail = response.text
        raise PushValidationError(f"Record rejected (422): {detail}")

    # HTTP 5xx — server error, retryable
    if 500 <= response.status_code < 600:
        raise PushRetryableError(
            f"Server error (HTTP {response.status_code}): {response.text}"
        )

    # Any other unexpected status — treat as retryable to avoid data loss
    raise PushRetryableError(
        f"Unexpected HTTP {response.status_code}: {response.text}"
    )
