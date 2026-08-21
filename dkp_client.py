"""
dkp_client.py — Cloud-syncing wrapper around the existing DKP bid tracker.

Reuses BidTracker, process_line, and monitor_log from dkptrackerv3.py,
adding background cloud sync of closed auction records. Network operations
run in background threads so the console log monitor is never blocked.

If the config is missing required fields (local-only mode), cloud sync is
disabled but the console tracker continues to work normally.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

# Version — shared with dkptrackerv3
from dkptrackerv3 import __version__ as _tracker_version

__version__ = _tracker_version

from client_config import load_config
from models import AuctionRecord, BidEntry, compute_dedup_key, compute_raid_session_id
from offline_queue import OfflineQueue, PushRetryableError, PushUnauthorizedError, PushValidationError
from api_client import push_record

from dkptrackerv3 import (
    BidTracker,
    ItemValidator,
    PriorityRollTracker,
    monitor_log,
    process_line,
)


# ---------------------------------------------------------------------------
# Cloud-syncing BidTracker subclass
# ---------------------------------------------------------------------------


class CloudBidTracker(BidTracker):
    """
    Subclass of BidTracker that hooks into close_item to push
    AuctionRecords to the backend API in a background thread.

    If local_only is True, behaves identically to the base BidTracker
    (no cloud sync, no network calls).
    """

    def __init__(self, validator, config, offline_queue):
        super().__init__(validator)
        self._config = config
        self._offline_queue = offline_queue
        self._local_only = config.local_only

    def close_item(self, item, winner, amount, timestamp=None):
        """
        Close an auction item locally, then push the record to the
        backend API in a background thread (non-blocking).
        """
        # Capture bid history BEFORE the base class clears it
        history = list(self.bids_by_item[item]['history'])
        log_source = getattr(self, '_current_log', None) or ""

        # Call the original close_item logic (console display, local history)
        super().close_item(item, winner, amount, timestamp=timestamp)

        # If local-only mode, skip cloud sync entirely
        if self._local_only:
            return

        # Build the AuctionRecord from closed item data
        record = self._build_record(item, winner, amount, timestamp, history, log_source)
        if record is None:
            return

        # Submit background thread to push the record (non-blocking)
        t = threading.Thread(
            target=self._background_push,
            args=(record,),
            daemon=True,
        )
        t.start()

    def reopen_item(self, item, erroneous_winner):
        """
        Override reopen_item to also send a DELETE to the API,
        removing the erroneous record from the server.
        """
        # Get the snapshot before the base class pops it (need dedup_key)
        needle = item.lower()
        target = None
        for key in self._closed_snapshots:
            if needle in key.lower() or key.lower() in needle:
                target = key
                break

        # Compute what the dedup_key would have been for the erroneous record
        dedup_key_to_delete = None
        if target and target in self._closed_snapshots:
            snap = self._closed_snapshots[target]
            # The erroneous gratss used this winner/amount
            ts = snap.get('timestamp') or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            raid_session_id = compute_raid_session_id(ts)
            dedup_key_to_delete = compute_dedup_key(
                target, erroneous_winner, snap.get('amount', 0), raid_session_id
            )

        # Call the base class reopen logic
        result = super().reopen_item(item, erroneous_winner)

        # If successful and we have cloud sync, delete from server in background
        if result and not self._local_only and dedup_key_to_delete:
            t = threading.Thread(
                target=self._background_delete,
                args=(dedup_key_to_delete,),
                daemon=True,
            )
            t.start()

        # Remove erroneous record from local history regardless of sync mode
        if result and dedup_key_to_delete:
            self._remove_local_record(dedup_key_to_delete)

        return result

    def _remove_local_record(self, dedup_key):
        """Remove a record from the local dkp_history.json by dedup_key."""
        try:
            if not os.path.exists(_HISTORY_FILE):
                return
            with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            records = data.get("records", [])
            original_count = len(records)
            data["records"] = [r for r in records if r.get("dedup_key") != dedup_key]
            if len(data["records"]) < original_count:
                with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
        except (OSError, json.JSONDecodeError):
            pass  # Non-critical

    def _background_delete(self, dedup_key):
        """Send a DELETE request to remove an erroneous record from the server."""
        import requests
        url = f"{self._config.api_url.rstrip('/')}/auctions/{dedup_key}"
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        try:
            response = requests.delete(url, headers=headers, timeout=10)
            if response.status_code == 200:
                print(f"[CloudSync] Deleted erroneous record from server.")
            elif response.status_code == 404:
                pass  # Already gone or never uploaded — fine
            else:
                print(f"[CloudSync] Failed to delete record (HTTP {response.status_code})")
        except Exception as e:
            print(f"[CloudSync] Could not delete record from server: {e}")

    def _build_record(self, item, winner, amount, timestamp, history, log_source):
        """
        Construct an AuctionRecord from closed auction data.
        Returns None if the record cannot be built (e.g. missing timestamp).
        """
        # Use provided timestamp or fall back to current UTC time
        if timestamp:
            ts = timestamp
        else:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Compute raid session ID from the timestamp's date
        raid_session_id = compute_raid_session_id(ts)

        # Compute dedup key
        dedup_key = compute_dedup_key(item, winner, amount, raid_session_id)

        # Convert history tuples to BidEntry objects
        # History format: (player, amount, bid_type_str, is_correction)
        bids = []
        for entry in history:
            player_name, bid_amount, bid_type, is_correction = entry
            # bid_type in history is 'main' or 'alt' already
            bids.append(BidEntry(
                player=player_name,
                amount=int(bid_amount),
                bid_type=bid_type,
                is_correction=bool(is_correction),
            ))

        # If no bids were recorded, add the winner as the single bid
        if not bids:
            bids.append(BidEntry(
                player=winner,
                amount=int(amount),
                bid_type="main",
                is_correction=False,
            ))

        record = AuctionRecord(
            item_name=item,
            winner=winner,
            amount=int(amount),
            timestamp=ts,
            raid_session_id=raid_session_id,
            dedup_key=dedup_key,
            log_source=os.path.basename(log_source) if log_source else "",
            bids=bids,
            uploaded_by=self._extract_officer_name(log_source),
        )

        return record

    def _extract_officer_name(self, log_source: str) -> str:
        """
        Extract the character name from the EQ log filename.
        Pattern: eqlog_<CharacterName>_pq.proj.txt
        Returns the character name or empty string if extraction fails.
        """
        if not log_source:
            return ""
        basename = os.path.basename(log_source)
        # Pattern: eqlog_CharName_server.txt
        parts = basename.split("_")
        if len(parts) >= 2 and parts[0].lower() == "eqlog":
            return parts[1]
        return ""

    def _background_push(self, record):
        """
        Push an AuctionRecord to the backend API. On failure, enqueue
        to the offline queue for later retry. This runs in a daemon thread.
        """
        try:
            push_record(
                record=record,
                api_url=self._config.api_url,
                api_key=self._config.api_key,
            )
            # Success — mark in local history and confirm to user
            print(f"[CloudSync] ✓ Uploaded: {record.item_name} → {record.winner} ({record.amount} DKP)")
            self._mark_local_record_uploaded(record.dedup_key)
        except PushUnauthorizedError:
            print(
                "[CloudSync] ✗ API returned 401 Unauthorized. "
                "Check your API key in dkp_client.ini."
            )
            # Still enqueue so it can be retried after key is fixed
            self._offline_queue.enqueue(record)
        except PushValidationError as e:
            print(f"[CloudSync] ✗ Record rejected (422): {record.item_name} → {record.winner} | {e}")
            # Validation errors are not retryable — don't enqueue
        except (PushRetryableError, Exception) as e:
            # Network/transient failure — enqueue for later retry
            print(f"[CloudSync] ⏳ Queued (network issue): {record.item_name} → {record.winner}")
            self._offline_queue.enqueue(record)

    def _mark_local_record_uploaded(self, dedup_key):
        """Mark a record in the local dkp_history.json as uploaded."""
        try:
            if not os.path.exists(_HISTORY_FILE):
                return
            with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
            for record in data.get("records", []):
                if record.get("dedup_key") == dedup_key:
                    record["uploaded"] = True
                    break
            else:
                return  # Record not found locally — no-op
            with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except (OSError, json.JSONDecodeError):
            pass  # Non-critical


# ---------------------------------------------------------------------------
# Periodic flush timer thread
# ---------------------------------------------------------------------------


class FlushTimer:
    """
    Background timer that periodically flushes the offline queue.
    Runs as a daemon thread so it doesn't block program exit.
    """

    def __init__(self, offline_queue, config):
        self._queue = offline_queue
        self._config = config
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        """Start the periodic flush timer in a background thread."""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Signal the timer to stop."""
        self._stop_event.set()

    def _run(self):
        """Periodically flush the offline queue."""
        interval = self._config.retry_interval

        while not self._stop_event.is_set():
            # Wait for the retry interval (or until stopped)
            self._stop_event.wait(timeout=interval)
            if self._stop_event.is_set():
                break

            # Only flush if there are pending records
            if self._queue.pending_count > 0:
                try:
                    sent = self._queue.flush(self._make_push_fn())
                    if sent > 0:
                        print(f"[CloudSync] Flushed {sent} queued record(s) to server.")
                except Exception:
                    pass  # Errors are handled inside flush/push_fn

    def _make_push_fn(self):
        """Create a push function compatible with OfflineQueue.flush()."""
        config = self._config

        def _push(record):
            push_record(
                record=record,
                api_url=config.api_url,
                api_key=config.api_key,
            )

        return _push


