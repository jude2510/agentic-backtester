#!/bin/bash

set -e

# Load environment variables if .env exists
if [ -f ".env" ]; then
    echo "🔧 Loading configuration from .env..."
    export $(cat .env | grep -v '^#' | xargs)
fi

# Default configuration
AWS_REGION="${AWS_REGION:-us-east-1}"

echo "=================================================="
echo "📦 Deploying Strategy Quant Agent"
echo "=================================================="
echo ""

if [ -f "quant_agent.py" ]; then
    echo "✅ Agent file found: quant_agent.py"
    echo ""
    
    # Ensure .env file is included in deployment
    echo "📝 Configuring agent..."
    if [ ! -f ".env" ]; then
        echo "❌ .env file not found!"
        exit 1
    fi
    
    agentcore configure \
        --entrypoint quant_agent.py \
        --name quant_agent \
        --requirements-file requirements.txt \
        --idle-timeout 900 \
        --non-interactive
    
    # Build environment variables from .env file
    echo "🔧 Preparing environment variables from .env..."
    ENV_ARGS=""
    if [ -f ".env" ]; then
        # Read .env file and build --env arguments
        while IFS='=' read -r key value; do
            # Skip empty lines and comments
            if [[ ! -z "$key" && ! "$key" =~ ^# ]]; then
                # Remove any quotes from value
                value=$(echo "$value" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
                ENV_ARGS="$ENV_ARGS --env $key=$value"
                echo "   ✓ $key"
            fi
        done < .env
    fi
    
    # Launch the agent with environment variables
    echo "🚀 Launching agent to AgentCore with environment variables: $ENV_ARGS"
    agentcore launch --auto-update-on-conflict $ENV_ARGS
    
    echo "✅ Strategy Quant deployed successfully!"
    echo ""

    # --- Ensure the quant agent can invoke its sub-agents (idempotent) --------
    # The toolkit-created execution role does NOT grant cross-runtime
    # bedrock-agentcore:InvokeAgentRuntime by default; without it the agent gets
    # AccessDenied on the Strategy Generator / Results Summarizer and silently
    # falls back to inline strategy generation. Re-applied on every deploy so it
    # survives a role recreation; role + sub-agent ARNs are discovered dynamically.
    echo "🔐 Ensuring InvokeAgentRuntime permission on sub-agents..."
    ROLE_NAME=$(awk -F': ' '/execution_role: /{print $2; exit}' .bedrock_agentcore.yaml | tr -d '[:space:]'); ROLE_NAME="${ROLE_NAME##*/}"
    STRAT_ARN=$(grep -E '^STRATEGY_GENERATOR_RUNTIME_ARN=' .env | cut -d= -f2- | tr -d '" ')
    SUMM_ARN=$(grep -E '^BACKTEST_SUMMARY_RUNTIME_ARN=' .env | cut -d= -f2- | tr -d '" ')
    if [ -n "$ROLE_NAME" ] && [ -n "$STRAT_ARN" ] && [ -n "$SUMM_ARN" ]; then
        aws iam put-role-policy \
            --role-name "$ROLE_NAME" \
            --policy-name QuantAgentInvokeSubAgents \
            --region "$AWS_REGION" \
            --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"InvokeSubAgents\",\"Effect\":\"Allow\",\"Action\":\"bedrock-agentcore:InvokeAgentRuntime\",\"Resource\":[\"$STRAT_ARN\",\"$STRAT_ARN/*\",\"$SUMM_ARN\",\"$SUMM_ARN/*\"]}]}" \
            && echo "   ✅ InvokeAgentRuntime granted to $ROLE_NAME" \
            || echo "   ⚠️ Could not apply policy — grant it manually if backtests fall back to inline generation"
    else
        echo "   ⚠️ Skipped (could not resolve role name or sub-agent ARNs)"
    fi
    echo ""

    # Check status
    echo "📊 Checking agent status..."
    agentcore status --agent quant_agent
    
    # Test invoke
    echo ""
    echo "🧪 Testing agent invocation..."
    agentcore invoke '{"prompt": "how is the strategy performance: {\"name\": \"EMA Crossover Strategy\", \"stock_symbol\": \"AMZN\", \"backtest_window\": \"1Y\", \"max_positions\": 1, \"stop_loss\": 5, \"take_profit\": 10, \"buy_conditions\": \"10-period SMA crosses above 30-period SMA (bullish momentum)\", \"sell_conditions\": \"10-period SMA crosses below 30-period SMA (bearish momentum)\", \"average\": 30}"}'

else
    echo "❌ Agent file not found: quant_agent.py"
fi

