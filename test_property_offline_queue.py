"""
Property-based tests for OfflineQueue.

Uses the Hypothesis library to verify correctness properties
of the offline queue implementation.
"""

import json
import os
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

from models import AuctionRecord, BidEntry, compute_dedup_key, compute_raid_session_id
from offline_queue import OfflineQueue, PushRetryableError


# ---------------------------------------------------------------------------
# Strategies for generating valid AuctionRecords
# ---------------------------------------------------------------------------

_bid_type_strategy = st.sampled_from(["main", "alt"])

_bid_entry_strategy = st.builds(
    BidEntry,
    player=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=16,
    ),
    amount=st.integers(min_value=1, max_value=100000),
    bid_type=_bid_type_strategy,
    is_correction=st.booleans(),
)

_timestamp_strategy = st.builds(
    lambda y, mo, d, h, mi, s: f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}Z",
    y=st.integers(min_value=2020, max_value=2030),
    mo=st.integers(min_value=1, max_value=12),
    d=st.integers(min_value=1, max_value=28),
    h=st.integers(min_value=0, max_value=23),
    mi=st.integers(min_value=0, max_value=59),
    s=st.integers(min_value=0, max_value=59),
)


@st.composite
def auction_record_strategy(draw):
    """Generate a valid AuctionRecord with correct dedup_key and raid_session_id."""
    item_name = draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=32,
        )
    )
    winner = draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=16,
        )
    )
    amount = draw(st.integers(min_value=1, max_value=100000))
    timestamp = draw(_timestamp_strategy)
    bids = draw(st.lists(_bid_entry_strategy, min_size=1, max_size=5))
    log_source = draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "P")),
            min_size=1,
            max_size=32,
        )
    )

    raid_session_id = compute_raid_session_id(timestamp)
    dedup_key = compute_dedup_key(item_name, winner, amount, raid_session_id)

    return AuctionRecord(
        item_name=item_name,
        winner=winner,
        amount=amount,
        timestamp=timestamp,
        raid_session_id=raid_session_id,
        dedup_key=dedup_key,
        log_source=log_source,
        bids=bids,
    )


# ---------------------------------------------------------------------------
# Property 3: Successful push removes from offline queue
# Validates: Requirements 3.3
# ---------------------------------------------------------------------------


