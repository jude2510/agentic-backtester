"""
Tests for the backtest worker's handling of the agent's stream.

The DynamoDB table and the agent are fakes; the stream is a real botocore
StreamingBody, so the reading code is exercised as deployed. Needs boto3
(>= 1.43.72), e.g. the pipeline's venv:

    market-data-pipeline/.venv/bin/python backtest-worker/test_handler.py
"""

import io
import json
import os
import sys
import threading
import time
from decimal import Decimal

os.environ.setdefault("AGENTCORE_ARN", "arn:test:quant-agent")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

from botocore.response import StreamingBody  # noqa: E402
from urllib3.response import HTTPResponse  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handler  # noqa: E402

STRATEGY = {"name": "EMA Crossover", "stock_symbol": "AMZN", "backtest_window": "1Y",
            "position_pct": 95, "stop_loss": 5, "take_profit": 15,
            "buy_conditions": "5-day EMA crosses above 20-day EMA",
            "sell_conditions": "5-day EMA crosses below 20-day EMA"}

STEP_NAMES = ["prepare", "generate_strategy", "fetch_market_data", "run_backtest", "summarize"]


class FakeTable:
    """Applies put_item / update_item to an in-memory row, and refuses floats
    the way DynamoDB does."""

    def __init__(self):
        self.row = {}
        self.writes = []  # (operation, fields) in order

    def _check(self, value):
        if isinstance(value, float):
            raise TypeError("Float types are not supported. Use Decimal types instead.")
        if isinstance(value, dict):
            for v in value.values():
                self._check(v)
        if isinstance(value, list):
            for v in value:
                self._check(v)

    def put_item(self, Item):
        self._check(Item)
        self.row = dict(Item)
        self.writes.append(("put", dict(Item)))

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        self._check(ExpressionAttributeValues)
        fields = {ExpressionAttributeNames[f"#{name[1:]}"]: value
                  for name, value in ExpressionAttributeValues.items()}
        self.row.update(fields)
        self.writes.append(("update", fields))


def sse(*events) -> bytes:
    return b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)


def body(raw: bytes) -> StreamingBody:
    return StreamingBody(HTTPResponse(body=io.BytesIO(raw), preload_content=False), len(raw))


class FakeAgent:
    def __init__(self, raw: bytes):
        self.raw = raw
        self.calls = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": body(self.raw), "contentType": "text/event-stream"}


def step(name, status, detail=None, ms=None):
    return {"type": "step", "step": name, "status": status, "detail": detail, "duration_ms": ms}


def complete_run():
    events = []
    for name in STEP_NAMES:
        events += [step(name, "running"), step(name, "ok", f"{name} done", 1200)]
    events.append({"type": "result", "status": "complete", "failed_step": None, "error": None,
                   "strategy_code": "import backtrader as bt", "trades": [{"pnl": 1523.37}],
                   "trade_summary": {"total_trades": 1}, "summary_report": {"executiveSummary": "ok"},
                   "backtest_metrics": {"total_return": 12.34, "metrics": {"Sharpe Ratio": 1.1}},
                   "data_warnings": [], "data_period": {"start": "2025-09-30", "end": "2026-09-30",
                                                         "trading_days": 252},
                   "versions": {"quant_agent": "v1"}})
    return events


def run(raw: bytes, event=None):
    table, agent = FakeTable(), FakeAgent(raw)
    handler._table, handler._agentcore = table, agent
    error = None
    try:
        handler.handler(event or {"jobId": "job-1", "strategyInput": STRATEGY}, None)
    except Exception as e:
        error = e
    return table, agent, error


