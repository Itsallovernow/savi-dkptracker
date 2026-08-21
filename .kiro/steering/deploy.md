---
inclusion: manual
---

# Deploy — Full Stack Deployment

When the user says "deploy" or "deploy!", execute these steps in order from the project root (`c:\Users\Bennie\Downloads\bidtracker`):

## Step 0: Ensure AWS Login

Before running any AWS commands, verify credentials are active. If any step fails with an auth/token error, run:

```
aws sso login --profile savi-dev-profile
```

This opens a browser for SSO login. All subsequent AWS commands should use `--profile savi-dev-profile` or have `AWS_PROFILE=savi-dev-profile` set.

## Step 1: Upload Guild Data

```
python upload_guild_data.py
```

Converts pkl files to JSON and uploads to S3. If the pkl files don't exist, skip this step and note it.

## Step 2: Deploy Backend (SAM)

```
cd backend
sam build
sam deploy --profile savi-dev-profile
```

Builds and deploys the Lambda + API Gateway stack. Watch for errors — if `sam build` fails, stop and report.

## Step 3: Deploy Web Viewer (S3 Sync)

```
aws s3 sync web/ s3://savi-dkp-viewer --exclude ".hypothesis/*" --exclude ".pytest_cache/*" --exclude "__pycache__/*" --exclude "*.py" --exclude "*.json" --exclude "*.lock" --exclude "seed_items.txt" --profile savi-dev-profile
```

Uploads all web assets (HTML, CSS, JS, images) to the S3 static hosting bucket.

## Completion

After all steps finish, report:
- Whether guild data upload succeeded (and how many entries)
- Whether SAM deploy succeeded
- Whether S3 sync succeeded
- The viewer URL: http://savi-dkp-viewer.s3-website-us-west-1.amazonaws.com
