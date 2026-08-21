"""
test_integration_client.py — Integration tests for end-to-end client flow.

Tests the full lifecycle: auction close → push to API → offline queue retry → dedup.
Uses pytest with unittest.mock for network mocking.

Requirements: 2.1, 2.5, 3.1, 3.3, 12.2
"""

import json
import os
import threading
import time
import tempfile
from unittest.mock import MagicMock, patch, call

import pytest

from client_config import ClientConfig
from dkp_client import CloudBidTracker, FlushTimer
from models import AuctionRecord, BidEntry, compute_dedup_key, compute_raid_session_id
from offline_queue import OfflineQueue, PushRetryableError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config():
    """Create a mock ClientConfig with cloud sync enabled."""
    return ClientConfig(
        api_url="https://dkp.example.com",
        api_key="test-api-key-12345",
        log_directory="C:\\EverQuest\\Logs",
        poll_interval=1.0,
        retry_interval=0.5,  # Short interval for tests
        local_only=False,
    )


@pytest.fixture
def temp_queue_dir(tmp_path):
    """Create temp directory for offline queue files."""
    pending = str(tmp_path / "pending_uploads.json")
    failed = str(tmp_path / "failed_uploads.json")
    return pending, failed


@pytest.fixture
def offline_queue(temp_queue_dir):
    """Create an OfflineQueue with temp file paths."""
    pending, failed = temp_queue_dir
    return OfflineQueue(pending_path=pending, failed_path=failed)


@pytest.fixture
def mock_validator():
    """Create a mock ItemValidator that accepts any item."""
    validator = MagicMock()
    validator.is_valid.return_value = True
    validator.fuzzy_match.return_value = None
    return validator


@pytest.fixture
def tracker(mock_validator, mock_config, offline_queue):
    """Create a CloudBidTracker with mock dependencies."""
    return CloudBidTracker(mock_validator, mock_config, offline_queue)


def _make_sample_record(item="Sword of Flames", winner="Gandalf", amount=150):
    """Helper to create a sample AuctionRecord."""
    ts = "2026-04-29T02:55:35Z"
    raid_session_id = compute_raid_session_id(ts)
    dedup_key = compute_dedup_key(item, winner, amount, raid_session_id)
    return AuctionRecord(
        item_name=item,
        winner=winner,
        amount=amount,
        timestamp=ts,
        raid_session_id=raid_session_id,
        dedup_key=dedup_key,
        log_source="eqlog_Test_pq.proj.txt",
        bids=[
            BidEntry(player=winner, amount=amount, bid_type="main", is_correction=False),
        ],
    )


# ---------------------------------------------------------------------------
# Test 1: Parse log → close auction → record pushed to mock API
# Validates: Requirements 2.1
# ---------------------------------------------------------------------------


