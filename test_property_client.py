"""
Property-based tests for CloudBidTracker non-blocking push behavior.

Uses the Hypothesis library to verify that close_item returns immediately
regardless of network latency, proving the push is happening in a background thread.

**Validates: Requirements 1.3, 2.1**
"""

import tempfile
import time
import os
from unittest.mock import patch, MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

from client_config import ClientConfig
from dkp_client import CloudBidTracker
from models import AuctionRecord, BidEntry, compute_dedup_key, compute_raid_session_id
from offline_queue import OfflineQueue


# ---------------------------------------------------------------------------
# Strategies for generating valid auction data
# ---------------------------------------------------------------------------

_item_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=32,
)

_player_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L",)),
    min_size=1,
    max_size=16,
)

_amount_strategy = st.integers(min_value=1, max_value=100000)

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
def auction_close_data(draw):
    """Generate data for closing an auction: item name, winner, amount, timestamp."""
    item = draw(_item_name_strategy)
    winner = draw(_player_name_strategy)
    amount = draw(_amount_strategy)
    timestamp = draw(_timestamp_strategy)
    return item, winner, amount, timestamp


# ---------------------------------------------------------------------------
# Property 1: Auction close triggers non-blocking push
# Validates: Requirements 1.3, 2.1
# ---------------------------------------------------------------------------


class TestNonBlockingPush:
    """
    **Validates: Requirements 1.3, 2.1**

    Property 1: For any closed auction (via gratss), the DKP_Client SHALL
    initiate a push of the Auction_Record to the Backend_API without blocking
    the log monitor loop — i.e., the next log line is processable immediately
    regardless of network latency.
    """

    @given(data=auction_close_data())
    @settings(max_examples=50, deadline=None)
    def test_close_item_returns_immediately_despite_slow_push(self, data):
        """
        close_item() should return in < 100ms even when push_record
        would take 2 seconds, proving the push runs in a background thread.
        """
        item, winner, amount, timestamp = data

        # Create a non-local-only config
        config = ClientConfig(
            api_url="https://dkp.example.com",
            api_key="test-api-key-12345",
            log_directory="C:\\EverQuest\\Logs",
            poll_interval=1.0,
            retry_interval=30.0,
            local_only=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            pending_path = os.path.join(tmpdir, "pending_uploads.json")
            failed_path = os.path.join(tmpdir, "failed_uploads.json")
            offline_queue = OfflineQueue(
                pending_path=pending_path, failed_path=failed_path
            )

            # Create a mock validator that accepts any item
            mock_validator = MagicMock()
            mock_validator.validate_item.return_value = item

            tracker = CloudBidTracker(mock_validator, config, offline_queue)

            # Pre-populate the tracker's bid state with the correct structure
            # matching how BidTracker.bids_by_item defaultdict entries work:
            # {'main': {player: amount}, 'alt': {}, 'history': [(player, amount, type, is_correction)]}
            tracker.bids_by_item[item] = {
                "main": {winner: amount},
                "alt": {},
                "history": [(winner, amount, "main", False)],
            }
            tracker.bid_order.append(item)

            # Patch push_record to simulate 2 seconds of network latency,
            # and patch _print_above_table / dkp_logger to avoid console/file I/O
            with patch("dkp_client.push_record") as mock_push, \
                 patch.object(tracker, "_print_above_table"), \
                 patch("dkptrackerv3._dkp_logger", create=True) as mock_logger, \
                 patch("dkptrackerv3._HISTORY_ENABLED", False):

                def slow_push(*args, **kwargs):
                    time.sleep(2.0)
                    return True

                mock_push.side_effect = slow_push

                # Measure how long close_item takes to return
                start = time.perf_counter()
                tracker.close_item(item, winner, amount, timestamp=timestamp)
                elapsed = time.perf_counter() - start

            # close_item must return almost immediately (< 100ms)
            # even though push_record would take 2 seconds
            assert elapsed < 0.1, (
                f"close_item took {elapsed:.3f}s — expected < 0.1s. "
                f"The push is blocking the main thread instead of running "
                f"in a background thread."
            )
