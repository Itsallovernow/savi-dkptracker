"""
test_dkp_import.py — Tests for dkp_import.py replay correctness.

Verifies two key properties:
1. No duplicates on re-replay: Running replay + record on the same log file
   twice produces the same number of records (second run writes 0 new records).
2. Replay matches live monitor: The records produced by replay_log() match
   what the live monitor would produce for the same log file.
"""

import json
import os
import tempfile
import unittest

import dkp_logger
from dkp_import import replay_log
from dkptrackerv3 import (
    ItemValidator,
    BidTracker,
    PriorityRollTracker,
    process_line,
    extract_player_from_log,
)

# Use a real log file from the project directory
LOG_FILE = os.path.join(os.path.dirname(__file__), "eqlog_Warderallover_pq.proj.txt")


class TestReplayNoDuplicates(unittest.TestCase):
    """
    Validates: Requirements 3.3
    Replaying the same log file twice produces no duplicate records.
    The second run should write 0 new records (all skipped as duplicates).
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.history_file = os.path.join(self.tmpdir, "dkp_history.json")

    def tearDown(self):
        for f in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, f))
        os.rmdir(self.tmpdir)

    @unittest.skipUnless(os.path.isfile(LOG_FILE), "Log file not available")
    def test_replay_same_log_twice_no_duplicates(self):
        """Running replay + record on the same log twice writes 0 new records on second pass."""
        records = replay_log(LOG_FILE)
        self.assertGreater(len(records), 0, "Log file should contain at least one auction")

        # First pass: write all records
        written_first = 0
        for item, winner, amount, history, timestamp in records:
            result = dkp_logger.record_auction(
                item, winner, amount, history,
                log_file_path=LOG_FILE,
                history_file=self.history_file,
                timestamp=timestamp,
            )
            if result:
                written_first += 1

        # Verify first pass wrote records
        self.assertGreater(written_first, 0, "First pass should write at least one record")

        with open(self.history_file, encoding='utf-8') as f:
            data_after_first = json.load(f)
        count_after_first = len(data_after_first["records"])

        # Second pass: replay the same log again
        records_second = replay_log(LOG_FILE)
        written_second = 0
        for item, winner, amount, history, timestamp in records_second:
            result = dkp_logger.record_auction(
                item, winner, amount, history,
                log_file_path=LOG_FILE,
                history_file=self.history_file,
                timestamp=timestamp,
            )
            if result:
                written_second += 1

        # Second pass should write zero new records
        self.assertEqual(written_second, 0, "Second replay should write 0 new records (all duplicates)")

        # Total record count should be unchanged
        with open(self.history_file, encoding='utf-8') as f:
            data_after_second = json.load(f)
        count_after_second = len(data_after_second["records"])

        self.assertEqual(count_after_first, count_after_second,
                         "Record count should not change after second replay")

    @unittest.skipUnless(os.path.isfile(LOG_FILE), "Log file not available")
    def test_replay_produces_consistent_record_count(self):
        """Two separate replay_log() calls on the same file return the same number of records."""
        records_a = replay_log(LOG_FILE)
        records_b = replay_log(LOG_FILE)
        self.assertEqual(len(records_a), len(records_b),
                         "replay_log() should be deterministic — same file, same count")


class TestReplayMatchesLiveMonitor(unittest.TestCase):
    """
    Validates: Correctness Property 5 (Import equivalence)
    The records produced by replay_log() should match what the live monitor
    would produce for the same log file (same items, winners, amounts).
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.history_file = os.path.join(self.tmpdir, "dkp_history.json")

    def tearDown(self):
        for f in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, f))
        os.rmdir(self.tmpdir)

    @unittest.skipUnless(os.path.isfile(LOG_FILE), "Log file not available")
    def test_replay_matches_live_monitor_output(self):
        """
        Replay records match what the live monitor produces for the same log.

        We simulate the live monitor by running process_line() with a patched
        close_item that captures (item, winner, amount) tuples, then compare
        against replay_log() output.
        """
        import io
        import sys

        # --- Simulate live monitor ---
        live_captured = []

        _real_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            validator = ItemValidator()
            tracker = BidTracker(validator)
            roll_tracker = PriorityRollTracker()
        finally:
            sys.stdout = _real_stdout

        player_name = extract_player_from_log(LOG_FILE)

        _original_close_item = tracker.close_item

        def _live_capturing_close_item(item, winner, amount, timestamp=None):
            history = list(tracker.bids_by_item[item]['history'])
            _original_close_item(item, winner, amount, timestamp=timestamp)
            live_captured.append((item, winner, amount, history))

        tracker.close_item = _live_capturing_close_item

        # Feed all lines through the live monitor
        _real_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            with open(LOG_FILE, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    process_line(line, tracker, roll_tracker, player_name)
        finally:
            sys.stdout = _real_stdout

        # --- Get replay output ---
        replay_records = replay_log(LOG_FILE)

        # --- Compare ---
        self.assertEqual(
            len(live_captured), len(replay_records),
            f"Live monitor found {len(live_captured)} auctions, "
            f"replay found {len(replay_records)}"
        )

        for i, (live, replayed) in enumerate(zip(live_captured, replay_records)):
            live_item, live_winner, live_amount, live_history = live
            replay_item, replay_winner, replay_amount, replay_history, _ = replayed

            self.assertEqual(
                live_item, replay_item,
                f"Auction {i}: item mismatch — live={live_item!r}, replay={replay_item!r}"
            )
            self.assertEqual(
                live_winner, replay_winner,
                f"Auction {i}: winner mismatch — live={live_winner!r}, replay={replay_winner!r}"
            )
            self.assertEqual(
                live_amount, replay_amount,
                f"Auction {i}: amount mismatch — live={live_amount!r}, replay={replay_amount!r}"
            )

    @unittest.skipUnless(os.path.isfile(LOG_FILE), "Log file not available")
    def test_replay_bid_histories_match_live(self):
        """
        The bid history arrays from replay match the live monitor's histories.
        Each bid entry should have the same (player, amount, bid_type, is_correction).
        """
        import io
        import sys

        # --- Simulate live monitor ---
        live_captured = []

        _real_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            validator = ItemValidator()
            tracker = BidTracker(validator)
            roll_tracker = PriorityRollTracker()
        finally:
            sys.stdout = _real_stdout

        player_name = extract_player_from_log(LOG_FILE)

        _original_close_item = tracker.close_item

        def _live_capturing_close_item(item, winner, amount, timestamp=None):
            history = list(tracker.bids_by_item[item]['history'])
            _original_close_item(item, winner, amount, timestamp=timestamp)
            live_captured.append((item, winner, amount, history))

        tracker.close_item = _live_capturing_close_item

        _real_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            with open(LOG_FILE, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    process_line(line, tracker, roll_tracker, player_name)
        finally:
            sys.stdout = _real_stdout

        # --- Get replay output ---
        replay_records = replay_log(LOG_FILE)

        # --- Compare bid histories ---
        self.assertEqual(len(live_captured), len(replay_records))

        for i, (live, replayed) in enumerate(zip(live_captured, replay_records)):
            _, _, _, live_history = live
            _, _, _, replay_history, _ = replayed

            self.assertEqual(
                len(live_history), len(replay_history),
                f"Auction {i}: bid history length mismatch — "
                f"live has {len(live_history)} bids, replay has {len(replay_history)}"
            )

            for j, (live_bid, replay_bid) in enumerate(zip(live_history, replay_history)):
                self.assertEqual(
                    live_bid, replay_bid,
                    f"Auction {i}, bid {j}: mismatch — "
                    f"live={live_bid!r}, replay={replay_bid!r}"
                )


if __name__ == "__main__":
    unittest.main()
