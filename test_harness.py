"""
DKP Tracker — Test Harness
==========================
Replays a real EverQuest log file through the tracker logic.

Modes
-----
  --mode replay   Feed every line with a small delay (quasi-real-time).
                  Use --speed to scale the delay (default 1.0 = real-time,
                  0.1 = 10x faster, 0 = instant).

  --mode auction  Extract only the lines that are part of auction sessions
                  (bids + gratss) and replay just those, with a short pause
                  between each line so the output is easy to read.

  --mode instant  Run all lines with zero delay — useful for a quick sanity
                  check that nothing crashes.

Item filter
-----------
  --item <name>   Narrow the replay to lines that mention a specific item
                  (case-insensitive substring match on item name).
                  Works with any --mode; the mode controls the delay only.
                  Partial names are fine: --item "flamewarding" matches
                  "Ring of Flamewarding".

Usage examples
--------------
  python test_harness.py 428_eqlog_Warderallover_pq.proj.txt
  python test_harness.py 428_eqlog_Warderallover_pq.proj.txt --mode auction
  python test_harness.py 428_eqlog_Warderallover_pq.proj.txt --mode replay --speed 20
  python test_harness.py 428_eqlog_Warderallover_pq.proj.txt --mode instant
  python test_harness.py 428_eqlog_Warderallover_pq.proj.txt --item "Ring of Flamewarding"
  python test_harness.py 428_eqlog_Warderallover_pq.proj.txt --item flamewarding --mode replay
"""

import argparse
import os
import re
import sys
import time

