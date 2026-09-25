# Frontend — Agentic Backtester

A Next.js 14 (App Router) UI for the Agentic Backtester: a strategy form, a results page with metrics, trade log and the AI-written report, and a chat view over past runs. It runs on AWS Amplify Hosting at **https://main.d1x6utcih65eg5.amplifyapp.com**.

## How it works

A backtest takes a minute or more, but Amplify's server-side rendering cuts off any request after 30 seconds, so no route waits on a model:

1. **POST** `/api/execute-backtest-async` checks the quota counters, writes a job row to DynamoDB and invokes the worker Lambda asynchronously. It returns a job ID in well under a second.
2. The **worker** ([`backtest-worker/`](../backtest-worker)) invokes the Quant Agent and writes the result to the job row.
3. The results page **polls** the same route with `?jobId=` until the job is complete or has failed.

Chat (`/api/chat`) works the same way. Quota enforcement is in [`lib/quota.ts`](./lib/quota.ts): atomic DynamoDB counters, which fail closed if the table can't be reached.

- **`app/page.tsx`**: strategy form. It offers only the symbols and windows the stored data covers, per the coverage table in `types/strategy.ts`, which is kept in step with `market-data-pipeline/symbols.json` by hand.
- **`app/results/`**: metrics, trades, the report, and warnings when the data fell short of the requested window
- **`app/chat/`**: questions about past backtests
- **`components/Disclaimer.tsx`**: the site-wide "not financial advice" footer

## Run locally

```bash
npm install
cp .env.example .env.local
export AWS_PROFILE=<your-profile>   # the routes use the deployed tables and worker
npm run dev                         # http://localhost:3000
```

`QUOTA_ENFORCED=false` in `.env.local` skips the usage caps locally. AWS credentials come from your profile through the SDK's default chain, and no keys are stored in the repo.

See the repo [DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md) for the hosting setup.

## Tech stack

Next.js 14 · TypeScript · Tailwind CSS · Framer Motion · AWS SDK v3 (Lambda, DynamoDB)
