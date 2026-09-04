"""
test_handler.py — Unit tests for POST /auctions handler.

Tests cover:
- API key validation (missing, invalid, correct)
- AuctionRecord schema validation
- BidEntry validation
- Deduplication logic (new record → 201, existing → 200)
- JSON parsing errors
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure the backend module is importable
sys.path.insert(0, os.path.dirname(__file__))

# Mock boto3 before importing handler since it may not be installed locally
mock_boto3 = MagicMock()
sys.modules["boto3"] = mock_boto3

from handler import (
    handle_post_auctions,
    _validate_api_key,
    _validate_auction_record,
    _validate_bid_entry,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

VALID_API_KEY = "test-guild-key-12345"

VALID_RECORD = {
    "item_name": "Blade of Carnage",
    "winner": "Grizzly",
    "amount": 150,
    "timestamp": "2026-04-29T02:55:35Z",
    "raid_session_id": "a" * 64,
    "dedup_key": "b" * 64,
    "log_source": "eqlog_Grizzly_project1999.txt",
    "bids": [
        {"player": "Grizzly", "amount": 150, "bid_type": "main", "is_correction": False},
        {"player": "Frostbite", "amount": 100, "bid_type": "alt", "is_correction": False},
    ],
}


def _make_event(body=None, api_key=VALID_API_KEY, headers=None):
    """Build a minimal API Gateway HTTP API v2 event for POST /auctions."""
    if headers is None:
        headers = {}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    event = {
        "rawPath": "/auctions",
        "requestContext": {"http": {"method": "POST"}},
        "headers": headers,
        "body": json.dumps(body) if body is not None else "",
        "isBase64Encoded": False,
    }
    return event


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------


class TestApiKeyValidation:
    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY})
    def test_missing_authorization_header_returns_401(self):
        event = _make_event(body=VALID_RECORD, api_key=None)
        event["headers"] = {}
        result = _validate_api_key(event)
        assert result is not None
        assert result["statusCode"] == 401

    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY})
    def test_invalid_api_key_returns_401(self):
        event = _make_event(body=VALID_RECORD, api_key="wrong-key")
        result = _validate_api_key(event)
        assert result is not None
        assert result["statusCode"] == 401

    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY})
    def test_valid_api_key_returns_none(self):
        event = _make_event(body=VALID_RECORD, api_key=VALID_API_KEY)
        result = _validate_api_key(event)
        assert result is None

    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY})
    def test_non_bearer_scheme_returns_401(self):
        event = _make_event(body=VALID_RECORD, api_key=None)
        event["headers"] = {"authorization": f"Basic {VALID_API_KEY}"}
        result = _validate_api_key(event)
        assert result is not None
        assert result["statusCode"] == 401

    @patch.dict(os.environ, {"API_KEY": ""})
    def test_no_server_api_key_configured_returns_401(self):
        event = _make_event(body=VALID_RECORD, api_key=VALID_API_KEY)
        result = _validate_api_key(event)
        assert result is not None
        assert result["statusCode"] == 401


# ---------------------------------------------------------------------------
# AuctionRecord validation tests
# ---------------------------------------------------------------------------


class TestAuctionRecordValidation:
    def test_valid_record_has_no_errors(self):
        errors = _validate_auction_record(VALID_RECORD)
        assert errors == []

    def test_empty_item_name_rejected(self):
        record = {**VALID_RECORD, "item_name": ""}
        errors = _validate_auction_record(record)
        assert any("item_name" in e for e in errors)

    def test_item_name_too_long_rejected(self):
        record = {**VALID_RECORD, "item_name": "x" * 129}
        errors = _validate_auction_record(record)
        assert any("item_name" in e and "128" in e for e in errors)

    def test_empty_winner_rejected(self):
        record = {**VALID_RECORD, "winner": ""}
        errors = _validate_auction_record(record)
        assert any("winner" in e for e in errors)

    def test_non_alphanumeric_winner_rejected(self):
        record = {**VALID_RECORD, "winner": "player one"}
        errors = _validate_auction_record(record)
        assert any("alphanumeric" in e for e in errors)

    def test_negative_amount_rejected(self):
        record = {**VALID_RECORD, "amount": -5}
        errors = _validate_auction_record(record)
        assert any("amount" in e for e in errors)

    def test_zero_amount_accepted(self):
        # A 0-DKP award is valid — historical loot-council / reserved items
        # were tracked at 0 and charged later. The backend must still accept
        # these so old history files can be imported.
        record = {**VALID_RECORD, "amount": 0}
        errors = _validate_auction_record(record)
        assert not any("amount" in e for e in errors)

    def test_invalid_timestamp_rejected(self):
        record = {**VALID_RECORD, "timestamp": "not-a-date"}
        errors = _validate_auction_record(record)
        assert any("timestamp" in e for e in errors)

    def test_dedup_key_wrong_length_rejected(self):
        record = {**VALID_RECORD, "dedup_key": "abc123"}
        errors = _validate_auction_record(record)
        assert any("dedup_key" in e for e in errors)

    def test_dedup_key_non_hex_rejected(self):
        record = {**VALID_RECORD, "dedup_key": "g" * 64}
        errors = _validate_auction_record(record)
        assert any("dedup_key" in e for e in errors)

    def test_empty_bids_rejected(self):
        record = {**VALID_RECORD, "bids": []}
        errors = _validate_auction_record(record)
        assert any("bids" in e for e in errors)

    def test_boolean_amount_rejected(self):
        record = {**VALID_RECORD, "amount": True}
        errors = _validate_auction_record(record)
        assert any("amount" in e for e in errors)


# ---------------------------------------------------------------------------
# BidEntry validation tests
# ---------------------------------------------------------------------------


class TestBidEntryValidation:
    def test_valid_bid_has_no_errors(self):
        bid = {"player": "Grizzly", "amount": 100, "bid_type": "main", "is_correction": False}
        errors = _validate_bid_entry(bid, 0)
        assert errors == []

    def test_empty_player_rejected(self):
        bid = {"player": "", "amount": 100, "bid_type": "main", "is_correction": False}
        errors = _validate_bid_entry(bid, 0)
        assert any("player" in e for e in errors)

    def test_invalid_bid_type_rejected(self):
        bid = {"player": "Grizzly", "amount": 100, "bid_type": "tank", "is_correction": False}
        errors = _validate_bid_entry(bid, 0)
        assert any("bid_type" in e for e in errors)

    def test_non_boolean_is_correction_rejected(self):
        bid = {"player": "Grizzly", "amount": 100, "bid_type": "main", "is_correction": "yes"}
        errors = _validate_bid_entry(bid, 0)
        assert any("is_correction" in e for e in errors)

    def test_non_dict_bid_rejected(self):
        errors = _validate_bid_entry("not a dict", 0)
        assert any("must be an object" in e for e in errors)


# ---------------------------------------------------------------------------
# Integration tests for handle_post_auctions (mocked DynamoDB)
# ---------------------------------------------------------------------------


class TestHandlePostAuctions:
    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY, "TABLE_NAME": "TestTable"})
    @patch("handler.boto3")
    def test_new_record_returns_201(self, mock_boto3):
        mock_table = MagicMock()
        # Dedup check uses table.query — return no matching items so the
        # record is treated as new.
        mock_table.query.return_value = {"Items": []}
        mock_boto3.resource.return_value.Table.return_value = mock_table

        event = _make_event(body=VALID_RECORD)
        result = handle_post_auctions(event)

        assert result["statusCode"] == 201
        body = json.loads(result["body"])
        assert body["dedup_key"] == VALID_RECORD["dedup_key"]
        mock_table.put_item.assert_called_once()

    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY, "TABLE_NAME": "TestTable"})
    @patch("handler.boto3")
    def test_duplicate_record_returns_200(self, mock_boto3):
        mock_table = MagicMock()
        # Dedup check uses table.query — return the existing record so the
        # request is treated as a duplicate.
        mock_table.query.return_value = {"Items": [VALID_RECORD]}
        mock_boto3.resource.return_value.Table.return_value = mock_table

        event = _make_event(body=VALID_RECORD)
        result = handle_post_auctions(event)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "Duplicate" in body["message"]
        mock_table.put_item.assert_not_called()

    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY, "TABLE_NAME": "TestTable"})
    def test_missing_auth_returns_401(self):
        event = _make_event(body=VALID_RECORD, api_key=None)
        event["headers"] = {}
        result = handle_post_auctions(event)
        assert result["statusCode"] == 401

    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY, "TABLE_NAME": "TestTable"})
    def test_invalid_body_returns_422(self):
        event = _make_event(body=VALID_RECORD, api_key=VALID_API_KEY)
        event["body"] = "not json {{"
        result = handle_post_auctions(event)
        assert result["statusCode"] == 422

    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY, "TABLE_NAME": "TestTable"})
    def test_invalid_record_returns_422_with_details(self):
        bad_record = {**VALID_RECORD, "amount": -1, "winner": ""}
        event = _make_event(body=bad_record, api_key=VALID_API_KEY)
        result = handle_post_auctions(event)
        assert result["statusCode"] == 422
        body = json.loads(result["body"])
        assert "details" in body
        assert len(body["details"]) >= 2
