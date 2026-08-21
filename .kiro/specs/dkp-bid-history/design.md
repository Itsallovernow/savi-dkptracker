# Design Document: DKP Bid History

## Overview

Three new files are added alongside the existing `dkptrackerv3.py`:

- **`dkp_logger.py`** — Python module (stdlib only). Called from `close_item()` in `dkptrackerv3.py` to persist each closed auction to `dkp_history.json`.
- **`dkp_import.py`** — CLI script. Replays one or more EQ log files through the existing tracker logic and writes auction records to `dkp_history.json`.
- **`dkp_history.html`** — Self-contained single-file HTML/JS/CSS viewer. Reads `dkp_history.json` and provides item history, player profiles, and statistics.

`dkptrackerv3.py` requires only two small changes: import `dkp_logger` and call `record_auction()` at the end of `close_item()`.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Live Monitor                          │
│  dkptrackerv3.py                                        │
│    monitor_log() → process_line() → BidTracker          │
│      close_item()                                       │
│        └─► dkp_logger.record_auction()                  │
│                  │                                      │
│                  ▼                                      │
│           dkp_history.json  ◄── dkp_import.py (CLI)    │
│                  │                                      │
│                  ▼                                      │
│           dkp_history.html (browser viewer)             │
└─────────────────────────────────────────────────────────┘
```

---

## dkp_history.json Schema

```json
{
  "version": 1,
  "records": [
    {
      "item_name": "Ring of Flamewarding",
      "winner": "Caezar",
      "amount": 370,
      "timestamp": "2026-04-28T21:23:00Z",
      "raid_session_id": "a3f2c1...",
      "dedup_key": "9b4e7d...",
      "log_source": "eqlog_Warderallover_pq.proj.txt",
      "bids": [
        { "player": "Dynasty",       "amount": 10,   "bid_type": "alt",  "is_correction": false },
        { "player": "Caezar",        "amount": 200,  "bid_type": "main", "is_correction": false },
        { "player": "Marethe",       "amount": 2225, "bid_type": "main", "is_correction": false },
        { "player": "Marethe",       "amount": 225,  "bid_type": "main", "is_correction": true  },
        { "player": "Caezar",        "amount": 370,  "bid_type": "main", "is_correction": false }
      ]
    }
  ]
}
```

**Field notes:**
- `version`: integer, currently `1`. Increment when schema changes.
- `raid_session_id`: SHA-256 of the YYYY-MM-DD date extracted from the first timestamp line of the log file. Ties records from multiple officers to the same raid regardless of timezone.
- `dedup_key`: SHA-256 of `item_name + \x00 + winner + \x00 + str(amount) + \x00 + raid_session_id`. Used to discard duplicate records from concurrent officer monitors.
- `log_source`: basename of the log file, for debugging.
- `bids`: ordered array, chronological. `is_correction: true` marks typo corrections and retractions.

---

## dkp_logger.py

### Public API

```python
def record_auction(
    item_name: str,
    winner: str,
    amount: int,
    history: list,          # list of (player, amount, bid_type, is_correction)
    log_file_path: str | None,
    history_file: str = "dkp_history.json",
) -> bool:
    """
    Persist one closed auction to history_file.
    Returns True if written, False if duplicate or error.
    """
```

### Implementation

```python
import hashlib, json, os, sys, datetime, re

HISTORY_VERSION = 1

def _raid_session_id(log_file_path):
    """Extract YYYY-MM-DD from first timestamp in log file, hash it."""
    ts_pat = re.compile(r'\[(\w+ \w+ +\d+ \d+:\d+:\d+ \d+)\]')
    if log_file_path:
        try:
            with open(log_file_path, encoding='utf-8', errors='ignore') as f:
                for line in f:
                    m = ts_pat.search(line)
                    if m:
                        from time import strptime
                        import calendar
                        t = strptime(m.group(1), "%a %b %d %H:%M:%S %Y")
                        date_str = f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
                        return hashlib.sha256(date_str.encode()).hexdigest()
        except OSError:
            pass
    # Fallback: current UTC date
    date_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    return hashlib.sha256(date_str.encode()).hexdigest()

