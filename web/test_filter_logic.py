"""
Property-based test for web viewer search filter correctness.

**Validates: Requirements 10.4**

This test replicates the client-side filter logic from web/app.js in Python
and verifies it with hypothesis to ensure correctness across all inputs.

The filter logic (from app.js applyFilter):
- Match against item_name (case-insensitive substring)
- Match against winner (case-insensitive substring)
- Match against any player name in bids array (case-insensitive substring)
- Return true if any match
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


# --- Replicated filter logic from app.js ---

def matches_filter(record: dict, query: str) -> bool:
    """
    Replicate the JavaScript applyFilter logic from web/app.js.

    Returns True if the record matches the search query via case-insensitive
    substring match on item_name, winner, or any bid player name.
    """
    if not query:
        return True

    q = query.strip().lower()
    if not q:
        return True

    # Match against item name
    item_name = record.get("item_name") or ""
    if q in item_name.lower():
        return True

    # Match against winner name
    winner = record.get("winner") or ""
    if q in winner.lower():
        return True

    # Match against any player name in bid history
    bids = record.get("bids") or []
    for bid in bids:
        player = bid.get("player") or ""
        if q in player.lower():
            return True

    return False


def filter_records(records: list, query: str) -> list:
    """
    Filter a list of auction records by query, replicating app.js applyFilter.

    If query is empty/whitespace, returns all records.
    Otherwise returns only records that match via matches_filter.
    """
    q = (query or "").strip().lower()
    if not q:
        return list(records)
    return [r for r in records if matches_filter(r, query)]


# --- Hypothesis strategies ---

# Strategy for generating a bid entry
bid_entry_strategy = st.fixed_dictionaries({
    "player": st.text(min_size=1, max_size=30),
    "amount": st.integers(min_value=1, max_value=10000),
    "bid_type": st.sampled_from(["main", "alt"]),
    "is_correction": st.booleans(),
})

# Strategy for generating an auction record
auction_record_strategy = st.fixed_dictionaries({
    "item_name": st.text(min_size=1, max_size=60),
    "winner": st.text(min_size=1, max_size=30),
    "amount": st.integers(min_value=1, max_value=50000),
    "timestamp": st.just("2026-04-29T02:55:35Z"),
    "bids": st.lists(bid_entry_strategy, min_size=0, max_size=5),
})

# Strategy for search queries (including empty and whitespace)
search_query_strategy = st.text(min_size=0, max_size=20)


# --- Property test ---

class TestWebViewerFilterCorrectness:
    """Property 16: Web viewer search filter correctness"""

    @given(
        records=st.lists(auction_record_strategy, min_size=0, max_size=10),
        query=search_query_strategy,
    )
    @settings(max_examples=200)
    def test_filtered_results_only_contain_matching_records(self, records, query):
        """
        **Validates: Requirements 10.4**

        For any search query string and any set of displayed AuctionRecords,
        the filtered results SHALL include only records where the item name
        or a player name in the bid history contains the query as a
        case-insensitive substring.
        """
        filtered = filter_records(records, query)
        q = (query or "").strip().lower()

        if not q:
            # Empty query returns all records
            assert filtered == list(records)
        else:
            # Every filtered record must match the query
            for record in filtered:
                item_match = q in (record.get("item_name") or "").lower()
                winner_match = q in (record.get("winner") or "").lower()
                bids = record.get("bids") or []
                player_match = any(
                    q in (bid.get("player") or "").lower() for bid in bids
                )
                assert item_match or winner_match or player_match, (
                    f"Record did not match query '{q}': "
                    f"item_name='{record.get('item_name')}', "
                    f"winner='{record.get('winner')}', "
                    f"players={[b.get('player') for b in bids]}"
                )

            # Every non-filtered record must NOT match the query
            for record in records:
                if record not in filtered:
                    item_match = q in (record.get("item_name") or "").lower()
                    winner_match = q in (record.get("winner") or "").lower()
                    bids = record.get("bids") or []
                    player_match = any(
                        q in (bid.get("player") or "").lower() for bid in bids
                    )
                    assert not (item_match or winner_match or player_match), (
                        f"Record should have been included but was not: "
                        f"item_name='{record.get('item_name')}', "
                        f"winner='{record.get('winner')}', "
                        f"players={[b.get('player') for b in bids]}"
                    )

    @given(
        record=auction_record_strategy,
    )
    @settings(max_examples=100)
    def test_item_name_substring_match(self, record):
        """
        **Validates: Requirements 10.4**

        If the query is a substring of the item_name (case-insensitive),
        the record must be included in filtered results.
        """
        item_name = record["item_name"]
        if len(item_name) >= 2:
            # Use a substring of the item name as query
            mid = len(item_name) // 2
            query = item_name[mid - 1:mid + 1]
            assert matches_filter(record, query)

    @given(
        record=st.fixed_dictionaries({
            "item_name": st.text(
                alphabet=st.characters(whitelist_categories=("L", "N"), max_codepoint=127),
                min_size=1, max_size=60,
            ),
            "winner": st.text(
                alphabet=st.characters(whitelist_categories=("L", "N"), max_codepoint=127),
                min_size=2, max_size=30,
            ),
            "amount": st.integers(min_value=1, max_value=50000),
            "timestamp": st.just("2026-04-29T02:55:35Z"),
            "bids": st.lists(bid_entry_strategy, min_size=0, max_size=5),
        }),
    )
    @settings(max_examples=100)
    def test_winner_substring_match(self, record):
        """
        **Validates: Requirements 10.4**

        If the query is a substring of the winner (case-insensitive),
        the record must be included in filtered results.
        """
        winner = record["winner"]
        # Use a substring of the winner as query (in different case)
        mid = len(winner) // 2
        query = winner[mid - 1:mid + 1].upper()
        assert matches_filter(record, query)

    @given(
        record=auction_record_strategy,
    )
    @settings(max_examples=100)
    def test_bid_player_substring_match(self, record):
        """
        **Validates: Requirements 10.4**

        If the query is a substring of any bid player name (case-insensitive),
        the record must be included in filtered results.
        """
        bids = record.get("bids") or []
        if bids:
            player = bids[0]["player"]
            if len(player) >= 2:
                mid = len(player) // 2
                query = player[mid - 1:mid + 1].lower()
                assert matches_filter(record, query)

    @given(
        record=auction_record_strategy,
        query=st.text(
            alphabet=st.characters(whitelist_categories=("Nd",)),
            min_size=5,
            max_size=10,
        ),
    )
    @settings(max_examples=100)
    def test_non_matching_query_excludes_record(self, record, query):
        """
        **Validates: Requirements 10.4**

        If a query does not appear as a substring in item_name, winner,
        or any bid player, the record must NOT be in the filtered results.
        """
        q = query.strip().lower()
        if not q:
            return

        item_name = (record.get("item_name") or "").lower()
        winner = (record.get("winner") or "").lower()
        bids = record.get("bids") or []
        players = [(b.get("player") or "").lower() for b in bids]

        # Only assert non-match if the query truly isn't a substring anywhere
        if q not in item_name and q not in winner and not any(q in p for p in players):
            assert not matches_filter(record, query)
