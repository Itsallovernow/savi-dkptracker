"""
test_dkp_logger.py — Unit tests for dkp_logger.py correctness properties.

Tests the four correctness properties from the design document:
1. Round-trip: json.loads(json.dumps(record)) == record for any Auction Record
2. Dedup idempotency: calling record_auction() twice with identical args writes exactly one record
3. Dedup cross-officer: same (item, winner, amount, raid_session_id) but different log_source
   produces the same dedup_key and only one is stored
4. History monotonicity: record_auction() never removes or modifies existing records
"""

import json
import os
import tempfile
import unittest

import dkp_logger


class TestRoundTrip(unittest.TestCase):
    """
    Validates: Requirements 4.4
    Round-trip property: json.loads(json.dumps(record)) == record
    for any Auction Record written by dkp_logger.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.history_file = os.path.join(self.tmpdir, "dkp_history.json")

    def tearDown(self):
        for f in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, f))
        os.rmdir(self.tmpdir)

    def test_round_trip_simple_record(self):
        """A written record survives JSON serialization round-trip."""
        history = [
            ("Caezar", 200, "main", False),
            ("Dynasty", 10, "alt", False),
        ]
        dkp_logger.record_auction(
            "Ring of Flamewarding", "Caezar", 200, history,
            log_file_path=None, history_file=self.history_file,
        )

        with open(self.history_file, encoding='utf-8') as f:
            raw = f.read()

        # Round-trip: parse then re-serialize then parse again
        data1 = json.loads(raw)
        serialized = json.dumps(data1, indent=2, ensure_ascii=False)
        data2 = json.loads(serialized)

        self.assertEqual(data1, data2)

    def test_round_trip_special_characters(self):
        """Records with special characters in item/player names survive round-trip."""
        history = [
            ("Player'sAlt", 50, "alt", True),
            ("Über-Lëet", 300, "main", False),
        ]
        dkp_logger.record_auction(
            "Blade of the Ñight", "Über-Lëet", 300, history,
            log_file_path=None, history_file=self.history_file,
        )

        with open(self.history_file, encoding='utf-8') as f:
            raw = f.read()

        data1 = json.loads(raw)
        serialized = json.dumps(data1, indent=2, ensure_ascii=False)
        data2 = json.loads(serialized)

        self.assertEqual(data1, data2)

    def test_round_trip_preserves_bid_fields(self):
        """Each bid entry's fields are preserved exactly through round-trip."""
        history = [
            ("Alice", 100, "main", False),
            ("Bob", 150, "alt", True),
        ]
        dkp_logger.record_auction(
            "TestItem", "Bob", 150, history,
            log_file_path=None, history_file=self.history_file,
        )

        with open(self.history_file, encoding='utf-8') as f:
            data = json.load(f)

        record = data["records"][0]
        # Verify bid fields have correct types after round-trip
        self.assertEqual(record["bids"][0]["player"], "Alice")
        self.assertEqual(record["bids"][0]["amount"], 100)
        self.assertEqual(record["bids"][0]["bid_type"], "main")
        self.assertIs(record["bids"][0]["is_correction"], False)
        self.assertEqual(record["bids"][1]["player"], "Bob")
        self.assertEqual(record["bids"][1]["amount"], 150)
        self.assertEqual(record["bids"][1]["bid_type"], "alt")
        self.assertIs(record["bids"][1]["is_correction"], True)