def main() -> int:
    failures = []

    def check(name, ok, detail=""):
        print(f"{'✅' if ok else '❌'} {name}{'' if ok else ' — ' + str(detail)}")
        if not ok:
            failures.append(name)

    # 1. A complete run.
    table, agent, error = run(sse(*complete_run()))
    row = table.row
    check("complete: handler succeeds", error is None, error)
    sent = json.loads(agent.calls[0]["payload"])
    check("complete: the form is sent as `strategy`, with the worker's user id",
          sent == {"strategy": STRATEGY} and agent.calls[0]["runtimeUserId"] == "backtest-worker", sent)
    progress = [w for op, w in table.writes if op == "update" and "status" not in w]
    check("complete: each step event is written as it arrives",
          len(progress) == 10 and [len(w["steps"]) for w in progress] == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5],
          [len(w["steps"]) for w in progress])
    check("complete: a step's later status replaces its earlier one",
          [s["status"] for s in row["steps"]] == ["ok"] * 5, row.get("steps"))
    check("complete: job marked complete with the results page's fields",
          row["status"] == "complete" and row["data"]["strategyInput"] == STRATEGY
          and row["data"]["summary_report"] == {"executiveSummary": "ok"}
          and row["data"]["data_period"]["trading_days"] == 252, row.get("status"))
    check("complete: floats stored as Decimal",
          row["data"]["backtest_metrics"]["total_return"] == Decimal("12.34"))

    # 2. A run that stopped at a step is an answer, not a crash.
    stopped = [step("prepare", "running"), step("prepare", "ok"),
               step("generate_strategy", "running"), step("generate_strategy", "ok"),
               step("fetch_market_data", "running"),
               step("fetch_market_data", "empty", "no market data is stored for ZZZZ in this window"),
               step("run_backtest", "skipped"), step("summarize", "skipped"),
               {"type": "result", "status": "failed", "failed_step": "fetch_market_data",
                "error": "no market data is stored for ZZZZ in this window",
                "strategy_code": "import backtrader as bt"}]
    table, _, error = run(sse(*stopped))
    row = table.row
    check("stopped: handler succeeds (no Lambda error)", error is None, error)
    check("stopped: job is an error naming the step and the reason",
          row["status"] == "error" and row["failedStep"] == "fetch_market_data"
          and "ZZZZ" in row["error"], {k: row.get(k) for k in ("status", "failedStep", "error")})
    check("stopped: the steps are kept, including the skipped ones",
          [s["status"] for s in row["steps"]] == ["ok", "ok", "empty", "skipped", "skipped"])
    check("stopped: the generated code is kept for the UI",
          row["data"]["strategyCode"] == "import backtrader as bt")

    # 3. The runtime's own error event: the pipeline raised.
    raw = sse(step("prepare", "running"),
              {"error": "boom", "error_type": "RuntimeError", "message": "An error occurred during streaming"})
    table, _, error = run(raw)
    check("agent error: handler raises and the job records it",
          error is not None and table.row["status"] == "error" and "boom" in table.row["error"],
          table.row.get("error"))
    check("agent error: the steps recorded before it stay",
          table.row.get("steps") == [step("prepare", "running")], table.row.get("steps"))

    # 4. A stream that ends without a result.
    table, _, error = run(sse(step("prepare", "running")))
    check("no result: reported, not treated as success",
          error is not None and "without a result" in table.row["error"], table.row.get("error"))

    # 5. Events are read as they arrive, not in 1024-byte blocks.
    first = sse(step("prepare", "running"))
    rest = sse(*complete_run())
    r, w = os.pipe()

    def feed():
        os.write(w, first)
        time.sleep(0.5)
        os.write(w, rest)
        os.close(w)

    threading.Thread(target=feed).start()
    stream = StreamingBody(HTTPResponse(body=os.fdopen(r, "rb", buffering=0), preload_content=False),
                           len(first) + len(rest))
    started = time.monotonic()
    first_event = next(handler._events(stream))
    first_event_at = time.monotonic() - started
    check("streaming: the first small event is read before later ones are sent",
          first_event["step"] == "prepare" and first_event_at < 0.3, f"{first_event_at:.2f}s")

    # 6. Chat is unchanged.
    reply = json.dumps({"result": {"role": "assistant", "content": [{"text": "Your EMA runs..."}]}}).encode()
    table, agent, error = run(reply, {"jobId": "job-2", "mode": "chat", "prompt": "compare my runs"})
    check("chat: prompt sent in chat mode, reply stored as the message",
          error is None and json.loads(agent.calls[0]["payload"]) == {"mode": "chat", "prompt": "compare my runs"}
          and table.row["data"]["message"] == "Your EMA runs...", error or table.row)

    print("\n" + "=" * 56)
    if failures:
        print(f"FAILED — {len(failures)} problem(s)")
        return 1
    print("ALL WORKER TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