def _dedup_key(item_name, winner, amount, raid_session_id):
    raw = f"{item_name}\x00{winner}\x00{amount}\x00{raid_session_id}"
    return hashlib.sha256(raw.encode()).hexdigest()

def _load(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": HISTORY_VERSION, "records": []}

def _save(path, data):
    # Atomic write: write to .tmp then rename
    tmp = path + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def record_auction(item_name, winner, amount, history,
                   log_file_path=None, history_file="dkp_history.json"):
    session_id = _raid_session_id(log_file_path)
    key        = _dedup_key(item_name, winner, amount, session_id)

    # File-level lock for concurrent writers
    import fcntl  # Unix; Windows fallback below
    lock_path = history_file + ".lock"
    try:
        lock_fd = open(lock_path, 'w')
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except AttributeError:
            pass  # Windows: no flock, accept rare race on small files

        data = _load(history_file)
        existing_keys = {r['dedup_key'] for r in data.get('records', [])}
        if key in existing_keys:
            print(f"[dkp_logger] Duplicate skipped: {item_name} → {winner} {amount}dkp")
            return False

        record = {
            "item_name":      item_name,
            "winner":         winner,
            "amount":         amount,
            "timestamp":      datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "raid_session_id": session_id,
            "dedup_key":      key,
            "log_source":     os.path.basename(log_file_path) if log_file_path else "",
            "bids": [
                {"player": p, "amount": a, "bid_type": t, "is_correction": c}
                for p, a, t, c in history
            ],
        }
        data.setdefault('records', []).append(record)
        _save(history_file, data)
        return True

    except OSError as e:
        print(f"[dkp_logger] Write error: {e}", file=sys.stderr)
        return False
    finally:
        try:
            lock_fd.close()
        except Exception:
            pass
```

### Integration with dkptrackerv3.py

Two changes only:

1. At the top of `dkptrackerv3.py`, add:
   ```python
   try:
       import dkp_logger as _dkp_logger
       _HISTORY_ENABLED = True
   except ImportError:
       _HISTORY_ENABLED = False
   ```

2. At the end of `close_item()`, after `_print_above_table(...)`:
   ```python
   if _HISTORY_ENABLED:
       _dkp_logger.record_auction(
           item, winner, amount, history,
           log_file_path=getattr(self, '_current_log', None)
       )
   ```
   `_current_log` is set on the tracker instance from `monitor_log()`.

---

## dkp_import.py

### CLI Interface

```
python dkp_import.py <logfile> [<logfile> ...] [--output dkp_history.json]

Arguments:
  logfile       One or more EQ log file paths to replay
  --output      Path to history file (default: dkp_history.json)
  --dry-run     Parse and report without writing
```

### Implementation Approach

```python
from dkptrackerv3 import (
    ItemValidator, BidTracker, PriorityRollTracker,
    process_line, ROLL_PATTERN, ROLL_RESULT_PATTERN,
    extract_player_from_log
)
import dkp_logger

# For each log file:
#   1. Create fresh validator/tracker/roll_tracker
#   2. Monkey-patch BidTracker.close_item to capture records instead of printing
#   3. Feed all lines through process_line()
#   4. Call dkp_logger.record_auction() for each captured record
#   5. Print summary: N found, N written, N skipped
```

The monkey-patch approach avoids duplicating parsing logic. `close_item()` is wrapped to intercept the `(item, winner, amount, history)` tuple before the display code runs.

---

## dkp_history.html Architecture

### File Structure (all inlined)

```
dkp_history.html
├── <style>          CSS — dark theme matching terminal aesthetic
├── <script>
│   ├── PATTERNS     JS regex ports of BID_PATTERN, GRATSS_PATTERN, etc.
│   ├── Parser       parseLogFile(text) → AuctionRecord[]
│   ├── Dedup        raidSessionId(text), dedupKey(item,winner,amount,sid)
│   ├── Store        { records[], importedKeys Set }
│   ├── Router       hashchange handler → render(view, params)
│   ├── Views
│   │   ├── renderItemList()     searchable dropdown + drop cards
│   │   ├── renderPlayerProfile(name)
│   │   ├── renderStats()
│   │   └── renderDropCards(records)  horizontal scroll
│   ├── Chart        drawTrendLine(canvas, points)  — hand-rolled SVG
│   └── IO           loadHistory(), importLog(), exportJSON()
└── <body>           nav bar + #main-content div
```

### UI Layout

```
┌─────────────────────────────────────────────────────────┐
│  ⚔ DKP History  [Items ▼] [Stats] [Import] [Export↓]   │
├─────────────────────────────────────────────────────────┤
│  Search: [Ring of Flamewarding          ▼]              │
│                                                         │
│  ← scroll ──────────────────────────────── scroll →    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐               │
│  │2026-05-11│ │2026-04-28│ │2026-03-20│  ...           │
│  │Winner:   │ │Winner:   │ │Winner:   │               │
│  │Caezar    │ │Caezar    │ │Adann     │               │
│  │370 DKP   │ │370 DKP   │ │200 DKP   │               │
│  │──────────│ │──────────│ │──────────│               │
│  │Dynasty10A│ │Dynasty10A│ │Caezar150 │               │
│  │Caezar200 │ │Caezar200 │ │Adann 200 │               │
│  │*Marethe  │ │*Marethe  │ │          │               │
│  │ 225 →370 │ │ 225 →370 │ │          │               │
│  └──────────┘ └──────────┘ └──────────┘               │
│                                                         │
│  Price Trend ──────────────────────────────────────    │
│  400 ┤                    ●                            │
│  300 ┤         ●                                       │
│  200 ┤  ●                                              │
│      └──────────────────────────────────────────────   │
└─────────────────────────────────────────────────────────┘
```

### Price Trend Chart

Hand-rolled SVG — no external library needed:

```javascript
function drawTrendLine(containerId, points) {
    // points: [{date: '2026-03-20', amount: 200}, ...]
    // Renders an <svg> with polyline, axis labels, dot markers
    // ~60 lines of JS, no dependencies
}
```

### Browser Log Import

```javascript
const GRATSS_RE = /^(.+?);\s*(\d+);\s*(.+?)\s+gratss\b/i;
const BID_RE    = /([A-Za-z].+?)\s*(\d+)\s*(?:x\d+)?\s*(?:alt|a)?[^']*$/i;

function parseLogFile(text) {
    // Port of dkptrackerv3 logic in ~150 lines of JS
    // Returns AuctionRecord[]
}
```

SHA-256 in the browser uses `crypto.subtle.digest('SHA-256', ...)` (async, returns Promise).

### State Management

```javascript
const Store = {
    records: [],          // loaded from dkp_history.json
    importedKeys: new Set(),
    view: 'items',        // 'items' | 'player' | 'stats'
    selectedItem: null,
    selectedPlayer: null,
};
```

Navigation is hash-based: `#item=Ring+of+Flamewarding`, `#player=Adann`, `#stats`. The `hashchange` event re-renders the appropriate view.

---

## Correctness Properties

1. **Round-trip**: `json.loads(json.dumps(record)) == record` for any Auction Record.
2. **Dedup idempotency**: calling `record_auction()` twice with identical arguments writes exactly one record.
3. **Dedup cross-officer**: two records with the same `(item, winner, amount, raid_session_id)` but different `log_source` produce the same `dedup_key` and only one is stored.
4. **History monotonicity**: `record_auction()` never removes or modifies existing records.
5. **Import equivalence**: replaying a log file with `dkp_import.py` produces the same set of records as running the live monitor against the same file (modulo timestamp precision).
6. **Browser parse parity**: `parseLogFile(text)` in JS produces the same auction records as `dkp_import.py` for the same log file content.
7. **Bid order preservation**: the `bids` array in every stored record matches the chronological order of bids as they appeared in the log.
8. **No phantom items**: `record_auction()` is only called from `close_item()`, which is only called on a valid gratss — never on chat noise or unrecognised items.
