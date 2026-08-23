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

from aws_cdk import Stack, CfnOutput, RemovalPolicy
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

# Derived from the measured ~$0.22/backtest. Kept here as the single source of
# truth; the frontend reads them from stack outputs / env rather than
# hardcoding its own copy.
MONTHLY_BACKTEST_LIMIT = 110   # ≈ $25 of model spend
DAILY_BACKTEST_LIMIT = 15      # stops one bad day consuming the month
PER_USER_DAILY_LIMIT = 3       # fairness; enforced once auth provides identity

MONTHLY_BUDGET_USD = 25


class HostingStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *,
                 alert_email: str | None = None, **kwargs) -> None:
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
        CfnOutput(self, "BudgetAlertTopicArn", value=topic.topic_arn,
                  description="SNS topic for budget threshold alerts")
        CfnOutput(self, "MonthlyBacktestLimit", value=str(MONTHLY_BACKTEST_LIMIT))
        CfnOutput(self, "DailyBacktestLimit", value=str(DAILY_BACKTEST_LIMIT))
        CfnOutput(self, "PerUserDailyLimit", value=str(PER_USER_DAILY_LIMIT))
