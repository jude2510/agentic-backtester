"""
Tests for the live pipeline steps in tools/, individually and end to end
through the pipeline. Only the network is faked: the sub-agent runtimes and
the Gateway answer with canned responses, while the sandbox check, the Gateway
response parsing, Backtrader and the summary payload are the real code.

Needs the agent's venv (backtrader, pandas, botocore, httpx, pydantic):

    cd agents/app/quant_agent && .venv/bin/python test_pipeline_steps.py
"""

import contextlib
import datetime as dt
import io
import json
import math
import os
import sys
import types

# A stand-in for config.py, which loads .env and prints the environment.
config = types.ModuleType("config")
config.SUB_AGENT_TIMEOUT_SECONDS = 120
config.saved_to_memory = []
config.save_backtest_results_to_memory_sync = (
    lambda result, strategy_code=None: config.saved_to_memory.append(result))
sys.modules["config"] = config

os.environ["STRATEGY_GENERATOR_RUNTIME_ARN"] = "arn:test:strategy-generator"
os.environ["BACKTEST_SUMMARY_RUNTIME_ARN"] = "arn:test:results-summary"

import httpx  # noqa: E402
from botocore.exceptions import ReadTimeoutError  # noqa: E402

import tools.market_data as market_data  # noqa: E402
from pipeline import Steps, run_pipeline  # noqa: E402
from step_types import MarketData, StepError  # noqa: E402
from tools import backtest_strategy, fetch_market_data, generate_strategy, summarize_backtest  # noqa: E402

STRATEGY_ARN = os.environ["STRATEGY_GENERATOR_RUNTIME_ARN"]
SUMMARY_ARN = os.environ["BACKTEST_SUMMARY_RUNTIME_ARN"]

SMA_CROSS = '''```python
import backtrader as bt


class SmaCrossStrategy(bt.Strategy):
    params = (("fast", 5), ("slow", 20))

    def __init__(self):
        fast = bt.indicators.SMA(self.data.close, period=self.params.fast)
        slow = bt.indicators.SMA(self.data.close, period=self.params.slow)
        self.cross = bt.indicators.CrossOver(fast, slow)

    def next(self):
        if not self.position:
            if self.cross > 0:
                size = int(self.broker.getcash() * 0.95 / self.data.close[0])
                if size > 0:
                    self.buy(size=size)
        elif self.cross < 0:
            self.close()
```'''

REPORT = {"executiveSummary": "Trend-following works on this series but the sample is small.",
          "detailedAnalysis": "Four paragraphs at most.",
          "concernsAndRecommendations": {"highPriority": [], "mediumPriority": [], "considerTesting": []}}

FORM = {"name": "SMA Cross", "stock_symbol": "AMZN", "backtest_window": "1Y", "position_pct": 50,
        "stop_loss": 5, "take_profit": 10, "buy_conditions": "5-day SMA crosses above 20-day",
        "sell_conditions": "5-day SMA crosses below 20-day"}


class FakeRuntime:
    """Answers invoke_agent_runtime by ARN and records each payload."""

    def __init__(self, answers):
        self.answers = answers  # ARN -> response object, raw string, or exception
        self.calls = []

    def invoke_agent_runtime(self, agentRuntimeArn, payload, **kwargs):
        self.calls.append((agentRuntimeArn, json.loads(payload)))
        answer = self.answers[agentRuntimeArn]
        if isinstance(answer, Exception):
            raise answer
        body = answer if isinstance(answer, str) else json.dumps(answer)
        return {"response": io.BytesIO(body.encode("utf-8"))}


def lambda_rows(symbol, start, end):
    """Weekday bars shaped like the market-data Lambda's rows, newest first
    (the Iceberg scan doesn't promise an order). A sine wave, so moving
    averages cross often enough to trade."""
    d, rows, i = dt.date.fromisoformat(start), [], 0
    while d <= dt.date.fromisoformat(end):
        if d.weekday() < 5:
            close = 100 + 10 * math.sin(i / 8)
            rows.append({"date": d.isoformat(), "symbol": symbol, "open_price": close,
                         "high_price": close + 1, "low_price": close - 1, "close_price": close,
                         "volume": 1_000_000, "adj_close": close})
            i += 1
        d += dt.timedelta(days=1)
    return list(reversed(rows))


def gateway_response(symbol, rows, success=True, error=None):
    """The Gateway's JSON-RPC envelope around the Lambda's response body."""
    body = {"success": success, "data": rows,
            "metadata": {"symbol": symbol, "total_rows": len(rows or [])}}
    if error:
        body["error"] = error
    text = json.dumps({"statusCode": 200, "body": json.dumps(body)})
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"isError": False, "content": [{"type": "text", "text": text}]}}


