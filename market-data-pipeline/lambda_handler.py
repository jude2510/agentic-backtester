"""
Scheduled ingest — `ingest.py --delta`, then a read-back through the agent's
own data path, then publish what the table actually holds.

Until 2026-09-23 the ingest only ever ran by hand, and the table sat 40 days
stale with nothing to say so. This runs it on a schedule and then checks the
result from the reader's side: every symbol is read back through the
market-data Lambda the agent calls, and the run fails unless each one serves a
recent bar. A write that succeeded but cannot be served is still a failure.

The read-back also keeps the market-data Lambda warm. It is a container image,
and Lambda deactivates idle image functions; the Gateway calls it synchronously,
and a synchronous call on an inactive function fails while it restores. Without
regular traffic, the first backtest after a quiet spell would pay for its model
calls and then fail to get data.

Finally it publishes each symbol's stored date range to an SSM parameter. The
frontend reads that to decide which backtest windows to offer, so the form
follows the data instead of a hardcoded copy of it.
"""

import datetime as dt
import json
import os

import boto3

# Served data older than this fails the run. Matches the agent's coverage
# tolerance: a long weekend is 3-4 days, so 5 flags only real staleness.
MAX_STALENESS_DAYS = 5

_ssm = boto3.client("ssm")
_secrets = boto3.client("secretsmanager")
_lambda = boto3.client("lambda")


def _load_api_key() -> None:
    """Fetch the Massive key once per container, into the env var the provider reads.

    The same secret backs the news gateway target, so there is one copy to rotate.
    """
    if not os.getenv("MASSIVE_API_KEY"):
        secret = _secrets.get_secret_value(SecretId=os.environ["MASSIVE_API_KEY_SECRET"])
        value = json.loads(secret["SecretString"])[os.environ["MASSIVE_API_KEY_JSON_KEY"]]
        os.environ["MASSIVE_API_KEY"] = value


def _served_last_date(symbol: str) -> dt.date:
    """Newest bar the market-data Lambda serves for `symbol` (it returns the last N rows)."""
    resp = _lambda.invoke(
        FunctionName=os.environ["MARKET_DATA_FUNCTION"],
        Payload=json.dumps({"symbol": symbol, "limit": 1}).encode(),
    )
    payload = json.loads(resp["Payload"].read())
    if resp.get("FunctionError"):
        raise RuntimeError(f"{symbol}: market-data Lambda errored: {payload}")

    body = payload.get("body")
    body = json.loads(body) if isinstance(body, str) else (body or {})
    rows = body.get("data") or []
    if not rows:
        raise RuntimeError(f"{symbol}: market-data Lambda served no rows")
    return dt.date.fromisoformat(rows[-1]["date"])


def _publish_coverage() -> dict:
    """Write each symbol's stored date range where the frontend reads it."""
    from iceberg_writer import coverage, get_catalog, load_table_if_exists

    stored = coverage(load_table_if_exists(get_catalog()))
    _ssm.put_parameter(
        Name=os.environ["COVERAGE_PARAM"],
        Type="String",
        Overwrite=True,
        Value=json.dumps({
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "symbols": stored,
        }),
    )
    return stored


def handler(event, context):
    # Imported here, not at module level. pyarrow, pyiceberg and pandas take
    # longer to import than Lambda's fixed 10-second init phase allows, so a
    # module-level import made every cold start time out once and run init
    # again inside the invocation.
    from ingest import load_symbols, main as ingest_main

    _load_api_key()

    if ingest_main(["--delta"]) != 0:
        raise RuntimeError("ingest reported a failure — see the log above")

    today = dt.date.today()
    served = {s: _served_last_date(s) for s in load_symbols()}
    stale = {s: d for s, d in served.items() if (today - d).days > MAX_STALENESS_DAYS}

    for s, d in served.items():
        print(f"📡 {s}: serving through {d}")

    # Published even when stale, so the site reports what it really holds.
    stored = _publish_coverage()
    print(f"🗺️  coverage published for {len(stored)} symbols")

    if stale:
        raise RuntimeError(f"written but still stale when read back: {stale}")

    return {s: d.isoformat() for s, d in served.items()}
