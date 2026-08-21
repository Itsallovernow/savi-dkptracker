"""
Property-based tests for AuctionRecord serialization round-trip.

**Validates: Requirements 9.1, 9.3**

Property 14: For any valid AuctionRecord, serializing with json.dumps()
and deserializing with json.loads() SHALL produce an equivalent in-memory structure.
"""

import json
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from models import AuctionRecord, BidEntry, compute_dedup_key, compute_raid_session_id


# ---------------------------------------------------------------------------
# Strategies for generating valid model instances
# ---------------------------------------------------------------------------

# Valid player/winner names: alphanumeric, 1-64 characters
alphanumeric_names = st.text(
    alphabet=string.ascii_letters + string.digits,
    min_size=1,
    max_size=64,
)

# Item names: non-empty printable strings, 1-128 characters
item_names = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z")),
    min_size=1,
    max_size=128,
).filter(lambda s: s.strip() != "")

# Positive integers for amounts
positive_amounts = st.integers(min_value=1, max_value=10_000_000)

# Bid types
bid_types = st.sampled_from(["main", "alt"])

# ISO 8601 timestamps with Z suffix
timestamps = st.builds(
    lambda y, mo, d, h, mi, s: f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}Z",
    y=st.integers(min_value=2000, max_value=2099),
    mo=st.integers(min_value=1, max_value=12),
    d=st.integers(min_value=1, max_value=28),  # safe for all months
    h=st.integers(min_value=0, max_value=23),
    mi=st.integers(min_value=0, max_value=59),
    s=st.integers(min_value=0, max_value=59),
)

# Log source filenames
log_sources = st.text(
    alphabet=string.ascii_letters + string.digits + "_-.",
    min_size=1,
    max_size=100,
)


@st.composite
def bid_entries(draw):
    """Generate a valid BidEntry."""
    return BidEntry(
        player=draw(alphanumeric_names),
        amount=draw(positive_amounts),
        bid_type=draw(bid_types),
        is_correction=draw(st.booleans()),
    )


@st.composite
def auction_records(draw):
    """Generate a valid AuctionRecord with consistent dedup_key and raid_session_id."""
    item_name = draw(item_names)
    winner = draw(alphanumeric_names)
    amount = draw(positive_amounts)
    timestamp = draw(timestamps)
    log_source = draw(log_sources)
    bids = draw(st.lists(bid_entries(), min_size=1, max_size=10))

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
# Property Test
# ---------------------------------------------------------------------------


@given(record=auction_records())
@settings(max_examples=200)
def test_auction_record_serialization_round_trip(record: AuctionRecord):
    """
    Property 14: Auction record serialization round-trip.

    **Validates: Requirements 9.1, 9.3**

    For any valid AuctionRecord, serializing with json.dumps() and
    deserializing with json.loads() SHALL produce an equivalent in-memory structure.
    """
    # Serialize to dict, then to JSON string
    record_dict = record.to_dict()
    json_str = json.dumps(record_dict)

    # Deserialize from JSON string back to dict, then to AuctionRecord
    restored_dict = json.loads(json_str)
    restored_record = AuctionRecord.from_dict(restored_dict)

    # Verify all top-level fields are preserved
    assert restored_record.item_name == record.item_name
    assert restored_record.winner == record.winner
    assert restored_record.amount == record.amount
    assert restored_record.timestamp == record.timestamp
    assert restored_record.raid_session_id == record.raid_session_id
    assert restored_record.dedup_key == record.dedup_key
    assert restored_record.log_source == record.log_source

    # Verify bids list length is preserved
    assert len(restored_record.bids) == len(record.bids)

    # Verify each bid entry is preserved
    for original_bid, restored_bid in zip(record.bids, restored_record.bids):
        assert restored_bid.player == original_bid.player
        assert restored_bid.amount == original_bid.amount
        assert restored_bid.bid_type == original_bid.bid_type
        assert restored_bid.is_correction == original_bid.is_correction

    # Verify the restored record also validates successfully
    errors = restored_record.validate()
    assert errors == [], f"Restored record has validation errors: {errors}"

    # Verify dict round-trip is also stable (to_dict produces same output)
    assert restored_record.to_dict() == record_dict
