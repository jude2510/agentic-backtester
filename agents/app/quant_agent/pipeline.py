"""
The backtest pipeline behind the strategy form.

Code fixes the order of the steps; models do two bounded jobs inside it: write
the strategy code, and write the analysis. This replaced an LLM orchestrator
that chose the order itself. It spent about 40 of every 80 seconds deciding
what to call next and writing a narrative the UI never displayed, and when a
step failed it could quietly work around the failure instead of reporting it.

Each step reports a typed status as it happens, so the UI shows real progress
and a failed run names the step it failed in:

    ok       produced what the next step needs
    empty    ran correctly and found nothing (no rows, no trades)
    failed   could not do its job
    timeout  ran out of time
    skipped  an earlier step stopped the run

Empty is not failure. No market data stops the run, because there is nothing
to backtest. A strategy that never trades is a real result, so the analysis
still runs and explains it. A failed analysis doesn't fail the run either:
the numbers stand on their own.

The step implementations are passed in (`Steps`), so the order and the status
rules are tested with fakes, without models, AWS or Backtrader.
"""

import datetime as dt
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from step_types import (
    STEP_ORDER, GeneratedStrategy, MarketData, StepError, StepName, StepStatus, SummaryReport,
)

# Window -> (calendar days back, approximate trading days). The frontend's
# WINDOW_DAYS mirrors the day counts.
WINDOWS = {
    "1M": (30, 21), "3M": (91, 63), "6M": (182, 126), "1Y": (365, 252),
    "2Y": (730, 504), "5Y": (1825, 1260), "10Y": (3652, 2520), "20Y": (7305, 5040),
}


class StrategyInput(BaseModel):
    """The strategy form's fields, checked before anything is spent on them."""
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    stock_symbol: str = Field(pattern=r"^[A-Z]{1,5}$")
    backtest_window: str
    position_pct: int = Field(default=95, ge=1, le=95)
    stop_loss: Optional[float] = Field(default=None, ge=0, le=100)
    take_profit: Optional[float] = Field(default=None, ge=0, le=100)
    buy_conditions: str = Field(min_length=1, max_length=500)
    sell_conditions: str = Field(min_length=1, max_length=500)

    @field_validator("stock_symbol", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("backtest_window")
    @classmethod
    def _known_window(cls, value: str) -> str:
        if value not in WINDOWS:
            raise ValueError(f"must be one of {', '.join(WINDOWS)}")
        return value


@dataclass(frozen=True)
class Window:
    start: str
    end: str
    limit: int


def resolve_window(window: str, today: dt.date) -> Window:
    """Dates for a backtest window, counted back from today.

    Deterministic work, so it's done here: a model has no clock and anchors on
    its training cutoff. Resolved per run, never at import, because the
    runtime container outlives any one day.
    """
    days, limit = WINDOWS[window]
    return Window((today - dt.timedelta(days=days)).isoformat(), today.isoformat(), limit)


@dataclass
class Steps:
    """The step implementations: the live ones in tools/, or fakes in tests.

    generate  -> code that has already passed the sandbox's static check
    fetch     -> bars sorted ascending, with any coverage gaps listed
    backtest  -> Backtrader's result record
    summarize -> the written analysis
    Each raises StepError when it can't produce what the next step needs.
    """
    generate: Callable[[dict], GeneratedStrategy]
    fetch: Callable[[str, str, str, int], MarketData]
    backtest: Callable[[str, MarketData, int], dict]
    summarize: Callable[[dict, MarketData], SummaryReport]


class StepEvent(BaseModel):
    type: str = "step"
    step: StepName
    status: StepStatus
    detail: Optional[str] = None
    duration_ms: Optional[int] = None


class PipelineResult(BaseModel):
    type: str = "result"
    status: str  # "complete" | "failed"
    failed_step: Optional[StepName] = None
    error: Optional[str] = None
    strategy_code: Optional[str] = None
    backtest_metrics: Optional[dict] = None
    trades: list = Field(default_factory=list)
    trade_summary: dict = Field(default_factory=dict)
    summary_report: Optional[dict] = None
    data_warnings: list = Field(default_factory=list)
    data_period: Optional[dict] = None
    versions: dict = Field(default_factory=dict)


def run_pipeline(raw_strategy: Any, steps: Steps, *, agent_version: str,
                 today: Optional[dt.date] = None) -> Iterator[dict]:
    """Run one backtest, yielding a step event per status change and then
    exactly one result. Never raises: every failure ends as a result."""
    today = today or dt.date.today()
    versions = {"quant_agent": agent_version}
    generated: Optional[GeneratedStrategy] = None
    market: Optional[MarketData] = None

    try:
        strategy, window = yield from _step(
            "prepare", lambda: _prepare(raw_strategy, today), _describe_prepare)
        generated = yield from _step(
            "generate_strategy", lambda: steps.generate(strategy.model_dump(exclude_none=True)),
            _describe_strategy)
        versions["strategy_generator"] = generated.version
        market = yield from _step(
            "fetch_market_data",
            lambda: steps.fetch(strategy.stock_symbol, window.start, window.end, window.limit),
            _describe_market)
        backtest = yield from _step(
            "run_backtest", lambda: steps.backtest(generated.code, market, strategy.position_pct),
            _describe_backtest)
    except StepError as e:
        for name in STEP_ORDER[STEP_ORDER.index(e.step) + 1:]:
            yield _event(name, "skipped")
        # Whatever was produced before the failure still helps explain it,
        # e.g. the generated code when the backtest couldn't run it.
        yield PipelineResult(
            status="failed", failed_step=e.step, error=str(e),
            strategy_code=generated.code if generated else None,
            data_warnings=market.warnings if market else [],
            data_period=(market.period or None) if market else None,
            versions=versions,
        ).model_dump()
        return

    # The numbers stand without the written analysis, so a failure here is
    # reported on its step and the run still completes.
    report = None
    try:
        summary = yield from _step(
            "summarize", lambda: steps.summarize(backtest, market), _describe_summary)
        report = summary.report
        versions["results_summary"] = summary.version
    except StepError:
        pass

    yield PipelineResult(
        status="complete",
        strategy_code=generated.code,
        backtest_metrics={k: backtest.get(k)
                          for k in ("initial_value", "final_value", "total_return", "metrics")},
        trades=backtest.get("trades") or [],
        trade_summary=backtest.get("trade_summary") or {},
        summary_report=report,
        data_warnings=market.warnings,
        data_period=market.period,
        versions=versions,
    ).model_dump()


def _step(name: StepName, work: Callable[[], Any],
          describe: Callable[[Any], tuple]) -> Iterator[dict]:
    """Run one step: announce it, time it, and report how it went.

    A generator so the caller can `yield from` it and still get the step's
    value back. Whatever goes wrong, including a bug inside the step, ends as
    a StepError tagged with this step's name, never an unhandled crash.
    """
    yield _event(name, "running")
    started = time.monotonic()
    try:
        value = work()
        status, detail = describe(value)
    except StepError as e:
        error = e
    except Exception as e:
        traceback.print_exc()
        error = StepError(f"{type(e).__name__}: {e}")
    else:
        yield _event(name, status, detail, started)
        return value

    error.step = name
    yield _event(name, error.status, str(error), started)
    raise error


def _event(step: StepName, status: StepStatus, detail: Optional[str] = None,
           started: Optional[float] = None) -> dict:
    duration_ms = None if started is None else int((time.monotonic() - started) * 1000)
    timing = f" ({duration_ms} ms)" if duration_ms is not None else ""
    print(f"🧭 {step}: {status}{timing}{' — ' + detail if detail else ''}")
    return StepEvent(step=step, status=status, detail=detail, duration_ms=duration_ms).model_dump()


def _prepare(raw_strategy: Any, today: dt.date) -> tuple:
    try:
        strategy = StrategyInput.model_validate(raw_strategy)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}" for err in e.errors())
        raise StepError(f"the strategy has invalid fields: {problems}")
    return strategy, resolve_window(strategy.backtest_window, today)


