"""
Creds-free smoke test for the Results Summary agent's typed output.

Uses Pydantic AI's TestModel to exercise the agent WITHOUT calling Bedrock:
TestModel auto-generates data matching the agent's output_type, so this verifies
that BacktestReport is a valid schema and that run_sync().output is a typed,
validated instance — no AWS credentials or network required.

Run from app/results_summary/:
    uv run python test_results_summary.py
"""

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from results_summary import BacktestReport, Recommendations


def test_backtest_report_output():
    # Swap in TestModel — no real LLM call. It fills the output_type schema.
    agent = Agent(TestModel(), output_type=BacktestReport)
    report = agent.run_sync("analyze this backtest").output

    assert isinstance(report, BacktestReport), type(report)
    assert isinstance(report.executive_summary, str)
    assert isinstance(report.detailed_analysis, str)
    assert isinstance(report.concerns_and_recommendations, Recommendations)
    assert isinstance(report.concerns_and_recommendations.high_priority, list)

    # Round-trips to the camelCase JSON contract the caller expects
    payload = report.model_dump_json(by_alias=True)
    assert '"executiveSummary"' in payload
    assert '"concernsAndRecommendations"' in payload

    print("✅ BacktestReport works as a Pydantic AI output_type")
    print("   sample:", payload[:160], "...")


if __name__ == "__main__":
    test_backtest_report_output()
