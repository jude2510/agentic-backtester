"""
AgentCore Interactive Backtesting Agent
Demonstrates "Agent as Tool" patterns with AgentCore Gateway integration.

Orchestrator built with Pydantic AI (agent + tools) hosted on Amazon Bedrock AgentCore.
It coordinates strategy generation, market data, backtesting, and results summary.
"""

import os
import json
import datetime as dt
from typing import Literal
from pydantic import BaseModel, Field
from bedrock_agentcore import BedrockAgentCoreApp
import config
from tools import (
    fetch_market_data_via_gateway,
    generate_trading_strategy,
    run_backtest,
    create_results_summary,
    get_backtest_history
)

# Initialize the AgentCore app (lightweight)
app = BedrockAgentCoreApp()


def _as_message(text: str) -> dict:
    """Shape a plain-text agent output as the message object the frontend expects
    (`result.content[0].text`), preserving the response contract across the UI routes."""
    return {"role": "assistant", "content": [{"text": text}]}


# (window token, calendar days back, approximate trading days)
_WINDOWS = (
    ("1M", 30, 21), ("3M", 91, 63), ("6M", 182, 126), ("1Y", 365, 252),
    ("2Y", 730, 504), ("5Y", 1825, 1260), ("10Y", 3652, 2520), ("20Y", 7305, 5040),
)


def _date_reference() -> str:
    """Build a lookup table of resolved date ranges for every backtest window.

    The model has no clock: it cannot know the current date and will otherwise
    anchor on its training cutoff, silently backtesting a window that can be a
    year or more stale. Date arithmetic is deterministic work, so it is done
    here and handed over as a lookup rather than asked of the LLM.

    Computed per invocation, not at init — the runtime container is reused
    across calls, so a date resolved once at startup would go stale.
    """
    today = dt.date.today()
    rows = "\n".join(
        f"  {w}: start_date={(today - dt.timedelta(days=days)).isoformat()}, "
        f"end_date={today.isoformat()}, limit={limit}"
        for w, days, limit in _WINDOWS
    )
    return (
        f"DATE REFERENCE — today is {today.isoformat()}.\n"
        f"Use these EXACT values for fetch_market_data_via_gateway. Match the\n"
        f"strategy's backtest_window field to a row and copy the values verbatim.\n"
        f"Do NOT compute dates yourself.\n{rows}\n"
    )


