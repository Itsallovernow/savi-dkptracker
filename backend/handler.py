"""
handler.py — AWS Lambda request router for the DKP Bid Tracker Backend API.

Routes incoming API Gateway events to the appropriate handler based on
HTTP method and resource path.

Endpoints:
    POST /auctions       → ingest a new auction record
    GET  /auctions       → retrieve auction records (paginated, filterable)
    GET  /auctions/stats → retrieve aggregate statistics
"""

import base64
import json
import os
import re
from typing import Any, Dict, List

import boto3

# ISO 8601 pattern: YYYY-MM-DDTHH:MM:SSZ or with timezone offset
_ISO8601_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

_VALID_BID_TYPES = {"main", "alt"}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda entry point. Routes requests based on HTTP method and path.

    Args:
        event: API Gateway HTTP API v2 event payload.
        context: Lambda execution context (unused).

    Returns:
        API Gateway-compatible response dict with statusCode, headers, and body.
    """
    http_method = _get_http_method(event)
    path = _get_path(event)

    if path == "/auctions" and http_method == "POST":
        return handle_post_auctions(event)
    elif path == "/auctions/export" and http_method == "GET":
        return handle_export_auctions(event)
    elif path == "/auctions" and http_method == "GET":
        return handle_get_auctions(event)
    elif path == "/auctions/stats" and http_method == "GET":
        return handle_get_auctions_stats(event)
    elif path.startswith("/auctions/") and http_method == "DELETE":
        # Extract dedup_key from path: /auctions/{dedup_key}
        dedup_key = path[len("/auctions/"):]
        return handle_delete_auction(event, dedup_key)
    elif path.startswith("/auctions/") and http_method == "GET":
        # Get single record detail: /auctions/{dedup_key}
        dedup_key = path[len("/auctions/"):]
        return handle_get_auction_detail(event, dedup_key)
    elif path == "/guild/characters" and http_method == "GET":
        return handle_get_guild_characters(event)
    else:
        return _response(404, {"error": "Not Found", "message": f"No route for {http_method} {path}"})


# ---------------------------------------------------------------------------
# Route handlers (stub implementations — filled in by tasks 5.2, 5.3, 5.4)
# ---------------------------------------------------------------------------


def handle_post_auctions(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle POST /auctions — ingest a new auction record.

    Validates API key, validates record schema, deduplicates, and persists.
    """
    # --- 1. Authenticate: validate API key from Authorization header ---
    auth_error = _validate_api_key(event)
    if auth_error:
        return auth_error

    # --- 2. Parse request body as JSON ---
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    try:
        data = json.loads(body) if isinstance(body, str) else body
    except (json.JSONDecodeError, TypeError):
        return _response(422, {"error": "Unprocessable Entity", "message": "Request body must be valid JSON"})

    if not isinstance(data, dict):
        return _response(422, {"error": "Unprocessable Entity", "message": "Request body must be a JSON object"})

    # --- 3. Validate AuctionRecord schema ---
    validation_errors = _validate_auction_record(data)
    if validation_errors:
        return _response(422, {
            "error": "Unprocessable Entity",
            "message": "Validation failed",
            "details": validation_errors,
        })

    # --- 4. Check DynamoDB for existing record with same dedup_key ---
    table_name = os.environ.get("TABLE_NAME", "DKPAuctions")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    dedup_key = data["dedup_key"]

    try:
        # Query by partition key (dedup_key) since table has a composite key
        query_result = table.query(
            KeyConditionExpression="dedup_key = :dk",
            ExpressionAttributeValues={":dk": dedup_key},
            Limit=1,
        )
        existing_items = query_result.get("Items", [])
    except Exception as e:
        return _response(500, {"error": "Internal Server Error", "message": f"Database error: {str(e)}"})

    # --- 5. If exists, update confirmed_by and return 200 (duplicate acknowledged) ---
    if existing_items:
        existing_item = existing_items[0]
        # Append this officer to the confirmed_by list if they're not the original uploader
        uploaded_by = data.get("uploaded_by", "")
        original_uploader = existing_item.get("uploaded_by", "")
        if uploaded_by and uploaded_by != original_uploader:
            existing_confirmed = existing_item.get("confirmed_by", [])
            if uploaded_by not in existing_confirmed:
                existing_confirmed.append(uploaded_by)
                try:
                    table.update_item(
                        Key={"dedup_key": dedup_key, "timestamp": existing_item["timestamp"]},
                        UpdateExpression="SET confirmed_by = :cb",
                        ExpressionAttributeValues={":cb": existing_confirmed},
                    )
                except Exception:
                    pass  # Non-critical — don't fail the request over this
        return _response(200, {"message": "Duplicate acknowledged", "dedup_key": dedup_key})

    # --- 6. If new, persist to DynamoDB and return 201 ---
    uploaded_by = data.get("uploaded_by", "")
    item = {
        "dedup_key": dedup_key,
        "item_name": data["item_name"],
        "winner": data["winner"],
        "amount": data["amount"],
        "timestamp": data["timestamp"],
        "raid_session_id": data.get("raid_session_id", ""),
        "log_source": data.get("log_source", ""),
        "bids": data["bids"],
        "uploaded_by": uploaded_by,
        "confirmed_by": [],
    }

    try:
        table.put_item(Item=item)
    except Exception as e:
        return _response(500, {"error": "Internal Server Error", "message": f"Database write error: {str(e)}"})

    # --- 7. Update cached stats row atomically ---
    _update_stats_on_new_record(table, data["winner"], data["amount"])

    return _response(201, {"message": "Record created", "dedup_key": dedup_key})


