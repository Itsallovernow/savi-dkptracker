"""
test_api_client.py — Unit tests for api_client.push_record.

Tests cover: success (201/200), timeout, connection error, HTTP 5xx,
HTTP 401 (PushUnauthorizedError), and HTTP 422 (PushValidationError).
"""

import unittest
from unittest.mock import patch, MagicMock

import requests

from api_client import push_record, DEFAULT_TIMEOUT
from models import AuctionRecord, BidEntry
from offline_queue import PushRetryableError, PushUnauthorizedError, PushValidationError


def _make_record() -> AuctionRecord:
    """Create a valid AuctionRecord for testing."""
    return AuctionRecord(
        item_name="Blade of Carnage",
        winner="Playerone",
        amount=150,
        timestamp="2026-04-29T02:55:35Z",
        raid_session_id="a" * 64,
        dedup_key="b" * 64,
        log_source="eqlog_Playerone_rizlona.txt",
        bids=[
            BidEntry(player="Playerone", amount=150, bid_type="main", is_correction=False),
        ],
    )


class TestPushRecordSuccess(unittest.TestCase):
    """Test successful push scenarios."""

    @patch("api_client.requests.post")
    def test_returns_true_on_201(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_post.return_value = mock_resp

        result = push_record(_make_record(), "https://api.example.com", "my-key")

        self.assertTrue(result)
        mock_post.assert_called_once()

    @patch("api_client.requests.post")
    def test_returns_true_on_200_duplicate(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        result = push_record(_make_record(), "https://api.example.com", "my-key")

        self.assertTrue(result)

    @patch("api_client.requests.post")
    def test_posts_to_correct_url(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_post.return_value = mock_resp

        push_record(_make_record(), "https://api.example.com/", "my-key")

        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.example.com/auctions")

    @patch("api_client.requests.post")
    def test_sends_bearer_token(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_post.return_value = mock_resp

        push_record(_make_record(), "https://api.example.com", "secret-key-123")

        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret-key-123")

    @patch("api_client.requests.post")
    def test_uses_configurable_timeout(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_post.return_value = mock_resp

        push_record(_make_record(), "https://api.example.com", "key", timeout=5.0)

        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["timeout"], 5.0)

    @patch("api_client.requests.post")
    def test_default_timeout_is_used(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_post.return_value = mock_resp

        push_record(_make_record(), "https://api.example.com", "key")

        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["timeout"], DEFAULT_TIMEOUT)


class TestPushRecordRetryableErrors(unittest.TestCase):
    """Test retryable error scenarios (timeout, connection, 5xx)."""

    @patch("api_client.requests.post")
    def test_raises_retryable_on_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("timed out")

        with self.assertRaises(PushRetryableError):
            push_record(_make_record(), "https://api.example.com", "key")

    @patch("api_client.requests.post")
    def test_raises_retryable_on_connection_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")

        with self.assertRaises(PushRetryableError):
            push_record(_make_record(), "https://api.example.com", "key")

    @patch("api_client.requests.post")
    def test_raises_retryable_on_500(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp

        with self.assertRaises(PushRetryableError):
            push_record(_make_record(), "https://api.example.com", "key")

    @patch("api_client.requests.post")
    def test_raises_retryable_on_503(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = "Service Unavailable"
        mock_post.return_value = mock_resp

        with self.assertRaises(PushRetryableError):
            push_record(_make_record(), "https://api.example.com", "key")

    @patch("api_client.requests.post")
    def test_raises_retryable_on_generic_request_exception(self, mock_post):
        mock_post.side_effect = requests.exceptions.RequestException("something broke")

        with self.assertRaises(PushRetryableError):
            push_record(_make_record(), "https://api.example.com", "key")


class TestPushRecordNonRetryableErrors(unittest.TestCase):
    """Test non-retryable error scenarios (401, 422)."""

    @patch("api_client.requests.post")
    def test_raises_unauthorized_on_401(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_post.return_value = mock_resp

        with self.assertRaises(PushUnauthorizedError):
            push_record(_make_record(), "https://api.example.com", "bad-key")

    @patch("api_client.requests.post")
    def test_raises_validation_error_on_422(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.return_value = {"error": "item_name must be non-empty"}
        mock_post.return_value = mock_resp

        with self.assertRaises(PushValidationError):
            push_record(_make_record(), "https://api.example.com", "key")

    @patch("api_client.requests.post")
    def test_validation_error_includes_detail(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.return_value = {"error": "amount must be positive"}
        mock_post.return_value = mock_resp

        with self.assertRaises(PushValidationError) as ctx:
            push_record(_make_record(), "https://api.example.com", "key")

        self.assertIn("amount must be positive", str(ctx.exception))

    @patch("api_client.requests.post")
    def test_validation_error_handles_non_json_response(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.side_effect = ValueError("no json")
        mock_resp.text = "Bad request body"
        mock_post.return_value = mock_resp

        with self.assertRaises(PushValidationError) as ctx:
            push_record(_make_record(), "https://api.example.com", "key")

        self.assertIn("Bad request body", str(ctx.exception))


class TestPushRecordPayload(unittest.TestCase):
    """Test that the payload is serialized correctly."""

    @patch("api_client.requests.post")
    def test_sends_record_as_json(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_post.return_value = mock_resp

        record = _make_record()
        push_record(record, "https://api.example.com", "key")

        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"], record.to_dict())


if __name__ == "__main__":
    unittest.main()
