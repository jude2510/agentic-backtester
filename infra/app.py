#!/usr/bin/env python3
import os

import aws_cdk as cdk

from infra.data_stack import DataStack
from infra.backend_stack import BackendStack
from infra.hosting_stack import HostingStack
from infra.pipeline_stack import PipelineStack

app = cdk.App()
env = cdk.Environment(account="765607525378", region="us-east-1")

# Alert address for the budget and pipeline alarms, supplied at deploy time
# rather than committed. Deploying a stack WITHOUT it removes that stack's email
# subscription, so export it once per shell:
#   export BUDGET_ALERT_EMAIL=you@example.com
alert_email = app.node.try_get_context("alertEmail") or os.getenv("BUDGET_ALERT_EMAIL")

data_stack = DataStack(app, "agentic-backtest-data", env=env)

backend_stack = BackendStack(
    app, "agentic-backtest-backend",
    market_data_bucket_name=data_stack.table_bucket_name,
    env=env,
)
backend_stack.add_dependency(data_stack)

# Public-service guardrails (quota counters, job worker, budget alarm).
HostingStack(
    app, "agentic-backtest-hosting",
    alert_email=alert_email,
    agentcore_arn=os.getenv(
        "AGENTCORE_ARN",
        "arn:aws:bedrock-agentcore:us-east-1:765607525378:runtime/quant_agent-eYNAk3BW0d",
    ),
    env=env,
)

# Scheduled ingest + alarms. Writes to the table DataStack owns.
pipeline_stack = PipelineStack(app, "agentic-backtest-pipeline", alert_email=alert_email, env=env)
pipeline_stack.add_dependency(data_stack)

app.synth()