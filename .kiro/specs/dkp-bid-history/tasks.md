# Implementation Plan

## Overview

Build persistent DKP bid history tracking with a JSON data store, CLI import tool, and self-contained HTML viewer. Three new files (`dkp_logger.py`, `dkp_import.py`, `dkp_history.html`) plus minimal integration changes to `dkptrackerv3.py`.

## Task Dependency Graph

```
Task 1 (dkp_logger.py)
  └─► Task 2 (integrate into dkptrackerv3.py)
  └─► Task 3 (dkp_import.py)
Task 4 (HTML skeleton)
  └─► Task 5 (item history view)
  └─► Task 6 (player profile view)
  └─► Task 7 (statistics panel)
  └─► Task 8 (browser import/export)
  └─► Task 9 (styling and polish)
```

## Tasks

- [x] 1. dkp_logger.py — persistence module
  - [x] 1.1 Implement `_raid_session_id(log_file_path)` — extract first timestamp date from log, SHA-256 hash it; fallback to current UTC date
  - [x] 1.2 Implement `_dedup_key(item_name, winner, amount, raid_session_id)` — SHA-256 of null-byte-separated fields
  - [x] 1.3 Implement `_load(path)` and `_save(path, data)` — atomic write via `.tmp` + `os.replace()`
  - [x] 1.4 Implement `record_auction(item_name, winner, amount, history, log_file_path, history_file)` — full flow with file locking, dedup check, append, and error handling
  - [x] 1.5 Write unit tests: round-trip property, dedup idempotency, dedup cross-officer, history monotonicity

- [x] 2. Integrate dkp_logger into dkptrackerv3.py
  - [x] 2.1 Add optional import of `dkp_logger` at top of `dkptrackerv3.py` with `_HISTORY_ENABLED` flag
  - [x] 2.2 Store `_current_log` on the `BidTracker` instance — set it from `monitor_log()` when the active log file changes
  - [x] 2.3 Call `dkp_logger.record_auction()` at the end of `close_item()` for both single and qty-item paths
  - [x] 2.4 Verify existing test harness runs unchanged when `dkp_logger.py` is absent

- [x] 3. dkp_import.py — CLI replay script
  - [x] 3.1 Implement argument parsing: positional log file paths, `--output`, `--dry-run`
  - [x] 3.2 Implement log file replay using `dkptrackerv3` imports — wrap `BidTracker.close_item` to capture records without printing
  - [x] 3.3 Call `dkp_logger.record_auction()` for each captured record, applying dedup
  - [x] 3.4 Print per-file summary: auctions found / written / skipped
  - [x] 3.5 Handle missing/unreadable files gracefully — print error to stderr, continue
  - [x] 3.6 Test: replay same log twice produces no duplicates; replay matches live monitor output

- [x] 4. dkp_history.html — viewer skeleton and data loading
  - [x] 4.1 Create HTML skeleton with nav bar (Items, Stats, Import, Export buttons) and `#main-content` div
  - [x] 4.2 Implement `loadHistory()` — fetch `dkp_history.json` via `fetch()`, populate `Store.records`; show error message if file not found
  - [x] 4.3 Implement hash-based router — `hashchange` listener dispatches to correct render function
  - [x] 4.4 Implement version mismatch warning banner for unknown schema versions

- [x] 5. dkp_history.html — item history view
  - [x] 5.1 Implement searchable item dropdown — alphabetically sorted, filters as user types
  - [x] 5.2 Implement `renderDropCards(records)` — horizontally scrollable row, newest left
  - [x] 5.3 Render each Drop Card: date, winner (clickable), winning amount, full bid list with correction markers (`*`)
  - [x] 5.4 Implement `drawTrendLine(containerId, points)` — hand-rolled SVG line chart, Y=amount, X=date
  - [x] 5.5 Show trend chart only when item has 2+ records; hide otherwise

- [x] 6. dkp_history.html — player profile view
  - [x] 6.1 Implement `renderPlayerProfile(name)` — summary stats: total bids, wins, win rate %, main/alt breakdown
  - [x] 6.2 Render player's bid list sorted by date descending — item name (clickable), date, highest bid, bid type, won/lost indicator
  - [x] 6.3 Make all player names clickable throughout the viewer to navigate to their profile

- [x] 7. dkp_history.html — statistics panel
  - [x] 7.1 Implement `renderStats()` — compute top 10 contested items by unique bidder count
  - [x] 7.2 Compute top 10 most active bidders by auction participation count
  - [x] 7.3 Compute overall main-win vs alt-win ratio across all records
  - [x] 7.4 All stats computed client-side from `Store.records` on each render

- [x] 8. dkp_history.html — browser log import and export
  - [x] 8.1 Port bid-parsing regex patterns from `dkptrackerv3.py` to JavaScript
  - [x] 8.2 Implement `parseLogFile(text)` in JS — same auction detection logic, returns `AuctionRecord[]`
  - [x] 8.3 Implement SHA-256 dedup in JS using `crypto.subtle.digest()`
  - [x] 8.4 Implement `importLog()` — FileReader API, parse, dedup-merge into `Store`, show added/skipped count
  - [x] 8.5 Implement `exportJSON()` — serialise `Store.records` to JSON, trigger browser download as `dkp_history.json`; enable Export button only when imported records exist
  - [x] 8.6 Test: import same log twice in browser produces no duplicates; export round-trips correctly

- [x] 9. dkp_history.html — styling and polish
  - [x] 9.1 Apply dark theme CSS consistent with terminal aesthetic (dark background, cyan/pink/green accents)
  - [x] 9.2 Ensure horizontal scroll on drop cards works on Chrome, Firefox, Edge
  - [x] 9.3 Make player name links visually distinct (underline or colour)
  - [x] 9.4 Add loading spinner while `dkp_history.json` is being fetched
  - [x] 9.5 Verify no external network requests are made (all assets inlined)

## Notes

- Task 1 is complete — `dkp_logger.py` has been created with all core functions implemented.
- Tasks 2 and 3 depend on Task 1 (the logger module).
- Tasks 5–9 all depend on Task 4 (the HTML skeleton).
- Tasks 4–9 are independent of Tasks 2–3 and can be built in parallel.
- The HTML viewer (Tasks 4–9) is the largest body of work but is self-contained — no Python changes needed.
