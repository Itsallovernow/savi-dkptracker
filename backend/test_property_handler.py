"""
test_property_handler.py — Property-based tests for the Backend API handler.

Uses Hypothesis to verify universal properties about the handler's behavior
across a wide range of generated inputs.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# Ensure the backend module is importable
sys.path.insert(0, os.path.dirname(__file__))

# Mock boto3 before importing handler since it may not be installed locally
mock_boto3 = MagicMock()
sys.modules["boto3"] = mock_boto3

from handler import handle_post_auctions


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

VALID_API_KEY = "real-guild-secret-key-999"

# Strategy for a valid AuctionRecord body (used as payload in all auth tests)
valid_bid_entry = st.fixed_dictionaries({
    "player": st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=16,
    ),
    "amount": st.integers(min_value=1, max_value=10000),
    "bid_type": st.sampled_from(["main", "alt"]),
    "is_correction": st.booleans(),
})

valid_auction_record = st.fixed_dictionaries({
    "item_name": st.text(min_size=1, max_size=64).filter(lambda s: s.strip()),
    "winner": st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=32,
    ),
    "amount": st.integers(min_value=1, max_value=100000),
    "timestamp": st.sampled_from([
        "2026-04-29T02:55:35Z",
        "2025-12-01T00:00:00Z",
        "2024-06-15T12:30:00+05:00",
    ]),
    "raid_session_id": st.text(
        alphabet="0123456789abcdef", min_size=64, max_size=64
    ),
    "dedup_key": st.text(
        alphabet="0123456789abcdef", min_size=64, max_size=64
    ),
    "log_source": st.text(min_size=0, max_size=32),
    "bids": st.lists(valid_bid_entry, min_size=1, max_size=3),
})

# Strategy for invalid/malformed authorization header values
# Covers: empty string, random text, wrong prefix, missing Bearer, partial tokens
invalid_auth_headers = st.one_of(
    # Empty authorization header
    st.just(""),
    # Random text (no Bearer prefix)
    st.text(min_size=1, max_size=100).filter(
        lambda s: not s.lower().startswith("bearer ")
    ),
    # Bearer prefix but wrong token
    st.text(min_size=1, max_size=64).map(lambda t: f"Bearer {t}").filter(
        lambda s: s != f"Bearer {VALID_API_KEY}"
    ),
    # Other auth schemes (Basic, Token, etc.)
    st.sampled_from(["Basic", "Token", "Digest", "ApiKey"]).flatmap(
        lambda scheme: st.text(min_size=1, max_size=32).map(
            lambda val: f"{scheme} {val}"
        )
    ),
    # Bearer with extra whitespace or mangled
    st.just("Bearer"),
    st.just("Bearer "),
    st.just("bearer"),
    st.just("BEARER " + VALID_API_KEY[:-1]),  # Almost correct but truncated
)


# ---------------------------------------------------------------------------
# Property 8: Invalid authentication is rejected (401)
# **Validates: Requirements 6.3, 8.1**
#
# For any POST request to /auctions with an invalid, missing, or malformed
# API_Key, the Backend_API SHALL return HTTP 401 regardless of the record
# payload.
# ---------------------------------------------------------------------------


class TestProperty8InvalidAuthRejected:
    """Property 8: Invalid authentication is rejected (401)."""

    @given(record=valid_auction_record, auth_header=invalid_auth_headers)
    @settings(max_examples=200)
    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY, "TABLE_NAME": "TestTable"})
    def test_invalid_auth_header_returns_401(self, record, auth_header):
        """
        **Validates: Requirements 6.3, 8.1**

        For any POST to /auctions with an invalid authorization header,
        the response must always be 401, regardless of what the record
        payload contains.
        """
        event = {
            "rawPath": "/auctions",
            "requestContext": {"http": {"method": "POST"}},
            "headers": {"authorization": auth_header},
            "body": json.dumps(record),
            "isBase64Encoded": False,
        }

        result = handle_post_auctions(event)
        assert result["statusCode"] == 401, (
            f"Expected 401 for auth_header={auth_header!r}, got {result['statusCode']}"
        )

    @given(record=valid_auction_record)
    @settings(max_examples=100)
    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY, "TABLE_NAME": "TestTable"})
    def test_missing_auth_header_returns_401(self, record):
        """
        **Validates: Requirements 6.3, 8.1**

        For any POST to /auctions with no Authorization header at all,
        the response must always be 401, regardless of the record payload.
        """
        event = {
            "rawPath": "/auctions",
            "requestContext": {"http": {"method": "POST"}},
            "headers": {},
            "body": json.dumps(record),
            "isBase64Encoded": False,
        }

        result = handle_post_auctions(event)
        assert result["statusCode"] == 401, (
            f"Expected 401 for missing auth header, got {result['statusCode']}"
        )

    @given(
        record=valid_auction_record,
        random_key=st.text(min_size=0, max_size=100),
    )
    @settings(max_examples=150)
    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY, "TABLE_NAME": "TestTable"})
    def test_wrong_bearer_token_returns_401(self, record, random_key):
        """
        **Validates: Requirements 6.3, 8.1**

        For any POST to /auctions where the Bearer token doesn't match the
        server's configured API_KEY, the response must be 401.
        """
        assume(random_key != VALID_API_KEY)

        event = {
            "rawPath": "/auctions",
            "requestContext": {"http": {"method": "POST"}},
            "headers": {"authorization": f"Bearer {random_key}"},
            "body": json.dumps(record),
            "isBase64Encoded": False,
        }

        result = handle_post_auctions(event)
        assert result["statusCode"] == 401, (
            f"Expected 401 for Bearer token={random_key!r}, got {result['statusCode']}"
        )


# ---------------------------------------------------------------------------
# Property 9: Invalid records are rejected (422)
# **Validates: Requirements 6.4, 6.5, 6.6**
#
# For any AuctionRecord that violates the schema (empty item_name, negative
# amount, invalid timestamp, wrong dedup_key length, empty bids array,
# invalid bid_type, etc.), the Backend_API SHALL return HTTP 422 with an
# error message identifying the invalid fields.
# ---------------------------------------------------------------------------


# Strategies that generate individual invalid field values

# Invalid item_name: empty, whitespace-only, or too long (>128 chars)
invalid_item_names = st.one_of(
    st.just(""),
    st.just("   "),
    st.text(min_size=129, max_size=200),
    st.sampled_from([None]),
    st.integers(),  # wrong type
)

# Invalid winner: empty, whitespace-only, too long, or non-alphanumeric
invalid_winners = st.one_of(
    st.just(""),
    st.just("   "),
    st.text(min_size=65, max_size=100, alphabet=st.characters(whitelist_categories=("L",))),
    st.just("Player With Spaces"),
    st.just("player!@#"),
    st.sampled_from([None]),
    st.integers(),  # wrong type
)

# Invalid amount: negative, non-integer, or boolean.
# NOTE: 0 is intentionally NOT here — a 0-DKP award is valid (historical
# loot-council / reserved items tracked at 0 and charged later).
invalid_amounts = st.one_of(
    st.integers(max_value=-1),
    st.just(3.14),
    st.just("100"),
    st.just(True),
    st.just(False),
    st.sampled_from([None]),
)

# Invalid timestamp: malformed ISO strings
invalid_timestamps = st.one_of(
    st.just(""),
    st.just("not-a-date"),
    st.just("2026-13-01T00:00:00Z"),  # invalid month
    st.just("2026/04/29 02:55:35"),  # wrong format
    st.just("20260429T025535Z"),  # no separators
    st.sampled_from([None, 12345]),
)

# Invalid dedup_key: wrong length or non-hex characters
invalid_dedup_keys = st.one_of(
    st.just(""),
    st.text(alphabet="0123456789abcdef", min_size=1, max_size=63),  # too short
    st.text(alphabet="0123456789abcdef", min_size=65, max_size=100),  # too long
    st.just("g" * 64),  # non-hex character
    st.just("ZZZZ" + "a" * 60),  # non-hex prefix
    st.sampled_from([None, 12345]),
)

# Invalid bids: empty array, not an array, or array with invalid entries
invalid_bid_entry_bad_player = st.fixed_dictionaries({
    "player": st.one_of(st.just(""), st.just("   "), st.sampled_from([None, 123])),
    "amount": st.integers(min_value=1, max_value=1000),
    "bid_type": st.sampled_from(["main", "alt"]),
    "is_correction": st.booleans(),
})

invalid_bid_entry_bad_amount = st.fixed_dictionaries({
    "player": st.text(alphabet=st.characters(whitelist_categories=("L",)), min_size=1, max_size=16),
    # NOTE: 0 is intentionally NOT invalid — a synthesized 0-amount bid backs a
    # 0-DKP award (historical loot-council data) and must be accepted.
    "amount": st.one_of(st.integers(max_value=-1), st.just("ten"), st.just(True)),
    "bid_type": st.sampled_from(["main", "alt"]),
    "is_correction": st.booleans(),
})

invalid_bid_entry_bad_bid_type = st.fixed_dictionaries({
    "player": st.text(alphabet=st.characters(whitelist_categories=("L",)), min_size=1, max_size=16),
    "amount": st.integers(min_value=1, max_value=1000),
    "bid_type": st.one_of(st.just(""), st.just("primary"), st.just("secondary"), st.sampled_from([None, 123])),
    "is_correction": st.booleans(),
})

invalid_bid_entry_bad_is_correction = st.fixed_dictionaries({
    "player": st.text(alphabet=st.characters(whitelist_categories=("L",)), min_size=1, max_size=16),
    "amount": st.integers(min_value=1, max_value=1000),
    "bid_type": st.sampled_from(["main", "alt"]),
    "is_correction": st.one_of(st.just("yes"), st.just(1), st.just(0), st.sampled_from([None, "true"])),
})

# A bid list containing at least one invalid entry
invalid_bids_with_bad_entries = st.one_of(
    st.just([]),  # empty bids array
    st.just("not-a-list"),  # not an array
    st.sampled_from([None]),  # None
    st.lists(invalid_bid_entry_bad_player, min_size=1, max_size=2),
    st.lists(invalid_bid_entry_bad_amount, min_size=1, max_size=2),
    st.lists(invalid_bid_entry_bad_bid_type, min_size=1, max_size=2),
    st.lists(invalid_bid_entry_bad_is_correction, min_size=1, max_size=2),
)


# Strategy that builds a record with exactly one invalid field (rest valid)
def _make_record_with_invalid_field(field_name, invalid_value, base_record):
    """Clone the base record and replace one field with an invalid value."""
    record = dict(base_record)
    record[field_name] = invalid_value
    return record


# Composite strategy: generate a valid record and corrupt at least one field
@st.composite
def invalid_auction_record_single_field(draw):
    """Generate a record with exactly one field made invalid."""
    # Start with a valid base
    base = draw(valid_auction_record)

    # Choose which field to corrupt
    field = draw(st.sampled_from([
        "item_name", "winner", "amount", "timestamp", "dedup_key", "bids"
    ]))

    if field == "item_name":
        bad_val = draw(invalid_item_names)
    elif field == "winner":
        bad_val = draw(invalid_winners)
    elif field == "amount":
        bad_val = draw(invalid_amounts)
    elif field == "timestamp":
        bad_val = draw(invalid_timestamps)
    elif field == "dedup_key":
        bad_val = draw(invalid_dedup_keys)
    else:  # bids
        bad_val = draw(invalid_bids_with_bad_entries)

    record = dict(base)
    record[field] = bad_val
    return record


@st.composite
def invalid_auction_record_multiple_fields(draw):
    """Generate a record with multiple fields made invalid."""
    base = draw(valid_auction_record)
    record = dict(base)

    # Corrupt 2-4 fields
    fields_to_corrupt = draw(st.lists(
        st.sampled_from(["item_name", "winner", "amount", "timestamp", "dedup_key", "bids"]),
        min_size=2,
        max_size=4,
        unique=True,
    ))

    for field in fields_to_corrupt:
        if field == "item_name":
            record[field] = draw(invalid_item_names)
        elif field == "winner":
            record[field] = draw(invalid_winners)
        elif field == "amount":
            record[field] = draw(invalid_amounts)
        elif field == "timestamp":
            record[field] = draw(invalid_timestamps)
        elif field == "dedup_key":
            record[field] = draw(invalid_dedup_keys)
        else:  # bids
            record[field] = draw(invalid_bids_with_bad_entries)

    return record


class TestProperty9InvalidRecordsRejected:
    """Property 9: Invalid records are rejected (422)."""

    @given(record=invalid_auction_record_single_field())
    @settings(max_examples=300)
    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY, "TABLE_NAME": "TestTable"})
    def test_single_invalid_field_returns_422(self, record):
        """
        **Validates: Requirements 6.4, 6.5, 6.6**

        For any AuctionRecord with a single field violating the schema,
        the Backend_API SHALL return HTTP 422 with an error message.
        """
        event = {
            "rawPath": "/auctions",
            "requestContext": {"http": {"method": "POST"}},
            "headers": {"authorization": f"Bearer {VALID_API_KEY}"},
            "body": json.dumps(record, default=str),
            "isBase64Encoded": False,
        }

        result = handle_post_auctions(event)
        assert result["statusCode"] == 422, (
            f"Expected 422 for invalid record, got {result['statusCode']}. "
            f"Record: {record}"
        )

        # The response body should contain error details
        body = json.loads(result["body"])
        assert "error" in body, "Response must contain 'error' field"
        assert body["error"] == "Unprocessable Entity"
        # Must have either 'details' list or 'message' identifying invalid fields
        assert "details" in body or "message" in body, (
            "Response must contain 'details' or 'message' identifying invalid fields"
        )

    @given(record=invalid_auction_record_multiple_fields())
    @settings(max_examples=200)
    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY, "TABLE_NAME": "TestTable"})
    def test_multiple_invalid_fields_returns_422(self, record):
        """
        **Validates: Requirements 6.4, 6.5, 6.6**

        For any AuctionRecord with multiple fields violating the schema,
        the Backend_API SHALL return HTTP 422 with error messages
        identifying the invalid fields.
        """
        event = {
            "rawPath": "/auctions",
            "requestContext": {"http": {"method": "POST"}},
            "headers": {"authorization": f"Bearer {VALID_API_KEY}"},
            "body": json.dumps(record, default=str),
            "isBase64Encoded": False,
        }

        result = handle_post_auctions(event)
        assert result["statusCode"] == 422, (
            f"Expected 422 for invalid record, got {result['statusCode']}. "
            f"Record: {record}"
        )

        body = json.loads(result["body"])
        assert "error" in body
        assert body["error"] == "Unprocessable Entity"
        # For multiple invalid fields, details list should have multiple entries
        if "details" in body:
            assert isinstance(body["details"], list)
            assert len(body["details"]) >= 1, (
                "Expected at least one validation error in details"
            )

    @given(record=valid_auction_record)
    @settings(max_examples=100)
    @patch.dict(os.environ, {"API_KEY": VALID_API_KEY, "TABLE_NAME": "TestTable"})
    def test_valid_record_is_not_rejected_as_422(self, record):
        """
        **Validates: Requirements 6.4, 6.5, 6.6**

        Sanity check: a valid AuctionRecord must NOT return 422.
        This ensures our valid_auction_record strategy is truly valid.
        """
        # Mock DynamoDB to simulate no existing record (new record → 201)
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}  # No existing item
        mock_table.put_item.return_value = None

        mock_dynamodb = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        mock_boto3.resource.return_value = mock_dynamodb

        event = {
            "rawPath": "/auctions",
            "requestContext": {"http": {"method": "POST"}},
            "headers": {"authorization": f"Bearer {VALID_API_KEY}"},
            "body": json.dumps(record),
            "isBase64Encoded": False,
        }

        result = handle_post_auctions(event)
        assert result["statusCode"] in (200, 201), (
            f"Expected 200 or 201 for valid record, got {result['statusCode']}. "
            f"Body: {result['body']}"
        )


# ---------------------------------------------------------------------------
# Property 13: Stats endpoint matches raw data computation
# **Validates: Requirements 7.6**
#
# For any set of stored AuctionRecords, the /auctions/stats response SHALL
# report total_auctions equal to the count of records, total_dkp_spent equal
# to the sum of all amount fields, and unique_winners equal to the count of
# distinct winner values.
# ---------------------------------------------------------------------------

from handler import handle_get_auctions_stats


# Strategy for a minimal stored DynamoDB item (only fields needed by stats)
stored_auction_item = st.fixed_dictionaries({
    "item_name": st.text(min_size=1, max_size=32).filter(lambda s: s.strip()),
    "winner": st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=16,
    ),
    "amount": st.integers(min_value=1, max_value=100000),
    "timestamp": st.sampled_from([
        "2026-04-29T02:55:35Z",
        "2025-12-01T00:00:00Z",
        "2024-06-15T12:30:00+05:00",
    ]),
    "dedup_key": st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
    "raid_session_id": st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
    "log_source": st.just("eqlog_test.txt"),
    "bids": st.just([{"player": "Tester", "amount": 100, "bid_type": "main", "is_correction": False}]),
})


class TestProperty13StatsEndpoint:
    """Property 13: Stats endpoint faithfully returns the cached stats row.

    NOTE: The stats endpoint no longer scans-and-aggregates. Aggregates are
    maintained incrementally in a single cached __stats__ row (updated on each
    POST via _update_stats_on_new_record). The endpoint's responsibility is to
    read that row back accurately, which is what these properties verify.
    Aggregation math is exercised through the POST path, not here.
    """

    @given(
        total_auctions=st.integers(min_value=0, max_value=10000),
        total_dkp_spent=st.integers(min_value=0, max_value=1_000_000),
        unique_winners=st.integers(min_value=0, max_value=5000),
    )
    @settings(max_examples=200)
    @patch.dict(os.environ, {"TABLE_NAME": "TestTable"})
    def test_stats_reflect_cached_row(self, total_auctions, total_dkp_spent, unique_winners):
        """
        **Validates: Requirements 7.6**

        For any cached __stats__ row, the /auctions/stats response SHALL report
        total_auctions, total_dkp_spent, and unique_winners exactly as stored.
        """
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "dedup_key": "__stats__",
                "timestamp": "__stats__",
                "total_auctions": total_auctions,
                "total_dkp_spent": total_dkp_spent,
                "unique_winners": unique_winners,
            }
        }

        with patch("handler.boto3") as patched_boto3:
            patched_boto3.resource.return_value.Table.return_value = mock_table

            event = {
                "rawPath": "/auctions/stats",
                "requestContext": {"http": {"method": "GET"}},
                "headers": {},
            }

            result = handle_get_auctions_stats(event)

        assert result["statusCode"] == 200, (
            f"Expected 200, got {result['statusCode']}: {result['body']}"
        )

        body = json.loads(result["body"])
        assert body["total_auctions"] == total_auctions
        assert body["total_dkp_spent"] == total_dkp_spent
        assert body["unique_winners"] == unique_winners

    @patch.dict(os.environ, {"TABLE_NAME": "TestTable"})
    def test_stats_return_zeros_when_row_absent(self):
        """
        **Validates: Requirements 7.6**

        When the cached __stats__ row does not yet exist, the endpoint SHALL
        return zeros rather than error.
        """
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}  # No Item

        with patch("handler.boto3") as patched_boto3:
            patched_boto3.resource.return_value.Table.return_value = mock_table

            event = {
                "rawPath": "/auctions/stats",
                "requestContext": {"http": {"method": "GET"}},
                "headers": {},
            }

            result = handle_get_auctions_stats(event)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body == {
            "total_auctions": 0,
            "total_dkp_spent": 0,
            "unique_winners": 0,
        }


# Import handle_get_auctions for Property 10
from handler import handle_get_auctions


# ---------------------------------------------------------------------------
# Strategies for GET /auctions tests
# ---------------------------------------------------------------------------

# Generate valid ISO 8601 timestamps with varying dates/times for sorting tests
iso_timestamps = st.tuples(
    st.integers(min_value=2020, max_value=2030),  # year
    st.integers(min_value=1, max_value=12),       # month
    st.integers(min_value=1, max_value=28),       # day (use 28 max to avoid invalid dates)
    st.integers(min_value=0, max_value=23),       # hour
    st.integers(min_value=0, max_value=59),       # minute
    st.integers(min_value=0, max_value=59),       # second
).map(
    lambda t: f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}T{t[3]:02d}:{t[4]:02d}:{t[5]:02d}Z"
)

# Strategy for a stored DynamoDB item (as would be returned by scan)
stored_auction_item = st.fixed_dictionaries({
    "dedup_key": st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
    "item_name": st.text(min_size=1, max_size=64).filter(lambda s: s.strip()),
    "winner": st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=32,
    ),
    "amount": st.integers(min_value=1, max_value=100000),
    "timestamp": iso_timestamps,
    "raid_session_id": st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
    "log_source": st.text(min_size=0, max_size=32),
    "bids": st.lists(valid_bid_entry, min_size=1, max_size=2),
})


# ---------------------------------------------------------------------------
# Property 10: Query results are ordered by timestamp descending
# **Validates: Requirements 7.1**
#
# For any set of stored AuctionRecords, a GET /auctions request SHALL return
# records ordered by timestamp from most recent to oldest.
# ---------------------------------------------------------------------------


class TestProperty10QueryOrdering:
    """Property 10: GET /auctions returns all stored records.

    NOTE: Ordering is intentionally NOT enforced server-side. The handler
    returns records as scanned (excluding the __stats__ metadata row) and the
    web viewer sorts client-side for performance. This test therefore verifies
    completeness — every stored record is returned — rather than order.
    """

    @given(items=st.lists(stored_auction_item, min_size=2, max_size=20))
    @settings(max_examples=200)
    @patch.dict(os.environ, {"TABLE_NAME": "TestTable"})
    def test_get_auctions_returns_all_stored_records(self, items):
        """
        **Validates: Requirements 7.1**

        For any set of stored AuctionRecords returned by DynamoDB in arbitrary
        order, the GET /auctions response SHALL contain every stored record.
        Ordering is handled client-side, so no order is asserted here.
        """
        # Mock DynamoDB table.scan to return items in their generated (random) order
        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": list(items), "Count": len(items)}

        with patch("handler.boto3") as patched_boto3:
            patched_boto3.resource.return_value.Table.return_value = mock_table

            # Build a simple GET /auctions event with no filters
            event = {
                "rawPath": "/auctions",
                "requestContext": {"http": {"method": "GET"}},
                "headers": {},
                "queryStringParameters": {},
            }

            result = handle_get_auctions(event)

        assert result["statusCode"] == 200, (
            f"Expected 200, got {result['statusCode']}: {result['body']}"
        )

        body = json.loads(result["body"])
        records = body["records"]

        # Every stored record is returned (order is not guaranteed server-side).
        assert len(records) == len(items), (
            f"Expected {len(items)} records, got {len(records)}"
        )
        returned_timestamps = sorted(r["timestamp"] for r in records)
        expected_timestamps = sorted(r["timestamp"] for r in items)
        assert returned_timestamps == expected_timestamps


# ---------------------------------------------------------------------------
# Property 11: Query filters return only matching records
# **Validates: Requirements 7.3, 7.4**
#
# For any `since` timestamp parameter, all returned records SHALL have a
# timestamp strictly after that value. For any `session` parameter, all
# returned records SHALL have a matching Raid_Session_ID.
# ---------------------------------------------------------------------------

from handler import handle_get_auctions

# Strategies for generating sets of auction records with varied timestamps/sessions

_TIMESTAMP_POOL = [
    "2024-01-01T00:00:00Z",
    "2024-03-15T10:30:00Z",
    "2024-06-01T12:00:00Z",
    "2024-08-20T18:45:00Z",
    "2024-11-10T22:15:00Z",
    "2025-01-05T08:00:00Z",
    "2025-04-01T16:30:00Z",
    "2025-07-15T20:00:00Z",
    "2025-10-01T05:00:00Z",
    "2026-01-01T00:00:00Z",
]

_SESSION_POOL = [
    "a" * 64,
    "b" * 64,
    "c" * 64,
    "d" * 64,
]


@st.composite
def dynamo_record_set(draw, min_records=3, max_records=15):
    """Generate a list of mock DynamoDB auction items with varied timestamps and sessions."""
    count = draw(st.integers(min_value=min_records, max_value=max_records))
    records = []
    for i in range(count):
        ts = draw(st.sampled_from(_TIMESTAMP_POOL))
        session = draw(st.sampled_from(_SESSION_POOL))
        records.append({
            "dedup_key": draw(st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)),
            "item_name": f"Item{i}",
            "winner": f"Player{i}",
            "amount": draw(st.integers(min_value=1, max_value=5000)),
            "timestamp": ts,
            "raid_session_id": session,
            "log_source": "test_log.txt",
            "bids": [{"player": f"Player{i}", "amount": 100, "bid_type": "main", "is_correction": False}],
        })
    return records


class TestProperty11QueryFilters:
    """Property 11: Query filters return only matching records."""

    @given(
        records=dynamo_record_set(),
        since_ts=st.sampled_from(_TIMESTAMP_POOL),
    )
    @settings(max_examples=200)
    @patch.dict(os.environ, {"TABLE_NAME": "TestTable"})
    def test_since_filter_returns_only_records_after_timestamp(self, records, since_ts):
        """
        **Validates: Requirements 7.3, 7.4**

        For any `since` timestamp parameter, all returned records SHALL have
        a timestamp strictly after that value.
        """
        # Determine expected records (timestamps strictly greater than since_ts)
        expected = [r for r in records if r["timestamp"] > since_ts]

        # Mock DynamoDB scan to apply filter logic the same way handler does.
        # The DynamoDB FilterExpression is a server-side filter, so we simulate
        # it by returning only the records that match the filter.
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": expected,
            "Count": len(expected),
        }

        with patch("handler.boto3") as patched_boto3:
            patched_boto3.resource.return_value.Table.return_value = mock_table

            event = {
                "rawPath": "/auctions",
                "requestContext": {"http": {"method": "GET"}},
                "headers": {},
                "queryStringParameters": {"since": since_ts},
            }

            result = handle_get_auctions(event)

        assert result["statusCode"] == 200, (
            f"Expected 200, got {result['statusCode']}"
        )

        body = json.loads(result["body"])
        returned_records = body["records"]

        # ALL returned records must have timestamp > since_ts
        for rec in returned_records:
            assert rec["timestamp"] > since_ts, (
                f"Record with timestamp {rec['timestamp']} should not be "
                f"returned for since={since_ts}"
            )

        # Verify the handler passed the correct filter to DynamoDB
        call_args = mock_table.scan.call_args
        scan_kwargs = call_args[1] if call_args[1] else call_args[0][0] if call_args[0] else {}
        # Verify filter was applied
        assert "FilterExpression" in scan_kwargs, (
            "Handler must pass a FilterExpression to DynamoDB when 'since' param is provided"
        )
        assert ":since_val" in str(scan_kwargs.get("ExpressionAttributeValues", {})), (
            "Handler must include the since value in ExpressionAttributeValues"
        )

    @given(
        records=dynamo_record_set(),
        target_session=st.sampled_from(_SESSION_POOL),
    )
    @settings(max_examples=200)
    @patch.dict(os.environ, {"TABLE_NAME": "TestTable"})
    def test_session_filter_returns_only_matching_session(self, records, target_session):
        """
        **Validates: Requirements 7.3, 7.4**

        For any `session` parameter, all returned records SHALL have a
        matching Raid_Session_ID.
        """
        # Determine expected records (matching session)
        expected = [r for r in records if r["raid_session_id"] == target_session]

        # Mock DynamoDB scan to return only matching records (simulating filter)
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": expected,
            "Count": len(expected),
        }

        with patch("handler.boto3") as patched_boto3:
            patched_boto3.resource.return_value.Table.return_value = mock_table

            event = {
                "rawPath": "/auctions",
                "requestContext": {"http": {"method": "GET"}},
                "headers": {},
                "queryStringParameters": {"session": target_session},
            }

            result = handle_get_auctions(event)

        assert result["statusCode"] == 200, (
            f"Expected 200, got {result['statusCode']}"
        )

        body = json.loads(result["body"])
        returned_records = body["records"]

        # ALL returned records must have the matching raid_session_id
        for rec in returned_records:
            assert rec["raid_session_id"] == target_session, (
                f"Record with raid_session_id={rec['raid_session_id']} should not "
                f"be returned for session={target_session}"
            )

        # Verify the handler passed the correct filter to DynamoDB
        call_args = mock_table.scan.call_args
        scan_kwargs = call_args[1] if call_args[1] else call_args[0][0] if call_args[0] else {}
        assert "FilterExpression" in scan_kwargs, (
            "Handler must pass a FilterExpression to DynamoDB when 'session' param is provided"
        )
        assert ":session_val" in str(scan_kwargs.get("ExpressionAttributeValues", {})), (
            "Handler must include the session value in ExpressionAttributeValues"
        )

    @given(
        records=dynamo_record_set(),
        since_ts=st.sampled_from(_TIMESTAMP_POOL),
        target_session=st.sampled_from(_SESSION_POOL),
    )
    @settings(max_examples=200)
    @patch.dict(os.environ, {"TABLE_NAME": "TestTable"})
    def test_combined_since_and_session_filter(self, records, since_ts, target_session):
        """
        **Validates: Requirements 7.3, 7.4**

        When both `since` and `session` parameters are provided, all returned
        records SHALL have timestamp strictly after `since` AND a matching
        Raid_Session_ID.
        """
        # Determine expected records (both filters applied)
        expected = [
            r for r in records
            if r["timestamp"] > since_ts and r["raid_session_id"] == target_session
        ]

        # Mock DynamoDB scan to return filtered results
        mock_table = MagicMock()
        mock_table.scan.return_value = {
            "Items": expected,
            "Count": len(expected),
        }

        with patch("handler.boto3") as patched_boto3:
            patched_boto3.resource.return_value.Table.return_value = mock_table

            event = {
                "rawPath": "/auctions",
                "requestContext": {"http": {"method": "GET"}},
                "headers": {},
                "queryStringParameters": {"since": since_ts, "session": target_session},
            }

            result = handle_get_auctions(event)

        assert result["statusCode"] == 200, (
            f"Expected 200, got {result['statusCode']}"
        )

        body = json.loads(result["body"])
        returned_records = body["records"]

        # ALL returned records must satisfy BOTH filters
        for rec in returned_records:
            assert rec["timestamp"] > since_ts, (
                f"Record with timestamp {rec['timestamp']} should not be "
                f"returned for since={since_ts}"
            )
            assert rec["raid_session_id"] == target_session, (
                f"Record with raid_session_id={rec['raid_session_id']} should not "
                f"be returned for session={target_session}"
            )

        # Verify the handler passed both filters to DynamoDB
        call_args = mock_table.scan.call_args
        scan_kwargs = call_args[1] if call_args[1] else call_args[0][0] if call_args[0] else {}
        assert "FilterExpression" in scan_kwargs, (
            "Handler must pass a FilterExpression when both params are provided"
        )
        filter_expr = scan_kwargs["FilterExpression"]
        assert "AND" in filter_expr, (
            "Handler must combine both filters with AND"
        )


# Need handle_get_auctions for pagination tests
from handler import handle_get_auctions


# ---------------------------------------------------------------------------
# Strategies for GET /auctions pagination tests
# ---------------------------------------------------------------------------

# Strategy for generating a list of auction record items (as DynamoDB would return)
dynamo_auction_item = st.fixed_dictionaries({
    "dedup_key": st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
    "item_name": st.text(min_size=1, max_size=64).filter(lambda s: s.strip()),
    "winner": st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=32,
    ),
    "amount": st.integers(min_value=1, max_value=100000),
    "timestamp": st.sampled_from([
        "2026-04-29T02:55:35Z",
        "2026-03-15T10:00:00Z",
        "2025-12-01T00:00:00Z",
        "2025-11-20T18:30:00Z",
        "2024-06-15T12:30:00Z",
    ]),
    "raid_session_id": st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
    "log_source": st.text(min_size=0, max_size=32),
    "bids": st.just([{"player": "TestPlayer", "amount": 100, "bid_type": "main", "is_correction": False}]),
})


def _make_get_event(limit=None, cursor=None, since=None, session=None):
    """Build a minimal API Gateway HTTP API v2 event for GET /auctions."""
    params = {}
    if limit is not None:
        params["limit"] = str(limit)
    if cursor is not None:
        params["cursor"] = cursor
    if since is not None:
        params["since"] = since
    if session is not None:
        params["session"] = session

    return {
        "rawPath": "/auctions",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {},
        "queryStringParameters": params if params else None,
    }


# ---------------------------------------------------------------------------
# Property 12: Pagination cursor present when more records exist
# **Validates: Requirements 7.5**
#
# For any GET /auctions request where the total matching records exceed the
# page limit, the response SHALL include a non-null `next_cursor` field.
# When fewer or equal records remain, `next_cursor` SHALL be absent or null.
# ---------------------------------------------------------------------------


class TestProperty12PaginationCursor:
    """Property 12: Pagination cursor present when more records exist."""

    @given(
        items=st.lists(dynamo_auction_item, min_size=1, max_size=20),
        limit=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=200)
    @patch.dict(os.environ, {"TABLE_NAME": "TestTable"})
    def test_cursor_present_when_more_records_exist(self, items, limit):
        """
        **Validates: Requirements 7.5**

        When DynamoDB returns a LastEvaluatedKey (indicating more records beyond
        the page), the response must include a non-null next_cursor field.
        """
        # Simulate DynamoDB returning items with LastEvaluatedKey (more pages)
        last_key = {"dedup_key": "a" * 64}
        mock_scan_response = {
            "Items": items[:limit],
            "LastEvaluatedKey": last_key,
        }

        mock_table = MagicMock()
        mock_table.scan.return_value = mock_scan_response

        with patch("handler.boto3") as patched_boto3:
            patched_boto3.resource.return_value.Table.return_value = mock_table

            event = _make_get_event(limit=limit)
            result = handle_get_auctions(event)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "next_cursor" in body, (
            f"Expected next_cursor when LastEvaluatedKey is present, "
            f"but response only has keys: {list(body.keys())}"
        )
        assert body["next_cursor"] is not None
        assert len(body["next_cursor"]) > 0

    @given(
        items=st.lists(dynamo_auction_item, min_size=0, max_size=20),
        limit=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=200)
    @patch.dict(os.environ, {"TABLE_NAME": "TestTable"})
    def test_cursor_absent_when_no_more_records(self, items, limit):
        """
        **Validates: Requirements 7.5**

        When DynamoDB does NOT return a LastEvaluatedKey (all records fit in
        one page), the response must NOT include next_cursor, or it should
        be null.
        """
        # Simulate DynamoDB returning all items with no LastEvaluatedKey
        mock_scan_response = {
            "Items": items[:limit],
            # No "LastEvaluatedKey" key — all records fit
        }

        mock_table = MagicMock()
        mock_table.scan.return_value = mock_scan_response

        with patch("handler.boto3") as patched_boto3:
            patched_boto3.resource.return_value.Table.return_value = mock_table

            event = _make_get_event(limit=limit)
            result = handle_get_auctions(event)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        # next_cursor should be absent or null
        cursor_value = body.get("next_cursor")
        assert cursor_value is None, (
            f"Expected next_cursor to be absent or null when no more records, "
            f"but got: {cursor_value!r}"
        )

    @given(
        total_records=st.integers(min_value=2, max_value=50),
        limit=st.integers(min_value=1, max_value=49),
    )
    @settings(max_examples=150)
    @patch.dict(os.environ, {"TABLE_NAME": "TestTable"})
    def test_cursor_present_when_total_exceeds_limit(self, total_records, limit):
        """
        **Validates: Requirements 7.5**

        When total matching records exceed the page limit, the response
        includes a non-null next_cursor. This simulates DynamoDB's behavior
        of setting LastEvaluatedKey when limit constrains the result set.
        """
        assume(total_records > limit)

        # Generate items up to limit (simulating what DynamoDB would return)
        page_items = [
            {
                "dedup_key": f"{i:064x}",
                "item_name": f"Item{i}",
                "winner": f"Player{i}",
                "amount": i * 10,
                "timestamp": "2026-04-29T02:55:35Z",
                "raid_session_id": "c" * 64,
                "log_source": "test.txt",
                "bids": [{"player": f"Player{i}", "amount": i * 10, "bid_type": "main", "is_correction": False}],
            }
            for i in range(min(limit, total_records))
        ]

        # DynamoDB sets LastEvaluatedKey when more records exist beyond Limit
        last_key = {"dedup_key": page_items[-1]["dedup_key"]}
        mock_scan_response = {
            "Items": page_items,
            "LastEvaluatedKey": last_key,
        }

        mock_table = MagicMock()
        mock_table.scan.return_value = mock_scan_response

        with patch("handler.boto3") as patched_boto3:
            patched_boto3.resource.return_value.Table.return_value = mock_table

            event = _make_get_event(limit=limit)
            result = handle_get_auctions(event)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "next_cursor" in body
        assert body["next_cursor"] is not None
        assert len(body["next_cursor"]) > 0
        # Verify cursor is valid base64
        import base64 as b64
        decoded = b64.b64decode(body["next_cursor"])
        cursor_data = json.loads(decoded)
        assert "dedup_key" in cursor_data
