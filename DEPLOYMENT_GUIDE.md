# Deployment Guide

This guide deploys the Agentic Backtester end to end into your own AWS account:

1. **Backend infrastructure**: AWS CDK stacks for the market-data store, the market-data Lambda, Cognito and the AgentCore Gateway.
2. **Market data**: loaded from the Massive API by the ingest pipeline.
3. **Agents**: three Pydantic AI agents on AgentCore Runtime, deployed as one `@aws/agentcore` CLI project.
4. **Hosting**: quota counters, the job worker and a budget alarm (CDK), plus the Next.js app on Amplify Hosting.
5. **Scheduled ingest**: keeps the market data current (CDK).

Section 6 covers running the frontend locally against the deployed backend.

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Backend infrastructure](#2-backend-infrastructure)
3. [Market data](#3-market-data)
4. [Agents](#4-agents)
5. [Hosting](#5-hosting)
6. [Scheduled ingest](#6-scheduled-ingest)
7. [Local frontend development](#7-local-frontend-development)
8. [Teardown](#teardown)

---

## 1. Prerequisites

- **AWS CLI** with credentials (`export AWS_PROFILE=<profile> AWS_REGION=us-east-1`)
- **AWS CDK v2** (`npm install -g aws-cdk`) and **Node.js 20+**
- **Docker** (or colima) running. CDK builds three Lambda container images locally.
- **Python 3.11+**
- **`agentcore` CLI** (`npm install -g @aws/agentcore`) and **[uv](https://docs.astral.sh/uv/)** on the path
- **A [Massive](https://massive.com) API key**. The Stocks Starter plan (5 years of daily history) is enough.
- Amazon Bedrock model access to Claude Opus 4.6 and Claude Sonnet 4.6 in your region

All CDK commands run from `infra/` with its virtualenv active, since `cdk.json` runs `python3 app.py`:

```bash
cd infra
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cdk bootstrap aws://<ACCOUNT_ID>/us-east-1        # once per account/region
```

The account ID and region are set in [`infra/app.py`](./infra/app.py). Change them there for your account.

---

## 2. Backend infrastructure

```bash
cdk deploy agentic-backtest-data agentic-backtest-backend
```

- **`agentic-backtest-data`** creates the S3 Tables table bucket `agentic-backtest-market-data` (retained on delete).
- **`agentic-backtest-backend`** creates the market-data Lambda, a Cognito user pool with a machine-to-machine client, and the AgentCore Gateway that exposes the Lambda as an MCP tool.

Note the outputs `GatewayUrl` (the MCP endpoint is this URL **plus `/mcp`**), `CognitoDomain` and `CognitoClientId`. The client secret is deliberately not an output:

```bash
aws cognito-idp describe-user-pool-client \
  --user-pool-id <CognitoUserPoolId> --client-id <CognitoClientId> \
  --query 'UserPoolClient.ClientSecret' --output text
```

---

## 3. Market data

The pipeline in [`market-data-pipeline/`](./market-data-pipeline) creates the Iceberg table if needed and loads daily bars for the symbols in `symbols.json`. Every write is a filtered overwrite scoped to one symbol, so any run can be repeated safely.

```bash
cd market-data-pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export MASSIVE_API_KEY=<your key>

python ingest.py --years 5 --dry-run     # preview: what would be written, per symbol
python ingest.py --years 5               # backfill
```

**Optional: deeper AMZN history.** The repo includes AMZN daily bars back to 2000 in `market-data-mcp/data/amzn.daily.csv`. To keep them, seed them **before** the backfill, into the empty table:

```bash
cd market-data-mcp && pip install -r requirements.txt
python data/load_market_data.py          # refuses to run if the table already exists
```

The backfill then preserves those rows and extends AMZN to the present.

Check that the agent's read path serves the data:

```bash
aws lambda invoke --function-name agentic-backtest-market-data \
  --cli-binary-format raw-in-base64-out --payload '{"symbol":"SPY","limit":1}' out.json
cat out.json        # body.data[0].date should be the latest trading day
```

---

## 4. Agents

The three agents are a single [`@aws/agentcore`](https://github.com/aws/agentcore-cli) project in [`agents/`](./agents), deployed as one CloudFormation stack (`AgentCore-agenticbacktester-default`).

- **Code** lives in `agents/app/<agent>/`.
- **Non-secret config** is in the `envVars` of each runtime in [`agents/agentcore/agentcore.json`](./agents/agentcore/agentcore.json). That covers model IDs, the Gateway URL, the Cognito domain and client ID, and the sub-agent ARNs.
- **The one secret**, the Cognito client secret, is currently supplied through a gitignored `agents/app/quant_agent/.env`. The CLI packages everything in an agent's directory and each agent calls `load_dotenv()`, so that file is deployed with the code. This is a stopgap (see the note below).
- **Memory IDs** are injected automatically as `MEMORY_<NAME>_ID`.

> `agents/agentcore/.env.local` is **not** injected into runtimes as environment variables. The CLI uses it for AgentCore Identity credential providers. The planned fix is an outbound OAuth credential provider, so the agent gets Gateway tokens from AgentCore Identity and never holds the secret. Until then, keep everything *except* that secret in `agentcore.json`: any other `.env` under `agents/app/` silently becomes deployed configuration.

### 4.1 Configure and deploy

Set the `quant_agent` `envVars` in `agentcore.json` from section 2: `AGENTCORE_GATEWAY_URL` (`<GatewayUrl>/mcp`), `COGNITO_DOMAIN` and `COGNITO_CLIENT_ID`. Then:

```bash
cd agents
echo "COGNITO_CLIENT_SECRET=<from describe-user-pool-client>" > app/quant_agent/.env   # gitignored
agentcore deploy -y
agentcore status          # all three READY; note each runtime ARN
```

On a first deploy, the sub-agent ARNs don't exist yet. Once they do, put them in the `quant_agent` `envVars` (`STRATEGY_GENERATOR_RUNTIME_ARN`, `BACKTEST_SUMMARY_RUNTIME_ARN`) and run `agentcore deploy -y` again.

### 4.2 Let the Quant Agent call its sub-agents

The CLI's execution role for `quant_agent` does **not** include permission to invoke the other two runtimes. Without it, the sub-agent calls fail with AccessDenied, and the orchestrator quietly writes the strategy itself instead, so the results look plausible but are wrong. Add the grant yourself:

```bash
QUANT_ROLE=$(aws bedrock-agentcore-control get-agent-runtime \
  --agent-runtime-id <quant_agent runtime id> --query roleArn --output text | cut -d/ -f2)

aws iam put-role-policy --role-name "$QUANT_ROLE" --policy-name QuantAgentInvokeSubAgents \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Sid": "InvokeSubAgents",
      "Effect": "Allow",
      "Action": "bedrock-agentcore:InvokeAgentRuntime",
      "Resource": [
        "<strategy_generator runtime ARN>", "<strategy_generator runtime ARN>/*",
        "<results_summary runtime ARN>",    "<results_summary runtime ARN>/*"
      ]
    }]
  }'
```

The inline policy survives later `agentcore deploy` runs.

---

## 5. Hosting

### 5.1 Guardrails and the job worker

```bash
cd infra && source .venv/bin/activate
export AGENTCORE_ARN=<quant_agent runtime ARN>
export BUDGET_ALERT_EMAIL=<you@example.com>
cdk deploy agentic-backtest-hosting
```

This creates the quota counters and job table (DynamoDB), the worker Lambda that runs each backtest and chat turn, the IAM role Amplify's server-side code runs as, and a $25/month AWS Budget. The budget alerts at 50/80/100% of **gross** usage, so promotional credits can't hide spend. Confirm the SNS subscription email it sends.

> Set `BUDGET_ALERT_EMAIL` every time you deploy this stack (or `agentic-backtest-pipeline`). Deploying without it removes the email subscription.

Usage limits (15 backtests a day, 110 a month) are set in [`infra/infra/hosting_stack.py`](./infra/infra/hosting_stack.py) and [`frontend/lib/quota.ts`](./frontend/lib/quota.ts).

### 5.2 Amplify Hosting

In the Amplify console:

1. **Create new app**, choose GitHub, and authorize the Amplify GitHub App for your repository and the `main` branch.
2. Tick **"My app is a monorepo"** and set the app root to **`frontend`**. Do **not** tick "My monorepo uses Amplify Gen2 Backend": this repo has no Amplify backend, and that option makes the build fail.
3. Keep the detected Next.js build settings (`npm run build`, output `.next`). Amplify deploys it as SSR (`WEB_COMPUTE`).
4. Under **App settings → IAM roles**, set the **compute role** to the `AmplifySSRComputeRoleArn` output from 5.1. The server-side routes use that role, so no access keys are stored anywhere.

No environment variables are required: the table and worker names default to the deployed names. Every push to `main` then rebuilds the site.

---

## 6. Scheduled ingest

The `agentic-backtest-pipeline` stack runs `ingest.py --delta` at 02:00 UTC Tuesday to Saturday (after the US close all year round). It then reads every symbol back through the market-data Lambda and fails if any is more than five days old. It alarms on failure, and on three days without a run.

The Massive key goes in an SSM SecureString, which CloudFormation can't create. `$MASSIVE_API_KEY` is expanded by the shell, so the key doesn't end up in your shell history:

```bash
aws ssm put-parameter --name /agentic-backtest/massive-api-key \
  --type SecureString --value "$MASSIVE_API_KEY"

cd infra && source .venv/bin/activate
export BUDGET_ALERT_EMAIL=<you@example.com>
cdk deploy agentic-backtest-pipeline      # then confirm the SNS subscription email
```

Test it once. The first run is a cold start and takes about 90 seconds, which is longer than the AWS CLI's 60-second default, so raise the timeout or the CLI will retry and start a second run:

```bash
aws lambda invoke --function-name agentic-backtest-ingest \
  --cli-read-timeout 300 out.json && cat out.json      # latest date per symbol
```

---

## 7. Local frontend development

The local app uses the deployed quota table, job table and worker, with your own credentials:

```bash
cd frontend
npm install
cp .env.example .env.local        # set QUOTA_ENFORCED=false to skip the caps locally
export AWS_PROFILE=<profile>
npm run dev                       # http://localhost:3000
```

---

## Teardown

```bash
cd infra && source .venv/bin/activate
cdk destroy agentic-backtest-pipeline agentic-backtest-hosting agentic-backtest-backend agentic-backtest-data
aws cloudformation delete-stack --stack-name AgentCore-agenticbacktester-default    # the three agents
aws ssm delete-parameter --name /agentic-backtest/massive-api-key
```

Then delete the Amplify app in the console. The data bucket is retained on purpose. Delete it with `aws s3tables delete-table-bucket` if you want the data gone too.
