"""
Results Summary Agent - Analyzes and summarizes backtest results.

Built with Pydantic AI (agent + Bedrock model) hosted on Amazon Bedrock AgentCore.
Uses a typed `output_type` so the model returns a schema-validated report object
rather than free-form text the caller has to parse.
"""

import os
from typing import Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.providers.bedrock import BedrockProvider
from bedrock_agentcore import BedrockAgentCoreApp
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Version tracking for troubleshooting
VERSION = os.getenv('AGENT_VERSION', datetime.now().strftime('%Y%m%d_%H%M%S'))

# Initialize the AgentCore app
app = BedrockAgentCoreApp()


# ---------------------------------------------------------------------------
# Typed output schema. Field aliases map snake_case Python fields to the
# camelCase JSON keys the downstream tool + frontend already consume, so the
# response contract ({"analysis": "<json string>"}) is unchanged.
# ---------------------------------------------------------------------------
class Recommendations(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    high_priority: list[str] = Field(alias="highPriority")
    medium_priority: list[str] = Field(alias="mediumPriority")
    consider_testing: list[str] = Field(alias="considerTesting")


class BacktestReport(BaseModel):
    """A structured quant review of a Backtrader backtest."""
    model_config = ConfigDict(populate_by_name=True)
    executive_summary: str = Field(alias="executiveSummary", description="2-3 sentence overview of the strategy's viability")
    detailed_analysis: str = Field(alias="detailedAnalysis", description="In-depth examination with specific numbers and interpretations")
    concerns_and_recommendations: Recommendations = Field(alias="concernsAndRecommendations")


class ResultsSummaryAgent:
    """Agent that analyzes backtest results and provides summaries"""

    def __init__(self):
        instructions = """
You are an expert quantitative analyst with 20+ years of experience in algorithmic trading, portfolio management, and strategy optimization. Review the Backtrader backtest results and produce a professional, actionable assessment.

When analyzing, consider:
- Total return and risk-adjusted returns (Sharpe, Sortino)
- Maximum drawdown, drawdown duration, and recovery periods
- Consistency across market regimes and sample-size adequacy
- Overfitting / data-quality red flags: Sharpe > 3, win rate > 70%, unrealistically smooth equity curves, very few trades (< 30), look-ahead or survivorship bias, ignored transaction costs

Populate the report fields:
- executiveSummary: a 2-3 sentence overview of the strategy's viability
- detailedAnalysis: an in-depth examination citing specific numbers and interpretations
- concernsAndRecommendations: highPriority (critical fixes), mediumPriority (optimizations), and considerTesting (experimental ideas)

Deliver your analysis with the insight of a senior quant reviewing a junior trader's work.
"""

        # Get Results Summary specific configuration from environment.
        # Default to a Claude model: Pydantic AI enforces structured output via
        # tool-calling, which Claude handles reliably (Nova-Lite can be flaky).
        aws_region = os.getenv('AWS_REGION', 'us-east-1')
        model_id = os.getenv('RESULTS_SUMMARY_MODEL_ID', 'us.anthropic.claude-sonnet-4-6')
        temperature = float(os.getenv('RESULTS_SUMMARY_TEMPERATURE', '0.3'))

        print("🔧 Results Summary Configuration:")
        print(f"   Version: {VERSION}")
        print(f"   Model ID: {model_id}")
        print(f"   Region: {aws_region}")
        print(f"   Temperature: {temperature}")

        # Create the Pydantic AI agent with a typed output schema
        model = BedrockConverseModel(model_id, provider=BedrockProvider(region_name=aws_region))
        self.agent = Agent(
            model,
            system_prompt=instructions,
            output_type=BacktestReport,
            model_settings={'temperature': temperature},
        )

    def analyze_results(self, backtest_results: Dict[str, Any]) -> str:
        """Analyze backtest results and return the report as a JSON string."""
        if 'error' in backtest_results:
            return f"❌ **Backtest Error**: {backtest_results['error']}"

        try:
            import json

            # Extract key information from backtest results
            initial_value = backtest_results.get('initial_value', 0)
            final_value = backtest_results.get('final_value', 0)
            total_return = backtest_results.get('total_return', 0)
            symbol = backtest_results.get('symbol', 'Unknown')
            strategy_name = backtest_results.get('strategy_class', 'Unknown Strategy')
            metrics = backtest_results.get('metrics', {})

            # Trade detail and the actual data period. Without these the model
            # can only report them as unknown — which read as hallucinated data
            # gaps next to a narrative that quoted the very same numbers.
            trades = backtest_results.get('trades') or []
            trade_summary = backtest_results.get('trade_summary') or {}
            period = backtest_results.get('backtest_period') or {}

            if period:
                period_block = (
                    f"**Backtest Period**: {period.get('start')} to {period.get('end')} "
                    f"({period.get('trading_days')} trading days)\n"
                )
            else:
                period_block = ""

            if trade_summary:
                summary_block = f"\nTrade Summary:\n{json.dumps(trade_summary, indent=2)}\n"
            else:
                summary_block = ""

            # Cap the trade list so a long backtest cannot blow up the prompt,
            # while still showing both ends of the sequence.
            if trades:
                if len(trades) > 40:
                    shown = trades[:20] + trades[-20:]
                    note = (f"\n(showing first 20 and last 20 of {len(trades)} "
                            f"trades)\n")
                else:
                    shown, note = trades, ""
                trades_block = f"\nTrades:{note}\n{json.dumps(shown, indent=2)}\n"
            else:
                trades_block = "\nTrades: none executed\n"

            # Create a comprehensive prompt for AI analysis
            prompt = f"""Please analyze the following Backtrader backtest results:

**Strategy Name**: {strategy_name}
**Symbol Traded**: {symbol}
{period_block}**Initial Capital**: ${initial_value:,.2f}
**Final Portfolio Value**: ${final_value:,.2f}
**Total Return**: {total_return:.2f}%
**Profit/Loss**: ${final_value - initial_value:,.2f}

Performance Metrics:
{json.dumps(metrics, indent=2)}
{summary_block}{trades_block}
Base your analysis only on the data above. Every field provided here is
complete — do not describe the trade count, win rate, or backtest period as
missing or unknown when they appear above.
"""

            # Use AI to analyze the results — output is a validated BacktestReport
            print("🤖 Invoking AI analysis for backtest results...")
            print(prompt)
            report = self.agent.run_sync(prompt).output
            # Serialize back to the camelCase JSON string the caller expects
            analysis = report.model_dump_json(by_alias=True)
            print(f"output: {analysis}")

            return analysis

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            return f"❌ **Analysis Error**: {str(e)}\n\nDetails:\n{error_details}"

    def process(self, input_data: Any) -> Any:
        """Process backtest results and return analysis"""
        return self.analyze_results(input_data)


# Lazy initialization globals
_initialized = False
_agent = None

def _ensure_initialized():
    """Initialize the agent on first use to avoid cold start timeout"""
    global _initialized, _agent
    if _initialized:
        return
    _agent = ResultsSummaryAgent()
    _initialized = True

@app.entrypoint
def invoke(payload, context=None):
    """Main entrypoint for the backtesting agent"""
    _ensure_initialized()
    analysis_result = _agent.process(payload)

    return {
        "analysis": str(analysis_result),
        "version": VERSION
    }


if __name__ == "__main__":
    print("\n🌐 Starting server on port 8080...")
    try:
        app.run(port=8080)
    except Exception as e:
        print(f"❌ Server startup failed: {e}")
        import traceback
        traceback.print_exc()
