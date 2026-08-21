# DKP Bid Tracker — Deployment Guide

## Prerequisites

- **Python 3.13+** installed and on PATH
- **PyInstaller** installed: `pip install pyinstaller`
- **AWS CLI** installed and configured: `aws configure`
- **AWS SAM CLI** installed: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html
- **AWS Account** (free tier covers expected usage)

---

## Part 1: Deploy the Backend (AWS)

### Step 1: Build the Lambda

```powershell
cd backend
sam build
```

SAM will warn that AuctionsFunction has no authentication — confirm yes (we handle auth in code).

### Step 2: Deploy the stack

```powershell
sam deploy --guided
```

Prompts:
- **Stack name**: `dkp-bid-tracker`
- **Region**: `us-east-1` (or your preference)
- **ApiKeyValue**: Choose a strong shared secret (bearer token for officers)
- **StageName**: `prod`
- **Confirm changes before deploy**: Yes
- **Allow SAM CLI IAM role creation**: Yes
- **AuctionsFunction has no authentication**: Yes (intentional)

### Step 3: Note your API endpoint

After deployment, SAM outputs:

```
ApiEndpoint: https://<api-id>.execute-api.<region>.amazonaws.com/prod
```

Save this URL — officers need it for their config.

### Step 4: Update the backend later

```powershell
cd backend
sam build
sam deploy
```

No `--guided` needed after the first deploy.

---

## Part 2: Deploy the Web Viewer

### Step 1: Set the API URL

Edit `web/app.js` line 3:
```js
const API_BASE_URL = window.__DKP_API_URL__ || 'https://<your-api-id>.execute-api.<region>.amazonaws.com/prod';
```

### Step 2: Create an S3 bucket for hosting

```powershell
aws s3 mb s3://your-guild-dkp-viewer
aws s3 website s3://your-guild-dkp-viewer --index-document index.html
```

### Step 3: Upload the web files

```powershell
aws s3 sync web/ s3://your-guild-dkp-viewer --exclude ".hypothesis/*" --exclude ".pytest_cache/*" --exclude "__pycache__/*" --exclude "*.py" --exclude "*.json" --exclude "*.lock" --exclude "seed_items.txt"
```

### Step 4: Set public access (bucket policy)

Newer S3 buckets block public access by default. Disable it, then apply the policy:

```powershell
aws s3api put-public-access-block --bucket your-guild-dkp-viewer --public-access-block-configuration "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false"
aws s3api put-bucket-policy --bucket your-guild-dkp-viewer --policy file://bucket-policy.json
```

The `bucket-policy.json` file is in the project root and grants read-only access to the static viewer files.

Alternatively, do this in the S3 console: Bucket → Permissions → Block public access → Edit → Uncheck all → Save → Then paste the bucket policy.

### Step 5: Share the URL

The viewer is available at:
```
http://your-guild-dkp-viewer.s3-website-<region>.amazonaws.com
```

Optional: Put CloudFront in front for HTTPS and a custom domain.

### Step 5: Update the viewer later

```powershell
aws s3 sync web/ s3://your-guild-dkp-viewer --exclude ".hypothesis/*" --exclude ".pytest_cache/*" --exclude "__pycache__/*" --exclude "*.py" --exclude "*.json" --exclude "*.lock" --exclude "seed_items.txt"
```

---

## Part 3: Build the Officer Executable

### Step 1: Build the .exe

```powershell
build.bat
```

Output: `dist\dkp_client.exe`

The .exe bundles Python, all dependencies, `items.zip`, and `seed_items.txt` into a single file.

### Step 2: Distribute to officers

Give each officer:
- `dkp_client.exe`

Tell them: "Put the exe in any folder. Run it once — it'll create `dkp_client.ini` next to it with placeholders. Edit that file, then restart."

Alternatively, pre-create the INI and ship both files together.

### Step 3: Officer configuration

`dkp_client.ini` must be in the same folder as the `.exe`. Exact format:

```ini
[server]
# Your guild's API endpoint (from sam deploy output)
api_url = https://czfat794tl.execute-api.us-west-1.amazonaws.com/prod

# Shared guild API key (the ApiKeyValue you set during sam deploy)
api_key = your-guild-secret-key-here

[client]
# Path to the EverQuest log directory on this officer's machine
log_directory = D:\Games\Quarm\TAKPv22

# How often to check for new log lines (seconds, default 1.0)
poll_interval = 1.0

# How often to retry failed uploads (seconds, default 30.0)
retry_interval = 30.0
```

**Required fields:** `api_url`, `api_key`, `log_directory` — if any are missing or empty, the client runs in local-only mode (no cloud sync, console tracking still works).

**Optional fields:** `poll_interval` and `retry_interval` have sensible defaults and can be omitted.

### Step 4: Rebuild after code changes

```powershell
build.bat
```

Redistribute the new `dist\dkp_client.exe` to officers.

---

## Verification

### Test the API

```powershell
curl https://<your-api-endpoint>/prod/auctions/stats
```

Expected: `{"total_auctions": 0, "total_dkp_spent": 0, "unique_winners": 0}`

### Test the client

1. Fill in `dkp_client.ini` with real API URL and key
2. Run `dkp_client.exe` (or `python dkp_client.py` from source)
3. Trigger a `gratss` in the EQ log
4. Check `curl .../auctions` — the record should appear

### Test the viewer

Open the S3 website URL in a browser. You should see the test auction.

---

## Traceability: uploaded_by & confirmed_by

Each auction record automatically includes:
- `uploaded_by`: Character name extracted from the log filename (e.g., "Bennie" from `eqlog_Bennie_pq.proj.txt`)
- `confirmed_by`: List of all officers who independently captured the same auction

No per-officer API keys needed — identity is derived from the log file being watched.

---

## Cost (AWS Free Tier)

| Resource | Free Tier | Expected Usage | Cost |
|----------|-----------|----------------|------|
| Lambda | 1M req/mo | ~5,000 req/mo | $0 |
| API Gateway | 1M req/mo | ~5,000 req/mo | $0 |
| DynamoDB | 25 GB | ~100 MB | $0 |
| S3 | 5 GB | ~5 MB | $0 |
| CloudFront | 1 TB/mo | ~1 GB/mo | $0 |

---

## Troubleshooting

**sam build fails with "python3.12 not found"**
→ Template uses python3.13 (matches your local install). If you change Python versions, update `Runtime` in `backend/template.yaml`.

**sam build warns "no authentication"**
→ Intentional. Auth is handled in application code (bearer token on POST, open GET).

**Client shows "Running in LOCAL-ONLY mode"**
→ Check `dkp_client.ini` — `api_url`, `api_key`, and `log_directory` must all be filled in.

**Records not appearing after gratss**
→ Check the console for `[CloudSync]` error messages. Common issues: wrong API URL, expired/wrong API key, no internet.

**Offline queue building up**
→ Records are queued in `pending_uploads.json` beside the .exe. They auto-flush every 30 seconds when connectivity returns.
