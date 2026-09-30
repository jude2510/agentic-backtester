"""
AgentCore Interactive Backtesting Agent, hosted on Amazon Bedrock AgentCore.

Two modes:
  backtest  the strategy form. A fixed pipeline in code (pipeline.py) calls the
            strategy generator and results summary runtimes, the market data
            Gateway, and Backtrader, and streams each step's status back.
  chat      a Pydantic AI agent that answers questions about past backtests.
"""

import os
import json
from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.runtime import BedrockAgentCoreContext
import config
from pipeline import Steps, run_pipeline
from tools import (
    backtest_strategy,
    fetch_market_data,
    generate_strategy,
    summarize_backtest,
    get_backtest_history,
)

# Initialize the AgentCore app (lightweight)
app = BedrockAgentCoreApp()

LIVE_STEPS = Steps(
    generate=generate_strategy,
    fetch=fetch_market_data,
    backtest=backtest_strategy,
    summarize=summarize_backtest,
)


def _as_message(text: str) -> dict:
    """Shape a plain-text agent output as the message object the frontend expects
    (`result.content[0].text`), preserving the response contract across the UI routes."""
    return {"role": "assistant", "content": [{"text": text}]}


def _ensure_initialized():
    """
    Lazy initialization of heavy resources.
    Called on first invoke() to defer expensive operations.
    """
    if config._initialized:
        return

    print("🔧 Initializing heavy resources (lazy init)...")

    # Initialize AWS clients and memory
    config.initialize_clients()

    print("✅ Lazy initialization complete")


def _chat_agent():
    """The chat agent, built on the first chat request. Backtests don't use a
    model here, so a backtest's cold start skips importing Pydantic AI."""
    if config._chat_agent is not None:
        return config._chat_agent

    from pydantic_ai import Agent
    from pydantic_ai.models.bedrock import BedrockConverseModel
    from pydantic_ai.providers.bedrock import BedrockProvider

    model_id = os.getenv('QUANT_AGENT_MODEL_ID', 'us.anthropic.claude-sonnet-4-6')
    print(f"   Chat Model ID: {model_id}")

    config._chat_agent = Agent(
        BedrockConverseModel(model_id, provider=BedrockProvider(region_name=config._region_name)),
        system_prompt="""You are the Quant Research Assistant. You help quants analyze their historical backtesting results and suggest strategy improvements.

You have access to the get_backtest_history tool which retrieves past backtest records including:
- Strategy description (plain English)
- Generated strategy code (Backtrader Python code)
- Trade records (entry/exit dates, prices, P&L)
- Performance metrics (Sharpe ratio, max drawdown, total return, win rate)

When users ask about their strategies:
1. Use get_backtest_history to retrieve relevant historical runs
2. Analyze patterns across multiple backtests
3. Identify strengths and weaknesses (focus on risk-adjusted returns, drawdowns, consistency)
4. Suggest specific improvements with rationale (e.g., parameter adjustments, risk management, position sizing)
5. Compare performance across different strategies/parameters when applicable

Be quantitative in your analysis. Reference specific metrics and trades. When suggesting improvements, explain the expected impact on risk-adjusted returns.

If no historical data is found, inform the user that no backtest history exists yet and suggest running a backtest first.

Always be concise but thorough. Prioritize actionable insights over generic advice.""",
        tools=[get_backtest_history]
    )
    return config._chat_agent


@app.entrypoint
def invoke(payload, context=None):
    """Main entrypoint: `{"mode": "chat", "prompt": ...}` or `{"strategy": {...}}`."""
    try:
        # Lazy initialization on first call
        _ensure_initialized()

        print("🚀 AgentCore Runtime: Backtesting Agent processing request")
        print(f"📥 Payload received: {payload}")

        # Parse payload if it's a string
        if isinstance(payload, str):
            payload = json.loads(payload)

        if payload.get("mode") == "chat":
            print("💬 Chat mode: Using chat agent for historical analysis")
            result = _chat_agent().run_sync(payload.get("prompt"))
            return {
                "result": _as_message(result.output)
            }

        if "strategy" in payload:
            # The runtime delivers this request's workload access token in
            # request context. Captured here and re-applied in the market-data
            # step, rather than trusting the context variable to reach the
            # thread that step runs on. Presence only is logged, never the token.
            config._workload_access_token = BedrockAgentCoreContext.get_workload_access_token()
            print(f"🪪 Workload access token in request context: {config._workload_access_token is not None}")

            # Returning the pipeline's generator makes the runtime stream its
            # events (server-sent events), so the worker can record each step
            # as it happens. The pipeline runs as the stream is read, after this
            # function has returned, which is why anything taken from request
            # context has to be captured above.
            print("🧭 Backtest mode: pipeline")
            return run_pipeline(payload["strategy"], LIVE_STEPS, agent_version=config.VERSION)

        # A payload with neither used to fall through to a full backtest. It now
        # gets told what was expected instead of running one on a guess.
        print("⚠️ Payload has neither mode=chat nor strategy; nothing run")
        return {"result": {"status": "error",
                           "error": 'expected {"mode": "chat", "prompt": ...} or {"strategy": {...}}'}}

    except Exception as e:
        print(f"❌ Error in invoke function: {e}")
        import traceback
        traceback.print_exc()
        return {"result": {"status": "error", "error": str(e)}}


if __name__ == "__main__":
    print("🚀 Starting Pydantic AI Multi-Agent Quant Backtesting Agent on AgentCore")
    print(f"   App type: {type(app)}")
    print(f"   App methods: {[m for m in dir(app) if not m.startswith('_')]}")

    print("\n🌐 Starting server on port 8080...")
    try:
        app.run(port=8080)
    except Exception as e:
        print(f"❌ Server startup failed: {e}")
        import traceback
        traceback.print_exc()