# ---------------------------------------------------------------------------
# History backfill — upload local records that predate cloud sync
# ---------------------------------------------------------------------------

# Resolve the directory where the .exe (or script) lives — for user-facing files
# like dkp_client.ini and dkp_history.json that officers place beside the exe.
# In a PyInstaller --onefile bundle, __file__ points to the temp extraction dir,
# so we use sys.executable which always points to the actual .exe location.
if getattr(sys, 'frozen', False):
    # Running as a PyInstaller bundle
    _EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    # Running from source
    _EXE_DIR = os.path.dirname(os.path.abspath(__file__))

# Path to the local history file (same directory as the exe)
_HISTORY_FILE = os.path.join(_EXE_DIR, "dkp_history.json")


def _start_history_backfill(config):
    """
    Kick off a background thread that reads dkp_history.json and pushes
    any records that haven't been uploaded yet. Marks them as uploaded
    after successful push. Safe to run multiple times (idempotent via
    dedup_key on the server + local 'uploaded' flag).
    """
    if not os.path.exists(_HISTORY_FILE):
        print(f"[Backfill] No local history file found at {_HISTORY_FILE}")
        return

    # Quick peek at how many records need uploading
    try:
        with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        records = data.get("records", [])
        pending = [r for r in records if r.get("uploaded") is not True]
        print(f"[Backfill] Found {_HISTORY_FILE}: {len(records)} total records, {len(pending)} not yet uploaded.")
        if not pending:
            return
    except (OSError, json.JSONDecodeError):
        print(f"[Backfill] Could not read {_HISTORY_FILE}")
        return

    t = threading.Thread(target=_backfill_worker, args=(config,), daemon=True)
    t.start()


