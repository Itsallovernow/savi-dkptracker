"""
offline_queue.py — Offline Queue for the DKP Client.

Persists unsent AuctionRecords to a local JSON file and retries delivery
on a timer. Records are stored in FIFO order and only removed after
successful push acknowledgment from the backend.

Uses file locking (msvcrt on Windows, fcntl on Unix) to prevent corruption
from concurrent access.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
"""

import json
import os
import sys
import time
from typing import Callable, List

from models import AuctionRecord


# ---------------------------------------------------------------------------
# File locking helpers (cross-platform)
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    import msvcrt

    def _lock_file(f):
        """Acquire an exclusive lock on the file (Windows)."""
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(f):
        """Release the exclusive lock on the file (Windows)."""
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(f):
        """Acquire an exclusive lock on the file (Unix)."""
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock_file(f):
        """Release the exclusive lock on the file (Unix)."""
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Custom exceptions for push_fn signaling
# ---------------------------------------------------------------------------


class PushUnauthorizedError(Exception):
    """Raised when the backend returns HTTP 401 (invalid API key)."""
    pass


class PushValidationError(Exception):
    """Raised when the backend returns HTTP 422 (unprocessable entity)."""

    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)


class PushRetryableError(Exception):
    """Raised when the push fails transiently (timeout, 5xx, connection error)."""
    pass


# ---------------------------------------------------------------------------
# OfflineQueue class
# ---------------------------------------------------------------------------


class OfflineQueue:
    """
    Persists unsent AuctionRecords to a local JSON file and retries.

    Records are stored in `pending_uploads.json` in FIFO order.
    Failed (non-retryable) records are moved to `failed_uploads.json`.
    """

    def __init__(
        self,
        pending_path: str = "pending_uploads.json",
        failed_path: str = "failed_uploads.json",
    ):
        self._pending_path = pending_path
        self._failed_path = failed_path

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def enqueue(self, record: AuctionRecord) -> None:
        """
        Append a record to the pending uploads file immediately.

        Uses file locking to prevent corruption from concurrent access.
        """
        records = self._read_pending()
        records.append(record.to_dict())
        self._write_pending(records)

    def flush(self, push_fn: Callable[[AuctionRecord], None]) -> int:
        """
        Attempt to send all pending records in FIFO order.

        Args:
            push_fn: A callable that takes an AuctionRecord and either
                     returns successfully (HTTP 200/201) or raises:
                     - PushUnauthorizedError for HTTP 401
                     - PushValidationError for HTTP 422
                     - PushRetryableError for transient failures

        Returns:
            The number of records successfully sent.

        Behavior:
            - On success (no exception): removes the record from pending file.
            - On HTTP 401: stops retrying all records, prints console message.
            - On HTTP 422: moves the record to failed_uploads.json, logs error.
            - On transient error: stops processing (records stay in queue for
              next flush cycle).
        """
        records = self._read_pending()
        if not records:
            return 0

        sent_count = 0
        remaining = list(records)  # copy to iterate safely

        for record_dict in records:
            auction_record = AuctionRecord.from_dict(record_dict)

            try:
                push_fn(auction_record)
            except PushUnauthorizedError:
                # HTTP 401 — stop retrying, alert the officer
                print(
                    "[OfflineQueue] ERROR: API returned 401 Unauthorized. "
                    "Please check your API key in dkp_client.ini."
                )
                # Write back remaining records and stop
                self._write_pending(remaining)
                return sent_count
            except PushValidationError as e:
                # HTTP 422 — move to dead-letter file, continue with next
                print(
                    f"[OfflineQueue] ERROR: Record rejected (422): {e.message}. "
                    f"Moving to {self._failed_path}."
                )
                self._move_to_failed(record_dict)
                remaining.remove(record_dict)
                continue
            except (PushRetryableError, Exception):
                # Transient failure — stop processing, try again next cycle
                self._write_pending(remaining)
                return sent_count

            # Success — remove from pending
            remaining.remove(record_dict)
            sent_count += 1

        # All records processed successfully (or moved to failed)
        self._write_pending(remaining)
        return sent_count

    @property
    def pending_count(self) -> int:
        """Number of records waiting to be pushed."""
        records = self._read_pending()
        return len(records)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_pending(self) -> List[dict]:
        """Read all pending records from the JSON file with locking."""
        if not os.path.exists(self._pending_path):
            return []

        try:
            with open(self._pending_path, "r+", encoding="utf-8") as f:
                _lock_file(f)
                try:
                    content = f.read()
                    if not content.strip():
                        return []
                    return json.loads(content)
                finally:
                    _unlock_file(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _write_pending(self, records: List[dict]) -> None:
        """Write all pending records to the JSON file with locking."""
        with open(self._pending_path, "w", encoding="utf-8") as f:
            _lock_file(f)
            try:
                json.dump(records, f, indent=2)
            finally:
                _unlock_file(f)

    def _read_failed(self) -> List[dict]:
        """Read all failed records from the dead-letter file."""
        if not os.path.exists(self._failed_path):
            return []

        try:
            with open(self._failed_path, "r+", encoding="utf-8") as f:
                _lock_file(f)
                try:
                    content = f.read()
                    if not content.strip():
                        return []
                    return json.loads(content)
                finally:
                    _unlock_file(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _write_failed(self, records: List[dict]) -> None:
        """Write all failed records to the dead-letter file with locking."""
        with open(self._failed_path, "w", encoding="utf-8") as f:
            _lock_file(f)
            try:
                json.dump(records, f, indent=2)
            finally:
                _unlock_file(f)

    def _move_to_failed(self, record_dict: dict) -> None:
        """Append a record to the dead-letter file."""
        failed_records = self._read_failed()
        failed_records.append(record_dict)
        self._write_failed(failed_records)
