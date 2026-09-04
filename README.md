# DKP Bid Tracker

A real-time DKP auction tracker for EverQuest emulator servers (built for a Project Quarm / TAKP guild). Officers run a lightweight Windows client that watches their in-game log file, parses loot auctions as they happen, and displays a live bid table in the console. Closed auctions sync to a serverless AWS backend, and guild members browse the full auction history through a static web viewer.

> **DKP** ("Dragon Kill Points") is a loot-distribution system. Players spend points to bid on raid drops; the highest bid wins the item. This tool automates capturing those auctions straight from the game log — no manual spreadsheets.

---

## What it does

- **Watches the EQ log in real time.** Reads new lines as the game writes them, no game modification required.
- **Parses auctions automatically.** Detects item announcements, bids (main and alt), typo/self corrections, priority rolls, `gratss` (award), `ungratss` (undo), and `clear` (cancel) — and renders a live, self-updating bid table.
- **Validates item names** against a bundled EQEmu item database (~25,000 items) plus a learnable seed list, so chat noise never gets mistaken for a bid.
- **Syncs to the cloud** in the background. Closed auctions are pushed to an AWS API; multiple officers capturing the same auction are de-duplicated server-side.
- **Survives network drops.** Failed uploads queue locally and auto-retry, so tracking never blocks on connectivity.
- **Serves a web history viewer** with per-item history, per-character DKP totals grouped by expansion, search, and stats.

---

## Architecture

```
┌────────────────────────┐        ┌──────────────────────────┐        ┌───────────────────┐
│  Officer's PC (Windows) │        │      AWS (serverless)     │        │   Guild members    │
│                        │  HTTPS │                          │  HTTPS │                   │
│  dkp_client.exe        │ ─────► │  API Gateway → Lambda     │ ◄───── │  Static web viewer │
│   • watches EQ log     │  POST  │       → DynamoDB          │  GET   │   (S3 website)     │
│   • live console table │        │   dedup + stats + query   │        │                   │
│   • offline queue      │        │                          │        │                   │
└────────────────────────┘        └──────────────────────────┘        └───────────────────┘
```

Three deliverables:

| Component | Location | Stack |
|-----------|----------|-------|
| Officer client | `dkp_client.py`, `dkptrackerv3.py` | Python 3.13, packaged as a single `.exe` via PyInstaller |
| Backend API | `backend/` | AWS Lambda + API Gateway (HTTP API) + DynamoDB, deployed with AWS SAM |
| Web viewer | `web/` | Vanilla JS/HTML/CSS (no build step), hosted on S3 |

There is also a **local-only history path** (`dkp_logger.py`, `dkp_import.py`, `dkp_history.html`) that works entirely offline — it writes a JSON history file and ships a self-contained HTML viewer, no cloud required.

---

## How it works

1. The client tails `eqlog_<Character>_pq.proj.txt` in the configured log directory.
2. An **announcement** (`Item A | Item B`) opens auctions for those items.
3. **Bids** (`Cloak of Flames 500`, `Ring of Flamewarding 150 alt`) update the live table. Higher bids replace lower ones; obvious typos (`225` → `2225`) and self-corrections are handled automatically.
4. A **`gratss`** line closes the auction and awards the item: `Cloak of Flames; 550; Morwen gratss`.
5. On close, the client builds an `AuctionRecord`, writes it to local history, and pushes it to the backend on a background thread. If the push fails, it lands in `pending_uploads.json` and retries every 30s.

De-duplication is deterministic: a `dedup_key` is the SHA-256 of `item_name`, `winner`, `amount`, and a per-day `raid_session_id`. Two officers who capture the same award produce the same key, so the server stores it once and records both as `confirmed_by`. The `uploaded_by` field comes from the log filename, so no per-officer credentials are needed.

---

## Sample output

### Live console (client / test harness)

Running a log through the tracker shows the item database loading, a self-refreshing bid table, and a `SOLD` banner on each award:

```
📦 Loaded 25,444 items from database
📋 Loaded 492 supplemental seed items
🛠️  BidTracker initialized — auctions close on gratss message

📢 Auction announced: Cloak of Flames | Ring of Flamewarding

----------------------------------------------------------------------
  ⚔  Running Auctions (2 items)  ⏱ 0s since announce  ⚔
----------------------------------------------------------------------
[Cloak of Flames]                    [Ring of Flamewarding]
  ⏱ last bid 0s ago                    ⏱ last bid 0s ago
  Morwen 550dkp [M]                    Gorak 150dkp [A]
  Thraxx 500dkp [M]
----------------------------------------------------------------------

------------------------------------------------------------
  🎉 SOLD  [Cloak of Flames]  ->  Morwen  550 DKP
------------------------------------------------------------
```

`[M]` = main bid, `[A]` = alt bid. The table redraws in place as bids arrive.

### CLI import (back-filling history from old logs)

```
$ python dkp_import.py eqlog_Bennie_pq.proj.txt --output dkp_history.json
eqlog_Bennie_pq.proj.txt — 2 found, 2 written, 0 skipped
```

### Stored record shape (`dkp_history.json` / API payload)

