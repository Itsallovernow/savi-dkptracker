import time
import datetime
import re
import os
import string
import glob
import shutil
import sys
import subprocess
from collections import defaultdict
from rapidfuzz import fuzz

try:
    import msvcrt
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False

# Version
__version__ = "3.0.20"

# Ensure emoji/unicode prints correctly on Windows consoles
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    import dkp_logger as _dkp_logger
    _HISTORY_ENABLED = True
except ImportError:
    _HISTORY_ENABLED = False

LOG_DIR  = r"D:\Games\Quarm\TAKPv22"
LOG_GLOB = os.path.join(LOG_DIR, "eqlog_*_pq.proj.txt")

# Resolve base path for bundled data files (PyInstaller-compatible)
# When running as a PyInstaller .exe, sys._MEIPASS points to the temp extraction dir.
# When running from source, use the script's directory.
_BASE_PATH = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

SEED_FILE = os.path.join(_BASE_PATH, "seed_items.txt")
# Writable seed file lives beside the .exe (or script) for learned items
if getattr(sys, 'frozen', False):
    _EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _EXE_DIR = os.path.dirname(os.path.abspath(__file__))
SEED_FILE_WRITE = os.path.join(_EXE_DIR, "seed_items.txt")
BLACKLIST = {"butthole", "test", "asdf", "123", "lol"}

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
BID_PATTERN  = re.compile(r"\[.*?\] (.*?) tells the raid,\s+'([A-Za-z].+?)\s*(\d+)\s*(x\d+)?\s*(alt|a)?[^']*'", re.IGNORECASE)
YOU_PATTERN  = re.compile(r"\[.*?\] You tell your raid,\s+'([A-Za-z].+?)\s*(\d+)\s*(x\d+)?\s*(alt|a)?[^']*'", re.IGNORECASE)
ROLL_PATTERN = re.compile(r"\*\*A Magic Die is rolled by (.+?)\.")
ROLL_RESULT_PATTERN = re.compile(r"\*\*It could have been any number from 0 to (\d+), but this time it turned up a (\d+)\.")
ROLL_CALL_PATTERN   = re.compile(r"(.+?) (\d{1,4})/(\d{1,4})")
GRATSS_PATTERN      = re.compile(r"^(.+?);\s*(\d+);\s*(.+?)\s+gratss\b", re.IGNORECASE)
UNGRATSS_PATTERN    = re.compile(r"^(.+?);\s*(\d+);\s*(.+?)\s+ungratss\b", re.IGNORECASE)
# Retraction: player says they meant a different (lower) amount
# e.g. "sorry - meant 460", "oops meant 460", "err meant 460"
RETRACTION_PATTERN  = re.compile(
    r"\[.*?\] (\S+) tells the raid,\s+'(?:sorry|oops|err+|my bad|mb)[^']*?\bmeant\b[^']*?(\d+)",
    re.IGNORECASE,
)
# Manual clear: only "You" can issue this — clears a stale auction without awarding
# Matches two formats:
#   You tell your raid, 'clear <item name>'
#   [timestamp] clear <item name>   (bare log line, e.g. from a /say or console)
CLEAR_PATTERN = re.compile(
    r"(?:"
    r"\[.*?\] You tell your raid,\s+'clear\s+(.+?)'"   # raid tell format
    r"|"
    r"\[.*?\]\s+clear\s+(.+)"                          # bare log line format
    r")",
    re.IGNORECASE,
)
AUCTION_ANNOUNCE_PATTERN = re.compile(
    r"\[.*?\] (?:.*? tells the raid,|You tell your raid,|.*? tells the guild,|You say to your guild,)"
    r"\s+'(.+\|.+)'",
    re.IGNORECASE,
)
QTY_PATTERN = re.compile(r'^(.+?)\s*\((\d+)\)\s*$')

# Lines from these chat channels are never auction-related
IGNORED_CHANNEL_PATTERN = re.compile(
    r"\[.*?\] \S+ tells (?:General|Ports|LFG|Lfg|ztsavagespam)\s*:\s*\d+,",
    re.IGNORECASE,
)

# Lines containing these strings are always ignored regardless of channel
IGNORED_CONTENT_PATTERN = re.compile(r"\bzealtag\b", re.IGNORECASE)

