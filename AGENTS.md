# AGENTS.md

Guidance for AI coding assistants working in this repository. This file is
plain Markdown and tool-agnostic — any assistant that reads a root context file
(Kiro, Claude Code, Cursor, Copilot, Aider, etc.) can use it. Kiro's steering
references this file so there is a single source of truth.

## What this project is

A real-time **DKP auction tracker for EverQuest emulator servers** (Project
Quarm / TAKP). DKP ("Dragon Kill Points") is a loot-distribution system: players
bid points on raid drops, highest bid wins. This tool captures those auctions
automatically by reading the game's log file — no game modification.

## Architecture

Three deliverables plus a local-only offline path:

| Component | Location | Stack |
|-----------|----------|-------|
| Officer client | `dkp_client.py`, `dkptrackerv3.py` | Python 3.13, packaged as a single Windows `.exe` via PyInstaller |
| Backend API | `backend/` | AWS Lambda + API Gateway + DynamoDB, deployed with AWS SAM |
| Web viewer | `web/` | Vanilla JS/HTML/CSS, no build step, hosted on S3 |
| Local history | `dkp_logger.py`, `dkp_import.py`, `dkp_history.html` | Offline JSON store + self-contained HTML viewer |

Data flow: client tails `eqlog_<Character>_pq.proj.txt` → parses bids and a
closing `gratss` line → writes local history and pushes an `AuctionRecord` to
the backend on a background thread → failed pushes queue in
`pending_uploads.json` and retry → web viewer reads the API.

De-duplication is deterministic: `dedup_key = SHA-256(item_name, winner, amount,
raid_session_id)` where `raid_session_id` is per-day. Two officers capturing the
same award produce the same key, so the server stores it once. `uploaded_by` is
derived from the log filename — no per-officer credentials.

## Key files

```
dkptrackerv3.py    Core bid-parsing engine + live console table (the heart of it)
dkp_client.py      Cloud-syncing wrapper (CloudBidTracker subclass)
api_client.py      HTTP push client with typed errors (401/422/retryable)
offline_queue.py   Durable pending-upload queue
models.py          AuctionRecord / BidEntry, dedup + raid-session helpers
client_config.py   INI config loading, local-only fallback
dkp_logger.py      Local JSON history (atomic writes, dedup)
dkp_import.py      CLI to back-fill history from existing logs
backend/handler.py Lambda request router + validation + DynamoDB access
web/app.js         Web viewer: table, character pages, expansion buckets
```

## Environment setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

Backend Lambda deps are separate: `backend/requirements.txt` (`boto3`).

## Common commands

```bash
# Run the client from source (reads dkp_client.ini beside it)
python dkp_client.py

# Replay a real EQ log through the tracker to see live output
python test_harness.py <path-to-eqlog>.txt --mode instant
python test_harness.py <path-to-eqlog>.txt --mode auction

# Back-fill history from old logs (accepts .txt or .zip)
python dkp_import.py <logfile> [...] --output dkp_history.json [--dry-run]

# Tests (pytest + Hypothesis property tests)
pytest

# Build the Windows .exe (Windows only)
build.bat            # → dist\dkp_client.exe

# Deploy backend
cd backend && sam build && sam deploy   # --guided on first deploy
```

Linting/formatting is managed by **Trunk** (`.trunk/trunk.yaml`): ruff, black,
isort for Python; prettier for web; plus bandit/trufflehog/checkov for security.
Run `trunk check` / `trunk fmt` if Trunk is installed.

## Conventions

- **Python:** stdlib-first. Only `rapidfuzz` (tracker) and `requests` (sync) are
  third-party runtime deps. Keep it that way unless there's a strong reason.
- **Backend:** `boto3` only; the Lambda handler is dependency-light on purpose.
- **Web:** vanilla JS in an IIFE pattern, no framework, no build tooling. Match
  the existing style in `web/app.js`.
- **Tests:** property-based with Hypothesis where a universal property exists;
  plain unit tests for specific cases. Don't add tests unless asked, but do run
  the suite after changes.
- **Versioning:** `__version__` in `dkptrackerv3.py` auto-bumps on edit via a
  Kiro hook (`3.0.x` → rolls to `3.1.0` at patch 99).

## Gotchas

- **Windows-first client.** The `.exe` build and log paths are Windows-oriented
  (`build.bat`, `taskkill`, `D:\Games\Quarm\...`). The tracker logic itself runs
  cross-platform from source, which is how you test on macOS/Linux.
- **Item validation.** Bids are validated against a bundled EQEmu item DB
  (`items.zip`, ~25k items) plus a learnable `seed_items.txt`. Chat noise is
  aggressively filtered — see `ItemValidator` and `is_chat_noise` in
  `dkptrackerv3.py` before changing parsing.
- **Local-only mode.** If `api_url`, `api_key`, or `log_directory` are missing
  from `dkp_client.ini`, the client disables cloud sync but console tracking
  still works. Preserve this fallback.

## Security

- **Never commit real secrets.** `dkp_client.ini` holds a shared guild API key
  and is gitignored. Use `dkp_client.ini.example` as the template. Reference
  secrets by name, never echo their values.
- **Outstanding task:** a leaked API key still needs rotating — see
  `SECURITY_TODO.md`. Surface this to the user if it hasn't been handled.

## Related docs

- `README.md` — project overview, sample output, setup.
- `DEPLOYMENT.md` — full deployment walkthrough (AWS + web + exe).
- `.kiro/specs/` — requirements/design/tasks for the three feature specs.
