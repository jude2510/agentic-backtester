#!/usr/bin/env python3
import aws_cdk as cdk
from infra.data_stack import DataStack
from infra.backend_stack import BackendStack

app = cdk.App()
env = cdk.Environment(account="765607525378", region="us-east-1")

data_stack = DataStack(app, "agentic-backtest-data", env=env)

backend_stack = BackendStack(
    app, "agentic-backtest-backend",
    market_data_bucket_name=data_stack.table_bucket_name,
    env=env,
)
backend_stack.add_dependency(data_stack)

app.synth()