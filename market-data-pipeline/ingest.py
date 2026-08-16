#!/usr/bin/env python3
"""
ingest.py — load market data from a provider into the Iceberg table.

Backfill and daily-delta are the same operation with different windows, because
every write is an idempotent filtered overwrite.

The preserve rule: if a symbol already has history reaching further back than
the provider can serve, only the recent window is replaced and the older rows
are left alone. That is what keeps AMZN's 25 years alive on a 5-year plan.

Usage:
    export MASSIVE_API_KEY=...
    export AWS_PROFILE=personal AWS_REGION=us-east-1

    # backfill everything in symbols.json (5 years, preserving deeper history)
    python ingest.py --years 5

    # daily delta — re-fetch the last 7 days for every symbol
    python ingest.py --delta

    # one symbol, explicit window, no writes
    python ingest.py --symbols NVDA --start 2021-08-16 --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from iceberg_writer import (
    ensure_partition_spec,
    get_catalog,
    last_date_for,
    load_or_create_table,
    load_table_if_exists,
    write_symbol,
)
from providers import get_market_data_provider

SYMBOLS_FILE = Path(__file__).parent / "symbols.json"

# Re-fetch this many days of already-stored bars on every run. Vendors restate
# recent bars (late corrections, consolidated volume), and the overlap is free
# because filtered overwrite is idempotent.
OVERLAP_DAYS = 5


def load_symbols() -> list[str]:
    if SYMBOLS_FILE.exists():
        return json.loads(SYMBOLS_FILE.read_text())["symbols"]
    return ["AMZN"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", help="comma-separated (default: symbols.json)")
    p.add_argument("--start", type=dt.date.fromisoformat, help="YYYY-MM-DD")
    p.add_argument("--end", type=dt.date.fromisoformat, default=dt.date.today())
    p.add_argument("--years", type=int, default=5,
                   help="lookback when --start is omitted (default 5)")
    p.add_argument("--delta", action="store_true",
                   help=f"only refresh the last {OVERLAP_DAYS * 2} days")
    p.add_argument("--replace-history", action="store_true",
                   help="DESTRUCTIVE: replace each symbol's entire history "
                        "instead of preserving rows older than the vendor window")
    p.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    symbols = ([s.strip().upper() for s in args.symbols.split(",")]
               if args.symbols else load_symbols())

    if args.delta:
        requested_start = args.end - dt.timedelta(days=OVERLAP_DAYS * 2)
    elif args.start:
        requested_start = args.start
    else:
        requested_start = args.end - dt.timedelta(days=365 * args.years)

    print(f"📅 window {requested_start} .. {args.end}")
    print(f"🎯 symbols: {', '.join(symbols)}")
    if args.dry_run:
        print("🧪 DRY RUN — no writes")
    print()

    provider = get_market_data_provider()
    print(f"🔌 provider: {provider.name}")

    # The table is loaded in BOTH modes: --dry-run needs to read existing
    # coverage to preview the preserve rule accurately. It just never writes,
    # never creates, and never evolves the partition spec.
    catalog = get_catalog()
    if args.dry_run:
        table = load_table_if_exists(catalog)
        if table is None:
            print("ℹ️  table does not exist yet — a real run would create it")
    else:
        table = load_or_create_table(catalog)
        ensure_partition_spec(table)
    print()

    results, warnings = [], []

    for symbol in symbols:
        # Decide the write floor BEFORE fetching, so we only pull what we'll use.
        since = None
        if table is not None and not args.replace_history:
            existing_last = last_date_for(table, symbol)
            if existing_last and existing_last >= requested_start:
                # Stored history already reaches past our window start; preserve
                # it and refresh only from just before the last stored bar.
                since = existing_last - dt.timedelta(days=OVERLAP_DAYS)
                print(f"🛡️  {symbol}: preserving rows before {since} "
                      f"(stored through {existing_last})")

        fetch_start = since or requested_start
        series = provider.get_daily_bars(symbol, fetch_start, args.end)
        print(f"   {series.coverage_note()}")

        if series.truncated and since is None:
            warnings.append(
                f"{symbol}: vendor served only from {series.actual_start} "
                f"(requested {fetch_start}) — plan history limit"
            )

        if args.dry_run:
            results.append({
                "symbol": symbol,
                "would_write": len(series.bars),
                "mode": "full-symbol replace" if since is None else f"replace from {since}",
                "preserved_before": since.isoformat() if since else None,
            })
            continue

        summary = write_symbol(table, series, since=since)
        results.append(summary)
        print(f"   💾 {summary}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total = sum(r.get("written", r.get("would_write", 0)) for r in results)
    print(f"rows {'that would be ' if args.dry_run else ''}written: {total:,}")
    for r in results:
        print(f"  {r}")

    if warnings:
        print("\n⚠️  COVERAGE WARNINGS")
        for w in warnings:
            print(f"  - {w}")
        print("\n  These are not failures. The vendor silently returns a later")
        print("  start when a request predates the plan's history limit, so a")
        print("  backfill can look successful while holding less than requested.")

    return 0


if __name__ == "__main__":
    if not os.getenv("MASSIVE_API_KEY"):
        print("❌ MASSIVE_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    sys.exit(main())