# Words that appear in conversational text but never in EQ item names.
# If an item candidate contains any of these as whole words, it's chat noise.
_CHAT_WORDS = re.compile(
    r"\b(he|she|it|me|my|we|you|they|him|her|his|its|our|their|i\b|"
    r"hit|hits|miss|for|a\s+few|few|times|plus|abd|lvl|level|"
    r"forever|nah|lol|omg|wtf|gg|brb|afk|lfg|wtb|wts|"
    r"sorry|oops|meant|err+|my\s+bad|mb|"
    r"at|inc|incoming|coth|up|go|goes|went|"
    r"to|into|onto|from|with|about|over|under|"
    r"is|was|are|were|be|been|being|have|has|had|do|does|did|"
    r"will|would|could|should|may|might|shall|must|can|"
    r"and|but|or|so|yet|nor|although|because|since|unless|until|"
    r"just|really|very|too|also|even|still|already|always|never|"
    r"here|there|where|when|how|why|what|who|which)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
PINK   = "\033[95m"
GREEN  = "\033[92m"
ORANGE = "\033[93m"
YELLOW = "\033[93m"
BLUE   = "\033[96m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

MIN_BID            = 2
FILE_POLL_INTERVAL = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize(text):
    return ''.join(c for c in text.lower().strip() if c not in string.punctuation)


# EQ log timestamp pattern: [Day Mon DD HH:MM:SS YYYY]
_LOG_TS_PAT = re.compile(r'\[(\w{3} \w{3} +\d{1,2} \d{2}:\d{2}:\d{2} \d{4})\]')

def _extract_log_timestamp(line):
    """
    Extract the EQ log timestamp from a line and convert to ISO 8601 UTC.
    EQ timestamps are in the local time of the machine that wrote the log.
    We use the system's local timezone to convert to UTC.
    Returns None if no timestamp found.
    """
    m = _LOG_TS_PAT.search(line)
    if not m:
        return None
    try:
        from time import strptime, mktime
        t = strptime(m.group(1), "%a %b %d %H:%M:%S %Y")
        # mktime interprets the struct_time as local time and gives us a UTC epoch
        epoch = mktime(t)
        # Convert epoch to UTC datetime
        utc_dt = datetime.datetime.utcfromtimestamp(epoch)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return None


def is_digit_typo(prev: int, new: int) -> bool:
    """True if new looks like prev with an extra digit accidentally typed (e.g. 225->2225)."""
    if new <= prev:
        return False
    sp, sn = str(prev), str(new)
    ratio = new / prev
    return 5 <= ratio <= 20 and sp in sn


def is_chat_noise(item: str) -> bool:
    """
    Return True if the item name looks like conversational text rather than
    an EQ item name. Checks:
      - Contains the word 'skip' (placeholder / skip vote)
      - Contains '@' (raid callout marker, never in item names)
      - More than 7 words (real items are short)
      - Contains a contraction (he's=he is, don't, can't, I'm, they're, etc.)
        NOTE: possessives like Borannin's are NOT contractions and are allowed.
      - Contains common conversational words that never appear in item names
    """
    if re.search(r'\bskip\b', item, re.IGNORECASE):
        return True
    if '@' in item:
        return True
    if len(item.split()) > 7:
        return True
    # Contractions: apostrophe followed by a grammatical suffix (not just 's')
    if re.search(r"'\s*(m|re|ve|ll|d|t|nt)\b", item, re.IGNORECASE):
        return True
    if _CHAT_WORDS.search(item):
        return True
    return False


def _parse_bid_fields(item: str, amount: str, alt_group) -> tuple:
    """
    Normalise item name and alt flag from raw regex captures.
    Handles all known alt formats:
      'Item 100 alt'   — alt_group captures 'alt'
      'Item alt 100'   — alt absorbed into item name
      'Item 100alt'    — alt glued to amount with no space
    Returns (clean_item, amount_str, is_alt).
    """
    is_alt = bool(alt_group) and alt_group.strip().lower() in ('alt', 'a')

    # 'Item alt 100' — alt was absorbed into the item name
    if not is_alt and re.search(r'\balt?\b\s*$', item, re.IGNORECASE):
        item   = re.sub(r'\s*\balt?\b\s*$', '', item, flags=re.IGNORECASE).strip()
        is_alt = True

    # '100alt' or '100a' — alt glued directly to the number with no space
    if not is_alt:
        m = re.match(r'^(\d+)\s*alt?$', amount.strip(), re.IGNORECASE)
        if m:
            amount = m.group(1)
            is_alt = True

    # Strip any x<N> multiplier glued to the amount (e.g. '500x2' -> '500')
    amount = re.sub(r'\s*x\d+\s*$', '', amount.strip(), flags=re.IGNORECASE)

    return item, amount, is_alt


def extract_player_from_log(path):
    """
    Extract the character name from an EQ log filename.
    Handles common patterns:
      - eqlog_Playername_pq.proj.txt (standard)
      - 5-28_eqlog_Playername_pq.proj.txt (date-prefixed)
      - anything_eqlog_Playername_anything.txt
    Returns 'Unknown' if the name cannot be extracted.
    """
    basename = os.path.basename(path)
    # Try standard pattern first
    match = re.search(r"eqlog_(.+?)_pq\.proj\.txt", basename, re.IGNORECASE)
    if match:
        return match.group(1)
    # Fallback: any eqlog_Name_ pattern
    match = re.search(r"eqlog_(.+?)_", basename, re.IGNORECASE)
    if match:
        return match.group(1)
    return "Unknown"


def find_active_log():
    candidates = glob.glob(LOG_GLOB)
    return max(candidates, key=lambda p: os.path.getmtime(p)) if candidates else None


# ---------------------------------------------------------------------------
# Item Validator
# ---------------------------------------------------------------------------

ITEMS_ZIP  = os.path.join(_BASE_PATH, "items.zip")   # EQEmu item database export


class ItemValidator:
    def __init__(self):
        # Primary DB: set of lowercased canonical names from items.zip
        self.db_names    = set()   # lowercase exact names
        self.db_norm     = set()   # normalised (no punctuation/extra spaces)
        self.db_ids      = {}      # lowercase name -> item id (str)
        self.db_norm_ids = {}      # normalised name -> item id (str)
        # Supplement: seed_items.txt for server-specific or newly-seen items
        self.seed_items  = set()
        self._load_db()
        self._load_seed()

    def _load_db(self):
        """Load item names from items.zip into fast lookup sets."""
        import zipfile, csv, io
        try:
            with zipfile.ZipFile(ITEMS_ZIP) as z:
                csv_name = next(n for n in z.namelist() if n.endswith('.csv'))
                with z.open(csv_name) as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding='utf-8', errors='replace'))
                    for row in reader:
                        name = row.get('Name', '').strip()
                        item_id = row.get('id', '').strip()
                        if name:
                            lower_name = name.lower()
                            norm_name = normalize(name)
                            self.db_names.add(lower_name)
                            self.db_norm.add(norm_name)
                            if item_id:
                                self.db_ids[lower_name] = item_id
                                self.db_norm_ids[norm_name] = item_id
            print(f"📦 Loaded {len(self.db_names):,} items from database")
        except (FileNotFoundError, StopIteration):
            print("⚠️  items.zip not found — falling back to seed_items.txt only")

    def _load_seed(self):
        """Load seed_items.txt as a supplement for items not in the DB.
        
        Reads from both the bundled path (inside .exe) and the writable path
        (beside the .exe) so learned items persist across runs.
        """
        for seed_path in (SEED_FILE, SEED_FILE_WRITE):
            try:
                with open(seed_path, "r", encoding="utf-8") as f:
                    for line in f:
                        item = line.strip()
                        if item:
                            self.seed_items.add(item)
            except FileNotFoundError:
                pass
        if self.seed_items:
            print(f"📋 Loaded {len(self.seed_items)} supplemental seed items")

    # Keep items set as a unified view for code that references validator.items
    @property
    def items(self):
        return self.seed_items

    def save_new_item(self, item):
        """Add an item to the seed supplement (not the DB)."""
        self.seed_items.add(item)
        with open(SEED_FILE_WRITE, "a", encoding="utf-8") as f:
            f.write(item + "\n")
        print(f"🆕 Learned new item: {item}")

    def in_db(self, item):
        """Check if an item exists in the game database (not seed)."""
        if item.lower() in self.db_names:
            return True
        if normalize(item) in self.db_norm:
            return True
        return False

    def get_item_id(self, item):
        """Return the item ID (as a string) for an item name, or None if not found."""
        item_id = self.db_ids.get(item.lower())
        if item_id:
            return item_id
        return self.db_norm_ids.get(normalize(item))

    def is_valid(self, item):
        if item.lower().startswith("ancient:"):
            return False
        # Spell and song links should never trigger auctions
        if re.match(r'^(spell|song):\s*', item, re.IGNORECASE):
            return False
        # Heal callouts: CH - <name> - <slot>, CE - <name> - <pct>%
        if re.match(r'^(ch|ce|coh)\s*[-–]', item, re.IGNORECASE):
            return False
        if "ch -" in item.lower() and "mana" in item.lower():
            return False
        if is_chat_noise(item):
            return False
        norm_item = normalize(item)
        if norm_item in BLACKLIST:
            return False

        # 1. Exact DB lookup (O(1))
        if item.lower() in self.db_names:
            return True

        # 2. Normalised DB lookup — handles minor punctuation/case differences
        if norm_item in self.db_norm:
            return True

        # 3. Seed supplement — server-specific or manually added items
        if item in self.seed_items:
            return True
        if norm_item in {normalize(s) for s in self.seed_items}:
            return True

        # 4. If the item was announced via pipe list it bypasses this entirely
        #    (handled in process_line via _is_announced), so we don't need
        #    the old word-count heuristic here. Unknown items are rejected.
        return False


