# Market Data Lambda

The market-data service for the Agentic Backtester: an AWS Lambda that serves historical **daily OHLCV price bars** from an S3 Tables (Apache Iceberg) store. It's exposed to the agents as an MCP tool through an AgentCore Gateway (Cognito-secured), and the backtest tool pulls its price history from here.

## Files

- **`lambda_function.py`** — Lambda handler; queries the Iceberg table and returns price bars
- **`Dockerfile`** / **`.dockerignore`** — arm64 container image for the Lambda (built + pushed by the CDK app)
- **`requirements.txt`** — Python dependencies (Lambda container + the local loader)
- **`data/load_market_data.py`** — one-time seed: creates the Iceberg table and loads `amzn.daily.csv`. Refuses to run if the table already exists; ongoing data comes from [`market-data-pipeline/`](../market-data-pipeline)
- **`data/amzn.daily.csv`** — AMZN daily bars from 2000, deeper than the vendor plan serves
- **`data/query_s3_table.py`** — helper to inspect the loaded table

## Interface

**Input** — the event passed to the Lambda / MCP tool. Only `symbol` is required (defaults to `AMZN`); `start_date`, `end_date`, and `limit` (default `252`) are optional filters:

```json
{
  "symbol": "AMZN",
  "start_date": "2024-01-01",
  "end_date": "2024-02-29",
  "limit": 10
}
```

**Output** — `statusCode`, a JSON-encoded `body`, and headers. The decoded `body`:

```json
{
  "success": true,
  "data": [
    {
      "date": "YYYY-MM-DD",
      "symbol": "AMZN",
      "open_price": 0.0,
      "high_price": 0.0,
      "low_price": 0.0,
      "close_price": 0.0,
      "volume": 0,
      "adj_close": 0.0
    }
  ],
  "metadata": {
    "symbol": "AMZN",
    "total_rows": 0,
    "columns": ["date", "symbol", "open_price", "high_price", "low_price", "close_price", "volume", "adj_close"],
    "source": "s3_tables_pyiceberg_container",
    "timestamp": "ISO-8601",
    "s3_tables_bucket": "agentic-backtest-market-data"
  }
}
```

## Environment variables (Lambda)

| Variable | Default | Purpose |
| --- | --- | --- |
| `S3_TABLES_BUCKET` | — | S3 Tables (table bucket) name |
| `S3_TABLES_NAMESPACE` | `daily_data` | Iceberg namespace |
| `S3_TABLES_TABLE` | `daily_data` | Iceberg table |
| `S3_TABLES_REGION` | `us-east-1` | AWS region |

## Local testing

```bash
python3 -c "
import json, lambda_function
print(json.dumps(lambda_function.lambda_handler(
    {'symbol': 'AMZN', 'start_date': '2024-01-01', 'end_date': '2024-02-29', 'limit': 10}, None), indent=2))
"
```

## Deployment

The Lambda, S3 Tables bucket, Cognito, and AgentCore Gateway are provisioned by the **AWS CDK** app in [`infra/`](../infra) (stacks `agentic-backtest-data` and `agentic-backtest-backend`). Data is loaded by the [ingest pipeline](../market-data-pipeline). See the repo [DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md).
