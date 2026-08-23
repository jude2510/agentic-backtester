"""
HostingStack — the guardrails for running this as a public service.

Separate from BackendStack on purpose: those resources exist whether or not
anyone else can reach the app. These exist *because* strangers can.

  1. Quota table (DynamoDB) — atomic counters that cap how many backtests can
     run per day and per month, globally and (once auth lands) per user.
  2. Budget alarm — an independent backstop, because an app-level counter fails
     open if the app has a bug.

Sizing: a backtest costs roughly $0.22, ~90% of it Bedrock model calls
(measured Aug 2026: $1.512 of LLM spend across 7 runs). A $25/month ceiling is
therefore about 110 backtests.
"""

import os

from aws_cdk import Stack, CfnOutput, Duration, RemovalPolicy
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk.aws_ecr_assets import Platform
from aws_cdk.aws_lambda import Architecture, DockerImageCode, DockerImageFunction
from constructs import Construct

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_WORKER_DIR = os.path.join(_REPO_ROOT, "backtest-worker")

# Derived from the measured ~$0.22/backtest. Kept here as the single source of
# truth; the frontend reads them from stack outputs / env rather than
# hardcoding its own copy.
MONTHLY_BACKTEST_LIMIT = 110   # ≈ $25 of model spend
DAILY_BACKTEST_LIMIT = 15      # stops one bad day consuming the month
PER_USER_DAILY_LIMIT = 3       # fairness; enforced once auth provides identity

MONTHLY_BUDGET_USD = 25


class HostingStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *,
                 alert_email: str | None = None,
                 agentcore_arn: str | None = None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Quota counters -------------------------------------------------
        # One item per counter window, e.g.
        #   global#month#2026-08          global#day#2026-08-23
        #   user#<sub>#day#2026-08-23
        # Increments are conditional (count < limit), so the check and the
        # increment are a single atomic operation — two concurrent requests
        # cannot both slip past the last remaining unit of quota.
        quota_table = dynamodb.Table(
            self, "QuotaTable",
            table_name="agentic-backtest-quota",
            partition_key=dynamodb.Attribute(
                name="id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            # Counters are derived state — losing them costs nothing but a
            # reset window, so they need not survive stack deletion.
            removal_policy=RemovalPolicy.DESTROY,
            # Expired windows clean themselves up; no sweeper needed.
            time_to_live_attribute="expires_at",
            point_in_time_recovery=False,
        )

        # --- Job state + worker ---------------------------------------------
        # A backtest runs ~85s. Serverless SSR freezes its execution
        # environment the moment a response is sent, so the slow work cannot
        # live in the request handler, and per-instance memory cannot hold the
        # job status. Both move here.
        jobs_table = dynamodb.Table(
            self, "JobsTable",
            table_name="agentic-backtest-jobs",
            partition_key=dynamodb.Attribute(
                name="jobId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="expiresAt",
        )

        worker = DockerImageFunction(
            self, "BacktestWorker",
            function_name="agentic-backtest-worker",
            code=DockerImageCode.from_image_asset(
                directory=_WORKER_DIR,
                platform=Platform.LINUX_ARM64,
            ),
            architecture=Architecture.ARM_64,
            # Comfortably past the ~85s a backtest takes, with headroom for a
            # cold agent runtime (which alone can add 90s).
            timeout=Duration.minutes(10),
            memory_size=512,
            environment={
                "JOBS_TABLE_NAME": jobs_table.table_name,
                # Falls back to the known runtime; overridable at deploy time.
                "AGENTCORE_ARN": agentcore_arn or "",
            },
        )

        # Asynchronous invocation retries a throwing function twice more by
        # default. For an idempotent, cheap function that is a sensible default;
        # here each attempt runs a fresh ~$0.22 backtest, and it compounds with
        # any client-level retry inside the function. One observed failure
        # produced 7 agent invocations from a single user request.
        #
        # This work is expensive and not idempotent: it should be attempted
        # once, and a failure should surface to the user rather than being
        # silently re-run at their expense.
        worker.configure_async_invoke(
            retry_attempts=0,
            max_event_age=Duration.minutes(15),
        )

        jobs_table.grant_read_write_data(worker)
        worker.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=["*"],  # scoped by the single ARN in env
            )
        )

        self.jobs_table = jobs_table
        self.worker = worker

        # --- Budget backstop -------------------------------------------------
        topic = sns.Topic(
            self, "BudgetAlertTopic",
            topic_name="agentic-backtest-budget-alerts",
            display_name="Agentic Backtester budget alerts",
        )
        if alert_email:
            topic.add_subscription(subs.EmailSubscription(alert_email))

        # AWS Budgets must be able to publish to the topic.
        topic.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["SNS:Publish"],
                principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
                resources=[topic.topic_arn],
            )
        )

        # include_credit=False is the important flag. This account's usage is
        # currently offset 1:1 by promotional credits, so a budget tracking NET
        # cost would read $0 right up until the credits expire and then jump
        # straight to real money. Tracking gross usage surfaces the true burn
        # rate now, while there is still time to react.
        budgets.CfnBudget(
            self, "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="agentic-backtest-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=MONTHLY_BUDGET_USD, unit="USD"
                ),
                cost_types=budgets.CfnBudget.CostTypesProperty(
                    include_credit=False,      # track gross usage, not net
                    include_refund=False,
                    include_discount=True,
                    use_amortized=False,
                ),
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type="ACTUAL",
                        threshold=threshold,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            address=topic.topic_arn, subscription_type="SNS"
                        )
                    ],
                )
                for threshold in (50, 80, 100)
            ],
        )

        self.quota_table = quota_table

        CfnOutput(self, "QuotaTableName", value=quota_table.table_name,
                  description="DynamoDB table holding backtest quota counters")
        CfnOutput(self, "JobsTableName", value=jobs_table.table_name,
                  description="DynamoDB table holding backtest job state")
        CfnOutput(self, "WorkerFunctionName", value=worker.function_name,
                  description="Lambda that runs a backtest to completion")
        CfnOutput(self, "WorkerFunctionArn", value=worker.function_arn)
        CfnOutput(self, "BudgetAlertTopicArn", value=topic.topic_arn,
                  description="SNS topic for budget threshold alerts")
        CfnOutput(self, "MonthlyBacktestLimit", value=str(MONTHLY_BACKTEST_LIMIT))
        CfnOutput(self, "DailyBacktestLimit", value=str(DAILY_BACKTEST_LIMIT))
        CfnOutput(self, "PerUserDailyLimit", value=str(PER_USER_DAILY_LIMIT))
