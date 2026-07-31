# Agentic Backtester — AgentCore Agents

The three agents behind the [Agentic Backtester](../../README.md), managed as a single [AgentCore CLI](https://github.com/aws/agentcore-cli) (`@aws/agentcore`) project. One CloudFormation stack provisions all three runtimes, their memories, and IAM.

| Agent (`app/`) | Role | Model |
| --- | --- | --- |
| `quant_agent` | Orchestrator — coordinates the other two plus the market-data and backtest tools; holds chat memory | Claude Sonnet |
| `strategy_generator` | Turns a natural-language strategy into Backtrader code | Claude Opus |
| `results_summary` | Writes the human-readable performance report | Amazon Nova |

See the repo [DEPLOYMENT_GUIDE.md](../../DEPLOYMENT_GUIDE.md) for the end-to-end deploy flow.

## Project structure

```
agenticbacktester/
├── AGENTS.md               # AI-assistant context (schema, invariants) — CLI-managed
├── agentcore/
│   ├── agentcore.json      # Project config: runtimes (+ envVars), memories
│   ├── aws-targets.json    # Deployment target (account + region)
│   ├── .env.local          # Secrets (gitignored) — the Cognito client secret
│   ├── .llm-context/       # Type definitions for the JSON config
│   └── cdk/                # Generated CDK (@aws/agentcore-cdk)
└── app/                    # Agent source (one dir per agent, each with a pyproject.toml)
    ├── quant_agent/
    ├── strategy_generator/
    └── results_summary/
```

## Getting Started

### Prerequisites

- **Node.js** 20.x or later
- **Python 3.10+** and **uv** for Python agents ([install uv](https://docs.astral.sh/uv/getting-started/installation/))
- **AWS credentials** configured (`aws configure` or environment variables)
- **Docker** (only for Container build agents)

### Development

Run your agent locally:

```bash
agentcore dev
```

### Deployment

Deploy to AWS:

```bash
agentcore deploy
```

## Commands

| Command | Description |
| --- | --- |
| `agentcore create` | Create a new AgentCore project |
| `agentcore add` | Add resources (agent, memory, credential, gateway, evaluator, policy) |
| `agentcore remove` | Remove resources |
| `agentcore dev` | Run agent locally with hot-reload |
| `agentcore deploy` | Deploy to AWS via CDK |
| `agentcore status` | Show deployment status |
| `agentcore invoke` | Invoke agent (local or deployed) |
| `agentcore logs` | View agent logs |
| `agentcore traces` | View agent traces |
| `agentcore eval` | Run evaluations |
| `agentcore package` | Package agent artifacts |
| `agentcore validate` | Validate configuration |
| `agentcore pause` | Pause a deployed agent |
| `agentcore resume` | Resume a paused agent |
| `agentcore fetch` | Fetch remote resource definitions |
| `agentcore import` | Import existing resources |
| `agentcore update` | Check for CLI updates |

## Configuration

Edit the JSON files in `agentcore/` to configure your project. See `agentcore/.llm-context/` for type definitions and validation constraints.

The project uses a **flat resource model** — agents, memories, credentials, gateways, evaluators, and policies are top-level arrays in `agentcore.json`. Resources are independent; agents discover memories and credentials at runtime via environment variables or SDK calls.

## Resources

| Resource | Purpose |
| --- | --- |
| Agent (runtime) | HTTP, MCP, or A2A agent deployed to AgentCore Runtime |
| Memory | Persistent context storage with configurable strategies |
| Credential | API key or OAuth credential providers |
| Gateway | MCP gateway that routes tool calls to targets |
| Gateway Target | Tool implementation (Lambda, MCP server, OpenAPI, Smithy, API Gateway) |
| Evaluator | Custom LLM-as-a-Judge or code-based evaluation |
| Online Eval Config | Continuous evaluation pipeline for deployed agents |
| Policy | Cedar authorization policies for gateway tools |

### Agent Types

- **Template agents**: Created from framework templates (Strands, LangChain/LangGraph, GoogleADK, OpenAI Agents, Autogen)
- **BYO agents**: Bring your own code with `agentcore add agent --type byo`
- **Import agents**: Import existing Bedrock agents with `agentcore import`

### Build Types

- **CodeZip**: Python source packaged as a zip and deployed directly to AgentCore Runtime
- **Container**: Docker image built via CodeBuild (ARM64), pushed to ECR, and deployed to AgentCore Runtime

## Documentation

- [AgentCore CLI](https://github.com/aws/agentcore-cli)
- [AgentCore CDK Constructs](https://github.com/aws/agentcore-l3-cdk-constructs)
- [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/)
