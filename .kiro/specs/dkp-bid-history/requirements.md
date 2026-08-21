# Requirements Document

## Introduction

This feature adds persistent bid history tracking and a browser-based viewer to the existing EverQuest DKP auction monitor (`dkptrackerv3.py`). When an auction closes, the full bid history is written to a JSON file. A standalone import script can replay existing log files to back-fill history. A self-contained HTML viewer lets officers and players browse item auction history, player bid records, and aggregate statistics — with the ability to import additional log files directly in the browser.

The system consists of three components:

- **DKP_Logger** — a Python module (`dkp_logger.py`) imported by `dkptrackerv3.py` that appends closed auction records to `dkp_history.json`.
- **DKP_Importer** — a standalone CLI script (`dkp_import.py`) that replays one or more EQ log files and populates `dkp_history.json` without running the live monitor.
- **DKP_Viewer** — a self-contained HTML file (`dkp_history.html`) that reads `dkp_history.json` and provides interactive browsing, player views, statistics, and browser-side log import.

---

## Glossary

- **Auction Record**: A single closed auction event, containing item name, winner, winning amount, timestamp, raid session ID, and the full ordered bid history.
- **Bid Entry**: A single bid within an Auction Record: `(player, amount, bid_type, is_correction)` where `bid_type` is `"main"` or `"alt"` and `is_correction` is a boolean.
- **Raid Session ID**: A deterministic identifier derived by hashing the date portion of the first timestamp found in a given log file. Used to correlate records from multiple officers logging the same raid.
- **Dedup Key**: A SHA-256 hash of `(item_name + winner + amount + raid_session_id)` used to prevent duplicate Auction Records when multiple officers run the monitor simultaneously.
- **Drop Card**: A visual card in the DKP_Viewer representing one Auction Record for a specific item drop instance.
- **DKP_Logger**: The `dkp_logger.py` Python module responsible for persisting Auction Records to `dkp_history.json`.
- **DKP_Importer**: The `dkp_import.py` CLI script that replays log files to back-fill `dkp_history.json`.
- **DKP_Viewer**: The `dkp_history.html` self-contained browser application for viewing and exploring auction history.
- **History File**: The `dkp_history.json` file that stores all persisted Auction Records.
- **Player Profile**: An aggregated view of all bids placed by a single player across all Auction Records.
- **Win Rate**: The ratio of auctions won to auctions bid on by a player, expressed as a percentage.
- **Contested Item**: An item whose Auction Records have a high count of unique bidders across all drops.

---

## Requirements

### Requirement 1: Auction Record Persistence

**User Story:** As a DKP officer, I want each closed auction to be automatically saved to disk, so that bid history is never lost between sessions.

#### Acceptance Criteria

1. WHEN `close_item()` is called in `dkptrackerv3.py`, THE DKP_Logger SHALL write an Auction Record to `dkp_history.json` before `close_item()` returns.
2. THE DKP_Logger SHALL store each Auction Record with the following fields: `item_name` (string), `winner` (string), `amount` (integer), `timestamp` (ISO 8601 UTC string), `raid_session_id` (string), `dedup_key` (string), and `bids` (ordered array of Bid Entries).
3. THE DKP_Logger SHALL store each Bid Entry as an object with fields: `player` (string), `amount` (integer), `bid_type` (string, `"main"` or `"alt"`), and `is_correction` (boolean).
4. THE DKP_Logger SHALL preserve the chronological order of Bid Entries exactly as received from `close_item()`.
5. WHEN `dkp_history.json` does not exist, THE DKP_Logger SHALL create it with a valid JSON structure before writing the first Auction Record.
6. WHEN `dkp_history.json` already exists, THE DKP_Logger SHALL append the new Auction Record without modifying any existing records.
7. IF a file I/O error occurs while writing, THEN THE DKP_Logger SHALL log the error to stderr and allow `dkptrackerv3.py` to continue operating without interruption.

---

### Requirement 2: Deduplication of Concurrent Officer Logs

**User Story:** As a DKP officer, I want duplicate auction records to be silently discarded when multiple officers run the monitor simultaneously, so that each auction appears exactly once in the history.

#### Acceptance Criteria

1. THE DKP_Logger SHALL compute the Raid Session ID by extracting the calendar date (YYYY-MM-DD) from the first EQ log timestamp found in the active log file and hashing it with SHA-256.
2. THE DKP_Logger SHALL compute the Dedup Key as the SHA-256 hash of the concatenation of `item_name`, `winner`, `str(amount)`, and `raid_session_id`, separated by a null byte (`\x00`).
3. WHEN an Auction Record is about to be written, THE DKP_Logger SHALL check whether any existing record in `dkp_history.json` shares the same Dedup Key.
4. IF a matching Dedup Key already exists, THEN THE DKP_Logger SHALL discard the new record and log a single-line notice to stdout identifying the duplicate.
5. IF no matching Dedup Key exists, THEN THE DKP_Logger SHALL write the new Auction Record.

