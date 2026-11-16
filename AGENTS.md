# Agentic Backtester: notes for coding agents

Read this before changing anything. It is the briefing for any agent working in
this repo, including parallel crews in their own worktrees. The project's owner
(the captain) reviews and merges every pull request, and runs every deploy.

## What this is

A public, hosted backtester: someone describes a trading strategy in plain
English, a model writes it as Backtrader code, it runs against stored market
data, and another model writes up the result. Live at
https://main.d1x6utcih65eg5.amplifyapp.com on a capped personal AWS budget.
[README.md](README.md) has the architecture diagram; [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
has the full deploy.

| Path | What it is | Deployed by |
| --- | --- | --- |
| `agents/` | Three AgentCore runtimes, one `@aws/agentcore` CLI project. `app/quant_agent/` runs the backtest pipeline (`pipeline.py`) and chat; `app/strategy_generator/` and `app/results_summary/` are the two model sub-agents | `cd agents && agentcore deploy -y` |
| `backtest-worker/` | Lambda that runs a backtest or chat turn outside the web request and records each step on the job row | `cdk deploy agentic-backtest-hosting` |
| `frontend/` | Next.js 14 on Amplify Hosting: strategy form, live step progress, results, chat | automatic on merge to `main` |
| `infra/` | AWS CDK stacks: data, backend (market-data Lambda, Cognito, Gateway), hosting (quota, jobs, worker, budget), pipeline (ingest) | `cdk deploy <stack>` |
| `market-data-mcp/` | Lambda behind the AgentCore Gateway that reads the Iceberg table | `cdk deploy agentic-backtest-backend` |
| `market-data-pipeline/` | Scheduled ingest of daily bars from the Massive API into S3 Tables (Iceberg) | `cdk deploy agentic-backtest-pipeline` |

A backtest: form → `POST /api/execute-backtest-async` (quota check, job row,
async worker invoke) → worker invokes the quant agent with `{"strategy": ...}` →
the pipeline runs prepare → generate_strategy → fetch_market_data →
run_backtest → summarize, streaming each step's status (server-sent events) →
the worker writes each step onto the job row → the results page polls it.

## Running the tests

Each component has dependency-light test scripts that print ✅/❌ and exit
non-zero on failure. Run the ones for every component you touch, from a fresh
checkout, with `uv`:

| Component | Command (from the repo root) |
| --- | --- |
| Quant agent | `cd agents/app/quant_agent && uv run python test_pipeline.py && uv run python test_pipeline_steps.py && python3 test_strategy_sandbox.py && python3 test_market_data_coverage.py` |
| Results summary agent | `cd agents/app/results_summary && uv run python test_results_summary.py` |
| Ingest pipeline | `cd market-data-pipeline && uv run --no-project --with-requirements requirements.txt python test_ingest.py` |
| Market-data Lambda | `AWS_DEFAULT_REGION=us-east-1 uv run --no-project --with-requirements market-data-pipeline/requirements.txt python market-data-mcp/test_lambda_function.py` |
| Worker | `uv run --no-project --with-requirements backtest-worker/requirements.txt python backtest-worker/test_handler.py` |
| Frontend | `cd frontend && npm ci && npx tsc --noEmit && npm run build` |
| Command guard (`.claude/hooks/`) | `python3 .claude/hooks/test_guard_commands.py` |
| Infra | `cd infra && uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt && source .venv/bin/activate && cdk synth --quiet` (needs the CDK CLI on PATH; synth needs no AWS credentials and writes nothing to AWS) |

None of these call AWS or a model. New behaviour gets a test in the same style:
fakes for the network, real code for everything else.

## Hard rules

1. **No deploys and no AWS changes.** Never run `cdk deploy/destroy`,
   `agentcore deploy`, or any AWS command that writes, deletes or invokes.
   Read-only AWS commands (describe, get, list, logs) are fine, with the
   `personal` profile only. If a change needs a deploy, say which one in the PR
   body; the captain runs it. `.claude/hooks/guard_commands.py` enforces this
   for shell commands; don't work around it.
2. **No live model calls.** Never invoke the agent runtimes, Bedrock, or the
   worker for real. Each backtest costs real money on a personal account.
3. **Merging to `main` deploys the frontend.** Keep `main` releasable: no
   half-finished UI behind a merge.
4. **No `.env` files under `agents/app/`.** The CLI packages each agent's whole
   directory and every agent calls `load_dotenv()`, so a local `.env` silently
   becomes deployed configuration. Runtime config goes in `envVars` in
   `agents/agentcore/agentcore.json`; secrets go through AgentCore Identity.
5. **Before removing any config source, check what the deployed artifact
   actually reads.** A note saying "verified" has been wrong before and broke
   production.
6. **Nothing from anyone's employer.** Use only general, public patterns; never
   copy internal documents, prompts, schemas, code or identifiers.
7. **No AI credit anywhere.** No `Co-Authored-By` trailers, no "Generated with"
   lines in commits, PR bodies, code comments or docs.
8. **No secrets in the repo.** Never print, copy or commit credentials, API
   keys or the contents of `~/.aws`.

## How code here is written

- **Comments explain why, in plain English**, often with the incident that
  motivated the code. Match the surrounding density; don't narrate the obvious.
- **Failure is not empty.** A step that found nothing reports `empty`; one that
  broke reports `failed` or `timeout`. Never return a confident, well-formed
  wrong answer: the project's real failure mode is lying convincingly, not
  crashing.
- **Deterministic work stays out of the model**: dates, arithmetic, statistics,
  ordering. Models get the computed facts.
- **Every model call has an explicit timeout and no automatic retries.** Every
  layer between a click and a model retries by default, and the retries
  multiply; one click once ran seven full backtests.
- **Every route that can reach a model goes through the quota** in
  `frontend/lib/quota.ts`, which fails closed.
- **Commits:** a short imperative subject (`feat(agent): ...`, `fix(pipeline): ...`,
  `docs: ...`) and a body that says why, with the measurement or incident when
  there is one.
- **Docs move with behaviour.** Update the README, DEPLOYMENT_GUIDE or component
  README in the same PR when behaviour they describe changes.

## Known gotchas

- **Amplify SSR has a hard 30-second request limit.** Anything that can take
  longer runs in the worker, never in an API route.
- **botocore defaults are wrong for model calls**: a 60s read timeout and
  retries on. Set both explicitly (see `backtest-worker/handler.py`,
  `agents/app/quant_agent/config.py`). Lambda async invokes also retry twice by
  default; the worker sets that to zero.
- **Reading the agent's event stream**: use `iter_lines(chunk_size=1)`. The
  default reads 1024 bytes at a time and holds small events back.
- **Models have no clock.** Pass today's date when dates matter; the summarizer
  once called real recent data "future-dated".
- **The Iceberg scan returns rows unordered.** Sort by date before using them.
- **The market-data Lambda's answers**: `success=false` with `data=[]` means no
  rows (an empty step); `success=false` with no data list is a failure.
- **`agentcore` CLI commands need `-y`** to run non-interactively.
- **`cdk deploy` for stacks with Lambda images needs Docker running**, and the
  hosting and pipeline stacks drop their alert email subscription unless
  `BUDGET_ALERT_EMAIL` (or `-c alertEmail=`) is set.

## A pull request is done when

- the tests for every touched component pass from a fresh checkout,
- the frontend type-checks and builds, if it was touched,
- docs describing changed behaviour are updated,
- the PR body says what changed, why, how it was verified, and which deploys
  (if any) the captain needs to run, in what order.