def _backfill_worker(config):
    """Background worker that uploads un-synced local history records."""
    try:
        with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
    except (OSError, json.JSONDecodeError) as e:
        print(f"[Backfill] Could not read {_HISTORY_FILE}: {e}")
        return

    records = data.get("records", [])
    if not records:
        return

    # Find records not yet uploaded (includes previously skipped ones)
    pending = [(i, r) for i, r in enumerate(records) if r.get("uploaded") is not True]
    if not pending:
        return

    print(f"[Backfill] Found {len(pending)} local record(s) to upload...")

    uploaded_count = 0
    total = len(pending)
    for idx, record_dict in pending:
        # Print progress in-place
        print(f"\r[Backfill] Uploading {uploaded_count + 1}/{total}...", end="", flush=True)

        # Build an AuctionRecord from the local data
        try:
            record = AuctionRecord.from_dict(record_dict)
            # If bids is empty (older records), synthesize one from winner/amount
            if not record.bids:
                record.bids = [BidEntry(
                    player=record.winner,
                    amount=record.amount,
                    bid_type="main",
                    is_correction=False,
                )]
            # Extract officer name from log_source for uploaded_by
            log_source = record_dict.get("log_source", "")
            parts = os.path.basename(log_source).split("_")
            if len(parts) >= 2 and parts[0].lower() == "eqlog":
                record.uploaded_by = parts[1]
            elif len(parts) >= 3 and parts[1].lower() == "eqlog":
                # Handle format like "428_eqlog_Warderallover_pq.proj.txt"
                record.uploaded_by = parts[2]
        except (KeyError, TypeError) as e:
            print(f"\r[Backfill] Skipping malformed record at index {idx}: {e}")
            continue

        # Push to server
        try:
            push_record(
                record=record,
                api_url=config.api_url,
                api_key=config.api_key,
            )
            # Mark as uploaded in local data
            records[idx]["uploaded"] = True
            uploaded_count += 1
        except PushUnauthorizedError:
            print(f"\r[Backfill] API returned 401 — stopping backfill. Check API key.")
            break
        except PushValidationError as e:
            # Record failed validation — skip for now but don't mark as uploaded
            # so it retries next launch (in case we fix the validation)
            item_name = record_dict.get("item_name", "?")
            winner = record_dict.get("winner", "?")
            dk = record_dict.get("dedup_key", "?")[:12]
            print(f"\r[Backfill] Rejected: {item_name} → {winner} (dedup:{dk}...) | {e}          ")
            continue
        except (PushRetryableError, Exception) as e:
            # Network issue — stop and try again next launch
            print(f"\r[Backfill] Network error, will retry next launch: {e}")
            break

    # Save progress (even partial) back to the history file
    if uploaded_count > 0:
        try:
            data["records"] = records
            with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"\r[Backfill] Done — uploaded {uploaded_count}/{total} record(s) to server.")
        except OSError as e:
            print(f"\r[Backfill] Could not save progress to {_HISTORY_FILE}: {e}")
    else:
        print(f"\r[Backfill] No records uploaded (network issue or empty).        ")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main():
    """
    Entry point for the cloud-syncing DKP client.

    1. Load config from dkp_client.ini
    2. Initialize the cloud-syncing BidTracker
    3. Start the offline queue flush timer (if not local-only)
    4. Begin monitoring the EQ log (blocking main loop)
    """
    # Determine config path: same directory as the exe
    config_path = os.path.join(_EXE_DIR, "dkp_client.ini")

    print(f"DKP Client v{__version__}")
    print("=" * 40)

    # Load configuration
    config = load_config(config_path)

    if config.local_only:
        print("[DKP Client] Running in LOCAL-ONLY mode (no cloud sync).")
        print("[DKP Client] Console bid tracking is fully operational.\n")
    else:
        print(f"[DKP Client] Cloud sync enabled -> {config.api_url}")
        print(f"[DKP Client] Retry interval: {config.retry_interval}s\n")

    # Override LOG_DIR if config specifies log_directory
    if config.log_directory:
        import dkptrackerv3
        dkptrackerv3.LOG_DIR = config.log_directory
        dkptrackerv3.LOG_GLOB = os.path.join(config.log_directory, "eqlog_*_pq.proj.txt")

    # Initialize components
    offline_queue = OfflineQueue()
    validator = ItemValidator()
    tracker = CloudBidTracker(validator, config, offline_queue)
    roll_tracker = PriorityRollTracker()

    # Start periodic flush timer (only if cloud sync is active)
    flush_timer = None
    if not config.local_only:
        flush_timer = FlushTimer(offline_queue, config)
        flush_timer.start()

        # Backfill: push any local history records that haven't been uploaded yet
        _start_history_backfill(config)

    # Start log monitoring (this blocks — it's the main event loop)
    try:
        monitor_log(tracker, roll_tracker)
    except KeyboardInterrupt:
        print("\n[DKP Client] Shutting down...")
        if flush_timer:
            flush_timer.stop()
        # Final flush attempt for any remaining queued records
        if not config.local_only and offline_queue.pending_count > 0:
            print(f"[DKP Client] Flushing {offline_queue.pending_count} remaining record(s)...")
            try:
                offline_queue.flush(lambda r: push_record(
                    record=r,
                    api_url=config.api_url,
                    api_key=config.api_key,
                ))
            except Exception:
                print("[DKP Client] Some records could not be flushed. They remain in pending_uploads.json.")
        print("[DKP Client] Goodbye.")


if __name__ == "__main__":
    main()
