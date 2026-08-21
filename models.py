"""
models.py — Shared data models for the Client-Server DKP Bid Tracker.

Defines AuctionRecord and BidEntry dataclasses with validation, serialization,
and helper functions for deduplication keys and raid session IDs.

No third-party dependencies — stdlib only.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Optional

# ISO 8601 pattern: YYYY-MM-DDTHH:MM:SSZ or with timezone offset
_ISO8601_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

_VALID_BID_TYPES = {"main", "alt"}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def compute_dedup_key(item_name: str, winner: str, amount: int, raid_session_id: str) -> str:
    """
    Compute a deduplication key using SHA-256 of null-byte-separated values.

    Two officers logging the same auction on the same raid will produce
    the same dedup_key, enabling server-side deduplication.
    """
    raw = f"{item_name}\x00{winner}\x00{amount}\x00{raid_session_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_raid_session_id(first_log_timestamp: str) -> str:
    """
    Compute a deterministic raid session ID from the first log timestamp.

    Uses SHA-256 of the calendar date (YYYY-MM-DD) extracted from the
    ISO 8601 timestamp. Officers logging the same raid will produce
    the same session ID regardless of exact time.
    """
    # Extract just the date portion (first 10 chars: YYYY-MM-DD)
    date_str = first_log_timestamp[:10]
    return hashlib.sha256(date_str.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# BidEntry dataclass
# ---------------------------------------------------------------------------


@dataclass
class BidEntry:
    """A single bid within an auction record."""

    player: str
    amount: int
    bid_type: str
    is_correction: bool

    def validate(self) -> List[str]:
        """
        Validate all field constraints. Returns a list of error messages.
        An empty list means the entry is valid.
        """
        errors = []

        # player: non-empty, max 64 characters
        if not self.player or not self.player.strip():
            errors.append("player must be non-empty")
        elif len(self.player) > 64:
            errors.append("player must be at most 64 characters")

        # amount: positive integer
        if not isinstance(self.amount, int) or self.amount <= 0:
            errors.append("amount must be a positive integer")

        # bid_type: must be "main" or "alt"
        if self.bid_type not in _VALID_BID_TYPES:
            errors.append(f"bid_type must be one of: {', '.join(sorted(_VALID_BID_TYPES))}")

        # is_correction: must be boolean
        if not isinstance(self.is_correction, bool):
            errors.append("is_correction must be a boolean")

        return errors

    def to_dict(self) -> dict:
        """Serialize to a plain dictionary for JSON encoding."""
        return {
            "player": self.player,
            "amount": self.amount,
            "bid_type": self.bid_type,
            "is_correction": self.is_correction,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BidEntry":
        """Deserialize from a plain dictionary."""
        return cls(
            player=data["player"],
            amount=data["amount"],
            bid_type=data["bid_type"],
            is_correction=data["is_correction"],
        )


# ---------------------------------------------------------------------------
# AuctionRecord dataclass
# ---------------------------------------------------------------------------


@dataclass
class AuctionRecord:
    """A closed auction record with full bid history."""

    item_name: str
    winner: str
    amount: int
    timestamp: str
    raid_session_id: str
    dedup_key: str
    log_source: str
    bids: List[BidEntry] = field(default_factory=list)
    uploaded_by: str = ""  # Character name extracted from log filename
    confirmed_by: List[str] = field(default_factory=list)  # All officers who captured this auction

    def validate(self) -> List[str]:
        """
        Validate all field constraints. Returns a list of error messages.
        An empty list means the record is valid.
        """
        errors = []

        # item_name: non-empty, max 128 characters
        if not self.item_name or not self.item_name.strip():
            errors.append("item_name must be non-empty")
        elif len(self.item_name) > 128:
            errors.append("item_name must be at most 128 characters")

        # winner: non-empty, max 64 characters, alphanumeric
        if not self.winner or not self.winner.strip():
            errors.append("winner must be non-empty")
        elif len(self.winner) > 64:
            errors.append("winner must be at most 64 characters")
        elif not self.winner.isalnum():
            errors.append("winner must be alphanumeric")

        # amount: positive integer
        if not isinstance(self.amount, int) or self.amount <= 0:
            errors.append("amount must be a positive integer")

        # timestamp: valid ISO 8601
        if not self.timestamp or not _ISO8601_PATTERN.match(self.timestamp):
            errors.append("timestamp must be valid ISO 8601 (e.g., 2026-04-29T02:55:35Z)")

        # dedup_key: 64-character hex string
        if not self.dedup_key or len(self.dedup_key) != 64:
            errors.append("dedup_key must be a 64-character hex string")
        elif not all(c in "0123456789abcdef" for c in self.dedup_key.lower()):
            errors.append("dedup_key must be a 64-character hex string")

        # bids: non-empty list
        if not self.bids:
            errors.append("bids must be a non-empty list")
        else:
            for i, bid in enumerate(self.bids):
                bid_errors = bid.validate()
                for err in bid_errors:
                    errors.append(f"bids[{i}]: {err}")

        return errors

    def to_dict(self) -> dict:
        """Serialize to a plain dictionary for JSON encoding."""
        d = {
            "item_name": self.item_name,
            "winner": self.winner,
            "amount": self.amount,
            "timestamp": self.timestamp,
            "raid_session_id": self.raid_session_id,
            "dedup_key": self.dedup_key,
            "log_source": self.log_source,
            "bids": [bid.to_dict() for bid in self.bids],
        }
        if self.uploaded_by:
            d["uploaded_by"] = self.uploaded_by
        if self.confirmed_by:
            d["confirmed_by"] = list(self.confirmed_by)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "AuctionRecord":
        """Deserialize from a plain dictionary."""
        bids = [BidEntry.from_dict(b) for b in data.get("bids", [])]
        return cls(
            item_name=data["item_name"],
            winner=data["winner"],
            amount=data["amount"],
            timestamp=data["timestamp"],
            raid_session_id=data["raid_session_id"],
            dedup_key=data["dedup_key"],
            log_source=data["log_source"],
            bids=bids,
            uploaded_by=data.get("uploaded_by", ""),
            confirmed_by=data.get("confirmed_by", []),
        )
