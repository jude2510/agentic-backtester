"""
PipelineStack — keeps the market-data table current without anyone remembering to.

Until 2026-09-23 the ingest ran by hand. After a month with no run the table
was 40 days stale, and backtests ran on it without complaint. This stack runs
the ingest on a schedule and alarms when it fails or stops running.

Separate from BackendStack so that deploying it never touches the Gateway,
Cognito or the reader Lambda the agent depends on.

  1. Ingest Lambda — `ingest.py --delta` plus a read-back through the
     market-data Lambda (see market-data-pipeline/lambda_handler.py)
  2. Schedule — weekdays after the US close
  3. Alarms — on failure, and on silence

The Massive API key lives in an SSM SecureString created outside CloudFormation
(CFN cannot create SecureString parameters, and the value must not be in the
template):

  aws ssm put-parameter --name /agentic-backtest/massive-api-key \\
      --type SecureString --value "$MASSIVE_API_KEY"
"""

import os

from aws_cdk import Stack, CfnOutput, Duration
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_ssm as ssm
from aws_cdk.aws_ecr_assets import Platform
from aws_cdk.aws_lambda import Architecture, DockerImageCode, DockerImageFunction
from constructs import Construct

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PIPELINE_DIR = os.path.join(_REPO_ROOT, "market-data-pipeline")

MASSIVE_KEY_PARAM = "/agentic-backtest/massive-api-key"

# Referenced by name rather than as a cross-stack export, so BackendStack can
# still replace or rename its Lambda without first unwinding an export.
MARKET_DATA_FUNCTION = "agentic-backtest-market-data"


class PipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *,
                 alert_email: str | None = None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Ingest Lambda ----------------------------------------------------
        ingest_fn = DockerImageFunction(
            self, "IngestFn",
            function_name="agentic-backtest-ingest",
            code=DockerImageCode.from_image_asset(
                directory=_PIPELINE_DIR,
                platform=Platform.LINUX_ARM64,
            ),
            architecture=Architecture.ARM_64,
            # Five symbols of a few dozen bars each; minutes of headroom over
            # the Iceberg commits, which dominate the runtime.
            timeout=Duration.minutes(5),
            memory_size=1024,
            environment={
                "MASSIVE_API_KEY_PARAM": MASSIVE_KEY_PARAM,
                "MARKET_DATA_FUNCTION": MARKET_DATA_FUNCTION,
            },
        )

        # Same broad managed policy the reader uses; scoping both down to the
        # one table bucket is the shared least-privilege follow-up.
        ingest_fn.role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3TablesFullAccess")
        )

        # Encrypted with the AWS-managed aws/ssm key, whose key policy already
        # lets SSM decrypt for in-account callers — so no explicit KMS grant.
        ssm.StringParameter.from_secure_string_parameter_attributes(
            self, "MassiveApiKey", parameter_name=MASSIVE_KEY_PARAM,
        ).grant_read(ingest_fn)

        lambda_.Function.from_function_name(
            self, "MarketDataFn", MARKET_DATA_FUNCTION,
        ).grant_invoke(ingest_fn)

        # --- Schedule ---------------------------------------------------------
        # 02:00 UTC Tue-Sat is 22:00 EDT / 21:00 EST Mon-Fri: after the close
        # and after extended hours in both halves of the year, so a plain UTC
        # cron needs no daylight-saving handling. Holidays need no calendar
        # either — a run on a closed day just rewrites the overlap unchanged.
        #
        # Lambda's default async retries (2) are left ON here, deliberately the
        # opposite of the backtest worker: this job is idempotent and nearly
        # free, so a retry after a transient vendor error is exactly right.
        events.Rule(
            self, "DailyIngest",
            rule_name="agentic-backtest-daily-ingest",
            schedule=events.Schedule.cron(minute="0", hour="2", week_day="TUE-SAT"),
            targets=[targets.LambdaFunction(ingest_fn)],
        )

        # --- Alarms -----------------------------------------------------------
        topic = sns.Topic(
            self, "PipelineAlertTopic",
            topic_name="agentic-backtest-pipeline-alerts",
            display_name="Agentic Backtester data pipeline alerts",
        )
        if alert_email:
            topic.add_subscription(subs.EmailSubscription(alert_email))

        failed = ingest_fn.metric_errors(
            period=Duration.days(1), statistic="Sum",
        ).create_alarm(
            self, "IngestFailedAlarm",
            alarm_name="agentic-backtest-ingest-failed",
            alarm_description="The scheduled market-data ingest failed (after retries).",
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        # A failure alarm is silent if the job never runs at all — a disabled
        # rule, a deleted target. Runs land on five UTC days a week, so the
        # longest normal gap is two empty days (Sun, Mon); three means stopped.
        silent = ingest_fn.metric_invocations(
            period=Duration.days(1), statistic="Sum",
        ).create_alarm(
            self, "IngestNotRunningAlarm",
            alarm_name="agentic-backtest-ingest-not-running",
            alarm_description="The market-data ingest has not run for 3 days.",
            threshold=1,
            evaluation_periods=3,
            datapoints_to_alarm=3,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
        )

        for alarm in (failed, silent):
            alarm.add_alarm_action(cw_actions.SnsAction(topic))

        CfnOutput(self, "IngestFunctionName", value=ingest_fn.function_name)
        CfnOutput(self, "PipelineAlertTopicArn", value=topic.topic_arn)