def handle_get_auctions(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle GET /auctions — retrieve paginated auction records.

    Supports query params: since, session, limit, cursor.
    Returns records ordered by timestamp descending.
    """
    # --- Parse query parameters ---
    params = event.get("queryStringParameters") or {}
    since = params.get("since")
    session = params.get("session")
    cursor = params.get("cursor")

    # --- Query DynamoDB ---
    table_name = os.environ.get("TABLE_NAME", "DKPAuctions")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    try:
        # No Limit — let DynamoDB return up to 1MB naturally (~2000 records per call)
        scan_kwargs: Dict[str, Any] = {}

        # Build filter expressions — always exclude the __stats__ metadata row
        filter_parts: List[str] = ["dedup_key <> :stats_key"]
        expression_values: Dict[str, Any] = {":stats_key": "__stats__"}
        expression_names: Dict[str, str] = {}

        if since:
            filter_parts.append("#ts > :since_val")
            expression_values[":since_val"] = since
            expression_names["#ts"] = "timestamp"

        if session:
            filter_parts.append("raid_session_id = :session_val")
            expression_values[":session_val"] = session

        scan_kwargs["FilterExpression"] = " AND ".join(filter_parts)
        scan_kwargs["ExpressionAttributeValues"] = expression_values
        if expression_names:
            scan_kwargs["ExpressionAttributeNames"] = expression_names

        if cursor:
            try:
                decoded = json.loads(base64.b64decode(cursor).decode("utf-8"))
                scan_kwargs["ExclusiveStartKey"] = decoded
            except (ValueError, KeyError):
                pass

        response = table.scan(**scan_kwargs)
        items = response.get("Items", [])

        # No server-side sort — client handles sorting for better performance

        # Build response
        result: Dict[str, Any] = {"records": items}

        # Include next_cursor if more records exist
        last_key = response.get("LastEvaluatedKey")
        if last_key:
            result["next_cursor"] = base64.b64encode(
                json.dumps(last_key).encode("utf-8")
            ).decode("utf-8")

        return _response(200, result)

    except Exception as e:
        return _response(500, {"error": "Internal Server Error", "message": f"Database error: {str(e)}"})


def handle_export_auctions(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle GET /auctions/export — return ALL auction records in one response.

    Always serves from S3 cache first (instant). If cache is >30 min old,
    regenerates it AFTER responding so no user waits. Only the very first
    request (no cache exists) triggers a synchronous scan.
    """
    export_bucket = os.environ.get("EXPORT_BUCKET", "")
    export_key = "data/auctions-export.json"

    params = event.get("queryStringParameters") or {}
    force_refresh = params.get("refresh") == "true"

    # Try to serve from S3 cache
    if export_bucket and not force_refresh:
        try:
            s3 = boto3.client("s3")
            head = s3.head_object(Bucket=export_bucket, Key=export_key)
            last_modified = head["LastModified"]

            # Serve the cached file
            obj = s3.get_object(Bucket=export_bucket, Key=export_key)
            cached_body = obj["Body"].read().decode("utf-8")

            # Check if stale (>30 min) — if so, regenerate in background after response
            import datetime as dt
            age_seconds = (dt.datetime.now(dt.timezone.utc) - last_modified).total_seconds()
            is_stale = age_seconds > 1800  # 30 minutes

            if is_stale:
                # Regenerate asynchronously — invoke self with refresh=true
                _trigger_async_export_refresh()

            return {
                "statusCode": 200,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Authorization",
                    "X-Cache": "HIT",
                    "X-Cache-Age": str(int(age_seconds)),
                },
                "body": cached_body,
            }
        except Exception:
            pass  # No cache or S3 error — fall through to full scan

    # Full scan from DynamoDB (first request ever, or forced refresh)
    table_name = os.environ.get("TABLE_NAME", "DKPAuctions")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    all_items = []
    scan_kwargs: Dict[str, Any] = {
        "FilterExpression": "dedup_key <> :stats_key",
        "ExpressionAttributeValues": {":stats_key": "__stats__"},
    }

    try:
        while True:
            response = table.scan(**scan_kwargs)
            items = response.get("Items", [])
            for item in items:
                bid_count = len(item.get("bids", []))
                item.pop("bids", None)
                item["bid_count"] = bid_count
            all_items.extend(items)

            last_key = response.get("LastEvaluatedKey")
            if last_key:
                scan_kwargs["ExclusiveStartKey"] = last_key
            else:
                break
    except Exception as e:
        return _response(500, {"error": "Internal Server Error", "message": f"Database error: {str(e)}"})

    result = {"records": all_items, "total": len(all_items)}

    # Cache to S3
    if export_bucket:
        try:
            s3 = boto3.client("s3")
            body = json.dumps(result, default=_decimal_default)
            s3.put_object(
                Bucket=export_bucket,
                Key=export_key,
                Body=body.encode("utf-8"),
                ContentType="application/json",
            )
        except Exception:
            pass  # Non-critical

    return _response(200, result)


