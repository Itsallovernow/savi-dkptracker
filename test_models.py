"""
test_models.py — Unit tests for the shared data models.

Tests AuctionRecord and BidEntry dataclasses, serialization round-trip,
compute_dedup_key, compute_raid_session_id, and validation methods.
"""

import json

from models import (
    AuctionRecord,
    BidEntry,
    compute_dedup_key,
    compute_raid_session_id,
)


# ---------------------------------------------------------------------------
# BidEntry tests
# ---------------------------------------------------------------------------


class TestBidEntry:
    def test_valid_main_bid(self):
        bid = BidEntry(player="Soandso", amount=100, bid_type="main", is_correction=False)
        assert bid.validate() == []

    def test_valid_alt_bid(self):
        bid = BidEntry(player="Alttoon", amount=50, bid_type="alt", is_correction=True)
        assert bid.validate() == []

    def test_empty_player(self):
        bid = BidEntry(player="", amount=100, bid_type="main", is_correction=False)
        errors = bid.validate()
        assert any("player" in e for e in errors)

    def test_whitespace_only_player(self):
        bid = BidEntry(player="   ", amount=100, bid_type="main", is_correction=False)
        errors = bid.validate()
        assert any("player" in e for e in errors)

    def test_player_too_long(self):
        bid = BidEntry(player="A" * 65, amount=100, bid_type="main", is_correction=False)
        errors = bid.validate()
        assert any("player" in e and "64" in e for e in errors)

    def test_zero_amount_accepted(self):
        # A synthesized 0-amount bid backs a 0-DKP award (historical
        # loot-council data), so it must not raise an amount error.
        bid = BidEntry(player="Player", amount=0, bid_type="main", is_correction=False)
        errors = bid.validate()
        assert not any("amount" in e for e in errors)

    def test_negative_amount(self):
        bid = BidEntry(player="Player", amount=-5, bid_type="main", is_correction=False)
        errors = bid.validate()
        assert any("amount" in e for e in errors)

    def test_invalid_bid_type(self):
        bid = BidEntry(player="Player", amount=100, bid_type="invalid", is_correction=False)
        errors = bid.validate()
        assert any("bid_type" in e for e in errors)

    def test_to_dict(self):
        bid = BidEntry(player="Soandso", amount=100, bid_type="main", is_correction=False)
        d = bid.to_dict()
        assert d == {
            "player": "Soandso",
            "amount": 100,
            "bid_type": "main",
            "is_correction": False,
        }

    def test_from_dict(self):
        d = {"player": "Soandso", "amount": 100, "bid_type": "alt", "is_correction": True}
        bid = BidEntry.from_dict(d)
        assert bid.player == "Soandso"
        assert bid.amount == 100
        assert bid.bid_type == "alt"
        assert bid.is_correction is True

    def test_round_trip(self):
        bid = BidEntry(player="TestPlayer", amount=250, bid_type="main", is_correction=True)
        restored = BidEntry.from_dict(bid.to_dict())
        assert restored == bid


# ---------------------------------------------------------------------------
# AuctionRecord tests
# ---------------------------------------------------------------------------


def _valid_record():
    """Helper to create a valid AuctionRecord for testing."""
    session_id = compute_raid_session_id("2026-04-29T02:55:35Z")
    dedup = compute_dedup_key("Cloak of Flames", "Soandso", 500, session_id)
    return AuctionRecord(
        item_name="Cloak of Flames",
        winner="Soandso",
        amount=500,
        timestamp="2026-04-29T02:55:35Z",
        raid_session_id=session_id,
        dedup_key=dedup,
        log_source="eqlog_Soandso_pq.proj.txt",
        bids=[
            BidEntry(player="Soandso", amount=500, bid_type="main", is_correction=False),
            BidEntry(player="Another", amount=300, bid_type="alt", is_correction=False),
        ],
    )