class TestDedupIdempotency(unittest.TestCase):
    """
    Validates: Requirements 2.3, 2.4, 2.5
    Dedup idempotency: calling record_auction() twice with identical arguments
    writes exactly one record.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.history_file = os.path.join(self.tmpdir, "dkp_history.json")

    def tearDown(self):
        for f in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, f))
        os.rmdir(self.tmpdir)

    def test_duplicate_call_writes_once(self):
        """Calling record_auction() twice with identical args produces one record."""
        args = ("Sword of Truth", "Warrior", 500,
                [("Warrior", 500, "main", False)],)
        kwargs = {"log_file_path": None, "history_file": self.history_file}

        result1 = dkp_logger.record_auction(*args, **kwargs)
        result2 = dkp_logger.record_auction(*args, **kwargs)

        self.assertTrue(result1)   # First call writes
        self.assertFalse(result2)  # Second call is duplicate

        with open(self.history_file, encoding='utf-8') as f:
            data = json.load(f)

        self.assertEqual(len(data["records"]), 1)

    def test_triple_call_still_one_record(self):
        """Even three identical calls produce exactly one record."""
        args = ("Shield of Ages", "Tank", 250,
                [("Tank", 250, "main", False), ("Healer", 100, "alt", False)],)
        kwargs = {"log_file_path": None, "history_file": self.history_file}

        dkp_logger.record_auction(*args, **kwargs)
        dkp_logger.record_auction(*args, **kwargs)
        dkp_logger.record_auction(*args, **kwargs)

        with open(self.history_file, encoding='utf-8') as f:
            data = json.load(f)

        self.assertEqual(len(data["records"]), 1)

    def test_different_items_both_written(self):
        """Different items are NOT deduplicated — both are written."""
        kwargs = {"log_file_path": None, "history_file": self.history_file}

        dkp_logger.record_auction("Item A", "Player1", 100,
                                  [("Player1", 100, "main", False)], **kwargs)
        dkp_logger.record_auction("Item B", "Player1", 100,
                                  [("Player1", 100, "main", False)], **kwargs)

        with open(self.history_file, encoding='utf-8') as f:
            data = json.load(f)

        self.assertEqual(len(data["records"]), 2)


class TestDedupCrossOfficer(unittest.TestCase):
    """
    Validates: Requirements 2.1, 2.2
    Dedup cross-officer: two records with the same (item, winner, amount,
    raid_session_id) but different log_source produce the same dedup_key
    and only one is stored.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.history_file = os.path.join(self.tmpdir, "dkp_history.json")
        # Create two fake log files with the same first timestamp (same raid)
        self.log1 = os.path.join(self.tmpdir, "eqlog_Officer1_pq.proj.txt")
        self.log2 = os.path.join(self.tmpdir, "eqlog_Officer2_pq.proj.txt")
        # Both logs have the same date in their first timestamp
        log_content = "[Mon Apr 28 21:00:00 2026] You have entered The Plane of Fear.\n"
        with open(self.log1, 'w', encoding='utf-8') as f:
            f.write(log_content)
        with open(self.log2, 'w', encoding='utf-8') as f:
            f.write(log_content)

    def tearDown(self):
        for f in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, f))
        os.rmdir(self.tmpdir)

    def test_same_raid_different_officers_one_record(self):
        """Two officers logging the same auction produce one record."""
        history = [("Caezar", 370, "main", False)]

        result1 = dkp_logger.record_auction(
            "Ring of Flamewarding", "Caezar", 370, history,
            log_file_path=self.log1, history_file=self.history_file,
        )
        result2 = dkp_logger.record_auction(
            "Ring of Flamewarding", "Caezar", 370, history,
            log_file_path=self.log2, history_file=self.history_file,
        )

        self.assertTrue(result1)
        self.assertFalse(result2)

        with open(self.history_file, encoding='utf-8') as f:
            data = json.load(f)

        self.assertEqual(len(data["records"]), 1)

    def test_dedup_key_independent_of_log_source(self):
        """The dedup_key is the same regardless of which officer's log is used."""
        # Compute raid session IDs — should be identical for same-date logs
        session1 = dkp_logger._raid_session_id(self.log1)
        session2 = dkp_logger._raid_session_id(self.log2)
        self.assertEqual(session1, session2)

        # Compute dedup keys — should be identical
        key1 = dkp_logger._dedup_key("TestItem", "Winner", 100, session1)
        key2 = dkp_logger._dedup_key("TestItem", "Winner", 100, session2)
        self.assertEqual(key1, key2)

    def test_different_raids_different_keys(self):
        """Different raid dates produce different session IDs and dedup keys."""
        log_day1 = os.path.join(self.tmpdir, "log_day1.txt")
        log_day2 = os.path.join(self.tmpdir, "log_day2.txt")
        with open(log_day1, 'w', encoding='utf-8') as f:
            f.write("[Mon Apr 28 21:00:00 2026] Zone entered.\n")
        with open(log_day2, 'w', encoding='utf-8') as f:
            f.write("[Tue Apr 29 21:00:00 2026] Zone entered.\n")

        session1 = dkp_logger._raid_session_id(log_day1)
        session2 = dkp_logger._raid_session_id(log_day2)
        self.assertNotEqual(session1, session2)

        # Same item/winner/amount but different raids → both written
        history = [("Player", 100, "main", False)]
        result1 = dkp_logger.record_auction(
            "Sword", "Player", 100, history,
            log_file_path=log_day1, history_file=self.history_file,
        )
        result2 = dkp_logger.record_auction(
            "Sword", "Player", 100, history,
            log_file_path=log_day2, history_file=self.history_file,
        )

        self.assertTrue(result1)
        self.assertTrue(result2)

        with open(self.history_file, encoding='utf-8') as f:
            data = json.load(f)

        self.assertEqual(len(data["records"]), 2)


