"""
Scheduled ingest — `ingest.py --delta`, then a read-back through the agent's
own data path.

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
"""

import datetime as dt
import json
import os

import boto3

from ingest import load_symbols, main as ingest_main

# Served data older than this fails the run. Matches the agent's coverage
# tolerance: a long weekend is 3-4 days, so 5 flags only real staleness.
MAX_STALENESS_DAYS = 5

_ssm = boto3.client("ssm")
_lambda = boto3.client("lambda")


def _load_api_key() -> None:
    """Fetch the Massive key from SSM once per container, into the env var the provider reads."""
    if not os.getenv("MASSIVE_API_KEY"):
        param = _ssm.get_parameter(Name=os.environ["MASSIVE_API_KEY_PARAM"],
                                   WithDecryption=True)
        os.environ["MASSIVE_API_KEY"] = param["Parameter"]["Value"]


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


def handler(event, context):
    _load_api_key()

    if ingest_main(["--delta"]) != 0:
        raise RuntimeError("ingest reported a failure — see the log above")

    today = dt.date.today()
    served = {s: _served_last_date(s) for s in load_symbols()}
    stale = {s: d for s, d in served.items() if (today - d).days > MAX_STALENESS_DAYS}

    for s, d in served.items():
        print(f"📡 {s}: serving through {d}")
    if stale:
        raise RuntimeError(f"written but still stale when read back: {stale}")

    return {s: d.isoformat() for s, d in served.items()}
