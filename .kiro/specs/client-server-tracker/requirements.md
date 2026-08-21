# Requirements Document

## Introduction

This feature evolves the DKP bid tracker from a local-only architecture to a client-server model. Officers continue running a local client (`.exe`) that monitors EverQuest log files, parses bid auctions in real-time, and displays a live bid table in the console/terminal window for manual awards via `gratss`. In addition to local operation, the client pushes closed auction records to a lightweight cloud backend. The backend stores history centrally and serves a web-based viewer so guild members can browse auction history from any browser without needing local files.

Key constraints: the local console experience remains front-and-center for raid use (live bid table, gratss commands, all existing keyboard interaction), cloud sync happens in the background without blocking or degrading local operation, the hosted backend stays within free-tier costs, and the `.exe` provides a zero-config double-click startup for non-technical officers on Windows.

---

## Glossary

- **DKP_Client**: The local Windows application (`.exe` bundled via PyInstaller) that wraps the existing `dkptrackerv3.py` logic. Provides the console bid table, handles gratss/ungratss commands, and pushes closed auction records to the Backend_API in the background.
- **Backend_API**: The serverless REST API (AWS Lambda + API Gateway) that receives, deduplicates, persists, and serves auction records.
- **Web_Viewer**: The static frontend (HTML/CSS/JS hosted on S3/CloudFront) that fetches and displays auction history from the Backend_API.
- **Offline_Queue**: A local persistence mechanism within the DKP_Client that stores unsent auction records when the network is unavailable and retries delivery later.
- **Auction_Record**: A JSON object representing a single closed auction, containing item name, winner, winning amount, timestamp, raid session ID, dedup key, log source, and full bid history.
- **Bid_Entry**: A single bid within an Auction_Record: player name, amount, bid type ("main" or "alt"), and is_correction flag.
- **Dedup_Key**: A SHA-256 hash of `(item_name + winner + amount + raid_session_id)` separated by null bytes, used to prevent duplicate records when multiple officers push the same auction.
- **Raid_Session_ID**: A deterministic identifier derived by hashing the calendar date from the first EQ log timestamp, used to correlate records from multiple officers logging the same raid.
- **API_Key**: A shared bearer token used to authenticate DKP_Client requests to the Backend_API.
- **Client_Config**: An INI file (`dkp_client.ini`) located beside the `.exe` that stores API URL, API key, log directory path, and timing parameters.

---

## Requirements

### Requirement 1: Local Console Bid Tracking Preserved

**User Story:** As a DKP officer, I want the `.exe` client to provide the same live console/terminal bid table and manual award commands I use today, so that my raid workflow is unchanged.

#### Acceptance Criteria

1. WHEN the DKP_Client starts, THE DKP_Client SHALL open a console/terminal window and begin monitoring the active EQ log file using the same logic as `dkptrackerv3.py`.
2. WHILE the DKP_Client is running, THE DKP_Client SHALL display a live bid table in the console that updates in real-time as bids are received from the EQ log.
3. WHEN a `gratss` command is detected in the EQ log, THE DKP_Client SHALL close the auction locally (update console display) and trigger a background push of the Auction_Record to the Backend_API.
4. WHEN an `ungratss` command is detected in the EQ log, THE DKP_Client SHALL reopen the auction locally and restore the bid table to its prior state.
5. WHEN a `clear` command is detected in the EQ log, THE DKP_Client SHALL remove the item from the bid table without awarding it.
6. THE DKP_Client SHALL support all existing bid patterns including alt bids, bid retractions, auction announcements, and priority rolls without behavioral changes.

---

### Requirement 2: Background Cloud Sync

**User Story:** As a DKP officer, I want closed auction records pushed to the cloud automatically in the background, so that the guild history stays up to date without any extra steps from me.

#### Acceptance Criteria

1. WHEN an auction is closed via `gratss`, THE DKP_Client SHALL push the Auction_Record to the Backend_API over HTTPS without blocking the console log monitor or bid table display.
2. WHILE the DKP_Client is pushing a record to the Backend_API, THE DKP_Client SHALL continue processing new log lines and updating the bid table with no perceptible delay.
3. WHEN the Backend_API returns HTTP 201 (Created), THE DKP_Client SHALL consider the record successfully synced.
4. WHEN the Backend_API returns HTTP 200 (duplicate acknowledged), THE DKP_Client SHALL consider the record successfully synced and take no further action.
5. IF the network request fails (timeout, connection error, or HTTP 5xx), THEN THE DKP_Client SHALL enqueue the record in the Offline_Queue for later retry.

---

### Requirement 3: Offline Queue and Retry