def gateway(answer):
    """Replace the Gateway call; returns the list its calls are recorded in."""
    calls = []

    def call(symbol, start, end, limit):
        calls.append((symbol, start, end, limit))
        if isinstance(answer, Exception):
            raise answer
        return answer(symbol, start, end) if callable(answer) else answer

    market_data.call_gateway_market_data_with_cognito = call
    return calls


def healthy_gateway(symbol, start, end):
    return gateway_response(symbol, lambda_rows(symbol, start, end))


@contextlib.contextmanager
def quiet():
    """The tools log generously; keep the test output to the checks."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def expect_step_error(fn, *args):
    try:
        with quiet():
            fn(*args)
    except StepError as e:
        return e
    return None


def main() -> int:
    failures = []

    def check(name, ok, detail=""):
        print(f"{'✅' if ok else '❌'} {name}{'' if ok else ' — ' + str(detail)}")
        if not ok:
            failures.append(name)

    # --- generate_strategy ---------------------------------------------------
    config._agentcore_runtime_client = FakeRuntime(
        {STRATEGY_ARN: {"code": SMA_CROSS, "version": "gen-v1"}})
    with quiet():
        generated = generate_strategy(FORM)
    check("generate: fences stripped, class and version read",
          not generated.code.startswith("```") and generated.class_name == "SmaCrossStrategy"
          and generated.version == "gen-v1", generated.class_name)
    check("generate: the form is sent to the generator as-is",
          config._agentcore_runtime_client.calls[0][1] == FORM)

    config._agentcore_runtime_client = FakeRuntime(
        {STRATEGY_ARN: json.dumps(json.dumps({"code": SMA_CROSS, "version": "gen-v1"}))})
    with quiet():
        unwrapped = generate_strategy(FORM)
    check("generate: a response wrapped in a JSON string is unwrapped",
          unwrapped.class_name == "SmaCrossStrategy")

    config._agentcore_runtime_client = FakeRuntime(
        {STRATEGY_ARN: {"code": "import os\n" + SMA_CROSS.strip("`python\n"), "version": "v"}})
    e = expect_step_error(generate_strategy, FORM)
    check("generate: code the sandbox rejects fails this step",
          e is not None and e.status == "failed" and "safety check" in str(e), e)

    config._agentcore_runtime_client = FakeRuntime({STRATEGY_ARN: {"code": "", "version": "v"}})
    e = expect_step_error(generate_strategy, FORM)
    check("generate: no code is a failure", e is not None and "no usable code" in str(e), e)

    config._agentcore_runtime_client = FakeRuntime(
        {STRATEGY_ARN: ReadTimeoutError(endpoint_url="https://runtime.test")})
    e = expect_step_error(generate_strategy, FORM)
    check("generate: a read timeout is reported as timeout",
          e is not None and e.status == "timeout", e)

    # --- fetch_market_data ---------------------------------------------------
    gateway(healthy_gateway)
    with quiet():
        market = fetch_market_data("amzn", "2025-09-30", "2026-09-29", 252)
    dates = [b["date"] for b in market.bars]
    check("fetch: bars come back sorted ascending, for the symbol asked for",
          dates == sorted(dates) and market.symbol == "AMZN" and len(dates) > 250, dates[:2])
    check("fetch: a full window has no coverage warnings", market.warnings == [], market.warnings)

    gateway(lambda s, start, end: gateway_response(s, lambda_rows(s, start, "2026-08-14")))
    with quiet():
        stale = fetch_market_data("AMZN", "2025-09-30", "2026-09-29", 252)
    check("fetch: stale data is listed as a coverage warning",
          len(stale.warnings) == 1 and "2026-08-14" in stale.warnings[0], stale.warnings)

    gateway(gateway_response("AMZN", [], success=False, error="No data found for symbol AMZN"))
    with quiet():
        empty = fetch_market_data("AMZN", "2025-09-30", "2026-09-29", 252)
    check("fetch: the Lambda's 'no rows' answer is data with no bars, not an error",
          empty.bars == [] and empty.warnings == [])

    gateway(gateway_response("AMZN", None, success=False, error="catalog unreachable"))
    e = expect_step_error(fetch_market_data, "AMZN", "2025-09-30", "2026-09-29", 252)
    check("fetch: a Lambda error is a failure carrying its message",
          e is not None and e.status == "failed" and "catalog unreachable" in str(e), e)

    gateway(lambda s, start, end: gateway_response("MSFT", lambda_rows("MSFT", start, end)))
    e = expect_step_error(fetch_market_data, "AMZN", "2025-09-30", "2026-09-29", 252)
    check("fetch: data for another symbol is refused", e is not None and "MSFT" in str(e), e)

    gateway(httpx.ReadTimeout("slow"))
    e = expect_step_error(fetch_market_data, "AMZN", "2025-09-30", "2026-09-29", 252)
    check("fetch: an HTTP timeout is reported as timeout", e is not None and e.status == "timeout", e)

    # --- backtest_strategy (real Backtrader) ---------------------------------
    config.saved_to_memory.clear()
    with quiet():
        result = backtest_strategy(generated.code, market, 50)
    trades = result["trades"]
    check("backtest: the strategy trades on the sine series",
          len(trades) >= 3 and isinstance(result["total_return"], float), len(trades))
    # One position at a time, so the cash at each entry is the starting cash
    # plus the P&L of the trades closed before it. 3% slack: orders are sized
    # at one bar's close and filled at the next bar's open.
    cash, oversized = 100_000.0, []
    for t in trades:
        if t["shares"] * t["entry_price"] > 0.5 * cash * 1.03:
            oversized.append(t)
        cash += t["pnl"] - t["commission"]
    check("backtest: position size caps each entry at 50% of the cash available",
          not oversized, oversized[:1])
    check("backtest: the result is saved to Memory for chat", len(config.saved_to_memory) == 1)

    broken = generated.code.replace("self.cross > 0", "self.no_such_indicator > 0")
    e = expect_step_error(backtest_strategy, broken, market, 50)
    check("backtest: code that fails while running fails this step",
          e is not None and e.status == "failed" and "no_such_indicator" in str(e), e)

    # --- summarize_backtest --------------------------------------------------
    many = {**result, "trades": trades * 4}
    config._agentcore_runtime_client = FakeRuntime(
        {SUMMARY_ARN: {"analysis": json.dumps(REPORT), "version": "sum-v1"}})
    with quiet():
        summary = summarize_backtest(many, market)
    sent = config._agentcore_runtime_client.calls[0][1]
    check("summarize: report parsed and version read",
          summary.report == REPORT and summary.version == "sum-v1")
    check("summarize: sends statistics over all trades but only the 10 most extreme",
          sent["trades_total"] == len(many["trades"]) and len(sent["trades"]) == 10
          and "profit_factor" in sent["trade_statistics"], (sent.get("trades_total"), len(sent["trades"])))
    check("summarize: sends the period the data actually covers",
          sent["backtest_period"] == market.period, sent.get("backtest_period"))
    check("summarize: sends today's date, so recent data isn't read as future-dated",
          sent.get("as_of") == dt.date.today().isoformat(), sent.get("as_of"))

    config._agentcore_runtime_client = FakeRuntime(
        {SUMMARY_ARN: {"analysis": "❌ **Analysis Error**: model unavailable\n\nDetails:\n...",
                       "version": "sum-v1"}})
    e = expect_step_error(summarize_backtest, result, market)
    check("summarize: the runtime's error text becomes a readable failure",
          e is not None and "model unavailable" in str(e) and "Details" not in str(e), e)

    # --- End to end through the pipeline ---------------------------------------
    live = Steps(generate=generate_strategy, fetch=fetch_market_data,
                 backtest=backtest_strategy, summarize=summarize_backtest)

    config._agentcore_runtime_client = FakeRuntime({
        STRATEGY_ARN: {"code": SMA_CROSS, "version": "gen-v1"},
        SUMMARY_ARN: {"analysis": json.dumps(REPORT), "version": "sum-v1"},
    })
    gateway(healthy_gateway)
    with quiet():
        events = list(run_pipeline(FORM, live, agent_version="agent-v1",
                                   today=dt.date(2026, 9, 30)))
    statuses = {e["step"]: e["status"] for e in events if e["type"] == "step"}
    final = events[-1]
    check("end to end: every step ok and the run completes",
          set(statuses.values()) == {"ok"} and final["status"] == "complete", statuses)
    check("end to end: the result carries trades, metrics, report and clean code",
          final["trades"] and final["backtest_metrics"]["metrics"] and final["summary_report"] == REPORT
          and final["strategy_code"].startswith("import backtrader"), final.get("error"))
    check("end to end: the whole stream is JSON-serialisable", bool(json.dumps(events)))

    config._agentcore_runtime_client = FakeRuntime({
        STRATEGY_ARN: {"code": "import os\n" + SMA_CROSS.strip("`python\n"), "version": "gen-v1"},
        SUMMARY_ARN: {"analysis": json.dumps(REPORT), "version": "sum-v1"},
    })
    fetches = gateway(healthy_gateway)
    with quiet():
        events = list(run_pipeline(FORM, live, agent_version="agent-v1",
                                   today=dt.date(2026, 9, 30)))
    arns = [arn for arn, _ in config._agentcore_runtime_client.calls]
    check("end to end: rejected code stops the run before data or summary is paid for",
          events[-1]["failed_step"] == "generate_strategy" and fetches == [] and arns == [STRATEGY_ARN],
          (events[-1].get("failed_step"), fetches, arns))

    print("\n" + "=" * 56)
    if failures:
        print(f"FAILED — {len(failures)} problem(s)")
        return 1
    print("ALL PIPELINE STEP TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