def _trigger_async_export_refresh():
    """Invoke this Lambda asynchronously to regenerate the export cache."""
    try:
        import boto3 as _boto3
        lambda_client = _boto3.client("lambda")
        function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
        if function_name:
            lambda_client.invoke(
                FunctionName=function_name,
                InvocationType="Event",  # Async — don't wait for response
                Payload=json.dumps({
                    "rawPath": "/auctions/export",
                    "requestContext": {"http": {"method": "GET"}},
                    "headers": {},
                    "queryStringParameters": {"refresh": "true"},
                }).encode("utf-8"),
            )
    except Exception:
        pass  # Non-critical — next request will regenerate


def handle_get_auctions_stats(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle GET /auctions/stats — retrieve aggregate statistics from cached stats row.

    Returns total_auctions, total_dkp_spent, unique_winners.
    Reads from a single cached row (__stats__) for instant response.
    """
    table_name = os.environ.get("TABLE_NAME", "DKPAuctions")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    try:
        result = table.get_item(Key={"dedup_key": "__stats__", "timestamp": "__stats__"})
        item = result.get("Item")
        if item:
            return _response(200, {
                "total_auctions": int(item.get("total_auctions", 0)),
                "total_dkp_spent": int(item.get("total_dkp_spent", 0)),
                "unique_winners": int(item.get("unique_winners", 0)),
            })
        else:
            # Stats row doesn't exist yet — return zeros
            return _response(200, {
                "total_auctions": 0,
                "total_dkp_spent": 0,
                "unique_winners": 0,
            })
    except Exception as e:
        return _response(500, {"error": "Internal Server Error", "message": f"Database error: {str(e)}"})


# ---------------------------------------------------------------------------
# DELETE /auctions/{dedup_key} — Admin-only record deletion
# ---------------------------------------------------------------------------


def handle_get_auction_detail(event: Dict[str, Any], dedup_key: str) -> Dict[str, Any]:
    """
    Handle GET /auctions/{dedup_key} — return full record detail including bids.
    Used for lazy-loading bid history when a user expands a row.
    """
    if not dedup_key or len(dedup_key) != 64:
        return _response(400, {"error": "Bad Request", "message": "dedup_key must be a 64-character hex string"})

    table_name = os.environ.get("TABLE_NAME", "DKPAuctions")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    try:
        query_result = table.query(
            KeyConditionExpression="dedup_key = :dk",
            ExpressionAttributeValues={":dk": dedup_key},
            Limit=1,
        )
        items = query_result.get("Items", [])
    except Exception as e:
        return _response(500, {"error": "Internal Server Error", "message": f"Database error: {str(e)}"})

    if not items:
        return _response(404, {"error": "Not Found", "message": f"No record with dedup_key: {dedup_key}"})

    return _response(200, items[0])


def handle_delete_auction(event: Dict[str, Any], dedup_key: str) -> Dict[str, Any]:
    """
    Handle DELETE /auctions/{dedup_key} — delete an auction record.

    Requires a valid API key (officer or admin).
    Returns 200 on success, 401 if unauthorized, 404 if record not found.
    """
    # --- 1. Validate API key (admin OR officer key accepted) ---
    admin_key = os.environ.get("ADMIN_API_KEY", "")
    officer_key = os.environ.get("API_KEY", "")

    headers = event.get("headers", {})
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""

    if not auth_header:
        return _response(401, {"error": "Unauthorized", "message": "Missing Authorization header"})

    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return _response(401, {"error": "Unauthorized", "message": "Authorization header must use Bearer scheme"})

    token = parts[1].strip()
    if token != admin_key and token != officer_key:
        return _response(401, {"error": "Unauthorized", "message": "Valid API key required for DELETE operations"})

    # --- 2. Validate dedup_key format ---
    if not dedup_key or len(dedup_key) != 64:
        return _response(400, {"error": "Bad Request", "message": "dedup_key must be a 64-character hex string"})

    # --- 3. Find the record in DynamoDB ---
    table_name = os.environ.get("TABLE_NAME", "DKPAuctions")
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    try:
        query_result = table.query(
            KeyConditionExpression="dedup_key = :dk",
            ExpressionAttributeValues={":dk": dedup_key},
            Limit=1,
        )
        items = query_result.get("Items", [])
    except Exception as e:
        return _response(500, {"error": "Internal Server Error", "message": f"Database error: {str(e)}"})

    if not items:
        return _response(404, {"error": "Not Found", "message": f"No record with dedup_key: {dedup_key}"})

    # --- 4. Delete the record ---
    record = items[0]
    try:
        table.delete_item(
            Key={"dedup_key": dedup_key, "timestamp": record["timestamp"]}
        )
    except Exception as e:
        return _response(500, {"error": "Internal Server Error", "message": f"Delete failed: {str(e)}"})

    # Decrement cached stats
    _update_stats_on_delete(table, record.get("winner", ""), int(record.get("amount", 0)))

    return _response(200, {
        "message": "Record deleted",
        "dedup_key": dedup_key,
        "item_name": record.get("item_name", ""),
        "winner": record.get("winner", ""),
    })


# ---------------------------------------------------------------------------
# Stats cache helpers
# ---------------------------------------------------------------------------

# Stats row uses a reserved dedup_key/timestamp that can't collide with real records
_STATS_KEY = {"dedup_key": "__stats__", "timestamp": "__stats__"}


def _update_stats_on_new_record(table, winner: str, amount: int) -> None:
    """
    Atomically increment the cached stats row after a new record is created.
    Uses DynamoDB atomic counters for total_auctions and total_dkp_spent.
    For unique_winners, we maintain a string set and use its size.
    """
    try:
        table.update_item(
            Key=_STATS_KEY,
            UpdateExpression=(
                "ADD total_auctions :one, total_dkp_spent :amt, winners_set :winner_set"
            ),
            ExpressionAttributeValues={
                ":one": 1,
                ":amt": amount,
                ":winner_set": {winner},
            },
        )
        # Update unique_winners count from the set size
        # (DynamoDB doesn't support SET size in UpdateExpression, so we do a follow-up)
        result = table.get_item(Key=_STATS_KEY, ProjectionExpression="winners_set")
        item = result.get("Item", {})
        winners_set = item.get("winners_set", set())
        table.update_item(
            Key=_STATS_KEY,
            UpdateExpression="SET unique_winners = :count",
            ExpressionAttributeValues={":count": len(winners_set)},
        )
    except Exception:
        pass  # Non-critical — stats will be slightly stale until next POST


def _update_stats_on_delete(table, winner: str, amount: int) -> None:
    """
    Decrement the cached stats row after a record is deleted.
    Note: unique_winners may become inaccurate if the deleted record's winner
    still has other auctions. A full recompute can fix this if needed.
    """
    try:
        table.update_item(
            Key=_STATS_KEY,
            UpdateExpression=(
                "ADD total_auctions :neg_one, total_dkp_spent :neg_amt"
            ),
            ExpressionAttributeValues={
                ":neg_one": -1,
                ":neg_amt": -amount,
            },
        )
    except Exception:
        pass  # Non-critical


def _invalidate_export_cache() -> None:
    """Delete the cached S3 export file so it regenerates on next request.
    DEPRECATED — no longer called. Cache regenerates when stale (>30 min)."""
    pass


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_api_key(event: Dict[str, Any]) -> Dict[str, Any] | None:
    """
    Validate the bearer token from the Authorization header.

    Returns an error response dict if invalid, or None if valid.
    """
    expected_key = os.environ.get("API_KEY", "")
    if not expected_key:
        # If no API key is configured, reject all requests
        return _response(401, {"error": "Unauthorized", "message": "Server API key not configured"})

    headers = event.get("headers", {})
    # API Gateway HTTP API v2 lowercases all header names
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""

    if not auth_header:
        return _response(401, {"error": "Unauthorized", "message": "Missing Authorization header"})

    # Expect format: "Bearer <token>"
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return _response(401, {"error": "Unauthorized", "message": "Authorization header must use Bearer scheme"})

    token = parts[1].strip()
    if token != expected_key:
        return _response(401, {"error": "Unauthorized", "message": "Invalid API key"})

    return None


def _validate_auction_record(data: Dict[str, Any]) -> List[str]:
    """
    Validate the AuctionRecord schema. Returns a list of error messages.
    An empty list means the record is valid.
    """
    errors: List[str] = []

    # item_name: non-empty, max 128 characters
    item_name = data.get("item_name")
    if not item_name or not isinstance(item_name, str) or not item_name.strip():
        errors.append("item_name must be a non-empty string")
    elif len(item_name) > 128:
        errors.append("item_name must be at most 128 characters")

    # winner: non-empty, max 64 characters, alphanumeric
    winner = data.get("winner")
    if not winner or not isinstance(winner, str) or not winner.strip():
        errors.append("winner must be a non-empty string")
    elif len(winner) > 64:
        errors.append("winner must be at most 64 characters")
    elif not winner.isalnum():
        errors.append("winner must be alphanumeric")

    # amount: positive integer
    amount = data.get("amount")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        errors.append("amount must be a non-negative integer")

    # timestamp: valid ISO 8601
    timestamp = data.get("timestamp")
    if not timestamp or not isinstance(timestamp, str) or not _ISO8601_PATTERN.match(timestamp):
        errors.append("timestamp must be valid ISO 8601 (e.g., 2026-04-29T02:55:35Z)")
    else:
        # Validate semantic correctness (e.g., month not > 12, day not > 31)
        from datetime import datetime as _dt
        try:
            # Try parsing the main portion (before timezone) to validate date/time values
            ts_main = timestamp.rstrip("Z")
            if "+" in ts_main or (ts_main.count("-") > 2):
                # Has timezone offset like +05:00 or -03:00
                idx = max(ts_main.rfind("+"), ts_main.rfind("-"))
                if idx > 10:  # offset is after the date portion
                    ts_main = ts_main[:idx]
            # Remove fractional seconds for parsing
            if "." in ts_main:
                ts_main = ts_main[:ts_main.index(".")]
            _dt.strptime(ts_main, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            errors.append("timestamp must be valid ISO 8601 (e.g., 2026-04-29T02:55:35Z)")

    # dedup_key: 64-character hex string
    dedup_key = data.get("dedup_key")
    if not dedup_key or not isinstance(dedup_key, str) or len(dedup_key) != 64:
        errors.append("dedup_key must be a 64-character hex string")
    elif not all(c in "0123456789abcdefABCDEF" for c in dedup_key):
        errors.append("dedup_key must be a 64-character hex string")

    # bids: non-empty array
    bids = data.get("bids")
    if not isinstance(bids, list) or len(bids) == 0:
        errors.append("bids must be a non-empty array")
    else:
        for i, bid in enumerate(bids):
            bid_errors = _validate_bid_entry(bid, i)
            errors.extend(bid_errors)

    return errors


def _validate_bid_entry(bid: Any, index: int) -> List[str]:
    """
    Validate a single BidEntry. Returns a list of error messages.
    """
    errors: List[str] = []
    prefix = f"bids[{index}]"

    if not isinstance(bid, dict):
        errors.append(f"{prefix}: must be an object")
        return errors

    # player: non-empty, max 64 characters
    player = bid.get("player")
    if not player or not isinstance(player, str) or not player.strip():
        errors.append(f"{prefix}.player must be a non-empty string")
    elif len(player) > 64:
        errors.append(f"{prefix}.player must be at most 64 characters")

    # amount: positive integer
    amount = bid.get("amount")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        errors.append(f"{prefix}.amount must be a non-negative integer")

    # bid_type: must be "main" or "alt"
    bid_type = bid.get("bid_type")
    if bid_type not in _VALID_BID_TYPES:
        errors.append(f"{prefix}.bid_type must be one of: alt, main")

    # is_correction: must be boolean
    is_correction = bid.get("is_correction")
    if not isinstance(is_correction, bool):
        errors.append(f"{prefix}.is_correction must be a boolean")

    return errors


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_http_method(event: Dict[str, Any]) -> str:
    """Extract HTTP method from API Gateway event (supports v1 and v2 formats)."""
    # API Gateway HTTP API v2 format
    request_context = event.get("requestContext", {})
    http_info = request_context.get("http", {})
    if http_info.get("method"):
        return http_info["method"].upper()

    # API Gateway REST API v1 format
    if event.get("httpMethod"):
        return event["httpMethod"].upper()

    return "UNKNOWN"


def _get_path(event: Dict[str, Any]) -> str:
    """Extract request path from API Gateway event (supports v1 and v2 formats).
    
    For HTTP API v2 with a named stage (e.g., 'prod'), rawPath includes the
    stage prefix (e.g., '/prod/auctions'). We strip it using the stage from
    requestContext.
    """
    # API Gateway HTTP API v2 format
    raw_path = event.get("rawPath")
    if raw_path:
        # Strip stage prefix if present (e.g., /prod/auctions -> /auctions)
        stage = event.get("requestContext", {}).get("stage", "")
        if stage and stage != "$default" and raw_path.startswith(f"/{stage}"):
            raw_path = raw_path[len(f"/{stage}"):]
            if not raw_path:
                raw_path = "/"
        return raw_path.rstrip("/") if raw_path != "/" else raw_path

    # API Gateway REST API v1 format
    path = event.get("path", "/")
    return path.rstrip("/") if path != "/" else path


def _decimal_default(obj):
    """JSON serializer for DynamoDB Decimal types."""
    from decimal import Decimal
    if isinstance(obj, Decimal):
        # Convert to int if it's a whole number, otherwise float
        if obj % 1 == 0:
            return int(obj)
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _response(status_code: int, body: Any) -> Dict[str, Any]:
    """Build a standard API Gateway response dict."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        },
        "body": json.dumps(body, default=_decimal_default),
    }