# Ensure emoji/unicode prints correctly on Windows consoles
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ---------------------------------------------------------------------------
# Import tracker components from the main module
# ---------------------------------------------------------------------------
from dkptrackerv3 import (
    ItemValidator,
    BidTracker,
    PriorityRollTracker,
    process_line,
    ROLL_PATTERN,
    ROLL_RESULT_PATTERN,
    GRATSS_PATTERN,
    extract_player_from_log,
    CYAN, BOLD, RESET, PINK, GREEN, YELLOW,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AUCTION_CONTEXT_LINES = 5   # extra lines before/after a bid cluster to include

def extract_auction_segments(lines):
    """
    Return a list of (line_number, line_text) tuples that are part of an
    auction session: any bid, gratss, or roll line, plus a small window of
    surrounding context so the output makes sense.
    """
    bid_pat   = re.compile(r"\[.*?\] (.*?) tells the raid,  +'(.+?) +(\d+)(?: +(Alt))?'", re.IGNORECASE)
    you_pat   = re.compile(r"\[.*?\] You tell your raid, +'(.+?) +(\d+)(?: +(Alt))?'", re.IGNORECASE)
    grat_pat  = GRATSS_PATTERN
    roll_pat  = ROLL_PATTERN
    roll_res  = ROLL_RESULT_PATTERN

    interesting = set()
    for i, line in enumerate(lines):
        if (bid_pat.search(line) or you_pat.search(line) or
                grat_pat.search(line) or roll_pat.search(line) or
                roll_res.search(line)):
            for j in range(max(0, i - AUCTION_CONTEXT_LINES),
                           min(len(lines), i + AUCTION_CONTEXT_LINES + 1)):
                interesting.add(j)

    return [(i, lines[i]) for i in sorted(interesting)]


def extract_item_segments(lines, item_filter):
    """
    Return (line_number, line_text) tuples relevant to a specific item.

    Matches lines where the item name appears (case-insensitive substring),
    including:
      - Auction announcements that list the item (checked per pipe-segment)
      - Bids on the item
      - Gratss lines for the item

    Context window is only expanded around bid/gratss lines, not around
    announcement lines, to avoid pulling in bids for co-announced items.
    """
    needle = item_filter.lower()

    bid_pat   = re.compile(r"\[.*?\] (?:.*? tells the raid,|You tell your raid,)\s*'(.+?) +\d+", re.IGNORECASE)
    grat_pat  = re.compile(r"'(.+?);\s*\d+;\s*.+?\s+gratss", re.IGNORECASE)
    pipe_pat  = re.compile(r"'([^']+\|[^']+)'", re.IGNORECASE)
    qty_strip = re.compile(r'\s*\(\d+\)\s*$')

    def is_bid_or_gratss_for_item(line):
        """True if this line is a bid or gratss specifically for the target item."""
        m = bid_pat.search(line)
        if m and needle in m.group(1).lower():
            return True
        m = grat_pat.search(line)
        if m and needle in m.group(1).lower():
            return True
        return False

    def is_announcement_for_item(line):
        """True if this is a pipe-separated announcement that includes the target item."""
        m = pipe_pat.search(line)
        if not m:
            return False
        for segment in m.group(1).split('|'):
            clean = qty_strip.sub('', segment).strip()
            if needle in clean.lower():
                return True
        return False

    interesting = set()
    for i, line in enumerate(lines):
        if is_bid_or_gratss_for_item(line):
            # Expand context window around bids and gratss
            for j in range(max(0, i - AUCTION_CONTEXT_LINES),
                           min(len(lines), i + AUCTION_CONTEXT_LINES + 1)):
                interesting.add(j)
        elif is_announcement_for_item(line):
            # Include the announcement line itself but no context expansion —
            # expanding would pull in bids for co-announced items
            interesting.add(i)

    return [(i, lines[i]) for i in sorted(interesting)]


def parse_timestamp(line):
    """Extract epoch seconds from '[Day Mon DD HH:MM:SS YYYY]' header."""
    m = re.match(r"\[(\w+ \w+ +\d+ \d+:\d+:\d+ \d+)\]", line)
    if m:
        import calendar
        from time import strptime
        try:
            t = strptime(m.group(1), "%a %b %d %H:%M:%S %Y")
            return float(calendar.timegm(t))
        except ValueError:
            pass
    return None


def print_banner(text, width=60):
    print(f"\n{CYAN}{'=' * width}{RESET}")
    print(f"{CYAN}  {BOLD}{text}{RESET}")
    print(f"{CYAN}{'=' * width}{RESET}\n")


# ---------------------------------------------------------------------------
# Replay engine
# ---------------------------------------------------------------------------

def replay(lines, player_name, delay_fn, label="", item_filter=None):
    """
    Feed lines through the tracker.
    delay_fn(line_index, line_text) → seconds to sleep before this line.
    item_filter: if set, only redraw the table when this item is active/closing.
    """
    validator    = ItemValidator()
    tracker      = BidTracker(validator)
    roll_tracker = PriorityRollTracker()

    filter_needle = item_filter.lower() if item_filter else None

    def item_is_active():
        """True if the filter item is currently in the live table."""
        if not filter_needle:
            return True
        return any(filter_needle in item.lower() for item in tracker.bid_order)

    print_banner(f"Player: {player_name}  |  {label}  |  {len(lines)} lines")

    i = 0
    while i < len(lines):
        line = lines[i].strip() if isinstance(lines[i], str) else lines[i][1].strip()

        sleep_time = delay_fn(i, line)
        if sleep_time > 0:
            time.sleep(sleep_time)

        # Dice roll needs to consume the very next line too
        roll_match = ROLL_PATTERN.search(line)
        if roll_match:
            roller = roll_match.group(1)
            next_line = ""
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip() if isinstance(lines[i + 1], str) else lines[i + 1][1].strip()
            result_match = ROLL_RESULT_PATTERN.search(next_line)
            if result_match:
                range_max, result = map(int, result_match.groups())
                roll_tracker.add_roll(roller, result, range_max)
                i += 2
                continue

        result = process_line(line, tracker, roll_tracker, player_name)
        if result == 'bid' and item_is_active():
            tracker.display_table()
        elif result == 'announce':
            if filter_needle is None or item_is_active():
                tracker.display_table()
        elif result == 'gratss':
            # For gratss: the item was just removed from bid_order.
            # Check if it was our target by looking at what was just closed —
            # the SOLD message was already printed; only redraw if items remain.
            if filter_needle is None or item_is_active():
                tracker.display_table()
            elif not tracker.bid_order:
                tracker._erase_table()

        i += 1

    # Final state
    if tracker.bid_order:
        if filter_needle is None or item_is_active():
            print_banner("Auction session ended — open items at EOF")
            tracker.display_table()
    else:
        print_banner("All auctions closed cleanly via gratss ✓")


# ---------------------------------------------------------------------------
# Delay strategies
# ---------------------------------------------------------------------------

def make_realtime_delay(lines, speed):
    """Return a delay function that mirrors real log timestamps, scaled by speed."""
    # Pre-compute timestamps
    timestamps = []
    for entry in lines:
        raw = entry if isinstance(entry, str) else entry[1]
        timestamps.append(parse_timestamp(raw))

    def delay_fn(i, line):
        if speed == 0:
            return 0
        if i == 0 or timestamps[i] is None or timestamps[i - 1] is None:
            return 0
        gap = timestamps[i] - timestamps[i - 1]
        gap = max(0, min(gap, 5))   # cap at 5s so we don't wait forever
        return gap / speed

    return delay_fn


def make_fixed_delay(seconds):
    return lambda i, line: seconds


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="DKP Tracker test harness — replay a real EQ log file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("logfile", help="Path to the EQ log file to replay")
    parser.add_argument(
        "--mode",
        choices=["replay", "auction", "instant"],
        default="auction",
        help="Replay mode (default: auction)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=10.0,
        help="Speed multiplier for 'replay' mode (default: 10 = 10x faster than real-time)",
    )
    parser.add_argument(
        "--line-delay",
        type=float,
        default=0.08,
        help="Fixed seconds between lines in 'auction' mode (default: 0.08)",
    )
    parser.add_argument(
        "--item",
        type=str,
        default=None,
        help="Filter to only show lines related to a specific item (fuzzy match on item name)",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.logfile):
        print(f"File not found: {args.logfile}")
        sys.exit(1)

    player_name = extract_player_from_log(args.logfile)

    print(f"Loading {args.logfile} ...")
    with open(args.logfile, encoding="utf-8", errors="ignore") as f:
        all_lines = f.readlines()
    print(f"   {len(all_lines):,} total lines read")

    # --item filter: narrow display to a specific item
    # Feed all auction lines to the tracker (for correct state), but only
    # redraw the table when the target item is active.
    if args.item:
        segments = extract_auction_segments(all_lines)
        if not segments:
            print("No auction lines found in log.")
            sys.exit(0)
        print(f"   {len(segments):,} auction-relevant lines  (filtering display to {args.item!r})")
        if args.mode == "replay":
            delay_fn = make_realtime_delay(segments, args.speed)
        else:
            delay_fn = make_fixed_delay(args.line_delay if args.mode == "auction" else 0)
        replay(segments, player_name,
               delay_fn=delay_fn,
               label=f"Item filter: {args.item!r}  mode={args.mode}",
               item_filter=args.item)
        return

    if args.mode == "instant":
        replay(all_lines, player_name,
               delay_fn=make_fixed_delay(0),
               label="Mode: instant")

    elif args.mode == "replay":
        delay_fn = make_realtime_delay(all_lines, args.speed)
        replay(all_lines, player_name,
               delay_fn=delay_fn,
               label=f"Mode: replay  speed={args.speed}x")

    elif args.mode == "auction":
        segments = extract_auction_segments(all_lines)
        print(f"   {len(segments):,} auction-relevant lines extracted\n")
        # Wrap as tuples for the replay engine
        replay(segments, player_name,
               delay_fn=make_fixed_delay(args.line_delay),
               label=f"Mode: auction  delay={args.line_delay}s/line")


if __name__ == "__main__":
    main()