class TestHistoryMonotonicity(unittest.TestCase):
    """
    Validates: Requirements 1.6
    History monotonicity: record_auction() never removes or modifies
    existing records.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.history_file = os.path.join(self.tmpdir, "dkp_history.json")

    def tearDown(self):
        for f in os.listdir(self.tmpdir):
            os.remove(os.path.join(self.tmpdir, f))
        os.rmdir(self.tmpdir)

    def test_new_record_does_not_modify_existing(self):
        """Adding a new record leaves all previous records unchanged."""
        kwargs = {"log_file_path": None, "history_file": self.history_file}

        # Write first record
        dkp_logger.record_auction(
            "Item A", "Player1", 100,
            [("Player1", 100, "main", False)], **kwargs
        )
        with open(self.history_file, encoding='utf-8') as f:
            snapshot1 = json.load(f)

        # Write second record
        dkp_logger.record_auction(
            "Item B", "Player2", 200,
            [("Player2", 200, "main", False)], **kwargs
        )
        with open(self.history_file, encoding='utf-8') as f:
            snapshot2 = json.load(f)

        # First record is unchanged
        self.assertEqual(snapshot1["records"][0], snapshot2["records"][0])
        # Total records grew by one
        self.assertEqual(len(snapshot2["records"]), 2)

    def test_duplicate_does_not_modify_existing(self):
        """A duplicate call does not alter existing records."""
        kwargs = {"log_file_path": None, "history_file": self.history_file}

        dkp_logger.record_auction(
            "Item A", "Player1", 100,
            [("Player1", 100, "main", False)], **kwargs
        )
        with open(self.history_file, encoding='utf-8') as f:
            snapshot_before = json.load(f)

        # Attempt duplicate
        dkp_logger.record_auction(
            "Item A", "Player1", 100,
            [("Player1", 100, "main", False)], **kwargs
        )
        with open(self.history_file, encoding='utf-8') as f:
            snapshot_after = json.load(f)

        self.assertEqual(snapshot_before, snapshot_after)

    def test_multiple_records_monotonically_grow(self):
        """Each successful write increases record count by exactly one."""
        kwargs = {"log_file_path": None, "history_file": self.history_file}

        items = [
            ("Sword", "Alice", 100),
            ("Shield", "Bob", 200),
            ("Helm", "Charlie", 300),
        ]

        for i, (item, winner, amount) in enumerate(items):
            dkp_logger.record_auction(
                item, winner, amount,
                [(winner, amount, "main", False)], **kwargs
            )
            with open(self.history_file, encoding='utf-8') as f:
                data = json.load(f)
            self.assertEqual(len(data["records"]), i + 1)

    def test_records_never_removed(self):
        """After writing N records, all N remain present with original content."""
        kwargs = {"log_file_path": None, "history_file": self.history_file}

        # Write 5 records
        snapshots = []
        for i in range(5):
            dkp_logger.record_auction(
                f"Item_{i}", f"Player_{i}", (i + 1) * 50,
                [(f"Player_{i}", (i + 1) * 50, "main", False)], **kwargs
            )
            with open(self.history_file, encoding='utf-8') as f:
                snapshots.append(json.load(f))

        # Final state should contain all records
        final = snapshots[-1]
        self.assertEqual(len(final["records"]), 5)

        # Each intermediate snapshot's records should be a prefix of the final
        for i, snap in enumerate(snapshots):
            self.assertEqual(snap["records"], final["records"][:i + 1])


if __name__ == "__main__":
    unittest.main()