class TestAuctionRecord:
    def test_valid_record(self):
        record = _valid_record()
        assert record.validate() == []

    def test_empty_item_name(self):
        record = _valid_record()
        record.item_name = ""
        errors = record.validate()
        assert any("item_name" in e for e in errors)

    def test_item_name_too_long(self):
        record = _valid_record()
        record.item_name = "X" * 129
        errors = record.validate()
        assert any("item_name" in e and "128" in e for e in errors)

    def test_empty_winner(self):
        record = _valid_record()
        record.winner = ""
        errors = record.validate()
        assert any("winner" in e for e in errors)

    def test_winner_too_long(self):
        record = _valid_record()
        record.winner = "A" * 65
        errors = record.validate()
        assert any("winner" in e and "64" in e for e in errors)

    def test_winner_not_alphanumeric(self):
        record = _valid_record()
        record.winner = "Player One"  # space is not alphanumeric
        errors = record.validate()
        assert any("alphanumeric" in e for e in errors)

    def test_zero_amount_accepted(self):
        # 0 is a valid award amount (historical loot-council / reserved items
        # tracked at 0 DKP and charged later). Must not raise an amount error.
        record = _valid_record()
        record.amount = 0
        errors = record.validate()
        assert not any("amount" in e for e in errors)

    def test_negative_amount(self):
        record = _valid_record()
        record.amount = -1
        errors = record.validate()
        assert any("amount" in e for e in errors)

    def test_invalid_timestamp(self):
        record = _valid_record()
        record.timestamp = "not-a-timestamp"
        errors = record.validate()
        assert any("timestamp" in e for e in errors)

    def test_invalid_dedup_key_wrong_length(self):
        record = _valid_record()
        record.dedup_key = "abc123"
        errors = record.validate()
        assert any("dedup_key" in e for e in errors)

    def test_invalid_dedup_key_non_hex(self):
        record = _valid_record()
        record.dedup_key = "g" * 64  # 'g' is not a hex char
        errors = record.validate()
        assert any("dedup_key" in e for e in errors)

    def test_empty_bids(self):
        record = _valid_record()
        record.bids = []
        errors = record.validate()
        assert any("bids" in e for e in errors)

    def test_invalid_bid_in_list(self):
        record = _valid_record()
        record.bids.append(
            BidEntry(player="", amount=-1, bid_type="invalid", is_correction=False)
        )
        errors = record.validate()
        assert any("bids[2]" in e for e in errors)

    def test_to_dict(self):
        record = _valid_record()
        d = record.to_dict()
        assert d["item_name"] == "Cloak of Flames"
        assert d["winner"] == "Soandso"
        assert d["amount"] == 500
        assert d["timestamp"] == "2026-04-29T02:55:35Z"
        assert len(d["bids"]) == 2
        assert d["bids"][0]["player"] == "Soandso"

    def test_from_dict(self):
        record = _valid_record()
        d = record.to_dict()
        restored = AuctionRecord.from_dict(d)
        assert restored.item_name == record.item_name
        assert restored.winner == record.winner
        assert restored.amount == record.amount
        assert restored.timestamp == record.timestamp
        assert restored.raid_session_id == record.raid_session_id
        assert restored.dedup_key == record.dedup_key
        assert restored.log_source == record.log_source
        assert len(restored.bids) == len(record.bids)

    def test_round_trip_json(self):
        """Serialize to JSON and back — the full round-trip property."""
        record = _valid_record()
        json_str = json.dumps(record.to_dict())
        restored = AuctionRecord.from_dict(json.loads(json_str))
        assert restored == record

    def test_round_trip_preserves_all_fields(self):
        record = _valid_record()
        d = record.to_dict()
        restored = AuctionRecord.from_dict(d)
        assert restored.to_dict() == d


# ---------------------------------------------------------------------------
# compute_dedup_key tests
# ---------------------------------------------------------------------------


class TestComputeDedupKey:
    def test_returns_64_char_hex(self):
        key = compute_dedup_key("Item", "Player", 100, "session123")
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_deterministic(self):
        key1 = compute_dedup_key("Item", "Player", 100, "session")
        key2 = compute_dedup_key("Item", "Player", 100, "session")
        assert key1 == key2

    def test_different_inputs_different_keys(self):
        key1 = compute_dedup_key("Item A", "Player", 100, "session")
        key2 = compute_dedup_key("Item B", "Player", 100, "session")
        assert key1 != key2

    def test_different_amounts_different_keys(self):
        key1 = compute_dedup_key("Item", "Player", 100, "session")
        key2 = compute_dedup_key("Item", "Player", 200, "session")
        assert key1 != key2


# ---------------------------------------------------------------------------
# compute_raid_session_id tests
# ---------------------------------------------------------------------------


class TestComputeRaidSessionId:
    def test_returns_64_char_hex(self):
        sid = compute_raid_session_id("2026-04-29T02:55:35Z")
        assert len(sid) == 64
        assert all(c in "0123456789abcdef" for c in sid)

    def test_same_date_same_id(self):
        sid1 = compute_raid_session_id("2026-04-29T02:55:35Z")
        sid2 = compute_raid_session_id("2026-04-29T23:59:59Z")
        assert sid1 == sid2

    def test_different_dates_different_ids(self):
        sid1 = compute_raid_session_id("2026-04-29T02:55:35Z")
        sid2 = compute_raid_session_id("2026-04-30T02:55:35Z")
        assert sid1 != sid2

    def test_uses_date_portion_only(self):
        """Same date with different times should produce the same session ID."""
        sid1 = compute_raid_session_id("2026-01-15T00:00:00Z")
        sid2 = compute_raid_session_id("2026-01-15T12:30:45Z")
        assert sid1 == sid2
