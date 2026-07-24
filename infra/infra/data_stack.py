"""
DataStack — the durable market-data layer.

Owns the S3 Tables *table bucket* (the named, stateful container). The
Iceberg namespace, table, schema, and rows are created by the data loader
(pyiceberg) at load time, because Iceberg table/schema management is
pyiceberg's job.
"""

from aws_cdk import Stack, RemovalPolicy, CfnOutput
from aws_cdk import aws_s3tables as s3tables
from constructs import Construct

# Stable, predictable name that the loader and BackendStack both reference.
MARKET_DATA_BUCKET_NAME = "agentic-backtest-market-data"


class DataStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        table_bucket = s3tables.CfnTableBucket(
            self,
            "MarketDataBucket",
            table_bucket_name=MARKET_DATA_BUCKET_NAME,
        )

        # This holds the data
        table_bucket.apply_removal_policy(RemovalPolicy.RETAIN)

        # Expose for BackendStack (wired in app.py) and the data loader.
        self.table_bucket_name = MARKET_DATA_BUCKET_NAME
        self.table_bucket_arn = table_bucket.attr_table_bucket_arn

        CfnOutput(self, "TableBucketName", value=self.table_bucket_name,
                  description="S3 Tables table bucket for market data")
        CfnOutput(self, "TableBucketArn", value=self.table_bucket_arn,
                  description="ARN of the S3 Tables table bucket")
