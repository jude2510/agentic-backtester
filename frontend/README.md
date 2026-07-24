# Frontend — Agentic Backtester

A Next.js 14 (App Router) UI for the Agentic Backtester. You describe a trading strategy in a form; the app invokes the Quant Agent on Amazon Bedrock AgentCore — server-side, via the AWS SDK inside a Next.js API route — and renders the backtest report plus a chat view over past runs.

## Run locally

```bash
npm install
export AWS_PROFILE=<your-profile>   # server-side API routes call AgentCore via the AWS SDK
npm run dev                         # http://localhost:3000
```

Create a `.env.local` in this directory:

```env
AWS_REGION=us-east-1
AGENTCORE_ARN=arn:aws:bedrock-agentcore:us-east-1:<ACCOUNT_ID>:runtime/quant_agent-xxxx
NEXT_PUBLIC_APP_VERSION=1.0.0
```

- **`AGENTCORE_ARN`** — the Quant Agent runtime ARN from the agent deploy (see the repo [DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md)).
- AWS credentials resolve from your profile/role via the default SDK chain — **no keys are stored in the repo**.

## How it works

- **`app/page.tsx`** — the strategy-builder form
- **`app/api/`** — server-side routes that invoke the Quant Agent (backtest) and the chat endpoint via the AWS SDK
- **`app/results/`** — renders the metrics, trade log, and the AI-written performance report
- **`lib/`, `types/`** — API client, shared state, and TypeScript types

## Tech stack

Next.js 14 · TypeScript · Tailwind CSS · AWS SDK v3 (Amazon Bedrock AgentCore)