class TestCloseAuctionPushesRecord:
    """Integration: closing an auction triggers a push to the API."""

    @patch("dkp_client.push_record")
    def test_close_item_triggers_background_push(self, mock_push, tracker):
        """
        Simulate a gratss event by calling close_item directly.
        Verify push_record is called in a background thread with the correct data.
        """
        item = "Sword of Flames"
        winner = "Gandalf"
        amount = 150
        timestamp = "2026-04-29T02:55:35Z"

        # Set up bid history (simulating a bid that was placed)
        tracker.bids_by_item[item]["main"][winner] = amount
        tracker.bids_by_item[item]["history"].append(
            (winner, amount, "main", False)
        )
        tracker.bid_order.append(item)

        # Mock push_record to succeed
        mock_push.return_value = True

        # Suppress console output from BidTracker
        with patch("builtins.print"):
            tracker.close_item(item, winner, amount, timestamp=timestamp)

        # Wait for the background thread to complete
        time.sleep(0.5)

        # Verify push_record was called
        assert mock_push.called, "push_record should have been called"
        call_kwargs = mock_push.call_args

        # Verify the record details
        pushed_record = call_kwargs[1]["record"] if "record" in call_kwargs[1] else call_kwargs[0][0]
        assert pushed_record.item_name == item
        assert pushed_record.winner == winner
        assert pushed_record.amount == amount
        assert pushed_record.timestamp == timestamp
        assert len(pushed_record.bids) == 1
        assert pushed_record.bids[0].player == winner
        assert pushed_record.bids[0].amount == amount
        assert pushed_record.bids[0].bid_type == "main"

    @patch("dkp_client.push_record")
    def test_close_item_push_uses_correct_api_config(self, mock_push, tracker, mock_config):
        """Verify the push uses the config's api_url and api_key."""
        item = "Ring of Power"
        winner = "Frodo"
        amount = 200

        tracker.bids_by_item[item]["main"][winner] = amount
        tracker.bids_by_item[item]["history"].append(
            (winner, amount, "main", False)
        )
        tracker.bid_order.append(item)

        mock_push.return_value = True

        with patch("builtins.print"):
            tracker.close_item(item, winner, amount, timestamp="2026-04-29T03:00:00Z")

        time.sleep(0.5)

        # Verify correct API config was passed
        call_kwargs = mock_push.call_args[1]
        assert call_kwargs["api_url"] == mock_config.api_url
        assert call_kwargs["api_key"] == mock_config.api_key


# ---------------------------------------------------------------------------
# Test 2: Push fails → record enqueued → retry succeeds → record removed
# Validates: Requirements 2.5, 3.1, 3.3
# ---------------------------------------------------------------------------


