#!/usr/bin/env python3
"""
dkp_import.py — CLI replay script for back-filling dkp_history.json.

Replays one or more EQ log files through the existing bid-parsing logic
and writes auction records to the history file using dkp_logger.
Supports both .txt log files and .zip archives containing .txt files.

Usage:
    python dkp_import.py <logfile> [<logfile> ...] [--output dkp_history.json] [--dry-run]
    python dkp_import.py archive.zip [--output dkp_history.json]
"""

import argparse
import glob
import io
import os
import sys
import tempfile
import zipfile

from dkptrackerv3 import (
    ItemValidator,
    BidTracker,
    PriorityRollTracker,
    process_line,
    extract_player_from_log,
)
import dkp_logger


def _build_parser():
    """Build and return the argument parser for dkp_import."""
    parser = argparse.ArgumentParser(
        prog="dkp_import",
        description="Replay EQ log files to back-fill dkp_history.json with auction records.",
    )
    parser.add_argument(
        "logfiles",
        nargs="+",
        metavar="logfile",
        help="One or more EQ log file paths (.txt) or zip archives containing .txt log files",
    )
    parser.add_argument(
        "--output",
        default="dkp_history.json",
        metavar="PATH",
        help="Path to history file (default: dkp_history.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report without writing",
    )
    return parser


def replay_log(log_path):
    """
    Replay a single EQ log file through the bid-parsing logic.

    Creates fresh tracker instances, monkey-patches close_item to capture
    auction records instead of printing to terminal, then feeds every line
    through process_line().

    Args:
        log_path: Path to the EQ log file to replay.

    Returns:
        A list of tuples (item, winner, amount, history, timestamp) for each
        closed auction detected in the log file. timestamp is an ISO 8601
        string extracted from the gratss log line, or None.
    """
    captured = []

    # Suppress stdout during tracker instantiation (BidTracker prints a banner)
    _real_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        validator = ItemValidator()
        tracker = BidTracker(validator)
        roll_tracker = PriorityRollTracker()
    finally:
        sys.stdout = _real_stdout

    # Disable dkp_logger inside close_item during replay — we handle writes
    # ourselves in main() so we can respect --dry-run and --output flags.
    import dkptrackerv3 as _tv3
    _prev_history_enabled = _tv3._HISTORY_ENABLED
    _tv3._HISTORY_ENABLED = False

    # Set the log path on the tracker (used by dkp_logger for raid session ID)
    tracker._current_log = log_path

    # Determine the player name from the log file name
    player_name = extract_player_from_log(log_path)

    # Save the original close_item method
    _original_close_item = tracker.close_item

    def _capturing_close_item(item, winner, amount, timestamp=None):
        """
        Wrapper around close_item that captures the auction record
        (item, winner, amount, history, timestamp) without printing to terminal.
        """
        # Grab the bid history snapshot before close_item cleans it up
        history = list(tracker.bids_by_item[item]['history'])

        # Run the original close_item logic (stdout is already suppressed
        # by the outer replay loop)
        _original_close_item(item, winner, amount, timestamp=timestamp)

        # Capture the record including the log-line timestamp
        captured.append((item, winner, amount, history, timestamp))

    # Monkey-patch close_item on this instance
    tracker.close_item = _capturing_close_item

    # Read and replay the log file line by line.
    # Suppress all stdout during replay (announce_items, display_table, etc.)
    _real_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        with open(log_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                process_line(line, tracker, roll_tracker, player_name)
    finally:
        sys.stdout = _real_stdout
        _tv3._HISTORY_ENABLED = _prev_history_enabled

    return captured


def _expand_paths(paths):
    """
    Expand a list of file paths, handling glob patterns (for Windows/PowerShell
    which don't expand wildcards) and extracting .txt files from .zip archives.
    Returns a list of (display_name, file_path, tmpdir) tuples. For zip entries,
    the file_path points to a temp-extracted file.

    On bash/Unix, globs are already expanded by the shell, so glob.glob() just
    returns the same path if it's already a concrete file. Safe on both platforms.
    """
    expanded = []
    for path in paths:
        # Expand glob patterns (handles *, ?, etc.)
        # If path has no wildcards, glob returns [path] if it exists, [] if not
        if any(c in path for c in '*?['):
            matched = sorted(glob.glob(path))
            if not matched:
                print(f"Warning: no files matched pattern '{path}'", file=sys.stderr)
                continue
        else:
            matched = [path]

        for resolved_path in matched:
            if resolved_path.lower().endswith('.zip'):
                try:
                    with zipfile.ZipFile(resolved_path, 'r') as zf:
                        txt_entries = [n for n in zf.namelist() if n.lower().endswith('.txt')]
                        if not txt_entries:
                            print(f"Warning: no .txt files found in '{resolved_path}'", file=sys.stderr)
                            continue
                        tmpdir = tempfile.mkdtemp(prefix="dkp_import_")
                        for entry in txt_entries:
                            extracted = zf.extract(entry, tmpdir)
                            display = f"{os.path.basename(resolved_path)}:{entry}"
                            expanded.append((display, extracted, tmpdir))
                except (zipfile.BadZipFile, OSError) as e:
                    print(f"Error: cannot read zip '{resolved_path}': {e}", file=sys.stderr)
            else:
                expanded.append((os.path.basename(resolved_path), resolved_path, None))
    return expanded


def main():
    """Entry point for the DKP import CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    # Expand zip files into individual .txt paths
    file_list = _expand_paths(args.logfiles)
    tmpdirs_to_clean = set()

    for display_name, log_path, tmpdir in file_list:
        if tmpdir:
            tmpdirs_to_clean.add(tmpdir)

        try:
            records = replay_log(log_path)
        except OSError as e:
            print(f"Error: cannot read '{display_name}': {e}", file=sys.stderr)
            continue

        found = len(records)
        written = 0
        skipped = 0

        for item, winner, amount, history, timestamp in records:
            if args.dry_run:
                # Dry-run mode: count records without writing
                pass
            else:
                result = dkp_logger.record_auction(
                    item, winner, amount, history,
                    log_file_path=log_path,
                    history_file=args.output,
                    timestamp=timestamp,
                )
                if result:
                    written += 1
                else:
                    skipped += 1

        if args.dry_run:
            print(f"DRY RUN: {display_name} — {found} auctions found (no writes)")
        else:
            print(f"{display_name} — {found} found, {written} written, {skipped} skipped")

    # Clean up temp directories from zip extraction
    import shutil
    for d in tmpdirs_to_clean:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


if __name__ == "__main__":
    main()
