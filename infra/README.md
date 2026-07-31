# Infrastructure (AWS CDK)

The Agentic Backtester's backend is defined here as Infrastructure-as-Code with **AWS CDK (Python)** — two stacks under the `agentic-backtest` prefix.

## Stacks

- **`agentic-backtest-data`** (`infra/data_stack.py`) — the S3 Tables *table bucket* `agentic-backtest-market-data`, the durable market-data store. The Iceberg table/schema/rows are loaded separately by [`load_market_data.py`](../market-data-mcp/data/load_market_data.py), because Iceberg table management belongs to pyiceberg, not CloudFormation.
- **`agentic-backtest-backend`** (`infra/backend_stack.py`) — the market-data **Lambda** (arm64 container image), **Cognito** (user pool + domain + machine-to-machine client for gateway auth), and the **AgentCore Gateway + Target** that exposes the Lambda as an MCP tool.

## Usage

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cdk bootstrap aws://<ACCOUNT_ID>/us-east-1                    # one-time per account/region
cdk deploy agentic-backtest-data agentic-backtest-backend    # Docker/colima must be running
```

Keep `.venv` activated for all `cdk` commands — `cdk.json` runs `python3 app.py`, which needs `aws-cdk-lib` on the path.

Handy commands: `cdk ls` (list stacks) · `cdk synth <stack>` (view the generated CloudFormation) · `cdk diff <stack>` · `cdk destroy <stack>`.

See the repo [DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md) for the full end-to-end flow (backend → data load → agents → frontend).
