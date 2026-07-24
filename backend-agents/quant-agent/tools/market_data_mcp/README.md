# Market Data MCP Tool

A comprehensive market data tool for AgentCore MCP Gateway that provides historical stock data, technical indicators, and market statistics for backtesting and analysis.

## Overview

This tool provides:
- **Historical Price Data**: daily day stock prices


## Core Files

- **`lambda_function.py`** - Lambda handler that serves market data from S3 Tables
- **`Dockerfile`** - arm64 container image for the Lambda (built + pushed by CDK)
- **`requirements.txt`** - Python dependencies (Lambda container + local loader)
- **`data/load_market_data.py`** - idempotent pyiceberg loader (creates the Iceberg table and loads `amzn.daily.csv`)

## Supported Symbols


## Data Sources

1. **S3 Tables (Primary)**: Iceberg format tables for scalable analytics

## API Interface

### Input Parameters
```json
{
  'symbol': 'AMZN', 
  'start_date': '2024-01-01', 
  'end_date': '2024-02-29', '
  limit': 10
}
```

### Output Format
```json
{
  "statusCode": 200,
  "body": {
    "success": true,
    "data": [
      {
        "date": "YYYY-MM-DD",
        "symbol": "TICKER",
        "open_price": 0.0,
        "high_price": 0.0,
        "low_price": 0.0,
        "close_price": 0.0,
        "volume": 0,
        "adj_close": 0.0
      },
      // ... more data entries
    ],
    "metadata": {
      "symbol": "TICKER",
      "total_rows": 0,
      "columns": ["date", "symbol", "open_price", "high_price", "low_price", "close_price", "volume", "adj_close"],
      "source": "string",
      "timestamp": "ISO-8601 timestamp",
      "s3_tables_bucket": "string"
    }
  },
  "headers": {
    "Content-Type": "application/json"
  }
}
```

## Local Testing

```bash
# Test the Lambda function locally
python3 lambda_function.py

# Test with specific symbol
python3 -c "
import lambda_function
import json
result = lambda_function.lambda_handler({'symbol': 'AMZN', 'start_date': '2024-01-01', 'end_date': '2024-02-29', 'limit': 10}, None)
print(json.dumps(result, indent=2))
"
```

## Deployment

The Lambda, S3 Tables bucket, Cognito, and AgentCore Gateway are provisioned by the **AWS CDK** app in [`infra/`](../../../../infra) (stacks `agentic-backtest-data` and `agentic-backtest-backend`). Load market data with `data/load_market_data.py`. See the repo's [DEPLOYMENT_GUIDE.md](../../../../DEPLOYMENT_GUIDE.md).

## Environment Variables

The Lambda function uses these environment variables:
- `S3_TABLES_BUCKET`: S3 Tables bucket name
- `S3_TABLES_NAMESPACE`: Table namespace (default: "daily_data")
- `S3_TABLES_TABLE`: Table name (default: "daily_data")