def _describe_prepare(value: tuple) -> tuple:
    strategy, window = value
    return "ok", f"{strategy.stock_symbol} · {strategy.backtest_window} · {window.start} → {window.end}"


def _describe_strategy(generated: GeneratedStrategy) -> tuple:
    lines = len(generated.code.splitlines())
    return "ok", f"{generated.class_name or 'Strategy'} · {lines} lines · passed the safety check"


def _describe_market(market: MarketData) -> tuple:
    if not market.bars:
        raise StepError(f"no market data is stored for {market.symbol} in this window", status="empty")
    period = market.period
    detail = f"{period['trading_days']} bars · {period['start']} → {period['end']}"
    if market.warnings:
        detail += " · covers less than the window asked for"
    return "ok", detail


def _describe_backtest(result: dict) -> tuple:
    # Closed and still-open trades counted apart, the way the results page's
    # transaction log shows them.
    summary = result.get("trade_summary") or {}
    closed, still_open = summary.get("total_closed", 0), summary.get("total_open", 0)
    total_return = result.get("total_return")
    returned = f"{total_return:+.2f}%" if isinstance(total_return, (int, float)) else "n/a"
    if not closed and not still_open:
        return "empty", f"no trades: the entry conditions never triggered in this window · {returned}"
    trades = [f"{closed} closed trade{'' if closed == 1 else 's'}" if closed else "",
              f"{still_open} still open" if still_open else ""]
    return "ok", f"{' + '.join(t for t in trades if t)} · {returned} return"


def _describe_summary(summary: SummaryReport) -> tuple:
    words = len(str(summary.report.get("executiveSummary", "")).split())
    return "ok", f"analysis written ({words}-word summary)"