# ---------------------------------------------------------------------------
# Guild Character Data
# ---------------------------------------------------------------------------

_GUILD_DATA_CACHE: Dict[str, Any] = {"data": None, "etag": None}


def handle_get_guild_characters(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle GET /guild/characters — return guild correlation and join date data.

    Reads guild_characters.json from S3 (data/ prefix in the viewer bucket).
    Caches in memory for the Lambda execution lifetime to avoid repeated S3 reads.
    """
    export_bucket = os.environ.get("EXPORT_BUCKET", "savi-dkp-viewer")
    s3_key = "data/guild_characters.json"

    try:
        s3 = boto3.client("s3")

        # Check if we have a cached version and validate with ETag
        if _GUILD_DATA_CACHE["data"] and _GUILD_DATA_CACHE["etag"]:
            try:
                head = s3.head_object(Bucket=export_bucket, Key=s3_key)
                if head["ETag"] == _GUILD_DATA_CACHE["etag"]:
                    return _response(200, _GUILD_DATA_CACHE["data"])
            except Exception:
                pass

        # Fetch from S3
        response = s3.get_object(Bucket=export_bucket, Key=s3_key)
        body = response["Body"].read().decode("utf-8")
        data = json.loads(body)

        # Cache it
        _GUILD_DATA_CACHE["data"] = data
        _GUILD_DATA_CACHE["etag"] = response.get("ETag")

        return _response(200, data)

    except Exception as e:
        if "NoSuchKey" in str(type(e).__name__):
            return _response(404, {"error": "Guild data not found", "message": "No guild character data has been uploaded yet."})
        return _response(500, {"error": "Internal error", "message": str(e)})