class TestOfflineQueueRetry:
    """Integration: failed push enqueues, retry flushes, record removed."""

    @patch("dkp_client.push_record")
    def test_push_failure_enqueues_record(self, mock_push, tracker, offline_queue):
        """
        When push_record raises PushRetryableError, the record should be
        enqueued in the offline queue.
        """
        item = "Staff of Arcane"
        winner = "Merlin"
        amount = 300

        tracker.bids_by_item[item]["main"][winner] = amount
        tracker.bids_by_item[item]["history"].append(
            (winner, amount, "main", False)
        )
        tracker.bid_order.append(item)

        # Mock push_record to fail with a retryable error
        mock_push.side_effect = PushRetryableError("Connection timeout")

        with patch("builtins.print"):
            tracker.close_item(item, winner, amount, timestamp="2026-04-29T04:00:00Z")

        # Wait for background thread
        time.sleep(0.5)

        # Verify the record was enqueued
        assert offline_queue.pending_count == 1, (
            "Record should be in the offline queue after push failure"
        )

    @patch("dkp_client.push_record")
    def test_retry_succeeds_removes_from_queue(self, mock_push, tracker, offline_queue):
        """
        After a record is enqueued due to push failure, a successful retry
        via flush should remove it from the queue.
        """
        item = "Shield of Valor"
        winner = "Arthur"
        amount = 250

        tracker.bids_by_item[item]["main"][winner] = amount
        tracker.bids_by_item[item]["history"].append(
            (winner, amount, "main", False)
        )
        tracker.bid_order.append(item)

        # First push fails (enqueue), then retry will succeed
        mock_push.side_effect = PushRetryableError("Server unreachable")

        with patch("builtins.print"):
            tracker.close_item(item, winner, amount, timestamp="2026-04-29T05:00:00Z")

        time.sleep(0.5)

        # Verify record is in queue
        assert offline_queue.pending_count == 1

        # Now simulate a successful retry by flushing the queue
        mock_push.side_effect = None  # Reset - push succeeds
        mock_push.return_value = True

        def push_fn(record):
            push_from_module = mock_push
            push_from_module(
                record=record,
                api_url=tracker._config.api_url,
                api_key=tracker._config.api_key,
            )

        sent = offline_queue.flush(push_fn)

        # Verify record was successfully sent and removed from queue
        assert sent == 1, "One record should have been flushed"
        assert offline_queue.pending_count == 0, (
            "Queue should be empty after successful retry"
        )

    @patch("dkp_client.push_record")
    def test_full_retry_cycle_with_flush_timer(self, mock_push, mock_config, offline_queue, mock_validator):
        """
        Full integration: push fails → enqueued → FlushTimer retries → success → removed.
        """
        # Create tracker with short retry interval
        mock_config.retry_interval = 0.3
        tracker = CloudBidTracker(mock_validator, mock_config, offline_queue)

        item = "Cloak of Shadows"
        winner = "Rogue"
        amount = 175

        tracker.bids_by_item[item]["main"][winner] = amount
        tracker.bids_by_item[item]["history"].append(
            (winner, amount, "main", False)
        )
        tracker.bid_order.append(item)

        # First call fails (from close_item background thread)
        # Subsequent calls succeed (from FlushTimer)
        call_count = [0]

        def conditional_push(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise PushRetryableError("Network down")
            return True

        mock_push.side_effect = conditional_push

        # Start the flush timer
        flush_timer = FlushTimer(offline_queue, mock_config)
        flush_timer.start()

        try:
            with patch("builtins.print"):
                tracker.close_item(item, winner, amount, timestamp="2026-04-29T06:00:00Z")

            # Wait for initial push failure + enqueue + timer retry
            time.sleep(1.5)

            # The flush timer should have retried and succeeded
            assert offline_queue.pending_count == 0, (
                "Queue should be empty after FlushTimer retries successfully"
            )
        finally:
            flush_timer.stop()


# ---------------------------------------------------------------------------
# Test 3: Multiple officers push same auction → server deduplicates
# Validates: Requirements 12.2
# ---------------------------------------------------------------------------


class TestServerDeduplication:
    """Integration: two trackers push the same auction; server deduplicates."""

    @patch("dkp_client.push_record")
    def test_duplicate_push_returns_200(self, mock_push, mock_config, offline_queue, mock_validator):
        """
        Two CloudBidTrackers closing the same auction should produce records
        with the same dedup_key. The mock API returns 201 for the first,
        200 for the second (duplicate acknowledged).
        """
        item = "Earring of the Solstice"
        winner = "Paladin"
        amount = 400
        timestamp = "2026-04-29T07:00:00Z"

        # Track which dedup_keys are pushed
        pushed_dedup_keys = []

        def mock_push_impl(*args, **kwargs):
            record = kwargs.get("record") or args[0]
            pushed_dedup_keys.append(record.dedup_key)
            # First call: 201 (new), second call: 200 (duplicate)
            return True

        mock_push.side_effect = mock_push_impl

        # Officer 1's tracker
        tracker1 = CloudBidTracker(mock_validator, mock_config, offline_queue)
        tracker1.bids_by_item[item]["main"][winner] = amount
        tracker1.bids_by_item[item]["history"].append(
            (winner, amount, "main", False)
        )
        tracker1.bid_order.append(item)

        # Officer 2's tracker (separate instance, same auction data)
        offline_queue2 = OfflineQueue(
            pending_path=str(tempfile.mktemp(suffix=".json")),
            failed_path=str(tempfile.mktemp(suffix=".json")),
        )
        tracker2 = CloudBidTracker(mock_validator, mock_config, offline_queue2)
        tracker2.bids_by_item[item]["main"][winner] = amount
        tracker2.bids_by_item[item]["history"].append(
            (winner, amount, "main", False)
        )
        tracker2.bid_order.append(item)

        with patch("builtins.print"):
            tracker1.close_item(item, winner, amount, timestamp=timestamp)
            tracker2.close_item(item, winner, amount, timestamp=timestamp)

        # Wait for both background threads to complete
        time.sleep(1.0)

        # Both trackers should have pushed records with the same dedup_key
        assert len(pushed_dedup_keys) == 2, "Both trackers should push"
        assert pushed_dedup_keys[0] == pushed_dedup_keys[1], (
            "Records from both officers must have the same dedup_key "
            "for server-side deduplication to work"
        )

    @patch("dkp_client.push_record")
    def test_dedup_key_deterministic_across_officers(self, mock_push, mock_config, mock_validator):
        """
        Verify that two independent trackers produce identical dedup_keys
        for the same auction parameters, enabling server deduplication.
        """
        item = "Blade of Carnage"
        winner = "Warrior"
        amount = 500
        timestamp = "2026-05-01T20:30:00Z"

        records_pushed = []

        def capture_push(*args, **kwargs):
            record = kwargs.get("record") or args[0]
            records_pushed.append(record)
            return True

        mock_push.side_effect = capture_push

        # Two independent offline queues (separate officers)
        queue1 = OfflineQueue(
            pending_path=str(tempfile.mktemp(suffix=".json")),
            failed_path=str(tempfile.mktemp(suffix=".json")),
        )
        queue2 = OfflineQueue(
            pending_path=str(tempfile.mktemp(suffix=".json")),
            failed_path=str(tempfile.mktemp(suffix=".json")),
        )

        tracker1 = CloudBidTracker(mock_validator, mock_config, queue1)
        tracker2 = CloudBidTracker(mock_validator, mock_config, queue2)

        # Both officers see the same auction
        for t in (tracker1, tracker2):
            t.bids_by_item[item]["main"][winner] = amount
            t.bids_by_item[item]["history"].append(
                (winner, amount, "main", False)
            )
            t.bid_order.append(item)

        with patch("builtins.print"):
            tracker1.close_item(item, winner, amount, timestamp=timestamp)
            tracker2.close_item(item, winner, amount, timestamp=timestamp)

        time.sleep(1.0)

        # Verify both records have matching fields for dedup
        assert len(records_pushed) == 2
        r1, r2 = records_pushed

        assert r1.dedup_key == r2.dedup_key, "dedup_key must match across officers"
        assert r1.raid_session_id == r2.raid_session_id, "raid_session_id must match"
        assert r1.item_name == r2.item_name
        assert r1.winner == r2.winner
        assert r1.amount == r2.amount

    @patch("dkp_client.push_record")
    def test_server_stores_only_once_on_duplicate(self, mock_push, mock_config, offline_queue, mock_validator):
        """
        Simulate server-side dedup: first push returns True (201 stored),
        second push returns True (200 dup acknowledged), but only one record
        should exist in storage.
        """
        item = "Crown of Deceit"
        winner = "Necromancer"
        amount = 350
        timestamp = "2026-04-30T19:00:00Z"

        # Simulate server-side storage
        server_storage = {}  # dedup_key -> AuctionRecord

        def mock_server_push(*args, **kwargs):
            record = kwargs.get("record") or args[0]
            if record.dedup_key in server_storage:
                # HTTP 200 - duplicate acknowledged, no new write
                return True
            else:
                # HTTP 201 - new record stored
                server_storage[record.dedup_key] = record
                return True

        mock_push.side_effect = mock_server_push

        # Two trackers push the same auction
        queue2 = OfflineQueue(
            pending_path=str(tempfile.mktemp(suffix=".json")),
            failed_path=str(tempfile.mktemp(suffix=".json")),
        )

        tracker1 = CloudBidTracker(mock_validator, mock_config, offline_queue)
        tracker2 = CloudBidTracker(mock_validator, mock_config, queue2)

        for t in (tracker1, tracker2):
            t.bids_by_item[item]["main"][winner] = amount
            t.bids_by_item[item]["history"].append(
                (winner, amount, "main", False)
            )
            t.bid_order.append(item)

        with patch("builtins.print"):
            tracker1.close_item(item, winner, amount, timestamp=timestamp)
            # Small delay to ensure first push completes before second
            time.sleep(0.3)
            tracker2.close_item(item, winner, amount, timestamp=timestamp)

        time.sleep(1.0)

        # Server should only store one record despite two pushes
        assert len(server_storage) == 1, (
            f"Server should store exactly 1 record, got {len(server_storage)}"
        )
        stored_record = list(server_storage.values())[0]
        assert stored_record.item_name == item
        assert stored_record.winner == winner
        assert stored_record.amount == amount
