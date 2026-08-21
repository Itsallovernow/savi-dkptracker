# Implementation Plan: Client-Server DKP Bid Tracker

## Overview

This plan implements the migration from a local-only DKP bid tracker to a client-server model. The local Windows client (`.exe`) continues to provide real-time console bid tracking while pushing closed auction records to a serverless AWS backend. A static web viewer lets guild members browse auction history. The implementation is split into: data models/interfaces, client-side sync and offline queue, backend API (Lambda + DynamoDB), web viewer, and packaging.

## Tasks

- [x] 1. Define shared data models and configuration
  - [x] 1.1 Create `models.py` with AuctionRecord and BidEntry dataclasses
    - Define `AuctionRecord` dataclass with fields: `item_name`, `winner`, `amount`, `timestamp`, `raid_session_id`, `dedup_key`, `log_source`, `bids`
    - Define `BidEntry` dataclass with fields: `player`, `amount`, `bid_type`, `is_correction`
    - Implement `to_dict()` and `from_dict()` serialization methods
    - Implement `compute_dedup_key(item_name, winner, amount, raid_session_id)` using SHA-256 of null-byte-separated values
    - Implement `compute_raid_session_id(first_log_timestamp)` using SHA-256 of the calendar date
    - Implement validation methods that check all field constraints (non-empty, max length, positive integer, valid ISO 8601, 64-char hex dedup_key, valid bid_type)
    - _Requirements: 9.1, 9.2, 6.5, 6.6_

  - [x] 1.2 Write property test for AuctionRecord serialization round-trip
    - **Property 14: Auction record serialization round-trip**
    - **Validates: Requirements 9.1, 9.3**

  - [x] 1.3 Create `client_config.py` with ClientConfig and INI parsing
    - Define `ClientConfig` dataclass with fields: `api_url`, `api_key`, `log_directory`, `poll_interval` (default 1.0), `retry_interval` (default 30.0)
    - Implement `load_config(ini_path)` that reads from INI file using `configparser`
    - If INI file does not exist, create a template with placeholder values and print instructions
    - If required fields are missing/empty, print error identifying missing fields and return config with `local_only=True` flag
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [x] 1.4 Write property test for missing config fields
    - **Property 5: Missing config fields enable local-only mode**
    - **Validates: Requirements 4.4**

- [x] 2. Implement offline queue
  - [x] 2.1 Create `offline_queue.py` with OfflineQueue class
    - Implement `enqueue(record: AuctionRecord)` that appends to `pending_uploads.json` immediately
    - Implement `flush(push_fn)` that attempts to send all pending records in FIFO order
    - Implement `pending_count` property
    - On successful push (HTTP 201 or 200), remove the record from the file
    - On HTTP 401, stop retrying and display console message about API key
    - On HTTP 422, move record to `failed_uploads.json` and log error
    - Use file locking to prevent corruption from concurrent access
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 2.2 Write property test for offline queue FIFO ordering
    - **Property 4: Offline queue preserves FIFO ordering**
    - **Validates: Requirements 3.4**

  - [x] 2.3 Write property test for network failure enqueue
    - **Property 2: Network failure enqueues to offline queue**
    - **Validates: Requirements 2.5, 3.1**

  - [x] 2.4 Write property test for successful push removes from queue
    - **Property 3: Successful push removes from offline queue**
    - **Validates: Requirements 3.3**

- [x] 3. Implement DKP client cloud sync integration
  - [x] 3.1 Create `api_client.py` with push_record and retry logic
    - Implement `push_record(record, api_url, api_key)` that POSTs to `/auctions` with bearer token
    - Return success on HTTP 201 or 200
    - Raise appropriate exceptions on timeout, connection error, HTTP 5xx
    - Handle HTTP 401 and 422 with distinct error types
    - Use `requests` library with configurable timeout
    - _Requirements: 2.1, 2.3, 2.4, 2.5_

  - [x] 3.2 Create `dkp_client.py` wrapping existing tracker with cloud sync
    - Import and reuse existing `BidTracker`, `process_line`, `monitor_log` from `dkptrackerv3.py`
    - Load config from `dkp_client.ini` on startup
    - Hook into `close_item` to trigger background push via threading
    - Start a background timer thread for offline queue flush (every `retry_interval` seconds)
    - Ensure log monitoring continues unblocked during network operations
    - If config is local-only mode, skip all cloud sync but keep console tracking working
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2_

  - [x] 3.3 Write property test for non-blocking push
    - **Property 1: Auction close triggers non-blocking push**
    - **Validates: Requirements 1.3, 2.1**