**User Story:** As a DKP officer, I want auction records queued locally when my internet drops during a raid, so that no data is lost and records sync automatically once connectivity returns.

#### Acceptance Criteria

1. WHEN a push to the Backend_API fails, THE Offline_Queue SHALL persist the Auction_Record to a local file (`pending_uploads.json`) immediately.
2. WHILE the Offline_Queue contains pending records, THE DKP_Client SHALL attempt to flush them every 30 seconds.
3. WHEN a queued record is successfully pushed (HTTP 201 or 200), THE Offline_Queue SHALL remove that record from the pending file.
4. THE Offline_Queue SHALL preserve FIFO ordering so that records are pushed to the server in the order they were closed.
5. IF the Backend_API returns HTTP 401 (Unauthorized) for a queued record, THEN THE DKP_Client SHALL stop retrying and display a console message instructing the officer to check the API key configuration.
6. IF the Backend_API returns HTTP 422 (Unprocessable Entity) for a queued record, THEN THE DKP_Client SHALL move the record to a dead-letter file (`failed_uploads.json`) and log the error to the console.

---

### Requirement 4: Client Configuration

**User Story:** As a DKP officer, I want to configure the client by editing a simple INI file next to the `.exe`, so that I can set my guild's API URL and key without touching code.

#### Acceptance Criteria

1. WHEN the DKP_Client starts, THE DKP_Client SHALL read configuration from `dkp_client.ini` located in the same directory as the executable.
2. THE Client_Config SHALL contain the following fields: `api_url` (string), `api_key` (string), `log_directory` (string), `poll_interval` (float, default 1.0 seconds), and `retry_interval` (float, default 30.0 seconds).
3. IF `dkp_client.ini` does not exist, THEN THE DKP_Client SHALL create a template INI file with placeholder values and print a console message instructing the officer to fill in the API URL and API key.
4. IF required fields (`api_url`, `api_key`, `log_directory`) are missing or empty, THEN THE DKP_Client SHALL print an error message to the console identifying the missing fields and continue operating in local-only mode (no cloud sync).

---

### Requirement 5: Windows Executable Packaging

**User Story:** As a DKP officer, I want to double-click a single `.exe` file to start the tracker, so that I don't need to install Python or manage dependencies.

#### Acceptance Criteria

1. THE DKP_Client SHALL be packaged as a single Windows `.exe` file using PyInstaller that bundles the Python runtime and all dependencies.
2. WHEN the `.exe` is launched, THE DKP_Client SHALL open a console window and begin operation without requiring any installed Python environment on the officer's machine.
3. THE DKP_Client SHALL bundle the following dependencies: `requests`, `rapidfuzz`, and the Python standard library.
4. THE DKP_Client SHALL function correctly on Windows 10 and Windows 11.

---

### Requirement 6: Backend API — Record Ingestion

**User Story:** As a system component, the Backend_API needs to accept auction records from clients, validate them, deduplicate, and persist them, so that guild history is centrally stored.

#### Acceptance Criteria

1. WHEN a POST request is received at `/auctions` with a valid Auction_Record and valid API_Key, THE Backend_API SHALL persist the record to the database and return HTTP 201.
2. WHEN a POST request is received with an Auction_Record whose Dedup_Key matches an existing record, THE Backend_API SHALL return HTTP 200 without creating a duplicate.
3. WHEN a POST request is received with an invalid or missing API_Key, THE Backend_API SHALL return HTTP 401 with an error message.
4. WHEN a POST request is received with an Auction_Record that fails schema validation, THE Backend_API SHALL return HTTP 422 with a descriptive error message identifying the invalid fields.
5. THE Backend_API SHALL validate that `item_name` is non-empty and at most 128 characters, `winner` is non-empty and at most 64 alphanumeric characters, `amount` is a positive integer, `timestamp` is valid ISO 8601, `dedup_key` is a 64-character hex string, and `bids` is a non-empty array.
6. THE Backend_API SHALL validate each Bid_Entry: `player` is non-empty and at most 64 characters, `amount` is a positive integer, `bid_type` is "main" or "alt", and `is_correction` is boolean.

---

### Requirement 7: Backend API — Record Retrieval

**User Story:** As the Web_Viewer, I need to fetch auction records from the Backend_API with filtering and pagination, so that I can display history efficiently without loading the entire dataset at once.

#### Acceptance Criteria