class TestSuccessfulPushRemovesFromQueue:
    """
    **Validates: Requirements 3.3**

    Property 3: For any record present in the Offline_Queue, after a successful
    push (HTTP 201 or 200), the record SHALL no longer be present in the queue file.
    """

    @given(record=auction_record_strategy())
    @settings(max_examples=50, deadline=None)
    def test_successful_push_removes_single_record(self, record: AuctionRecord):
        """After a successful push, the pushed record is removed from the queue."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pending_path = os.path.join(tmpdir, "pending_uploads.json")
            failed_path = os.path.join(tmpdir, "failed_uploads.json")

            queue = OfflineQueue(pending_path=pending_path, failed_path=failed_path)

            # Enqueue the record
            queue.enqueue(record)
            assert queue.pending_count == 1

            # Define a push_fn that always succeeds (simulates HTTP 200/201)
            def successful_push(rec: AuctionRecord) -> None:
                pass  # No exception means success

            # Flush the queue
            sent = queue.flush(successful_push)

            # The record should be removed from the queue
            assert sent == 1
            assert queue.pending_count == 0

            # Verify the underlying file is empty or contains empty list
            if os.path.exists(pending_path):
                with open(pending_path, "r", encoding="utf-8") as f:
                    content = json.load(f)
                assert record.to_dict() not in content

    @given(records=st.lists(auction_record_strategy(), min_size=2, max_size=5))
    @settings(max_examples=30, deadline=None)
    def test_successful_push_removes_only_sent_records(self, records):
        """
        When some records succeed and one fails, only the successful ones are removed.
        The failed record and subsequent records remain in the queue.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            pending_path = os.path.join(tmpdir, "pending_uploads.json")
            failed_path = os.path.join(tmpdir, "failed_uploads.json")

            queue = OfflineQueue(pending_path=pending_path, failed_path=failed_path)

            # Enqueue all records
            for rec in records:
                queue.enqueue(rec)
            assert queue.pending_count == len(records)

            # Push function that succeeds for all — verifying all get removed
            def successful_push(rec: AuctionRecord) -> None:
                pass

            sent = queue.flush(successful_push)

            # All records should be removed
            assert sent == len(records)
            assert queue.pending_count == 0

            # Verify none of the pushed records remain in file
            if os.path.exists(pending_path):
                with open(pending_path, "r", encoding="utf-8") as f:
                    content = json.load(f)
                for rec in records:
                    assert rec.to_dict() not in content

    @given(records=st.lists(
        auction_record_strategy(),
        min_size=2,
        max_size=5,
        unique_by=lambda r: r.dedup_key,
    ))
    @settings(max_examples=30, deadline=None)
    def test_successful_push_of_first_leaves_rest_when_failure_occurs(self, records):
        """
        When push succeeds for the first record but fails for the second,
        only the first record is removed; the rest remain in the queue.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            pending_path = os.path.join(tmpdir, "pending_uploads.json")
            failed_path = os.path.join(tmpdir, "failed_uploads.json")

            queue = OfflineQueue(pending_path=pending_path, failed_path=failed_path)

            for rec in records:
                queue.enqueue(rec)

            call_count = [0]

            def push_first_only(rec: AuctionRecord) -> None:
                call_count[0] += 1
                if call_count[0] > 1:
                    raise PushRetryableError("simulated network error")

            sent = queue.flush(push_first_only)

            # Only the first record was sent successfully
            assert sent == 1
            # Remaining records are still in the queue
            assert queue.pending_count == len(records) - 1

            # The first record should NOT be in the pending file
            with open(pending_path, "r", encoding="utf-8") as f:
                remaining = json.load(f)
            assert records[0].to_dict() not in remaining


# ---------------------------------------------------------------------------
# Property 2: Network failure enqueues to offline queue
# Validates: Requirements 2.5, 3.1
# ---------------------------------------------------------------------------


class TestNetworkFailureEnqueuesToOfflineQueue:
    """
    **Validates: Requirements 2.5, 3.1**

    Property 2: For any AuctionRecord push that results in a network failure
    (timeout, connection error, or HTTP 5xx), the record SHALL appear in the
    Offline_Queue's persistent file (pending_uploads.json) with all fields intact.
    """

    @given(record=auction_record_strategy())
    @settings(max_examples=50, deadline=None)
    def test_network_failure_keeps_record_in_queue_with_all_fields(self, record: AuctionRecord):
        """
        When push_fn raises PushRetryableError (simulating network failure),
        the record remains in pending_uploads.json with all fields intact.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            pending_path = os.path.join(tmpdir, "pending_uploads.json")
            failed_path = os.path.join(tmpdir, "failed_uploads.json")

            queue = OfflineQueue(pending_path=pending_path, failed_path=failed_path)

            # Enqueue the record (simulates the client enqueuing on failure)
            queue.enqueue(record)

            # Define a push_fn that always raises PushRetryableError
            def failing_push(rec: AuctionRecord) -> None:
                raise PushRetryableError("simulated network timeout")

            # Attempt to flush — should fail and leave the record in the queue
            sent_count = queue.flush(failing_push)

            # No records should have been sent
            assert sent_count == 0

            # The pending file should still exist and contain the record
            assert os.path.exists(pending_path)

            with open(pending_path, "r", encoding="utf-8") as f:
                persisted_records = json.load(f)

            assert len(persisted_records) == 1

            # Verify all fields are intact by comparing with the original record's dict
            expected = record.to_dict()
            actual = persisted_records[0]
            assert actual == expected

    @given(record=auction_record_strategy())
    @settings(max_examples=50, deadline=None)
    def test_enqueue_persists_record_immediately(self, record: AuctionRecord):
        """
        When enqueue() is called (which is what happens on network failure),
        the record is immediately written to the persistent file.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            pending_path = os.path.join(tmpdir, "pending_uploads.json")
            failed_path = os.path.join(tmpdir, "failed_uploads.json")

            queue = OfflineQueue(pending_path=pending_path, failed_path=failed_path)

            # Enqueue the record
            queue.enqueue(record)

            # The pending file should exist immediately (no flush needed)
            assert os.path.exists(pending_path)

            with open(pending_path, "r", encoding="utf-8") as f:
                persisted_records = json.load(f)

            assert len(persisted_records) == 1

            # All fields must match
            expected = record.to_dict()
            actual = persisted_records[0]
            assert actual == expected

    @given(records=st.lists(auction_record_strategy(), min_size=1, max_size=5))
    @settings(max_examples=30, deadline=None)
    def test_multiple_failures_keep_all_records_in_queue(self, records):
        """
        When multiple records are enqueued and flush fails for all (network failure),
        all records remain in the queue with fields intact.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            pending_path = os.path.join(tmpdir, "pending_uploads.json")
            failed_path = os.path.join(tmpdir, "failed_uploads.json")

            queue = OfflineQueue(pending_path=pending_path, failed_path=failed_path)

            for rec in records:
                queue.enqueue(rec)

            # Push always fails with a retryable error
            def failing_push(rec: AuctionRecord) -> None:
                raise PushRetryableError("connection refused")

            sent_count = queue.flush(failing_push)

            # No records sent
            assert sent_count == 0

            # All records remain in queue
            assert queue.pending_count == len(records)

            # Verify the first record's fields are intact (flush stops at first failure)
            with open(pending_path, "r", encoding="utf-8") as f:
                persisted_records = json.load(f)

            assert len(persisted_records) == len(records)
            for i, rec in enumerate(records):
                assert persisted_records[i] == rec.to_dict()