---

### Requirement 3: Log File Replay and History Import

**User Story:** As a DKP officer, I want to replay existing log files to back-fill auction history, so that records from past raids are available in the viewer without re-running the live monitor.

#### Acceptance Criteria

1. THE DKP_Importer SHALL accept one or more EQ log file paths as positional command-line arguments.
2. WHEN invoked, THE DKP_Importer SHALL parse each specified log file in the order provided, applying the same bid-parsing and gratss-detection logic used by `dkptrackerv3.py`.
3. THE DKP_Importer SHALL apply the same deduplication logic defined in Requirement 2 so that replaying a log file that was already monitored live does not create duplicate records.
4. WHEN a log file is fully processed, THE DKP_Importer SHALL print a summary to stdout: number of auctions found, number written, and number skipped as duplicates.
5. IF a specified log file does not exist or cannot be read, THEN THE DKP_Importer SHALL print an error message to stderr for that file and continue processing any remaining files.
6. THE DKP_Importer SHALL write all new Auction Records to `dkp_history.json` using the same format defined in Requirement 1.

---

### Requirement 4: History File Format and Schema

**User Story:** As a developer, I want `dkp_history.json` to follow a versioned, well-defined schema, so that the viewer and importer can reliably parse it and future schema changes can be handled gracefully.

#### Acceptance Criteria

1. THE DKP_Logger SHALL write `dkp_history.json` as a JSON object with a top-level `"version"` field (integer, currently `1`) and a top-level `"records"` field (array of Auction Record objects).
2. THE DKP_Logger SHALL ensure the JSON file is valid UTF-8 and human-readable (pretty-printed with 2-space indentation).
3. WHEN the DKP_Viewer reads a History File with a `"version"` value it does not recognise, THE DKP_Viewer SHALL display a warning banner identifying the version mismatch and render as much data as it can parse.
4. FOR ALL valid History Files written by DKP_Logger, parsing the file with `json.loads()` and re-serialising with `json.dumps()` SHALL produce a file that parses to an equivalent in-memory structure (round-trip property).

---

### Requirement 5: Item Auction History View

**User Story:** As a DKP officer or player, I want to select any item and see all of its auction instances in chronological order, so that I can review bidding patterns and price trends over time.

#### Acceptance Criteria

1. THE DKP_Viewer SHALL display a searchable dropdown listing every distinct item name present in the loaded History File, sorted alphabetically.
2. WHEN an item is selected from the dropdown, THE DKP_Viewer SHALL render one Drop Card per Auction Record for that item.
3. THE DKP_Viewer SHALL order Drop Cards with the most recent auction leftmost and the oldest rightmost, arranged in a horizontally scrollable row.
4. THE DKP_Viewer SHALL display on each Drop Card: the auction date (formatted as YYYY-MM-DD), the winner's name, the winning amount in DKP, and the full ordered bid list.
5. THE DKP_Viewer SHALL render each Bid Entry in the bid list showing: player name, bid amount, and bid type (`Main` or `Alt`), with correction bids visually distinguished (e.g., italicised or prefixed with `*`).
6. WHEN an item has two or more Auction Records, THE DKP_Viewer SHALL render a price trend line graph below the Drop Cards showing winning amount on the Y-axis and auction date on the X-axis.
7. WHEN an item has fewer than two Auction Records, THE DKP_Viewer SHALL not render a trend graph for that item.

---

### Requirement 6: Player Bid History View

**User Story:** As a DKP officer or player, I want to click any player name and see their complete bid history, so that I can review their bidding activity and win rate.

#### Acceptance Criteria

1. WHEN a player name is clicked anywhere in the DKP_Viewer, THE DKP_Viewer SHALL navigate to or display a Player Profile panel for that player.
2. THE DKP_Viewer SHALL display in the Player Profile: the player's name, total auctions bid on, total auctions won, Win Rate percentage, and a breakdown of main bids vs. alt bids.
3. THE DKP_Viewer SHALL list every Auction Record in which the player placed at least one bid, showing: item name, auction date, the player's highest bid amount, bid type, and whether the player won.
4. THE DKP_Viewer SHALL sort the Player Profile bid list by auction date, most recent first.
5. WHEN a player name is clicked within a Player Profile, THE DKP_Viewer SHALL navigate to the Player Profile for the clicked player.

---

### Requirement 7: Aggregate Statistics Panel