- [x] 4. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement Backend API — Lambda function
  - [x] 5.1 Create `backend/handler.py` with Lambda request routing
    - Set up Lambda handler that routes based on HTTP method and path
    - Route POST `/auctions` to ingestion handler
    - Route GET `/auctions` to retrieval handler
    - Route GET `/auctions/stats` to stats handler
    - Return 404 for unmatched routes
    - _Requirements: 6.1, 7.1, 7.6_

  - [x] 5.2 Implement POST `/auctions` ingestion with validation and dedup
    - Parse request body as JSON
    - Validate API key from `Authorization` header (bearer token)
    - Return HTTP 401 if key is invalid or missing
    - Validate AuctionRecord schema: `item_name` non-empty ≤128 chars, `winner` non-empty ≤64 alphanumeric, `amount` positive int, `timestamp` valid ISO 8601, `dedup_key` 64-char hex, `bids` non-empty array
    - Validate each BidEntry: `player` non-empty ≤64 chars, `amount` positive int, `bid_type` in ("main", "alt"), `is_correction` boolean
    - Return HTTP 422 with descriptive error on validation failure
    - Check DynamoDB for existing record with same `dedup_key`
    - If exists, return HTTP 200 (duplicate acknowledged)
    - If new, persist to DynamoDB and return HTTP 201
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 8.1, 12.1, 12.2, 12.3, 12.4_

  - [x] 5.3 Implement GET `/auctions` retrieval with filtering and pagination
    - Query DynamoDB ordered by timestamp descending
    - Support `since` param: filter records with timestamp after value
    - Support `session` param: filter by `raid_session_id`
    - Support `limit` param: default 50, max 100
    - Support `cursor` param: opaque pagination token (DynamoDB LastEvaluatedKey, base64-encoded)
    - Include `next_cursor` in response when more records exist
    - Allow unauthenticated access (no API key required for GET)
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 8.2_

  - [x] 5.4 Implement GET `/auctions/stats` aggregate endpoint
    - Query DynamoDB to compute: total auction count, total DKP spent (sum of `amount`), unique winner count
    - Return JSON response with `total_auctions`, `total_dkp_spent`, `unique_winners`
    - Allow unauthenticated access
    - _Requirements: 7.6, 8.2_

  - [x] 5.5 Write property test for valid records persisted (201)
    - **Property 6: Valid auction records are persisted (201)**
    - **Validates: Requirements 6.1, 12.4**

  - [x] 5.6 Write property test for deduplication idempotency
    - **Property 7: Deduplication is idempotent (200 on duplicate)**
    - **Validates: Requirements 6.2, 12.2, 12.3**

  - [x] 5.7 Write property test for invalid authentication rejected
    - **Property 8: Invalid authentication is rejected (401)**
    - **Validates: Requirements 6.3, 8.1**

  - [x] 5.8 Write property test for invalid records rejected (422)
    - **Property 9: Invalid records are rejected (422)**
    - **Validates: Requirements 6.4, 6.5, 6.6**

  - [x] 5.9 Write property test for query ordering
    - **Property 10: Query results are ordered by timestamp descending**
    - **Validates: Requirements 7.1**

  - [x] 5.10 Write property test for query filters
    - **Property 11: Query filters return only matching records**
    - **Validates: Requirements 7.3, 7.4**

  - [x] 5.11 Write property test for pagination cursor
    - **Property 12: Pagination cursor present when more records exist**
    - **Validates: Requirements 7.5**

  - [x] 5.12 Write property test for stats endpoint
    - **Property 13: Stats endpoint matches raw data computation**
    - **Validates: Requirements 7.6**

