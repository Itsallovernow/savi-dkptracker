# Implementation Plan: Character Detail Page

## Overview

Add hash-based client-side routing, a navigation bar, clickable character names, a character listing page, and a character detail page with expansion-based DKP grouping to the existing DKP Bid Tracker web viewer. All new views consume the existing `allRecords` in-memory data source. Implementation is vanilla JavaScript with no build tooling, following the existing `app.js` IIFE pattern.

## Tasks

- [x] 1. Add expansion bucket classifier and router infrastructure
  - [x] 1.1 Create expansion bucket constants and classifyRecord/groupByExpansion functions
    - Add `EXPANSION_BUCKETS` constant array (ordered newest-first) with name, startDate, endDate fields
    - Implement `classifyRecord(record)` that returns the bucket name based on timestamp
    - Handle pre-Classic timestamps → "Classic", missing/invalid timestamps → "Other"
    - Implement `groupByExpansion(records)` that returns a Map of bucket name → records
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [ ]* 1.2 Write property tests for expansion bucket classifier (Properties 1–3)
    - **Property 1: Record classification assigns exactly one correct bucket**
    - **Property 2: Pre-Classic timestamps classify as Classic**
    - **Property 3: Invalid or missing timestamps classify as Other**
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4**

  - [x] 1.3 Implement hash-based router (initRouter, parseRoute, navigateTo)
    - Add `parseRoute()` that parses location.hash into `{ view, param }` for routes: `#auctions`, `#characters`, `#character/{name}`
    - Add `navigateTo(route)` that sets `window.location.hash`
    - Add `initRouter()` that listens for `hashchange` and calls view-switching logic
    - Default empty/unrecognized hash to `#auctions`
    - URL-decode character names with `decodeURIComponent()`
    - _Requirements: 1.2, 1.3, 2.2_

- [x] 2. Implement navigation bar and update auction table
  - [x] 2.1 Add navigation bar HTML and renderNavBar function
    - Add navigation bar markup to `index.html` with "Auctions" and "Characters" links
    - Implement `renderNavBar(activeView)` that highlights the active link
    - Ensure nav bar is visible on all pages/views
    - Style the navigation bar with CSS (horizontal layout, active indicator)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 2.2 Make winner names clickable links in the auction table
    - Implement `renderWinnerLink(winnerName)` returning an anchor element with `href="#character/{name}"`
    - Modify `renderTable()` to use `renderWinnerLink` for the winner column cell
    - Add CSS for link hover/focus states (underline or color change)
    - Ensure links are keyboard-accessible (Tab and Enter)
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [ ]* 2.3 Write property test for winner link rendering (Property 5)
    - **Property 5: Winner name renders as link with correct route**
    - **Validates: Requirements 2.1, 2.2, 8.4**

- [x] 3. Checkpoint - Ensure routing and navigation work
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement character detail page view
  - [x] 4.1 Implement character data filtering and DKP summary rendering
    - Implement character record filtering from `allRecords` (case-insensitive winner match)
    - Implement `renderDkpSummary(groupedRecords)` showing expansion name, total DKP, item count per bucket
    - Include grand total DKP across all buckets
    - Display "No records found for {name}" if no matches, with link back to character listing
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 7.1, 7.2, 7.3_

  - [ ]* 4.2 Write property tests for character filter and DKP totals (Properties 4, 6)
    - **Property 4: Character filter returns only matching records (case-insensitive)**
    - **Property 6: Bucket DKP totals equal sum of record amounts**
    - **Validates: Requirements 7.1, 7.2, 5.1, 5.2, 5.4**

  - [x] 4.3 Implement item breakout rendering per expansion bucket
    - Implement `renderItemBreakout(bucketName, records)` showing item name, DKP amount, and formatted date
    - Sort items within each bucket by timestamp descending (newest-first)
    - Handle missing timestamps by displaying "--" as date
    - Handle missing amounts by treating as 0
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [ ]* 4.4 Write property tests for item breakout (Properties 7, 8)
    - **Property 7: Item breakout contains all record details**
    - **Property 8: Item breakout sorted newest-first within each bucket**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4**

  - [x] 4.5 Implement full renderCharacterDetail view with bucket ordering
    - Implement `renderCharacterDetail(characterName, allRecords)` composing summary + item breakouts
    - Display character name as page heading
    - Only display buckets with at least one record
    - Order buckets chronologically from newest to oldest
    - Wire into router so `#character/{name}` renders this view
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [ ]* 4.6 Write property tests for bucket display rules (Properties 9, 10)
    - **Property 9: Only non-empty buckets are displayed**
    - **Property 10: Buckets ordered newest-first**
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.6**

- [x] 5. Implement character listing page
  - [x] 5.1 Implement getCharacterSummaries and renderCharacterListing
    - Implement `getCharacterSummaries(records)` returning unique winners with total DKP, sorted alphabetically
    - Implement `renderCharacterListing(allRecords)` rendering the character list with DKP totals
    - Each character name is a clickable link navigating to `#character/{name}`
    - Wire into router so `#characters` renders this view
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [x] 5.2 Add search/filter input to character listing page
    - Add a text input for filtering the character list by name substring (case-insensitive)
    - Filter updates the displayed list on each keystroke (debounced)
    - _Requirements: 8.5_

  - [ ]* 5.3 Write property tests for character listing (Properties 11, 12)
    - **Property 11: Character listing contains all unique winners with correct totals, sorted alphabetically**
    - **Property 12: Character listing search filters by name substring**
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.5**

- [x] 6. Integration and re-render on data updates
  - [x] 6.1 Wire delta-fetch updates to re-render active character views
    - After `fetchDeltaInBackground` merges new records, trigger re-render of the currently active character detail or listing view
    - Ensure character detail page reflects new records without manual refresh
    - Ensure character listing page updates winner list when new records arrive
    - _Requirements: 7.4, 7.5_

  - [x] 6.2 Connect view-switching to hide/show existing auction view elements
    - When switching to `#characters` or `#character/{name}`, hide auction table, search, pagination, stats
    - When switching to `#auctions`, hide character views and show auction elements
    - Ensure allRecords state is preserved across view switches
    - _Requirements: 7.5, 1.5_

- [x] 7. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties using fast-check
- Unit tests validate specific examples and edge cases
- All code goes into the existing `web/app.js` IIFE and `web/index.html` — no new files required unless separating test files
- The implementation uses vanilla JavaScript with no build tooling, consistent with the existing project

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.3"] },
    { "id": 1, "tasks": ["1.2", "2.1", "2.2"] },
    { "id": 2, "tasks": ["2.3", "4.1", "6.2"] },
    { "id": 3, "tasks": ["4.2", "4.3", "5.1"] },
    { "id": 4, "tasks": ["4.4", "4.5", "5.2"] },
    { "id": 5, "tasks": ["4.6", "5.3", "6.1"] }
  ]
}
```