# ---------------------------------------------------------------------------
# Bid Tracker
# ---------------------------------------------------------------------------

class BidTracker:
    def __init__(self, validator):
        self.bids_by_item    = defaultdict(lambda: {'main': {}, 'alt': {}, 'history': []})
        self.bid_order       = []
        self.item_quantities = {}   # item -> total qty
        self.item_remaining  = {}   # item -> copies still to award
        self.announced_items = set()
        self.validator       = validator
        self._table_lines    = 0
        self._closed_snapshots = {}  # item -> snapshot saved at close_item time
        self._current_log    = None  # path to the active log file (set by monitor_log)
        self._max_bids       = defaultdict(lambda: defaultdict(int))  # item -> {player -> max_ever_bid}
        self._announce_time  = None   # wall-clock time of first announcement in current batch
        self._last_bid_time  = {}     # item -> wall-clock time of most recent bid
        print("🛠️  BidTracker initialized — auctions close on gratss message")

    def _erase_table(self):
        if self._table_lines > 0:
            sys.stdout.write(f"\033[{self._table_lines}A\033[J")
            sys.stdout.flush()
            self._table_lines = 0

    @staticmethod
    def _visual_rows(lines, term_width):
        """
        Count how many terminal rows a list of printed lines will occupy,
        accounting for word-wrap.  ANSI escape sequences are stripped before
        measuring because they have zero display width.
        """
        ansi_re = re.compile(r'\x1b\[[0-9;]*[mAJKHF]')
        total = 0
        for line in lines:
            visible = ansi_re.sub('', line)
            # Each logical line occupies at least 1 row; wraps add extra rows.
            # +1 for the newline that print() appends.
            if term_width > 0:
                total += max(1, (len(visible) + term_width - 1) // term_width)
            else:
                total += 1
        return total

    def _print_above_table(self, *lines):
        self._erase_table()
        for line in lines:
            print(line)

    def announce_items(self, items: list):
        added = []
        for raw in items:
            raw = raw.strip()
            if not raw or raw.lower().startswith("ancient:"):
                continue
            qty  = 1
            name = raw
            qm   = QTY_PATTERN.match(raw)
            if qm:
                name = qm.group(1).strip()
                qty  = int(qm.group(2))
            self.announced_items.add(name)
            if name not in self.validator.items and not self.validator.in_db(name):
                self.validator.save_new_item(name)
            if name not in self.item_quantities:
                self.item_quantities[name] = qty
                self.item_remaining[name]  = qty
            label = f"{name} (x{qty})" if qty > 1 else name
            added.append(label)
        if added:
            # Record announcement time (first announcement starts the clock)
            if self._announce_time is None:
                self._announce_time = time.time()
            self._print_above_table(
                f"📢 Auction announced: {CYAN}{' | '.join(added)}{RESET}"
            )

    def _is_announced(self, item: str) -> bool:
        if item in self.announced_items:
            return True
        norm = normalize(item)
        for a in self.announced_items:
            if fuzz.ratio(norm, normalize(a)) >= 85:
                return True
        return False

    def update_bid(self, item, player, amount, is_alt=False):
        amount    = int(amount)
        bid_type  = 'alt' if is_alt else 'main'
        item_bids = self.bids_by_item[item][bid_type]
        history   = self.bids_by_item[item]['history']
        prev      = item_bids.get(player, 0)

        if amount < MIN_BID:
            self._print_above_table(f"⛔ Ignored bid below minimum from {player}: {amount} DKP")
            return

        if item not in self.bid_order:
            self.bid_order.append(item)

        if amount > prev:
            item_bids[player] = amount
            history.append((player, amount, bid_type, False))
            # Track max ever bid per player per item
            if amount > self._max_bids[item][player]:
                self._max_bids[item][player] = amount
            self._last_bid_time[item] = time.time()
        elif prev > 0 and is_digit_typo(amount, prev):
            item_bids[player] = amount
            history.append((player, amount, bid_type, True))
            self._last_bid_time[item] = time.time()
            self._print_above_table(
                f"🔧 Typo correction: {GREEN}{player}{RESET} on [{item}]: "
                f"{YELLOW}{prev}{RESET} -> {YELLOW}{amount} DKP{RESET}"
            )
        elif prev > 0:
            # Lower bid — treat as self-correction (voice chat correction, etc.)
            # Record in history and update the active bid to the lower amount
            item_bids[player] = amount
            history.append((player, amount, bid_type, True))
            self._last_bid_time[item] = time.time()
            self._print_above_table(
                f"↩️  Bid correction: {GREEN}{player}{RESET} on [{item}]: "
                f"{YELLOW}{prev}{RESET} -> {YELLOW}{amount} DKP{RESET} (was higher)"
            )

    def retract_bid(self, player, new_amount):
        """
        Called when a player says 'sorry - meant <N>' or similar.
        Finds the most recent active item the player bid on and replaces
        their bid with new_amount, regardless of whether it's lower.
        Returns True if a correction was applied.
        """
        new_amount = int(new_amount)
        # Search bid_order in reverse to find the most recent item this player bid on
        for item in reversed(self.bid_order):
            for bid_type in ('main', 'alt'):
                if player in self.bids_by_item[item][bid_type]:
                    prev = self.bids_by_item[item][bid_type][player]
                    self.bids_by_item[item][bid_type][player] = new_amount
                    self.bids_by_item[item]['history'].append(
                        (player, new_amount, bid_type, True)
                    )
                    self._last_bid_time[item] = time.time()
                    self._print_above_table(
                        f"🔧 Retraction: {GREEN}{player}{RESET} on [{item}]: "
                        f"{YELLOW}{prev}{RESET} -> {YELLOW}{new_amount} DKP{RESET}"
                    )
                    return True
        return False

    def _format_history(self, history, term_width=80):
        """
        Format bid history as compact wrapped lines.
        Each token: "Player 250" or "Player 250(A)" or "*Player 225" for typo fixes.
        Tokens are space-separated and wrapped at term_width.
        """
        if not history:
            return ["  (no bids recorded)"]

        tokens = []
        for player, amt, btype, is_fix in history:
            prefix = "*" if is_fix else ""
            suffix = "(A)" if btype == 'alt' else ""
            tokens.append(f"{prefix}{player} {amt}{suffix}")

        # Wrap tokens into lines that fit within term_width - 2 (for indent)
        indent  = "  "
        max_w   = term_width - len(indent)
        lines   = []
        current = ""
        for tok in tokens:
            candidate = (current + "  " + tok) if current else tok
            if len(candidate) <= max_w:
                current = candidate
            else:
                if current:
                    lines.append(indent + current)
                current = tok
        if current:
            lines.append(indent + current)
        return lines

    def close_item(self, item, winner, amount, timestamp=None):
        qty       = self.item_quantities.get(item, 1)
        remaining = self.item_remaining.get(item, 1)
        history   = list(self.bids_by_item[item]['history'])  # snapshot before deletion

        # Save full snapshot so ungratss can reopen this item
        self._closed_snapshots[item] = {
            'main':      dict(self.bids_by_item[item]['main']),
            'alt':       dict(self.bids_by_item[item]['alt']),
            'history':   history,
            'qty':       qty,
            'remaining': remaining,
            'amount':    amount,
            'timestamp': timestamp,
        }

        term_width  = shutil.get_terminal_size((100, 40)).columns
        sep         = "-" * min(60, term_width)
        n_bids      = len(history)
        hist_header = f"  Bid history ({n_bids} bid{'s' if n_bids != 1 else ''}):"
        hist_lines  = self._format_history(history, term_width)

        def _sold_lines(label_extra=""):
            return [
                f"\n{sep}",
                f"  🎉 {BOLD}SOLD{RESET}{label_extra}  {PINK}[{item}]{RESET}"
                f"  ->  {GREEN}{winner}{RESET}  {YELLOW}{amount} DKP{RESET}",
                hist_header,
                *hist_lines,
                f"{sep}\n",
            ]

        if qty > 1:
            new_remaining = remaining - 1
            self.item_remaining[item] = new_remaining
            copy_label = f" (copy {qty - remaining + 1}/{qty})"
            if new_remaining <= 0:
                if item in self.bids_by_item:
                    del self.bids_by_item[item]
                if item in self.bid_order:
                    self.bid_order.remove(item)
                self.item_quantities.pop(item, None)
                self.item_remaining.pop(item, None)
                self._last_bid_time.pop(item, None)
                self._print_above_table(*_sold_lines(copy_label + " [all copies awarded]"))
            else:
                self._print_above_table(
                    *_sold_lines(f"{copy_label} [{new_remaining} cop{'y' if new_remaining == 1 else 'ies'} remaining]")
                )
            if _HISTORY_ENABLED:
                _dkp_logger.record_auction(
                    item, winner, amount, history,
                    log_file_path=getattr(self, '_current_log', None),
                    timestamp=timestamp,
                )
        else:
            if item in self.bids_by_item:
                del self.bids_by_item[item]
            if item in self.bid_order:
                self.bid_order.remove(item)
            self._last_bid_time.pop(item, None)
            self._print_above_table(*_sold_lines())
            if _HISTORY_ENABLED:
                _dkp_logger.record_auction(
                    item, winner, amount, history,
                    log_file_path=getattr(self, '_current_log', None),
                    timestamp=timestamp,
                )

        # Reset announcement clock if no active auctions remain
        if not self.bid_order:
            self._announce_time = None

    def clear_item(self, item):
        """
        Called when 'You tell your raid, clear <item>' is seen.
        Removes the item from the table without awarding it, and logs
        the bid history so there's a record of what was placed.
        Returns True if the item was found and cleared, False otherwise.
        """
        # Fuzzy-match the item name against active auctions
        target = None
        needle = item.lower()
        for active in self.bid_order:
            if needle in active.lower() or active.lower() in needle:
                target = active
                break
        if target is None:
            return False

        history    = list(self.bids_by_item[target]['history'])
        term_width = shutil.get_terminal_size((100, 40)).columns
        sep        = "-" * min(60, term_width)
        n_bids     = len(history)
        hist_lines = self._format_history(history, term_width)

        # Remove from tracking
        if target in self.bids_by_item:
            del self.bids_by_item[target]
        if target in self.bid_order:
            self.bid_order.remove(target)
        self.item_quantities.pop(target, None)
        self.item_remaining.pop(target, None)
        self._last_bid_time.pop(target, None)

        self._print_above_table(
            f"\n{sep}",
            f"  🗑️  {BOLD}CLEARED{RESET}  {PINK}[{target}]{RESET}  "
            f"{DIM}(no award — manually cleared){RESET}",
            f"  Bid history ({n_bids} bid{'s' if n_bids != 1 else ''}):",
            *hist_lines,
            f"{sep}\n",
        )

        # Reset announcement clock if no active auctions remain
        if not self.bid_order:
            self._announce_time = None

        return True

    def reopen_item(self, item, erroneous_winner):
        """
        Called when 'item;amount;player ungratss' is seen.
        Restores the item to the live table using the snapshot saved at close time.
        Returns True if the item was found in snapshots and reopened.
        """
        # Fuzzy-match against closed snapshots
        needle = item.lower()
        target = None
        for key in self._closed_snapshots:
            if needle in key.lower() or key.lower() in needle:
                target = key
                break
        if target is None:
            return False

        snap = self._closed_snapshots.pop(target)

        # Restore bids
        self.bids_by_item[target]['main']    = snap['main']
        self.bids_by_item[target]['alt']     = snap['alt']
        self.bids_by_item[target]['history'] = snap['history']
        if target not in self.bid_order:
            self.bid_order.append(target)
        if snap['qty'] > 1:
            self.item_quantities[target] = snap['qty']
            # Increment remaining since we're undoing one award
            self.item_remaining[target]  = snap['remaining'] + 1
        # Restore last bid timer (reset to now since auction is reopened)
        self._last_bid_time[target] = time.time()
        # Restore announcement clock if it was cleared
        if self._announce_time is None:
            self._announce_time = time.time()

        term_width = shutil.get_terminal_size((100, 40)).columns
        sep        = "-" * min(60, term_width)
        self._print_above_table(
            f"\n{sep}",
            f"  ↩️  {BOLD}UNGRATSS{RESET}  {PINK}[{target}]{RESET}  "
            f"— award to {GREEN}{erroneous_winner}{RESET} reversed, auction reopened",
            f"{sep}\n",
        )
        return True

    def _all_bids_sorted(self, item):
        """Flat sorted bid list, one entry per player (their current bid across main/alt).
        Returns list of (player, current_amount, type, max_amount) sorted by current descending."""
        combined = {}
        for p, a in self.bids_by_item[item]['main'].items():
            if a > combined.get(p, (0, ''))[0]:
                combined[p] = (a, 'M')
        for p, a in self.bids_by_item[item]['alt'].items():
            if a > combined.get(p, (0, ''))[0]:
                combined[p] = (a, 'A')
        # Include max bid for display
        max_bids = self._max_bids.get(item, {})
        result = []
        for p, (a, t) in combined.items():
            max_a = max_bids.get(p, a)
            result.append((p, a, t, max_a))
        return sorted(result, key=lambda x: -x[1])

    def _award_price(self, item):
        """For qty-N items, award price = Nth highest bid. Returns (price, qty)."""
        qty = self.item_quantities.get(item, 1)
        if qty <= 1:
            return 0, 1
        bids = self._all_bids_sorted(item)
        price = bids[qty - 1][1] if len(bids) >= qty else (bids[-1][1] if bids else 0)
        return price, qty

    def _leader(self, item):
        mains = self.bids_by_item[item]['main']
        alts  = self.bids_by_item[item]['alt']
        if mains:
            player, amt = max(mains.items(), key=lambda x: x[1])
            return player, amt, 'Main'
        elif alts:
            player, amt = max(alts.items(), key=lambda x: x[1])
            return player, amt, 'Alt'
        return None, 0, '-'

    @staticmethod
    def _fmt_elapsed(seconds):
        """Format seconds into a compact human-readable string like '2m 15s' or '1h 03m'."""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes, secs = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {secs:02d}s"
        hours, mins = divmod(minutes, 60)
        return f"{hours}h {mins:02d}m"

    def copy_status_to_clipboard(self):
        """
        Build a one-line summary of all active auctions and copy to clipboard.
        Uses EQ item link format: \x12 + 6-digit zero-padded ID + item name
        Format: \x12NNNNNN ItemName 300m/200a, \x12NNNNNN ItemName 150m
        Falls back to plain name if item ID is not found.
        """
        if not self.bid_order:
            self._print_above_table(f"{DIM}📋 Nothing to copy — no active auctions{RESET}")
            return

        LINK_CHAR = '\x12'

        parts = []
        for item in self.bid_order:
            qty = self.item_quantities.get(item, 1)
            bids = self._all_bids_sorted(item)  # sorted desc by current amount

            # Build item link or plain name
            item_id = self.validator.get_item_id(item)
            if item_id:
                item_label = f"{LINK_CHAR}{item_id.zfill(6)} {item}{LINK_CHAR}"
            else:
                item_label = item

            if not bids:
                parts.append(f"{item_label} (no bids)")
                continue

            # For multi-qty items, show top N bids; for single-qty, show top bid
            top_n = min(qty, len(bids))
            bid_strs = []
            for p, amt, btype, max_a in bids[:top_n]:
                tag = "m" if btype == "M" else "a"
                bid_strs.append(f"{amt}{tag}")
            parts.append(f"{item_label} {'/'.join(bid_strs)}")

        summary = "/rs Calling loot soon!  Status: " + ", ".join(parts)

        # Copy to system clipboard
        try:
            proc = subprocess.Popen(
                ["clip.exe"], stdin=subprocess.PIPE, shell=False
            )
            proc.communicate(input=summary.encode("utf-8"))
            self._print_above_table(
                f"📋 Copied to clipboard: {DIM}{summary}{RESET}"
            )
        except (OSError, subprocess.SubprocessError) as e:
            self._print_above_table(
                f"⚠️  Clipboard copy failed: {e}",
                f"   {summary}",
            )

    def display_table(self):
        if not self.bid_order:
            self._erase_table()
            return

        term_width = shutil.get_terminal_size((120, 40)).columns
        MIN_COL    = 28   # minimum column width before we wrap to next page
        MAX_COL    = 50   # cap so wide terminals don't spread too thin

        n_items = len(self.bid_order)

        # How many columns fit at MIN_COL width?
        cols_per_page = max(1, term_width // MIN_COL)
        # Actual column width: fill the terminal evenly, capped at MAX_COL
        col_width = min(MAX_COL, term_width // min(cols_per_page, n_items))

        # Split items into pages
        pages = [self.bid_order[i:i + cols_per_page]
                 for i in range(0, n_items, cols_per_page)]

        n_total = len(self.bid_order)
        title   = f"  ⚔  Running Auctions ({n_total} item{'s' if n_total != 1 else ''})"
        if self._announce_time:
            elapsed = self._fmt_elapsed(time.time() - self._announce_time)
            title += f"  ⏱ {elapsed} since announce"
        title += "  ⚔  "

        block = [""]

        for page_idx, page_items in enumerate(pages):
            n_page = len(page_items)
            sep    = "-" * min(col_width * n_page, term_width)

            # Title only on first page
            if page_idx == 0:
                block += [sep, f"{CYAN}{BOLD}{title}{RESET}", sep]
            else:
                block += [sep]   # continuation separator between pages

            # Header row
            header = ""
            for item in page_items:
                qty   = self.item_quantities.get(item, 1)
                rem   = self.item_remaining.get(item, qty)
                label = f"[{item}]" + (f" x{rem}" if qty > 1 else "")
                header += f"{PINK}{BOLD}{label:<{col_width}}{RESET}"
            block.append(header)

            # Per-item elapsed since last bid
            now = time.time()
            elapsed_row = ""
            has_any_bid_time = any(item in self._last_bid_time for item in page_items)
            if has_any_bid_time:
                for item in page_items:
                    if item in self._last_bid_time:
                        el = self._fmt_elapsed(now - self._last_bid_time[item])
                        cell = f"  ⏱ last bid {el} ago"
                    else:
                        cell = f"  (no bids)"
                    elapsed_row += f"{DIM}{cell:<{col_width}}{RESET}"
                block.append(elapsed_row)

            # Award-price row (qty items only)
            if any(self.item_quantities.get(i, 1) > 1 for i in page_items):
                price_row = ""
                for item in page_items:
                    award, qty = self._award_price(item)
                    if qty > 1 and award > 0:
                        price_row += f"{ORANGE}{'Award price: ' + str(award) + ' DKP':<{col_width}}{RESET}"
                    else:
                        price_row += f"{'':<{col_width}}"
                block.append(price_row)

            # Bid rows
            per_item_bids = [
                (item, self.item_quantities.get(item, 1), self._all_bids_sorted(item))
                for item in page_items
            ]
            max_rows = max((len(b) for _, _, b in per_item_bids), default=0)
            for i in range(max_rows):
                row = ""
                for item, qty, bids in per_item_bids:
                    if i < len(bids):
                        p, a, t, max_a = bids[i]
                        # Show max bid alongside current if they differ (correction occurred)
                        if max_a > a:
                            cell = f"  {p} {a}dkp (was {max_a}) [{t}]"
                        else:
                            cell = f"  {p} {a}dkp [{t}]"
                        if qty > 1 and i < qty:
                            row += f"{BOLD}{GREEN}{cell:<{col_width}}{RESET}"
                        else:
                            row += f"{cell:<{col_width}}"
                    else:
                        row += f"{'':<{col_width}}"
                block.append(row)

            block.append(sep)

        block.append("")

        self._erase_table()
        for line in block:
            print(line)
        self._table_lines = self._visual_rows(block, term_width)


# ---------------------------------------------------------------------------
# Priority Roll Tracker
# ---------------------------------------------------------------------------

class PriorityRollTracker:
    def __init__(self):
        self.active_item = None
        self.main_max    = None
        self.alt_max     = None
        self.rolls       = {}
        self.timer       = None

    def parse_roll_call(self, text):
        match = ROLL_CALL_PATTERN.search(text)
        if match:
            item, main, alt = match.groups()
            item = item.strip()

            # Strip the log timestamp/channel prefix if present — the regex
            # can match deep inside a line, so extract just the tail after
            # the last quote or comma that precedes the item name.
            # e.g. "...tells the raid,  'Leggings of the Fiery Star 1000/500'"
            # → item = "Leggings of the Fiery Star"
            quote_match = re.search(r"['\"](.+)$", item)
            if quote_match:
                item = quote_match.group(1).strip()

            # Reject if the item part looks like chat noise or is too long
            if is_chat_noise(item):
                return False
            # Real item names are short; a sentence-length match is noise
            if len(item.split()) > 8:
                return False
            # EQ item names always contain at least one capitalised word
            # (proper nouns). Pure lowercase phrases like "whats on the" are chat.
            if not any(w[0].isupper() for w in item.split() if w):
                return False
            # Both numbers must be plausible roll ranges (1–9999)
            main_n, alt_n = int(main), int(alt)
            if main_n < 1 or alt_n < 1:
                return False

            self.active_item = item
            self.main_max    = main_n
            self.alt_max     = alt_n
            self.rolls       = {}
            self.timer       = time.time()
            print(f"🎯 Priority Roll started for {PINK}{self.active_item}{RESET} "
                  f"— Mains: 1-{self.main_max}, Alts: 1-{self.alt_max}")
            return True
        return False

    def add_roll(self, player, rolled, range_max):
        if not self.active_item:
            return
        if rolled > range_max:
            print(f"⚠️  Invalid roll from {GREEN}{player}{RESET}: rolled {YELLOW}{rolled}{RESET} > {range_max}")
            return
        self.rolls[player] = (rolled, range_max)
        print(f"🎲 {GREEN}{player}{RESET} rolled {ORANGE}{rolled}{RESET} out of {YELLOW}{range_max}{RESET}")

    def check_roll_timeout(self):
        if not self.active_item or time.time() - self.timer < 60:
            return
        mains = {p: r for p, (r, rng) in self.rolls.items() if rng == self.main_max}
        alts  = {p: r for p, (r, rng) in self.rolls.items() if rng == self.alt_max}
        if mains:
            winner = max(mains.items(), key=lambda x: x[1])
            print(f"🎉 Roll ended — {GREEN}{winner[0]}{RESET} wins with {ORANGE}{winner[1]}{RESET} (Main)")
        elif alts:
            winner = max(alts.items(), key=lambda x: x[1])
            print(f"🎉 Roll ended — {GREEN}{winner[0]}{RESET} wins with {ORANGE}{winner[1]}{RESET} (Alt)")
        else:
            print("❌ No valid rolls received.")
        self.active_item = None


# ---------------------------------------------------------------------------
# Line processor
# ---------------------------------------------------------------------------

def process_line(line, tracker, roll_tracker, player_name):
    """
    Returns:
      'bid'    — a bid was recorded, redraw the table
      'gratss' — an item was closed, redraw the table
      None     — nothing actionable
    """
    # --- ignore noisy channels and flagged content ---
    if IGNORED_CHANNEL_PATTERN.search(line) or IGNORED_CONTENT_PATTERN.search(line):
        return None

    # --- auction announcement ---
    am = AUCTION_ANNOUNCE_PATTERN.search(line)
    if am:
        clean = [it.strip() for it in am.group(1).split('|') if it.strip()]
        if clean:
            tracker.announce_items(clean)
        return 'announce'

    # --- manual clear: You tell your raid, 'clear <item>' ---
    cm = CLEAR_PATTERN.search(line)
    if cm:
        item_name = (cm.group(1) or cm.group(2)).strip()
        if tracker.clear_item(item_name):
            return 'gratss'   # reuse gratss signal — triggers table redraw
        return None

    # --- gratss close ---
    inner  = line
    quoted = re.search(r"'(.+)'", line)
    if quoted:
        inner = quoted.group(1)
    inner = inner.strip()

    # Check ungratss BEFORE gratss — ungratss contains the word gratss
    um = UNGRATSS_PATTERN.match(inner)
    if um:
        item, amount, loser = um.group(1).strip(), int(um.group(2)), um.group(3).strip()
        if tracker.reopen_item(item, loser):
            return 'gratss'   # triggers table redraw
        return None

    gm = GRATSS_PATTERN.match(inner)
    if gm:
        item, amount, winner = gm.group(1).strip(), int(gm.group(2)), gm.group(3).strip()
        ts = _extract_log_timestamp(line)
        tracker.close_item(item, winner, amount, timestamp=ts)
        return 'gratss'

    # --- priority roll call ---
    if roll_tracker.parse_roll_call(line):
        return None

    # --- dice roll (consumed by caller) ---
    if ROLL_PATTERN.search(line):
        return None

    # --- retraction: "sorry - meant 460" style corrections ---
    rm = RETRACTION_PATTERN.search(line)
    if rm:
        player, new_amount = rm.group(1).strip(), int(rm.group(2))
        if tracker.retract_bid(player, new_amount):
            return 'bid'
        return None

    # --- bids: other players ---
    # Groups: 1=player, 2=item, 3=amount, 4=x<N> multiplier (ignored), 5=alt/a
    m = BID_PATTERN.search(line)
    if m:
        player = m.group(1).strip()
        item, amount, is_alt = _parse_bid_fields(m.group(2).strip(), m.group(3).strip(), m.group(5))
        if not is_chat_noise(item) and (tracker._is_announced(item) or tracker.validator.is_valid(item)):
            tracker.update_bid(item, player, amount, is_alt)
            return 'bid'
        return None

    # --- bids: your own character ---
    # Groups: 1=item, 2=amount, 3=x<N> multiplier (ignored), 4=alt/a
    m = YOU_PATTERN.search(line)
    if m:
        item, amount, is_alt = _parse_bid_fields(m.group(1).strip(), m.group(2).strip(), m.group(4))
        if not is_chat_noise(item) and (tracker._is_announced(item) or tracker.validator.is_valid(item)):
            tracker.update_bid(item, player_name, amount, is_alt)
            return 'bid'
        return None

    return None


# ---------------------------------------------------------------------------
# Log monitor
# ---------------------------------------------------------------------------

def monitor_log(tracker, roll_tracker):
    current_log    = None
    current_player = None
    file_pos       = 0       # byte offset where we last stopped reading
    last_file_check = 0
    last_roll_check = 0
    last_table_refresh = 0
    TABLE_REFRESH_INTERVAL = 5  # seconds between timer refreshes

    print("🔍 Scanning for active EverQuest log file...")
    if _HAS_MSVCRT:
        print(f"   {DIM}Press 'c' anytime to copy bid summary to clipboard{RESET}")

    try:
        while True:
            now = time.time()

            # Check for keyboard shortcuts (non-blocking)
            if _HAS_MSVCRT and msvcrt.kbhit():
                key = msvcrt.getch()
                if key == b'c' or key == b'C':
                    tracker.copy_status_to_clipboard()
                    tracker.display_table()

            if now - last_file_check >= FILE_POLL_INTERVAL:
                last_file_check = now
                active = find_active_log()
                if active and active != current_log:
                    new_player = extract_player_from_log(active)
                    current_log    = active
                    current_player = new_player
                    tracker._current_log = active
                    # Start reading from the end so we only see new lines
                    file_pos = os.path.getsize(current_log)
                    print(f"\n{'='*60}")
                    print(f"📂 Now watching: {os.path.basename(current_log)}")
                    print(f"👤 Player: {CYAN}{current_player}{RESET}")
                    print(f"{'='*60}\n")

            if current_log is None:
                time.sleep(1)
                continue

            # Open, read new content, close immediately — no persistent lock
            lines_to_process = []
            try:
                file_size = os.path.getsize(current_log)
                # If file was truncated/recreated, reset to beginning
                if file_size < file_pos:
                    file_pos = 0
                with open(current_log, "r", encoding="utf-8", errors="ignore") as fh:
                    fh.seek(file_pos)
                    new_data = fh.read()
                    file_pos = fh.tell()
            except OSError:
                time.sleep(1)
                continue

            if not new_data:
                time.sleep(0.5)
                if now - last_roll_check >= 5:
                    last_roll_check = now
                    roll_tracker.check_roll_timeout()
                # Periodic table refresh to keep timers current
                if tracker.bid_order and now - last_table_refresh >= TABLE_REFRESH_INTERVAL:
                    last_table_refresh = now
                    tracker.display_table()
                continue

            lines_to_process = new_data.splitlines()

            i = 0
            while i < len(lines_to_process):
                line = lines_to_process[i].strip()
                i += 1
                if not line:
                    continue

                roll_match = ROLL_PATTERN.search(line)
                if roll_match:
                    roller = roll_match.group(1)
                    # The roll result is on the next line
                    if i < len(lines_to_process):
                        result_line = lines_to_process[i].strip()
                        i += 1
                        result_match = ROLL_RESULT_PATTERN.search(result_line)
                        if result_match:
                            range_max, result = map(int, result_match.groups())
                            roll_tracker.add_roll(roller, result, range_max)
                    continue

                result = process_line(line, tracker, roll_tracker, current_player)
                if result in ('bid', 'gratss', 'announce'):
                    tracker.display_table()

    finally:
        pass  # no persistent file handle to close


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"DKP Bid Tracker v{__version__}")
    print("=" * 40)
    validator    = ItemValidator()
    tracker      = BidTracker(validator)
    roll_tracker = PriorityRollTracker()
    try:
        monitor_log(tracker, roll_tracker)
    except KeyboardInterrupt:
        print("\nExiting.")