1. WHEN a GET request is received at `/auctions`, THE Backend_API SHALL return a paginated list of Auction_Records ordered by timestamp descending.
2. THE Backend_API SHALL support the following query parameters: `since` (ISO 8601 timestamp), `session` (raid_session_id), `limit` (integer, default 50, max 100), and `cursor` (opaque pagination token).
3. WHEN the `since` parameter is provided, THE Backend_API SHALL return only records with a timestamp after the specified value.
4. WHEN the `session` parameter is provided, THE Backend_API SHALL return only records matching that Raid_Session_ID.
5. WHEN more records exist beyond the current page, THE Backend_API SHALL include a `next_cursor` field in the response that the client can use to fetch the next page.
6. WHEN a GET request is received at `/auctions/stats`, THE Backend_API SHALL return aggregate statistics: total auction count, total DKP spent, and unique winner count.

---

### Requirement 8: Backend API — Authentication and Security

**User Story:** As a guild leader, I want the API secured by a shared key so that only our officers can push records, while auction history remains readable by guild members.

#### Acceptance Criteria

1. THE Backend_API SHALL require a valid bearer token (API_Key) in the `Authorization` header for all POST requests.
2. THE Backend_API SHALL allow GET requests to `/auctions` and `/auctions/stats` without authentication so that guild members can view history.
3. THE Backend_API SHALL enforce HTTPS for all endpoints via API Gateway configuration.
4. THE Backend_API SHALL enforce a rate limit of 100 requests per second per source IP to prevent abuse.
5. IF a request exceeds the rate limit, THEN THE Backend_API SHALL return HTTP 429 (Too Many Requests).

---

### Requirement 9: Auction Record Data Format

**User Story:** As a developer, I want the Auction_Record format used in API communication to be identical to the existing `dkp_history.json` record format, so that backward compatibility is maintained.

#### Acceptance Criteria

1. THE Backend_API SHALL accept and return Auction_Records with the following fields: `item_name` (string), `winner` (string), `amount` (integer), `timestamp` (ISO 8601 string), `raid_session_id` (string), `dedup_key` (string), `log_source` (string), and `bids` (array of Bid_Entry objects).
2. THE Backend_API SHALL accept and return each Bid_Entry with the following fields: `player` (string), `amount` (integer), `bid_type` (string: "main" or "alt"), and `is_correction` (boolean).
3. FOR ALL valid Auction_Records, serializing with `json.dumps()` and deserializing with `json.loads()` SHALL produce an equivalent in-memory structure (round-trip property).

---

### Requirement 10: Web Viewer — Auction History Display

**User Story:** As a guild member, I want to browse auction history in a web browser, so that I can see who won items and how much was bid without needing the officer's local files.

#### Acceptance Criteria

1. WHEN the Web_Viewer is loaded, THE Web_Viewer SHALL fetch the most recent 50 Auction_Records from the Backend_API and display them in a table.
2. THE Web_Viewer SHALL display for each Auction_Record: item name, winner, winning amount, auction date, and the number of bids placed.
3. WHEN a user clicks on an Auction_Record row, THE Web_Viewer SHALL expand the row to show the full bid history with player names, amounts, bid types, and correction indicators.
4. THE Web_Viewer SHALL provide a search/filter input that filters displayed records by item name or player name.
5. THE Web_Viewer SHALL support pagination controls to load older records using the `next_cursor` from the Backend_API response.
6. THE Web_Viewer SHALL provide a manual refresh button and optionally auto-refresh every 60 seconds to display newly synced auctions.

---

### Requirement 11: Web Viewer — Hosting and Delivery

**User Story:** As a guild leader, I want the web viewer hosted publicly so any guild member can access it with just a URL.

#### Acceptance Criteria

1. THE Web_Viewer SHALL be deployed as static files (HTML, CSS, JavaScript) hosted on AWS S3 behind CloudFront CDN.
2. THE Web_Viewer SHALL load auction data exclusively from the Backend_API endpoint configured at build time.
3. THE Web_Viewer SHALL function correctly in the latest stable versions of Chrome, Firefox, and Edge.
4. WHEN the Backend_API is unreachable, THE Web_Viewer SHALL display an error message indicating the service is temporarily unavailable and offer a retry button.

---

### Requirement 12: Server-Side Deduplication

**User Story:** As a DKP officer running alongside other officers, I want the server to automatically discard duplicate records so that each auction appears exactly once in the history regardless of how many clients push it.

#### Acceptance Criteria

1. THE Backend_API SHALL compute deduplication using the Dedup_Key field provided in the incoming Auction_Record.
2. WHEN storing an Auction_Record, THE Backend_API SHALL check whether a record with the same Dedup_Key already exists in the database.
3. IF a matching Dedup_Key already exists, THEN THE Backend_API SHALL return HTTP 200 without creating a new record.
4. IF no matching Dedup_Key exists, THEN THE Backend_API SHALL persist the new record and return HTTP 201.

