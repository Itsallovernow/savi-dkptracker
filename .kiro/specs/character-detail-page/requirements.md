# Requirements Document

## Introduction

The Character Detail Page feature extends the DKP Bid Tracker web viewer with per-character DKP spending breakdowns. It adds a "Characters" navigation link, makes character names in the auction table clickable, and provides a dedicated page showing each character's DKP spending and items won, organized by expansion date ranges.

## Glossary

- **Web_Viewer**: The static HTML/CSS/JS client-side application that displays DKP auction history, hosted on S3
- **Auction_Table**: The main data table on the auction history page showing all auction records
- **Character_Detail_Page**: A dedicated page displaying a single character's DKP spending summary and item breakout per expansion period
- **Navigation_Bar**: The top horizontal section of the page containing page links (currently only the auction history page)
- **Expansion_Bucket**: A named date range corresponding to an EverQuest expansion era, used to group auction records chronologically
- **DKP_Summary**: An aggregated view showing total DKP spent by a character within each expansion bucket
- **Item_Breakout**: A detailed list of individual items won by a character within a specific expansion bucket, including item name and DKP amount
- **Auction_Record**: A data object containing item_name, winner, amount, timestamp, bid_count, dedup_key, uploaded_by, and confirmed_by fields

## Requirements

### Requirement 1: Navigation Bar with Characters Link

**User Story:** As a user, I want a navigation bar with links to all pages, so that I can easily move between the auction history and character views.

#### Acceptance Criteria

1. THE Navigation_Bar SHALL display a horizontal list of page links at the top of the Web_Viewer
2. THE Navigation_Bar SHALL include an "Auctions" link that navigates back to the main auction history page
3. THE Navigation_Bar SHALL include a "Characters" link that navigates to the character listing view
4. THE Navigation_Bar SHALL visually indicate which page is currently active
5. THE Navigation_Bar SHALL remain visible on all pages within the Web_Viewer

### Requirement 2: Clickable Character Names in Auction Table

**User Story:** As a user, I want character names in the auction table to be clickable links, so that I can quickly navigate to a character's detail page from the auction history.

#### Acceptance Criteria

1. THE Auction_Table SHALL render each winner name cell as a clickable link
2. WHEN a user clicks a winner name link, THE Web_Viewer SHALL navigate to the Character_Detail_Page for that character
3. THE winner name links SHALL be visually distinguishable from plain text (underline or color change on hover)
4. THE winner name links SHALL be keyboard-accessible using Tab and Enter keys

### Requirement 3: Character Detail Page Layout

**User Story:** As a user, I want a dedicated page for each character showing their DKP spending organized by expansion, so that I can review spending history at a glance.

#### Acceptance Criteria

1. THE Character_Detail_Page SHALL display the character name as a page heading
2. THE Character_Detail_Page SHALL display a DKP_Summary section showing total DKP spent in each Expansion_Bucket
3. THE Character_Detail_Page SHALL display an Item_Breakout section for each Expansion_Bucket containing at least one won item
4. THE Character_Detail_Page SHALL only display Expansion_Buckets in which the character has won at least one item
5. IF a character has won items with timestamps that do not fall within any defined Expansion_Bucket date range, THEN THE Character_Detail_Page SHALL display those items in an "Other" fallback section
6. THE Character_Detail_Page SHALL order Expansion_Buckets chronologically from newest to oldest

### Requirement 4: Expansion Bucket Date Ranges

**User Story:** As a user, I want auction records grouped by EverQuest expansion date ranges, so that I can see spending patterns across different game eras.

#### Acceptance Criteria

1. THE Web_Viewer SHALL classify Auction_Records into Expansion_Buckets using the following date ranges:
   - "Classic": December 1, 2023 through June 30, 2024
   - "Ruins of Kunark": July 1, 2024 through March 31, 2025
   - "Scars of Velious": April 1, 2025 through December 31, 2025
   - "Shadows of Luclin": January 1, 2026 through September 30, 2026
   - "Planes of Power": October 1, 2026 onward
2. THE Web_Viewer SHALL assign each Auction_Record to exactly one Expansion_Bucket based on the record timestamp
3. IF an Auction_Record has a timestamp before December 1, 2023, THEN THE Web_Viewer SHALL assign the record to the "Classic" Expansion_Bucket
4. IF an Auction_Record has a timestamp that does not fall within any defined Expansion_Bucket date range, THEN THE Web_Viewer SHALL assign the record to an "Other" bucket

### Requirement 5: DKP Summary Per Expansion

**User Story:** As a user, I want to see how much DKP a character spent in each expansion, so that I can understand their overall spending per era.

#### Acceptance Criteria

1. THE DKP_Summary SHALL display the Expansion_Bucket name and total DKP spent for each bucket
2. THE DKP_Summary SHALL calculate total DKP spent by summing the amount field of all Auction_Records where the character is the winner within that Expansion_Bucket
3. THE DKP_Summary SHALL display the count of items won in each Expansion_Bucket
4. THE DKP_Summary SHALL display a grand total of DKP spent across all Expansion_Buckets

### Requirement 6: Item Breakout Per Expansion

**User Story:** As a user, I want to see the individual items a character won in each expansion period, so that I can review exactly what they spent DKP on.

#### Acceptance Criteria

1. THE Item_Breakout SHALL list each item won by the character within the Expansion_Bucket
2. THE Item_Breakout SHALL display the item name and DKP amount for each entry
3. THE Item_Breakout SHALL display the date the item was won
4. THE Item_Breakout SHALL sort items within each bucket by date from newest to oldest

### Requirement 7: Character Data Loading

**User Story:** As a user, I want the character detail page to load data from all available auction records (cached + delta updates), so that I see the most current results without extra API calls.

#### Acceptance Criteria

1. THE Character_Detail_Page SHALL derive character data by filtering the in-memory allRecords array (which includes both cached records and delta-fetched updates) where the winner field matches the character name
2. THE Character_Detail_Page SHALL perform case-insensitive matching on the character name
3. IF no Auction_Records match the character name, THEN THE Character_Detail_Page SHALL immediately display a message indicating no records were found for that character
4. THE Character_Detail_Page SHALL reflect any new records loaded by background delta fetches without requiring a manual page refresh
5. THE Character_Detail_Page SHALL use the same allRecords data source as the main auction table (not a separate localStorage read)

### Requirement 8: Character Listing Page

**User Story:** As a user, I want a page that lists all characters who have won auctions, so that I can browse and find specific characters.

#### Acceptance Criteria

1. THE Character_Listing_Page SHALL display a list of all unique winner names from the cached Auction_Records
2. THE Character_Listing_Page SHALL display the total DKP spent next to each character name
3. THE Character_Listing_Page SHALL sort the list alphabetically by character name
4. WHEN a user clicks a character name in the listing, THE Web_Viewer SHALL navigate to that character's Character_Detail_Page
5. THE Character_Listing_Page SHALL include a search input to filter the character list by name
