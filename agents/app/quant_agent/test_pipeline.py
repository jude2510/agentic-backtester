"""
Tests for the backtest pipeline's order and status rules (pipeline.py).

The steps are fakes, so this needs no models, AWS or Backtrader, only pydantic
(in the agent's venv):

    cd agents/app/quant_agent && .venv/bin/python test_pipeline.py
"""

import contextlib
import datetime as dt
import io
import json
import sys

from pipeline import Steps, run_pipeline
from step_types import STEP_ORDER, GeneratedStrategy, MarketData, StepError, SummaryReport

TODAY = dt.date(2026, 9, 30)

FORM = {
    "name": "SMA Cross", "stock_symbol": " amzn ", "backtest_window": "1Y",
    "position_pct": 50, "stop_loss": 5, "take_profit": 10,
    "buy_conditions": "5-day SMA crosses above 20-day SMA",
    "sell_conditions": "5-day SMA crosses below 20-day SMA",
    "max_positions": 1,  # a field the form no longer sends; must be dropped, not rejected
}

BARS = [{"date": f"2026-0{m}-15", "close": 100.0 + m} for m in range(1, 10)]


def ok_generate(strategy):
    return GeneratedStrategy(code="import backtrader as bt\n" * 5, class_name="SmaCrossStrategy",
                             version="gen-v1")


def ok_fetch(symbol, start, end, limit):
    return MarketData(symbol=symbol, bars=list(BARS))


def ok_backtest(code, market, position_pct):
    return {"initial_value": 100000.0, "final_value": 112000.0, "total_return": 12.0,
            "metrics": {"Sharpe Ratio": 1.1}, "trades": [{"pnl": 12000.0}],
            "trade_summary": {"total_trades": 1, "total_closed": 1, "total_open": 0, "won": 1}}


def ok_summarize(result, market):
    return SummaryReport(report={"executiveSummary": "Viable but thin sample."}, version="sum-v1")


def make_steps(**overrides):
    """Steps built from the ok_* fakes, with any overridden, recording each call."""
    calls = []
    fakes = {"generate": ok_generate, "fetch": ok_fetch,
             "backtest": ok_backtest, "summarize": ok_summarize, **overrides}

    def recorded(name, fn):
        def call(*args):
            calls.append((name, args))
            return fn(*args)
        return call

    return Steps(**{name: recorded(name, fn) for name, fn in fakes.items()}), calls


def raises(error):
    def fail(*args):
        raise error
    return fail


def run(steps, form=FORM):
    return list(run_pipeline(form, steps, agent_version="agent-v1", today=TODAY))


def final_statuses(events):
    """Each step's last reported status."""
    return {e["step"]: e["status"] for e in events if e["type"] == "step"}


def details(events):
    return {e["step"]: e["detail"] for e in events if e["type"] == "step" and e["detail"]}


