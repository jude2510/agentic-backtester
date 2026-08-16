"""
Iceberg write layer for the market-data table (S3 Tables).

Write semantics are the important part. This module never appends and never
does a bare full-table overwrite. Every write is a *filtered* overwrite scoped
to one symbol (optionally floored at a date), which Iceberg executes as an
atomic delete-then-insert. Consequences:

  - re-running any window is idempotent
  - duplicate (symbol, date) rows are structurally impossible
  - backfill and daily-delta are the same code path, differing only in window
  - a symbol's history can be extended without disturbing older rows, which is
    what lets AMZN keep 25 years while the vendor only serves the last 5
"""

from __future__ import annotations

import datetime as dt

import boto3
import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual
from pyiceberg.transforms import IdentityTransform, YearTransform

from providers.base import BarSeries

BUCKET = "agentic-backtest-market-data"
NAMESPACE = "daily_data"
TABLE = "daily_data"

# Must match the live table exactly — the Lambda reads these column names.
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


def get_catalog(session: boto3.Session | None = None):
    """Connect to the S3 Tables Iceberg REST catalog (sigv4-signed)."""
    session = session or boto3.Session()
    account_id = session.client("sts").get_caller_identity()["Account"]
    region = session.region_name or "us-east-1"
    return load_catalog("s3tables", **{
        "type": "rest",
        "warehouse": f"arn:aws:s3tables:{region}:{account_id}:bucket/{BUCKET}",
        "uri": f"https://s3tables.{region}.amazonaws.com/iceberg",
        "rest.sigv4-enabled": "true",
        "rest.signing-name": "s3tables",
        "rest.signing-region": region,
    })


def load_table_if_exists(catalog):
    """Load the table read-only, returning None if it doesn't exist yet.

    Used by --dry-run, which must be able to inspect existing coverage (to
    preview the preserve rule) without creating or modifying anything.
    """
    try:
        return catalog.load_table(f"{NAMESPACE}.{TABLE}")
    except NoSuchTableError:
        return None


def load_or_create_table(catalog):
    """Load the daily_data table, creating namespace/table if absent."""
    try:
        catalog.create_namespace(NAMESPACE)
    except NamespaceAlreadyExistsError:
        pass

    identifier = f"{NAMESPACE}.{TABLE}"
    try:
        return catalog.load_table(identifier)
    except NoSuchTableError:
        print(f"🆕 creating {identifier}")
        return catalog.create_table(identifier, schema=ARROW_SCHEMA)


def ensure_partition_spec(table) -> bool:
    """Add identity(symbol) + year(date) partitioning if the table is unpartitioned.

    Partition *evolution*, not a rebuild: existing data files keep their current
    layout and stay readable, while new writes land partitioned. Nothing is
    dropped and no data is rewritten, so this is safe to run against a populated
    table. Symbol-scoped writes then align to partition boundaries instead of
    rewriting the whole dataset.

    Returns True if the spec was changed.
    """
    if table.spec().fields:
        print(f"↩️  already partitioned: {table.spec()}")
        return False

    print("🔧 adding partition spec: identity(symbol) + year(date)")
    with table.update_spec() as update:
        update.add_field("symbol", IdentityTransform(), "symbol_part")
        update.add_field("date", YearTransform(), "date_year")
    print(f"✅ partition spec now: {table.spec()}")
    return True


def last_date_for(table, symbol: str) -> dt.date | None:
    """Most recent bar date already stored for `symbol`, or None."""
    scan = table.scan(row_filter=EqualTo("symbol", symbol.upper()),
                      selected_fields=("date",))
    arrow = scan.to_arrow()
    if len(arrow) == 0:
        return None
    return max(arrow.column("date").to_pylist())


def series_to_arrow(series: BarSeries, since: dt.date | None = None) -> pa.Table:
    """Convert a BarSeries to an Arrow table matching the Iceberg schema."""
    rows = [b.to_row() for b in series.bars if since is None or b.date >= since]
    if not rows:
        return ARROW_SCHEMA.empty_table()
    cols = {name: [r[name] for r in rows] for name in ARROW_SCHEMA.names}
    return pa.Table.from_pydict(cols, schema=ARROW_SCHEMA)


def write_symbol(table, series: BarSeries, since: dt.date | None = None) -> dict:
    """Atomically replace `symbol`'s rows (optionally only those >= `since`).

    Args:
        since: when set, rows before this date are LEFT UNTOUCHED and only the
            window from `since` forward is replaced. This is what preserves
            history the vendor can no longer serve. When None, the symbol's
            entire history is replaced.

    Returns a summary dict for the run record.
    """
    symbol = series.symbol.upper()
    arrow = series_to_arrow(series, since=since)

    if len(arrow) == 0:
        return {"symbol": symbol, "written": 0, "skipped": "no rows in window"}

    if since is None:
        row_filter = EqualTo("symbol", symbol)
        mode = "full-symbol replace"
    else:
        row_filter = And(
            EqualTo("symbol", symbol),
            GreaterThanOrEqual("date", since.isoformat()),
        )
        mode = f"replace from {since}"

    table.overwrite(arrow, overwrite_filter=row_filter)

    return {
        "symbol": symbol,
        "written": len(arrow),
        "mode": mode,
        "range": f"{arrow.column('date')[0].as_py()}..{arrow.column('date')[-1].as_py()}",
        "truncated": series.truncated,
    }
