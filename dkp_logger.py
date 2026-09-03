"""
dkp_logger.py — DKP Bid History Persistence Module

Writes closed auction records to dkp_history.json.
Called from dkptrackerv3.py's BidTracker.close_item().

No third-party dependencies — stdlib only.
"""

import datetime
import hashlib
import json
import os
import re
import sys

HISTORY_VERSION = 1
DEFAULT_HISTORY_FILE = "dkp_history.json"

# EQ log timestamp pattern: [Day Mon DD HH:MM:SS YYYY]
_TS_PAT = re.compile(r'\[(\w{3} \w{3} +\d{1,2} \d{2}:\d{2}:\d{2} \d{4})\]')


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _raid_session_id(log_file_path):
    """
    Derive a deterministic Raid Session ID from the first timestamp in the
    log file.  Two officers logging the same raid will produce the same ID
    regardless of their local timezone because we use the date as written in
    the EQ log (which reflects the server/client clock, not UTC).

    Falls back to the current UTC date if the file cannot be read.

    DEPRECATED: Use _raid_session_id_from_timestamp() instead for new code.
    Kept for backward compatibility with dkp_import.py.
    """
    if log_file_path:
        try:
            with open(log_file_path, encoding='utf-8', errors='ignore') as f:
                for line in f:
                    m = _TS_PAT.search(line)
                    if m:
                        from time import strptime
                        t = strptime(m.group(1), "%a %b %d %H:%M:%S %Y")
                        date_str = f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
                        return hashlib.sha256(date_str.encode('utf-8')).hexdigest()
        except OSError:
            pass

    # Fallback: current UTC date
    date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return hashlib.sha256(date_str.encode('utf-8')).hexdigest()


def _raid_session_id_from_timestamp(iso_timestamp):
    """
    Derive a deterministic Raid Session ID from an ISO 8601 timestamp.
    Uses the date portion (first 10 characters: YYYY-MM-DD).

    This is the preferred method — ensures all officers produce the same
    session ID for the same auction regardless of log file start date.
    """
    date_str = iso_timestamp[:10]
    return hashlib.sha256(date_str.encode('utf-8')).hexdigest()


def _dedup_key(item_name, winner, amount, raid_session_id):
    """
    SHA-256 of null-byte-separated fields.
    Identical for two officers logging the same auction on the same raid.
    """
    raw = f"{item_name}\x00{winner}\x00{amount}\x00{raid_session_id}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _load(path):
    """Load history file, returning a default structure if missing or corrupt."""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        # Validate top-level structure
        if isinstance(data, dict) and 'records' in data:
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"version": HISTORY_VERSION, "records": []}


def _save(path, data):
    """
    Atomic write: write to <path>.tmp then rename.
    Prevents a corrupt history file if the process is interrupted mid-write.
    """
    tmp = path + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _acquire_lock(lock_path):
    """
    Best-effort file lock for concurrent writers.
    Returns an open file handle that must be closed to release the lock.
    """
    lock_fd = open(lock_path, 'w')
    try:
        import fcntl
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except (ImportError, AttributeError, OSError):
        # Windows or unsupported platform — no flock, accept rare race
        pass
    return lock_fd


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_auction(
    item_name,
    winner,
    amount,
    history,
    log_file_path=None,
    history_file=DEFAULT_HISTORY_FILE,
    timestamp=None,
):
    """
    Persist one closed auction to history_file.

    Parameters
    ----------
    item_name     : str   — canonical item name
    winner        : str   — player who won the auction
    amount        : int   — winning bid in DKP
    history       : list  — list of (player, amount, bid_type, is_correction)
                            tuples in chronological order
    log_file_path : str | None — path to the active EQ log file (used to
                                 derive the raid session ID)
    history_file  : str   — path to dkp_history.json (default)
    timestamp     : str | None — ISO 8601 UTC timestamp for the auction close
                                 event (from the log line). If None, falls back
                                 to current UTC time.

    Returns
    -------
    True if the record was written, False if it was a duplicate or an error
    occurred.
    """
    # Use the auction close timestamp for session ID (not the first log line)
    # This ensures all officers produce the same session ID for the same auction
    # regardless of when their log file started.
    ts = timestamp if timestamp else datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session_id = _raid_session_id_from_timestamp(ts)
    key        = _dedup_key(item_name, winner, amount, session_id)
    lock_path  = history_file + ".lock"
    lock_fd    = None

    try:
        lock_fd = _acquire_lock(lock_path)

        data = _load(history_file)
        existing_keys = {r.get('dedup_key') for r in data.get('records', [])}

        if key in existing_keys:
            print(
                f"[dkp_logger] Duplicate skipped: "
                f"{item_name} → {winner} {amount} DKP"
            )
            return False

        record = {
            "item_name":       item_name,
            "winner":          winner,
            "amount":          int(amount),
            "timestamp":       ts,
            "raid_session_id": session_id,
            "dedup_key":       key,
            "log_source":      os.path.basename(log_file_path) if log_file_path else "",
            "bids": [
                {
                    "player":       p,
                    "amount":       int(a),
                    "bid_type":     t,
                    "is_correction": bool(c),
                }
                for p, a, t, c in history
            ],
        }

        data.setdefault('records', []).append(record)
        _save(history_file, data)
        return True

    except OSError as exc:
        print(f"[dkp_logger] Write error: {exc}", file=sys.stderr)
        return False

    finally:
        if lock_fd is not None:
            try:
                lock_fd.close()
            except OSError:
                pass