def main() -> int:
    failures = []

    def check(name, ok, detail=""):
        print(f"{'✅' if ok else '❌'} {name}{'' if ok else ' — ' + str(detail)}")
        if not ok:
            failures.append(name)

    def invariants(label, events):
        """What must hold for every run, whatever happened in it."""
        results = [e for e in events if e["type"] == "result"]
        check(f"{label}: exactly one result, and it comes last",
              len(results) == 1 and events[-1]["type"] == "result", [e["type"] for e in events])
        statuses = final_statuses(events)
        check(f"{label}: every step ends in a final status",
              list(statuses) == list(STEP_ORDER) and "running" not in statuses.values(), statuses)
        try:
            json.dumps(events)
            check(f"{label}: every event is JSON-serialisable", True)
        except TypeError as e:
            check(f"{label}: every event is JSON-serialisable", False, e)

    # 1. Happy path.
    steps, calls = make_steps()
    events = run(steps)
    invariants("happy path", events)
    result = events[-1]
    check("happy path: every step ok", set(final_statuses(events).values()) == {"ok"},
          final_statuses(events))
    check("happy path: result complete with the backtest's numbers",
          result["status"] == "complete" and result["backtest_metrics"]["total_return"] == 12.0
          and result["summary_report"]["executiveSummary"] and result["trades"], result)
    check("happy path: 1Y resolves to 365 days back from today, 252 bars",
          calls[1] == ("fetch", ("AMZN", "2025-09-30", "2026-09-30", 252)), calls[1])
    sent = calls[0][1][0]
    check("happy path: generator gets the cleaned form, without unknown fields",
          sent["stock_symbol"] == "AMZN" and "max_positions" not in sent, sent)
    check("happy path: the user's position size reaches the backtest",
          calls[2][1][2] == 50, calls[2])
    check("happy path: all three versions reported",
          result["versions"] == {"quant_agent": "agent-v1", "strategy_generator": "gen-v1",
                                 "results_summary": "sum-v1"}, result["versions"])
    check("happy path: steps run in order, each announced before it reports",
          [(e["step"], e["status"]) for e in events[:-1]]
          == [(s, st) for s in STEP_ORDER for st in ("running", "ok")])

    # 2. Invalid form: stopped before anything is spent.
    steps, calls = make_steps()
    bad = {**FORM, "backtest_window": "7Y", "position_pct": 150}
    del bad["sell_conditions"]
    events = run(steps, bad)
    invariants("invalid form", events)
    message = details(events).get("prepare", "")
    check("invalid form: prepare fails naming every bad field",
          final_statuses(events)["prepare"] == "failed"
          and all(f in message for f in ("backtest_window", "position_pct", "sell_conditions")),
          message)
    check("invalid form: no step with a cost runs", calls == [], calls)
    check("invalid form: the rest are skipped",
          all(final_statuses(events)[s] == "skipped" for s in STEP_ORDER[1:]))

    # 3. Generated code rejected: one attempt, nothing downstream.
    steps, calls = make_steps(generate=raises(StepError("rejected by the safety check: import os")))
    events = run(steps)
    invariants("rejected code", events)
    check("rejected code: fails at generate_strategy, called once, nothing after",
          final_statuses(events)["generate_strategy"] == "failed"
          and [c[0] for c in calls] == ["generate"]
          and events[-1]["failed_step"] == "generate_strategy", (calls, events[-1]))

    # 4. No market data: empty, and the run stops.
    steps, calls = make_steps(fetch=lambda *a: MarketData(symbol="AMZN", bars=[]))
    events = run(steps)
    invariants("no data", events)
    statuses = final_statuses(events)
    check("no data: fetch is empty (not failed) and the run stops",
          statuses["fetch_market_data"] == "empty" and statuses["run_backtest"] == "skipped"
          and events[-1]["status"] == "failed", statuses)
    check("no data: the generated code is still returned", bool(events[-1]["strategy_code"]))

    # 5. Timeout is its own status.
    steps, _ = make_steps(fetch=raises(StepError("the market data request timed out", status="timeout")))
    events = run(steps)
    check("timeout: reported as timeout", final_statuses(events)["fetch_market_data"] == "timeout")

    # 6. No trades: a real result, so the analysis still runs.
    zero = {**ok_backtest(None, None, None), "trades": [],
            "trade_summary": {"total_trades": 0, "total_closed": 0, "total_open": 0}}
    steps, calls = make_steps(backtest=lambda *a: zero)
    events = run(steps)
    invariants("no trades", events)
    check("no trades: backtest is empty, summary still runs, run completes",
          final_statuses(events)["run_backtest"] == "empty"
          and "summarize" in [c[0] for c in calls] and events[-1]["status"] == "complete",
          final_statuses(events))

    # A position still open at the end is counted apart from closed trades,
    # as the transaction log shows it (live NVDA run: 9 closed + 1 open).
    open_at_end = {**ok_backtest(None, None, None),
                   "trade_summary": {"total_trades": 10, "total_closed": 9, "total_open": 1}}
    steps, _ = make_steps(backtest=lambda *a: open_at_end)
    events = run(steps)
    check("open position: reported as closed + still open",
          details(events)["run_backtest"].startswith("9 closed trades + 1 still open"),
          details(events)["run_backtest"])

    # 7. Summary fails: the numbers still stand.
    steps, _ = make_steps(summarize=raises(StepError("the results summary agent call failed")))
    events = run(steps)
    invariants("summary failed", events)
    result = events[-1]
    check("summary failed: step failed but the run completes with the numbers",
          final_statuses(events)["summarize"] == "failed" and result["status"] == "complete"
          and result["summary_report"] is None and result["backtest_metrics"], result)
    check("summary failed: no results_summary version claimed",
          "results_summary" not in result["versions"], result["versions"])

    # 8. A bug inside a step becomes a failed step, not a crash. The pipeline
    # prints the traceback for CloudWatch; it's expected here, so hidden.
    steps, _ = make_steps(backtest=raises(KeyError("close")))
    with contextlib.redirect_stderr(io.StringIO()):
        events = run(steps)
    invariants("bug in a step", events)
    check("bug in a step: failed with the exception named",
          final_statuses(events)["run_backtest"] == "failed"
          and "KeyError" in details(events)["run_backtest"], details(events))

    # 9. Coverage warnings reach the result.
    warning = "data ends 2026-08-14, 47 days before the requested end 2026-09-30"
    steps, _ = make_steps(fetch=lambda *a: MarketData(symbol="AMZN", bars=list(BARS), warnings=[warning]))
    events = run(steps)
    check("coverage warnings: in the result and noted on the step",
          events[-1]["data_warnings"] == [warning]
          and "less than the window" in details(events)["fetch_market_data"], events[-1])

    print("\n" + "=" * 56)
    if failures:
        print(f"FAILED — {len(failures)} problem(s)")
        return 1
    print("ALL PIPELINE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