**User Story:** As a DKP officer, I want a statistics panel showing raid-wide bidding trends, so that I can identify the most contested items and most active bidders.

#### Acceptance Criteria

1. THE DKP_Viewer SHALL display a statistics panel accessible from the main navigation.
2. THE DKP_Viewer SHALL display in the statistics panel: the top 10 most Contested Items ranked by unique bidder count across all Auction Records for that item.
3. THE DKP_Viewer SHALL display in the statistics panel: the top 10 most active bidders ranked by total number of Auction Records in which they placed at least one bid.
4. THE DKP_Viewer SHALL display in the statistics panel: the overall ratio of main-bid wins to alt-bid wins across all Auction Records.
5. WHEN the statistics panel is displayed, THE DKP_Viewer SHALL compute all statistics from the currently loaded History File data without requiring a page reload.

---

### Requirement 8: Browser-Side Log File Import

**User Story:** As a DKP officer, I want to import a log file directly in the browser to preview its auction data, so that I can review a session's history without modifying the persisted History File.

#### Acceptance Criteria

1. THE DKP_Viewer SHALL provide an Import button that opens a native file picker accepting `.txt` files.
2. WHEN a log file is selected, THE DKP_Viewer SHALL parse it in the browser using the same bid-parsing and gratss-detection logic as `dkptrackerv3.py`, without sending the file contents to any external server.
3. THE DKP_Viewer SHALL merge the parsed auction records from the imported log file with the currently loaded History File data for the duration of the browser session.
4. THE DKP_Viewer SHALL apply the same deduplication logic defined in Requirement 2 when merging imported records, so that records already present in the loaded History File are not duplicated.
5. WHEN the browser tab is closed or refreshed, THE DKP_Viewer SHALL discard all browser-imported data, reverting to the originally loaded History File.
6. THE DKP_Viewer SHALL display a visible indicator showing how many records were added from the browser import and how many were skipped as duplicates.

---

### Requirement 9: Export Updated History File

**User Story:** As a DKP officer, I want to download the merged history data after a browser import, so that I can persist the newly imported records to the History File without running the CLI importer.

#### Acceptance Criteria

1. THE DKP_Viewer SHALL provide an Export/Save JSON button that is enabled only when browser-imported records are present in the current session.
2. WHEN the Export/Save JSON button is clicked, THE DKP_Viewer SHALL trigger a browser download of a JSON file containing all currently loaded Auction Records (both original and browser-imported), serialised in the format defined in Requirement 4.
3. THE DKP_Viewer SHALL name the downloaded file `dkp_history.json`.
4. THE DKP_Viewer SHALL apply deduplication before export so that the downloaded file contains no records with duplicate Dedup Keys.

---

### Requirement 10: Self-Contained Viewer Delivery

**User Story:** As a DKP officer, I want the viewer to be a single HTML file with no external dependencies, so that it works offline and requires no installation.

#### Acceptance Criteria

1. THE DKP_Viewer SHALL be delivered as a single `.html` file (`dkp_history.html`) with all CSS, JavaScript, and charting logic inlined or bundled within the file.
2. THE DKP_Viewer SHALL load `dkp_history.json` from the same directory as `dkp_history.html` when opened in a browser that permits local file access (e.g., via a local HTTP server or a browser with `--allow-file-access-from-files`).
3. WHEN `dkp_history.json` cannot be loaded (file not found or fetch error), THE DKP_Viewer SHALL display a clear error message instructing the user to place `dkp_history.json` in the same directory and, if needed, serve the files via a local HTTP server.
4. THE DKP_Viewer SHALL function correctly in the latest stable versions of Chrome, Firefox, and Edge without requiring any browser extensions or plugins.

---

### Requirement 11: DKP_Logger Integration with dkptrackerv3.py

**User Story:** As a developer, I want `dkp_logger.py` to integrate with `dkptrackerv3.py` via a clean import interface, so that the existing monitor requires minimal changes to gain persistence.

#### Acceptance Criteria

1. THE DKP_Logger SHALL expose a single public function `record_auction(item_name, winner, amount, history, log_file_path)` that `dkptrackerv3.py` calls from `close_item()`.
2. THE DKP_Logger SHALL derive the Raid Session ID from `log_file_path` by reading the first timestamp line of that file.
3. WHEN `log_file_path` is `None` or the file cannot be read, THE DKP_Logger SHALL use the current UTC date as the Raid Session ID fallback.
4. THE DKP_Logger SHALL not import any third-party packages beyond the Python standard library, so that it installs without additional dependencies.
5. THE DKP_Logger SHALL complete the write operation within 500 milliseconds under normal disk conditions so that the live auction display is not perceptibly delayed.