- [x] 6. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement Backend Infrastructure (IaC)
  - [x] 7.1 Create `backend/template.yaml` SAM/CloudFormation template
    - Define Lambda function resource (Python 3.12 runtime)
    - Define API Gateway HTTP API with routes: POST /auctions, GET /auctions, GET /auctions/stats
    - Define DynamoDB table with `dedup_key` as partition key and `timestamp` as sort key (GSI for queries)
    - Configure API Gateway rate limiting at 100 req/s per IP
    - Enforce HTTPS via API Gateway
    - _Requirements: 8.3, 8.4, 8.5_

  - [x] 7.2 Create `backend/requirements.txt` with Lambda dependencies
    - Include `boto3` (AWS SDK)
    - Pin versions for reproducibility
    - _Requirements: 6.1_

- [x] 8. Implement Static Web Viewer
  - [x] 8.1 Create `web/index.html` with auction history table structure
    - Build HTML page with header, search input, auction table, pagination controls, and refresh button
    - Include expandable row detail for bid history
    - Ensure semantic HTML for accessibility
    - _Requirements: 10.1, 10.2, 10.3, 10.5, 10.6_

  - [x] 8.2 Create `web/style.css` with responsive styling
    - Style auction table with zebra striping and hover states
    - Style expandable bid history rows
    - Add responsive breakpoints for mobile/desktop
    - Style filter input, pagination buttons, and refresh controls
    - _Requirements: 10.1, 11.3_

  - [x] 8.3 Create `web/app.js` with API integration and UI logic
    - Fetch most recent 50 records from Backend_API on page load
    - Render auction table with item name, winner, amount, date, bid count
    - Implement row click to expand/collapse full bid history
    - Implement client-side search/filter by item name or player name (case-insensitive substring match)
    - Implement pagination using `next_cursor` from API response
    - Implement manual refresh button and optional 60-second auto-refresh
    - Display error message with retry button when API is unreachable
    - Configure API URL at build time (environment variable or config)
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 11.2, 11.4_

  - [x] 8.4 Write unit tests for web viewer filter logic
    - **Property 16: Web viewer search filter correctness**
    - **Validates: Requirements 10.4**

- [x] 9. Implement Windows executable packaging
  - [x] 9.1 Create `dkp_client.spec` PyInstaller spec file
    - Configure single-file `.exe` bundling
    - Include `requests`, `rapidfuzz`, and all standard library dependencies
    - Set console mode (not windowed) so terminal window opens on launch
    - Bundle `dkp_client.py` as entry point
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 9.2 Create `build.bat` script for building the `.exe`
    - Run PyInstaller with the spec file
    - Copy output `.exe` to a `dist/` folder
    - Print build completion message
    - _Requirements: 5.1_

- [x] 10. Integration wiring and final assembly
  - [x] 10.1 Wire `dkp_client.py` entry point end-to-end
    - Ensure startup flow: load config → init BidTracker → start log monitor → start retry timer
    - Ensure `gratss` triggers: close_item → build AuctionRecord → push_record (background thread) → on failure enqueue
    - Ensure `ungratss` triggers: reopen_item locally (no server interaction needed)
    - Ensure `clear` triggers: clear_item locally (no server interaction needed)
    - Verify all existing console commands work unchanged
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2_

  - [x] 10.2 Write integration tests for end-to-end client flow
    - Test: parse log → close auction → record pushed to mock API
    - Test: push fails → record enqueued → retry succeeds → record removed from queue
    - Test: multiple officers push same auction → server deduplicates
    - _Requirements: 2.1, 2.5, 3.1, 3.3, 12.2_

- [x] 11. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The backend uses AWS SAM for infrastructure-as-code
- The client reuses the existing `dkptrackerv3.py` logic directly — no rewrite of bid parsing

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.3"] },
    { "id": 1, "tasks": ["1.2", "1.4", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "3.1"] },
    { "id": 3, "tasks": ["3.2", "5.1", "7.1", "7.2"] },
    { "id": 4, "tasks": ["3.3", "5.2", "5.3", "5.4", "8.1", "8.2"] },
    { "id": 5, "tasks": ["5.5", "5.6", "5.7", "5.8", "5.9", "5.10", "5.11", "5.12", "8.3", "9.1"] },
    { "id": 6, "tasks": ["8.4", "9.2", "10.1"] },
    { "id": 7, "tasks": ["10.2"] }
  ]
}
```
