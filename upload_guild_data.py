"""
upload_guild_data.py — Convert guild pkl files to JSON and upload to S3.

Converts correlation_data.pkl and join_date_data.pkl to JSON format and
uploads them to the S3 bucket used by the web viewer. Run this whenever
you update the pkl files.

Usage:
    python upload_guild_data.py

Requires:
    - boto3 (already installed for the DKP client)
    - AWS CLI configured with write access to the viewer bucket
"""

import json
import os
import pickle
import sys
from datetime import datetime

import boto3

# S3 bucket name (same as the web viewer bucket)
S3_BUCKET = "savi-dkp-viewer"
S3_PREFIX = "data/"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CORRELATION_PKL = os.path.join(SCRIPT_DIR, "correlation_data.pkl")
JOIN_DATE_PKL = os.path.join(SCRIPT_DIR, "join_date_data.pkl")


def load_correlation_data():
    """Load correlation_data.pkl → dict of character_name → parent_name."""
    with open(CORRELATION_PKL, "rb") as f:
        data = pickle.load(f)
    # Ensure all values are strings
    return {str(k): str(v) for k, v in data.items()}


def load_join_date_data():
    """Load join_date_data.pkl → dict of parent_name → join_date (ISO string)."""
    with open(JOIN_DATE_PKL, "rb") as f:
        data = pickle.load(f)
    result = {}
    for k, v in data.items():
        if isinstance(v, datetime):
            result[str(k)] = v.isoformat()
        else:
            result[str(k)] = str(v)
    return result


def build_guild_data(correlation, join_dates):
    """
    Build the combined guild data JSON structure.

    Output format:
    {
        "correlations": { "AltName": "ParentName", ... },
        "join_dates": { "ParentName": "2024-01-15T00:00:00", ... }
    }
    """
    return {
        "correlations": correlation,
        "join_dates": join_dates,
    }


def upload_to_s3(data, bucket, key):
    """Upload JSON data to S3."""
    s3 = boto3.client("s3")
    body = json.dumps(data, indent=2)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
    )
    print(f"  Uploaded to s3://{bucket}/{key} ({len(body)} bytes)")


def main():
    # Check pkl files exist
    if not os.path.isfile(CORRELATION_PKL):
        print(f"ERROR: {CORRELATION_PKL} not found")
        sys.exit(1)
    if not os.path.isfile(JOIN_DATE_PKL):
        print(f"ERROR: {JOIN_DATE_PKL} not found")
        sys.exit(1)

    print("Loading pkl files...")
    correlation = load_correlation_data()
    join_dates = load_join_date_data()

    print(f"  Correlations: {len(correlation)} entries")
    print(f"  Join dates: {len(join_dates)} entries")

    guild_data = build_guild_data(correlation, join_dates)

    print(f"\nUploading to S3 bucket: {S3_BUCKET}")
    upload_to_s3(guild_data, S3_BUCKET, f"{S3_PREFIX}guild_characters.json")

    print("\nDone! The frontend will pick up the new data on next page load.")


if __name__ == "__main__":
    main()
