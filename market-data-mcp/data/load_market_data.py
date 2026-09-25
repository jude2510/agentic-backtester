#!/usr/bin/env python3
"""
load_market_data.py — load market data into the CDK-owned S3 Tables bucket.

The table *bucket* (agentic-backtest-market-data) is created by the CDK
DataStack. This script creates the `daily_data` namespace + table and seeds it
from a CSV.

ONE-TIME SEED ONLY. Ongoing data comes from market-data-pipeline/ingest.py.
The CSV's value now is AMZN history back to 2000, deeper than the vendor plan
serves; seed it into an empty table first, and the pipeline's backfill then
preserves it and extends it forward. The script refuses to touch a table that
already exists: it used to do a full-table overwrite on re-run, which against
the live table would replace every symbol with this one CSV.

Usage:
    AWS_PROFILE=personal AWS_REGION=us-east-1 python data/load_market_data.py
"""

import os
import sys
import boto3
import pandas as pd
import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchTableError, NamespaceAlreadyExistsError

# --- Configuration -----------------------------------------------------------
BUCKET_NAME = os.getenv("MARKET_DATA_BUCKET", "agentic-backtest-market-data")
NAMESPACE = "daily_data"
TABLE_NAME = "daily_data"
CSV_FILE_PATH = os.path.join(os.path.dirname(__file__), "amzn.daily.csv")

# Iceberg schema — must match what the Lambda / MCP reader expects.
ARROW_SCHEMA = pa.schema([
    pa.field("date", pa.date32(), nullable=False),
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("open_price", pa.float64(), nullable=True),
    pa.field("high_price", pa.float64(), nullable=True),
    pa.field("low_price", pa.float64(), nullable=True),
    pa.field("close_price", pa.float64(), nullable=True),
    pa.field("volume", pa.int64(), nullable=True),
    pa.field("adj_close", pa.float64(), nullable=True),
])

# table column -> CSV column
COLUMN_MAPPINGS = {
    "date": "timestamp",
    "symbol": "symbol",
    "open_price": "open",
    "high_price": "high",
    "low_price": "low",
    "close_price": "close",
    "volume": "volume",
    "adj_close": "adjusted_close",
}


def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        print(f"❌ CSV not found: {path}")
        sys.exit(1)
    df = pd.read_csv(path)
    print(f"📂 Loaded {len(df)} rows from {os.path.basename(path)} — columns: {list(df.columns)}")
    return df


def map_to_schema(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    for table_col, csv_col in COLUMN_MAPPINGS.items():
        if csv_col not in df.columns:
            continue
        if table_col == "date":
            out[table_col] = pd.to_datetime(df[csv_col]).dt.date
        elif table_col == "symbol":
            out[table_col] = df[csv_col].astype(str)
        elif table_col == "volume":
            out[table_col] = pd.to_numeric(df[csv_col], errors="coerce").astype("Int64")
        else:
            out[table_col] = pd.to_numeric(df[csv_col], errors="coerce").astype("float64")
    out = out.dropna(subset=["date", "symbol"])
    print(f"📊 Prepared {len(out)} rows for {NAMESPACE}.{TABLE_NAME}")
    return out


def get_catalog(session: boto3.Session):
    account_id = session.client("sts").get_caller_identity()["Account"]
    region = session.region_name or os.getenv("AWS_REGION", "us-east-1")
    catalog = load_catalog("s3tables", **{
        "type": "rest",
        "warehouse": f"arn:aws:s3tables:{region}:{account_id}:bucket/{BUCKET_NAME}",
        "uri": f"https://s3tables.{region}.amazonaws.com/iceberg",
        "rest.sigv4-enabled": "true",
        "rest.signing-name": "s3tables",
        "rest.signing-region": region,
    })
    print(f"🔗 Connected to S3 Tables catalog: {BUCKET_NAME} ({region})")
    return catalog


def main():
    print(f"🚀 Loading market data into '{BUCKET_NAME}'")
    session = boto3.Session()
    df = load_csv(CSV_FILE_PATH)
    mapped = map_to_schema(df)
    arrow_table = pa.Table.from_pandas(mapped, schema=ARROW_SCHEMA, preserve_index=False)

    catalog = get_catalog(session)

    # Ensure namespace exists (idempotent).
    try:
        catalog.create_namespace(NAMESPACE)
        print(f"✅ Created namespace: {NAMESPACE}")
    except NamespaceAlreadyExistsError:
        print(f"↩️  Namespace already exists: {NAMESPACE}")

    identifier = f"{NAMESPACE}.{TABLE_NAME}"
    try:
        catalog.load_table(identifier)
        print(f"❌ {identifier} already exists — refusing to overwrite it.\n"
              f"   This script only seeds an empty table. To update data, use\n"
              f"   market-data-pipeline/ingest.py (it preserves existing history).")
        sys.exit(1)
    except NoSuchTableError:
        print(f"🆕 Creating table: {identifier}")
        table = catalog.create_table(identifier, schema=ARROW_SCHEMA)
        table.append(arrow_table)

    print(f"\n🎉 Done — {len(mapped)} rows in {BUCKET_NAME}:{identifier}")


if __name__ == "__main__":
    main()
