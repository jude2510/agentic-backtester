"""
Tests for what the market-data Lambda returns when a query fails, when it
finds no rows, and when it finds some.

The agent tells the first two apart by shape (see parse_gateway_rows in the
quant agent's tools/market_data.py): success=false with data=[] is "no rows",
an empty step; any other unsuccessful answer is a failed step. The Lambda used
to answer a failed query with the "no rows" shape.

The catalog is faked, so no AWS access is needed. Run with a venv that has
pyiceberg, pyarrow and boto3; the pipeline's pins match this Lambda's:

    market-data-pipeline/.venv/bin/python market-data-mcp/test_lambda_function.py
"""

import datetime as dt
import json
import os
import sys

import pyarrow as pa

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lambda_function as lf  # noqa: E402

REQUEST = {"symbol": "amzn", "start_date": "2025-10-01", "end_date": "2026-10-01", "limit": 2}


def bars(dates):
    n = len(dates)
    return pa.table({
        "date": pa.array(dates, pa.date32()),
        "symbol": ["AMZN"] * n,
        "open_price": [1.0] * n, "high_price": [1.0] * n, "low_price": [1.0] * n,
        "close_price": [1.0] * n, "volume": [100] * n, "adj_close": [1.0] * n,
    })


def catalog_returning(table):
    class Scan:
        def to_arrow(self):
            return table

    class Table:
        def scan(self, row_filter=None):
            return Scan()

    class Catalog:
        def load_table(self, name):
            return Table()

    return lambda: Catalog()


def invoke():
    response = lf.lambda_handler(dict(REQUEST), None)
    return response["statusCode"], json.loads(response["body"])


def main() -> int:
    failures = []

    def check(name, ok, detail=""):
        print(f"{'✅' if ok else '❌'} {name}{'' if ok else ' — ' + str(detail)}")
        if not ok:
            failures.append(name)

    def unreachable():
        raise RuntimeError("catalog unreachable")

    lf.setup_s3_tables_catalog = unreachable
    status, body = invoke()
    check("a failed query is a 500 with no data list, carrying the error",
          status == 500 and body["success"] is False and body["data"] is None
          and "catalog unreachable" in body["error"], (status, body))

    lf.setup_s3_tables_catalog = catalog_returning(bars([]))
    status, body = invoke()
    check("no rows is a 200 with an empty data list",
          status == 200 and body["success"] is False and body["data"] == [], (status, body))

    unsorted = [dt.date(2026, 9, 30), dt.date(2026, 9, 28), dt.date(2026, 9, 29)]
    lf.setup_s3_tables_catalog = catalog_returning(bars(unsorted))
    status, body = invoke()
    dates = [row["date"] for row in body.get("data") or []]
    check("rows come back sorted, limited to the most recent `limit`",
          status == 200 and body["success"] is True and dates == ["2026-09-29", "2026-09-30"], dates)

    print("\n" + "=" * 56)
    if failures:
        print(f"FAILED — {len(failures)} problem(s)")
        return 1
    print("ALL MARKET-DATA LAMBDA TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
