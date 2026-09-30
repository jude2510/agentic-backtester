"""
BackendStack — the compute + auth + MCP layer.

Built in layers:
  1. Lambda (market-data-mcp) — reads S3 Tables, exposes get_market_data
  2. Cognito (user pool + M2M client) — inbound auth for the Gateway
  3. AgentCore Gateway + Target — the MCP endpoint the quant agent calls
  4. Massive news target — a second Gateway tool, calling the Massive API
     with a key the Gateway fetches from AgentCore Identity

Consumes the S3 Tables bucket created by DataStack.
"""

import json
import os

from aws_cdk import Stack, CfnOutput, Duration, RemovalPolicy, CfnResource
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_iam as iam
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk.aws_lambda import Architecture, DockerImageCode, DockerImageFunction
from aws_cdk.aws_ecr_assets import Platform

from constructs import Construct

# Absolute path to the market-data component (Dockerfile + lambda live here).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_MARKET_DATA_DIR = os.path.join(_REPO_ROOT, "market-data-mcp")
_NEWS_SPEC = os.path.join(os.path.dirname(__file__), "specs", "massive_news_openapi.json")

# The Massive API key, as a Secrets Manager secret holding {"api_key": "..."}.
# Created outside CloudFormation so the key never appears in a template:
#   aws secretsmanager create-secret --name agentic-backtest/massive-api-key \
#     --secret-string "{\"api_key\": \"$MASSIVE_API_KEY\"}"
# The news target and the ingest pipeline both read this one secret.
MASSIVE_API_KEY_SECRET = "agentic-backtest/massive-api-key"
MASSIVE_API_KEY_JSON_KEY = "api_key"
MASSIVE_CREDENTIAL_PROVIDER = "massive-api"


class BackendStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *,
                 market_data_bucket_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Layer 1: Market Data Lambda ------------------------------------
        # CDK builds the Docker image and pushes it to its own ECR — replacing
        # the entire manual ECR + docker build/push dance in deploy_lambda.sh.
        market_data_fn = DockerImageFunction(
            self, "MarketDataFn",
            function_name="agentic-backtest-market-data",
            code=DockerImageCode.from_image_asset(
                directory=_MARKET_DATA_DIR,
                platform=Platform.LINUX_ARM64,
            ),
            architecture=Architecture.ARM_64,
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                "S3_TABLES_BUCKET": market_data_bucket_name,
                "S3_TABLES_NAMESPACE": "daily_data",
                "S3_TABLES_TABLE": "daily_data",
                "S3_TABLES_REGION": self.region,
            },
        )

        # Read access to S3 Tables. Broad managed policy for now; can be
        # tightened to a scoped read-only policy later (a nice least-privilege
        # follow-up).
        market_data_fn.role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3TablesFullAccess")
        )

        self.market_data_fn = market_data_fn

        CfnOutput(self, "MarketDataFunctionArn", value=market_data_fn.function_arn)
        CfnOutput(self, "MarketDataFunctionName", value=market_data_fn.function_name)

        # --- Layer 2: Cognito (inbound auth for the Gateway) ----------------
        # Machine-to-machine (client-credentials) auth. The quant agent fetches
        # a token from this pool and presents it to the Gateway as a Bearer JWT.
        user_pool = cognito.UserPool(
            self, "GatewayUserPool",
            user_pool_name="agentic-backtest-gateway",
            removal_policy=RemovalPolicy.DESTROY,   # auth infra, recreatable
        )

        # A resource server + scope is required for the client-credentials flow.
        gateway_scope = cognito.ResourceServerScope(
            scope_name="invoke",
            scope_description="Invoke the market-data MCP gateway",
        )
        resource_server = user_pool.add_resource_server(
            "GatewayResourceServer",
            identifier="agentic-backtest",
            scopes=[gateway_scope],
        )

        # Hosted domain — the agent builds the token URL from this prefix.
        user_pool.add_domain(
            "GatewayDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix="agentic-backtest-mcp",
            ),
        )

        # M2M app client (has a secret, uses only client_credentials).
        gateway_client = user_pool.add_client(
            "GatewayClient",
            user_pool_client_name="agentic-backtest-gateway-client",
            generate_secret=True,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=[cognito.OAuthScope.resource_server(resource_server, gateway_scope)],
            ),
        )

        # Exposed for the Gateway (Layer 3, same stack) and for the agent's .env.
        self.user_pool = user_pool
        self.gateway_client = gateway_client

        CfnOutput(self, "CognitoUserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "CognitoClientId", value=gateway_client.user_pool_client_id)
        CfnOutput(self, "CognitoDomain", value="agentic-backtest-mcp")

        # --- Layer 3: AgentCore Gateway + Target ----------------------------
        # Execution role the Gateway assumes to invoke the Lambda target.
        gateway_role = iam.Role(
            self, "GatewayRole",
            role_name="agentic-backtest-gateway-role",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )
        market_data_fn.grant_invoke(gateway_role)

        # Cognito pool's OIDC discovery endpoint — the Gateway validates inbound
        # JWTs against this and checks client_id ∈ AllowedClients.
        discovery_url = (
            f"https://cognito-idp.{self.region}.amazonaws.com/"
            f"{user_pool.user_pool_id}/.well-known/openid-configuration"
        )

        gateway = CfnResource(
            self, "MarketDataGateway",
            type="AWS::BedrockAgentCore::Gateway",
            properties={
                "Name": "agentic-backtest-gateway",
                "AuthorizerType": "CUSTOM_JWT",
                "RoleArn": gateway_role.role_arn,
                "AuthorizerConfiguration": {
                    "CustomJWTAuthorizer": {
                        "DiscoveryUrl": discovery_url,
                        "AllowedClients": [gateway_client.user_pool_client_id],
                    }
                },
            },
        )
        gateway.node.add_dependency(gateway_role)

        # The one tool the target exposes. NOTE: PascalCase keys — this is
        # AgentCore's SchemaDefinition, NOT lowercase JSON Schema.
        get_market_data_tool = {
            "Name": "get_market_data",
            "Description": "Get comprehensive daily data for backtesting of a specific stock symbol",
            "InputSchema": {
                "Type": "object",
                "Properties": {
                    "symbol": {"Type": "string", "Description": "Stock symbol, e.g. AMZN, AAPL, MSFT"},
                    "start_date": {"Type": "string", "Description": "Start date YYYY-MM-DD (use 01 for single-digit month/day)"},
                    "end_date": {"Type": "string", "Description": "End date YYYY-MM-DD (use 01 for single-digit month/day)"},
                    "limit": {"Type": "integer", "Description": "Max data points to return (default 252)"},
                },
                "Required": [],
            },
        }

        # Target name is LOAD-BEARING: the agent calls
        # 'market-data-lambda-target___get_market_data'.
        target = CfnResource(
            self, "MarketDataTarget",
            type="AWS::BedrockAgentCore::GatewayTarget",
            properties={
                "Name": "market-data-lambda-target",
                "GatewayIdentifier": gateway.get_att("GatewayIdentifier").to_string(),
                "CredentialProviderConfigurations": [
                    {"CredentialProviderType": "GATEWAY_IAM_ROLE"}
                ],
                "TargetConfiguration": {
                    "Mcp": {
                        "Lambda": {
                            "LambdaArn": market_data_fn.function_arn,
                            "ToolSchema": {"InlinePayload": [get_market_data_tool]},
                        }
                    }
                },
            },
        )
        target.node.add_dependency(gateway)

        # --- Layer 4: Massive news target -------------------------------------
        # The Gateway calls Massive itself and attaches the API key on each
        # request, so the key never enters the agent's container. AgentCore
        # Identity holds it as an API-key credential provider that points at
        # the customer-managed secret (EXTERNAL), rather than copying it.
        massive_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "MassiveApiKeySecret", MASSIVE_API_KEY_SECRET,
        )

        massive_provider = agentcore.CfnApiKeyCredentialProvider(
            self, "MassiveApiKeyProvider",
            name=MASSIVE_CREDENTIAL_PROVIDER,
            api_key_secret_source="EXTERNAL",
            api_key_secret_config=agentcore.CfnApiKeyCredentialProvider.SecretReferenceProperty(
                secret_id=MASSIVE_API_KEY_SECRET,
                json_key=MASSIVE_API_KEY_JSON_KEY,
            ),
        )

        # What the Gateway needs to use that provider: a token for its own
        # workload identity, the API key from the token vault, and the secret
        # behind it. Scoped to this gateway, this provider and this secret.
        gateway_identity_resources = [
            self.format_arn(service="bedrock-agentcore", resource="workload-identity-directory",
                            resource_name="default"),
            self.format_arn(service="bedrock-agentcore", resource="workload-identity-directory",
                            resource_name="default/workload-identity/agentic-backtest-gateway-*"),
        ]
        gateway_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:GetWorkloadAccessToken"],
            resources=gateway_identity_resources,
        ))
        gateway_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:GetResourceApiKey"],
            resources=[
                self.format_arn(service="bedrock-agentcore", resource="token-vault",
                                resource_name="default"),
                massive_provider.attr_credential_provider_arn,
                *gateway_identity_resources,
            ],
        ))
        massive_secret.grant_read(gateway_role)

        with open(_NEWS_SPEC) as f:
            news_spec = json.load(f)

        # Tool name the agent calls: 'massive-news___getTickerNews'.
        news_target = CfnResource(
            self, "MassiveNewsTarget",
            type="AWS::BedrockAgentCore::GatewayTarget",
            properties={
                "Name": "massive-news",
                "GatewayIdentifier": gateway.get_att("GatewayIdentifier").to_string(),
                "CredentialProviderConfigurations": [{
                    "CredentialProviderType": "API_KEY",
                    "CredentialProvider": {
                        "ApiKeyCredentialProvider": {
                            "ProviderArn": massive_provider.attr_credential_provider_arn,
                            "CredentialLocation": "HEADER",
                            "CredentialParameterName": "Authorization",
                            "CredentialPrefix": "Bearer",
                        }
                    },
                }],
                "TargetConfiguration": {
                    "Mcp": {"OpenApiSchema": {"InlinePayload": json.dumps(news_spec)}}
                },
            },
        )
        # The permissions above live in the role's separate DefaultPolicy
        # resource; depend on that, not just the role, so they exist first.
        news_target.node.add_dependency(gateway, gateway_role.node.find_child("DefaultPolicy"))

        CfnOutput(self, "MassiveCredentialProviderArn",
                  value=massive_provider.attr_credential_provider_arn)
        CfnOutput(self, "GatewayUrl", value=gateway.get_att("GatewayUrl").to_string())
        CfnOutput(self, "GatewayArn", value=gateway.get_att("GatewayArn").to_string())