class BacktestRun(BaseModel):
    """Typed final output of the quant orchestrator (backtest mode).

    The numeric metrics/trades reach the UI via dedicated fields set by the tools
    (backtest_metrics, trades, summary_report); this schema captures the agent's
    own final response — the human-readable writeup plus a one-word verdict.
    """
    narrative: str = Field(description="Complete human-readable markdown performance report for the user")
    verdict: Literal["strong", "promising", "weak", "broken"] = Field(
        description="One-word overall assessment of the strategy's backtested performance"
    )


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

    # Create the Pydantic AI agent backed by a Bedrock model
    from pydantic_ai import Agent
    from pydantic_ai.models.bedrock import BedrockConverseModel
    from pydantic_ai.providers.bedrock import BedrockProvider

    _quant_model_id = os.getenv('QUANT_AGENT_MODEL_ID', 'us.anthropic.claude-sonnet-4-6')
    print(f"   Quant Agent Model ID: {_quant_model_id}")

    _quant_model = BedrockConverseModel(
        _quant_model_id,
        provider=BedrockProvider(region_name=config._region_name),
    )

    config._quant_agent = Agent(
        _quant_model,
        system_prompt="""You are the Quant Backtesting Agent. When you receive ANY request, you MUST automatically execute ALL 4 steps in this EXACT sequence:

STEP 1: ALWAYS call generate_trading_strategy first
- Use the user's request to create a JSON strategy format
- If no specific strategy is provided, create a default EMA crossover strategy for AMZN
- Pass the JSON strategy to generate_trading_strategy tool

STEP 2: ALWAYS call fetch_market_data_via_gateway
- Use the symbol from the strategy (default to AMZN if not specified)
- CRITICAL: A "DATE REFERENCE" table is included at the top of the user message.
  Find the row matching the strategy's backtest_window field and copy its
  start_date, end_date, and limit VERBATIM into the tool call.
- You do NOT know today's date. NEVER infer, guess, or calculate dates yourself —
  always take them from the DATE REFERENCE table.
- Call fetch_market_data_via_gateway with symbol, start_date, end_date, and limit

STEP 3: ALWAYS call run_backtest
- Use the strategy code from Step 1 and market data from Step 2
- Use initial investment of $10,000 if not specified
- Call run_backtest with all required parameters

STEP 4: ALWAYS call create_results_summary
- Use the backtest results from Step 3
- Call create_results_summary to format the final results

FINAL OUTPUT: After all 4 tools have run, return your answer as the structured
BacktestRun output:
- narrative: a complete, human-readable markdown performance report built from
  create_results_summary's output
- verdict: a one-word overall assessment (strong | promising | weak | broken)

CRITICAL RULES:
- Execute ALL 4 steps in sequence for EVERY request
- WAIT for each tool to complete before calling the next tool
- Do NOT call multiple tools simultaneously
- Do NOT ask for clarification - proceed with defaults AMZN 1-year if information is missing
- Do NOT explain what you're going to do - just DO all 4 steps, then return the BacktestRun
- Complete the entire workflow automatically and synchronously """,
        output_type=BacktestRun,
        tools=[
            fetch_market_data_via_gateway,
            generate_trading_strategy,
            run_backtest,
            create_results_summary,
            get_backtest_history
        ]
    )

    # Create chat mode agent for analyzing historical backtests
    config._chat_agent = Agent(
        _quant_model,
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

    print("✅ Lazy initialization complete")


@app.entrypoint
def invoke(payload, context=None):
    """Main entrypoint for the backtesting agent"""
    try:
        # Lazy initialization on first call
        _ensure_initialized()

        print("🚀 AgentCore Runtime: Backtesting Agent processing request")
        print(f"📥 Payload received: {payload}")

        # Parse payload if it's a string
        if isinstance(payload, str):
            payload = json.loads(payload)

        # Check if this is a chat mode request
        mode = payload.get("mode", "backtest")

        if mode == "chat":
            print("💬 Chat mode: Using chat agent for historical analysis")
            result = config._chat_agent.run_sync(payload.get("prompt"))
            return {
                "result": _as_message(result.output)
            }

        # Default: backtest execution mode
        print("🔬 Backtest mode: Using quant agent for 4-step backtest execution")

        # Reset before each run
        config._generated_strategy_code = None
        config._last_backtest_result = None
        config._results_summary_report = None

        # Prepend resolved dates so the model never has to guess "today".
        dated_prompt = f"{_date_reference()}\n{payload.get('prompt')}"
        result = config._quant_agent.run_sync(dated_prompt)

        # Use _last_backtest_result directly (set by run_backtest tool)
        # This is more reliable than reading from Memory which may return stale data
        trades = []
        trade_summary = {}
        if config._last_backtest_result:
            trades = config._last_backtest_result.get('trades', [])
            trade_summary = config._last_backtest_result.get('trade_summary', {})
            print(f"📊 invoke() returning {len(trades)} trades from _last_backtest_result")
        else:
            print("⚠️ invoke() _last_backtest_result is None, falling back to Memory")
            latest = config.get_backtest_results_from_memory()
            if latest:
                trades = latest.get('trades', [])
                trade_summary = latest.get('trade_summary', {})
                print(f"📊 invoke() returning {len(trades)} trades from Memory")

        # Build backtest_metrics from _last_backtest_result for frontend
        backtest_metrics = None
        if config._last_backtest_result and "error" not in config._last_backtest_result:
            backtest_metrics = {
                "initial_value": config._last_backtest_result.get("initial_value"),
                "final_value": config._last_backtest_result.get("final_value"),
                "total_return": config._last_backtest_result.get("total_return"),
                "metrics": config._last_backtest_result.get("metrics", {}),
            }
            print(f"backtest_metrics: {backtest_metrics}")

        # The results_summary agent returns a schema-validated report (typed
        # output_type). Pass it through as structured data so the frontend
        # renders the analysis directly instead of parsing it out of free text.
        summary_report = None
        if config._results_summary_report:
            try:
                summary_report = json.loads(config._results_summary_report)
            except Exception:
                summary_report = None

        return {
            "result": _as_message(result.output.narrative),
            "verdict": result.output.verdict,
            "summary_report": summary_report,
            "strategy_code": config._generated_strategy_code,
            "trades": trades,
            "trade_summary": trade_summary,
            "backtest_metrics": backtest_metrics,
            "versions": {
                "quant_agent": config.VERSION,
                "strategy_generator": config._strategy_generator_version,
                "results_summary": config._results_summary_version
            }
        }

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
