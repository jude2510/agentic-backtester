# Infrastructure (AWS CDK)

The Agentic Backtester's AWS infrastructure is defined here as Infrastructure-as-Code with **AWS CDK (Python)** — four stacks under the `agentic-backtest` prefix. (The agents are deployed separately by the AgentCore CLI; see [`agents/`](../agents).)

## Stacks

- **`agentic-backtest-data`** (`infra/data_stack.py`) — the S3 Tables *table bucket* `agentic-backtest-market-data`, the durable market-data store. The Iceberg table and rows are written by the [ingest pipeline](../market-data-pipeline), because Iceberg table management belongs to pyiceberg, not CloudFormation.
- **`agentic-backtest-backend`** (`infra/backend_stack.py`) — the market-data **Lambda** (arm64 container image), **Cognito** (user pool + domain + machine-to-machine client for gateway auth), and the **AgentCore Gateway + Target** that exposes the Lambda as an MCP tool.
- **`agentic-backtest-hosting`** (`infra/hosting_stack.py`) — what public hosting needs: DynamoDB quota counters and job table, the **backtest worker** Lambda, the IAM role Amplify's server-side routes run as, and a $25/month **AWS Budget** on gross usage.
- **`agentic-backtest-pipeline`** (`infra/pipeline_stack.py`) — the scheduled **ingest Lambda** (weekdays after the US close, with a read-back check) and its failure and not-running alarms.

`BUDGET_ALERT_EMAIL` (or `-c alertEmail=...`) must be set whenever you deploy the hosting or pipeline stack, or the alarm email subscriptions are removed.

## Usage

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cdk bootstrap aws://<ACCOUNT_ID>/us-east-1                    # one-time per account/region
cdk deploy agentic-backtest-data agentic-backtest-backend    # Docker/colima must be running
cdk deploy agentic-backtest-hosting agentic-backtest-pipeline   # after the agents; see the guide
```

Keep `.venv` activated for all `cdk` commands — `cdk.json` runs `python3 app.py`, which needs `aws-cdk-lib` on the path.

Handy commands: `cdk ls` (list stacks) · `cdk synth <stack>` (view the generated CloudFormation) · `cdk diff <stack>` · `cdk destroy <stack>`.

See the repo [DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md) for the full end-to-end flow (backend → market data → agents → hosting → scheduled ingest).
