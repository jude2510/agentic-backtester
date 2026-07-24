# Deployment Guide

This guide deploys the Agentic Backtester end to end:

1. **Backend infrastructure** — defined as Infrastructure-as-Code with **AWS CDK (Python)**: two stacks under the `agentic-backtest` prefix that provision the S3 Tables market-data store, the market-data Lambda, Cognito (machine-to-machine auth), and the AgentCore Gateway + Target (MCP). This replaces the original sample's imperative shell scripts.
2. **Agents** — three Strands agents deployed to AgentCore Runtime with the `agentcore` CLI.
3. **Frontend** — a Next.js app run locally against the orchestrator.

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Backend infrastructure (AWS CDK)](#2-backend-infrastructure-aws-cdk)
   - [2.1 Bootstrap & deploy the stacks](#21-bootstrap--deploy-the-stacks)
   - [2.2 Load market data](#22-load-market-data)
3. [Agents (AgentCore Runtime)](#3-agents-agentcore-runtime)
   - [3.1 Strategy Generator](#31-strategy-generator)
   - [3.2 Result Summarizer](#32-result-summarizer)
   - [3.3 Quant Agent (orchestrator)](#33-quant-agent-orchestrator)
4. [Frontend (Next.js)](#4-frontend-nextjs)

---

## 1. Prerequisites

- **AWS CLI** configured with credentials (e.g. `export AWS_PROFILE=<profile> AWS_REGION=us-east-1`)
- **AWS CDK v2** (`npm install -g aws-cdk`) and **Node.js 20+**
- **Docker** (or **colima**) running — CDK builds the Lambda container image locally
- **Python 3.11+**
- **`agentcore` CLI** (install with `pipx install bedrock-agentcore-starter-toolkit`) for the agents
- **`jq`** for JSON processing

Your AWS credentials need permissions for: CloudFormation, S3 Tables, Lambda, ECR, IAM, Cognito, and Bedrock AgentCore.

---

## 2. Backend infrastructure (AWS CDK)

The [`infra/`](./infra) directory is a CDK app with two stacks:

- **`agentic-backtest-data`** (DataStack) — the S3 Tables *table bucket* `agentic-backtest-market-data` (the durable data container). The Iceberg table/schema/rows are loaded separately (see 2.2), because Iceberg table management is pyiceberg's job, not CloudFormation's.
- **`agentic-backtest-backend`** (BackendStack) — the market-data Lambda (arm64 container image), Cognito (user pool + domain + M2M client), and the AgentCore Gateway + Target that exposes the Lambda as an MCP tool.

### 2.1 Bootstrap & deploy the stacks

```bash
cd infra
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# One-time per account/region:
cdk bootstrap aws://<ACCOUNT_ID>/us-east-1

# Deploy both stacks (Docker/colima must be running for the Lambda image build):
cdk deploy agentic-backtest-data agentic-backtest-backend
```

> Keep the `.venv` activated whenever you run `cdk` — `cdk.json` invokes `python3 app.py`, which needs `aws-cdk-lib` on the path.

Note the stack outputs (printed on deploy, or via `aws cloudformation describe-stacks`):

- **`GatewayUrl`** — the MCP endpoint is this URL **+ `/mcp`** (the `GatewayMcpUrl` output gives it directly)
- **`CognitoDomain`**, **`CognitoClientId`**, **`CognitoUserPoolId`**

The Cognito **client secret** is intentionally *not* a CloudFormation output. Fetch it when you need it:

```bash
aws cognito-idp describe-user-pool-client \
  --user-pool-id <CognitoUserPoolId> --client-id <CognitoClientId> \
  --query 'UserPoolClient.ClientSecret' --output text
```

### 2.2 Load market data

CDK creates the table *bucket*; this idempotent pyiceberg loader creates the `daily_data` table and loads rows. It performs a **full reload** on every run, so it's safe to re-run.

```bash
cd backend-agents/quant-agent/tools/market_data_mcp
python3 -m venv .venv && source .venv/bin/activate      # separate from the CDK venv
pip install -r requirements.txt
python data/load_market_data.py                          # loads data/amzn.daily.csv → agentic-backtest-market-data
```

Verify the Lambda can read the bucket end to end:

```bash
aws lambda invoke --function-name agentic-backtest-market-data \
  --payload '{"symbol":"AMZN","limit":5}' --cli-binary-format raw-in-base64-out out.json
jq '.body | fromjson | .metadata' out.json   # expect success:true, total_rows:5
```

---

## 3. Agents (AgentCore Runtime)

Each agent is deployed with the `agentcore` CLI via its `deploy_to_agentcore.sh`, which reads a local `.env` and passes the values as runtime environment variables.

### 3.1 Strategy Generator

Converts a natural-language strategy into executable Backtrader code.

```bash
cd backend-agents/strategy-generator-agent
cp .env.sample .env        # model is pinned to us.anthropic.claude-opus-4-6-v1 (opus-4-7 is account-gated)
./deploy_to_agentcore.sh
```

Save the Runtime ARN from the output.

### 3.2 Result Summarizer

Turns raw backtest metrics into a readable report (Amazon Nova).

```bash
cd backend-agents/result-summarizer-agent
cp .env.sample .env
./deploy_to_agentcore.sh
```

Save the Runtime ARN.

### 3.3 Quant Agent (orchestrator)

Create `backend-agents/quant-agent/.env` from the CDK outputs (section 2.1) and the two agent ARNs above:

```bash
AWS_REGION=us-east-1
QUANT_AGENT_MODEL_ID=us.anthropic.claude-sonnet-4-6

# Runtime ARNs from 3.1 and 3.2
STRATEGY_GENERATOR_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:<ACCOUNT_ID>:runtime/strategy_generator-xxxx
BACKTEST_SUMMARY_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:<ACCOUNT_ID>:runtime/results_summary-xxxx

# Market-data backend from the CDK stack outputs (2.1)
AGENTCORE_GATEWAY_URL=<GatewayUrl>/mcp
COGNITO_DOMAIN=<CognitoDomain>
COGNITO_CLIENT_ID=<CognitoClientId>
COGNITO_CLIENT_SECRET=<from describe-user-pool-client>
```

Then deploy:

```bash
cd backend-agents/quant-agent
./deploy_to_agentcore.sh
```

Save the Quant Agent Runtime ARN for the frontend.

> **Auth note:** the Quant Agent authenticates to the Gateway with the Cognito **client-credentials** grant (client id + secret → bearer JWT). No Cognito users and no extra IAM policy on the agent role are required — the Gateway validates the JWT against the Cognito pool's OIDC discovery URL.

---

## 4. Frontend (Next.js)

```bash
cd frontend
npm install
cp .env.example .env.local
# In .env.local set:
#   AGENTCORE_ARN=<Quant Agent Runtime ARN from 3.3>
#   AWS_REGION=us-east-1
#   NEXT_PUBLIC_APP_VERSION=1.0.0
export AWS_PROFILE=<profile>   # the server-side API routes invoke the agent via the AWS SDK
npm run dev                    # http://localhost:3000
```

### Test

1. Open `http://localhost:3000`.
2. Submit a strategy (e.g. *EMA 5/20 crossover on AMZN, 1 year*) and confirm a performance report renders.
3. Ask the chat *"list my last 3 backtests"* to confirm the memory path.

---

## Teardown

```bash
cd infra && source .venv/bin/activate
cdk destroy agentic-backtest-backend agentic-backtest-data
```

The data bucket has a `RETAIN` removal policy, so `cdk destroy` leaves `agentic-backtest-market-data` intact; delete it manually with `aws s3tables delete-table-bucket` if you also want the data gone. Delete the three agent runtimes with `aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id <id>`.

---

## Support and Resources

- **Amazon Bedrock AgentCore:** https://docs.aws.amazon.com/bedrock/latest/userguide/agents.html
- **AWS CDK (Python):** https://docs.aws.amazon.com/cdk/v2/guide/work-with-cdk-python.html
- **Docker:** https://docs.docker.com/get-docker/ · **colima:** https://github.com/abiosoft/colima