```json
{
  "version": 1,
  "records": [
    {
      "item_name": "Cloak of Flames",
      "winner": "Morwen",
      "amount": 550,
      "timestamp": "2026-09-05T03:05:01Z",
      "raid_session_id": "d4a3beeb32e4feac58120f3797b60b5dc445127a6c74048cbe8d8c310e1d8bd3",
      "dedup_key": "1a89dc9bef7406ce3ddd2cd49a6694a01e2b494487f424ea8c07a8529d7cbb05",
      "log_source": "eqlog_Bennie_pq.proj.txt",
      "bids": [
        { "player": "Thraxx", "amount": 500, "bid_type": "main", "is_correction": false },
        { "player": "Morwen", "amount": 550, "bid_type": "main", "is_correction": false }
      ]
    }
  ]
}
```

---

## Backend API

Auth: `POST` and `DELETE` require a bearer token (`Authorization: Bearer <api_key>`); `GET` endpoints are public.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/auctions` | Ingest an auction record. `201` created, `200` if a duplicate, `401` bad key, `422` invalid record. |
| `GET` | `/auctions` | List records, newest-first. Supports `since`, `session`, `limit` (max 100), and `cursor` pagination. |
| `GET` | `/auctions/{dedup_key}` | Full record detail including bid history. |
| `GET` | `/auctions/stats` | Aggregates: `total_auctions`, `total_dkp_spent`, `unique_winners`. |
| `DELETE` | `/auctions/{dedup_key}` | Remove an erroneous record (used when an officer issues `ungratss`). |

Example:

```bash
curl https://<your-api-id>.execute-api.<region>.amazonaws.com/prod/auctions/stats
# {"total_auctions": 0, "total_dkp_spent": 0, "unique_winners": 0}
```

---

## Web viewer

The static viewer (`web/`) reads from the API and provides:

- An **auction history table** — item, winner, amount, date, bid count — with client-side search and pagination.
- **Character pages** — click any winner to see their DKP totals grouped by EverQuest expansion era (Classic, Kunark, Velious, Luclin, PoP), with a per-item breakout.
- A **stats panel** and manual/auto refresh.

The API URL is set at build time via `window.__DKP_API_URL__` (or the fallback constant at the top of `web/app.js`).

---

## Setup

### Prerequisites

- Python 3.13+
- [PyInstaller](https://pyinstaller.org/) (for building the client `.exe`)
- [AWS CLI](https://aws.amazon.com/cli/) and [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) (for the backend)
- An AWS account (expected usage fits the free tier)

### Client (officer)

The client reads `dkp_client.ini` from the same folder as the executable:

```ini
[server]
api_url = https://<your-api-id>.execute-api.<region>.amazonaws.com/prod
api_key = <your-guild-shared-secret>

[client]
log_directory = D:\Games\Quarm\TAKPv22
poll_interval = 1.0
retry_interval = 30.0
```

If `api_url`, `api_key`, or `log_directory` are missing, the client runs in **local-only mode** — console tracking still works, cloud sync is disabled.

Run from source:

```bash
pip install rapidfuzz requests
python dkp_client.py
```

Build the distributable `.exe` (Windows):

```bat
build.bat
```

Output: `dist\dkp_client.exe`.

### Backend

```bash
cd backend
sam build
sam deploy --guided
```

Note the `ApiEndpoint` from the output — officers need it for their config, and it goes into `web/app.js`.

### Web viewer

```bash
aws s3 sync web/ s3://<your-bucket> \
  --exclude "*.py" --exclude "__pycache__/*" --exclude "*.json" --exclude "seed_items.txt"
```

See [`DEPLOYMENT.md`](DEPLOYMENT.md) for full step-by-step instructions, bucket policy, and troubleshooting.

---

## Repository layout

```
dkptrackerv3.py       Core bid-parsing engine and live console table
dkp_client.py         Cloud-syncing wrapper around the tracker
api_client.py         HTTP push client with retry/error handling
offline_queue.py      Durable pending-upload queue (pending_uploads.json)
models.py             AuctionRecord / BidEntry, dedup + session-id helpers
client_config.py      INI config loading, local-only fallback
dkp_logger.py         Local JSON history persistence (atomic writes, dedup)
dkp_import.py         CLI to back-fill history from existing log files
test_harness.py       Replay a real log through the tracker for testing
dkp_history.html      Self-contained offline history viewer
backend/              AWS SAM app: Lambda handler, template, tests
web/                  Static web viewer (index.html, app.js, style.css)
seed_items.txt        Learnable supplemental item names
items.zip             Bundled EQEmu item database export
```

---

## Testing

The project uses `pytest` with property-based tests (Hypothesis) across models, offline queue, API client, backend handler, and web filter logic:

```bash
pip install pytest hypothesis rapidfuzz requests
pytest
```

You can also replay a real log to watch the tracker in action:

```bash
python test_harness.py <path-to-eqlog>.txt --mode instant     # fast sanity check
python test_harness.py <path-to-eqlog>.txt --mode auction      # just the auction lines
python test_harness.py <path-to-eqlog>.txt --item "Cloak of Flames"
```

---

## Notes

- The client is Windows-first (single-file `.exe`, watches a Windows log path), but the tracker logic runs cross-platform from source.
- Never commit real API keys. `dkp_client.ini` holds a shared guild secret — keep production values out of version control.
