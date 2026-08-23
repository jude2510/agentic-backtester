#!/usr/bin/env python3
import os

import aws_cdk as cdk

from infra.data_stack import DataStack
from infra.backend_stack import BackendStack
from infra.hosting_stack import HostingStack

app = cdk.App()
env = cdk.Environment(account="765607525378", region="us-east-1")

data_stack = DataStack(app, "agentic-backtest-data", env=env)

backend_stack = BackendStack(
    app, "agentic-backtest-backend",
    market_data_bucket_name=data_stack.table_bucket_name,
    env=env,
)
backend_stack.add_dependency(data_stack)

# Public-service guardrails (quota counters + budget alarm). The alert address
# is supplied at deploy time rather than committed:
#   cdk deploy agentic-backtest-hosting -c alertEmail=you@example.com
HostingStack(
    app, "agentic-backtest-hosting",
    alert_email=app.node.try_get_context("alertEmail") or os.getenv("BUDGET_ALERT_EMAIL"),
    agentcore_arn=os.getenv(
        "AGENTCORE_ARN",
        "arn:aws:bedrock-agentcore:us-east-1:765607525378:runtime/quant_agent-eYNAk3BW0d",
    ),
    env=env,
)

app.synth()