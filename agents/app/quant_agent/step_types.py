"""
Types shared by the backtest pipeline (pipeline.py) and the step
implementations in tools/.

Dependency-free on purpose: the tool tests load tool modules on a bare Python
without the agent's packages, and those modules import from here.
"""

from dataclasses import dataclass, field
from typing import Literal, Optional

StepName = Literal["prepare", "generate_strategy", "fetch_market_data", "run_backtest", "summarize"]
StepStatus = Literal["running", "ok", "empty", "failed", "timeout", "skipped"]

STEP_ORDER: tuple = ("prepare", "generate_strategy", "fetch_market_data", "run_backtest", "summarize")


class StepError(Exception):
    """A step's verdict that the run can't use what it produced.

    `status` is "failed", "empty" or "timeout". The message is shown to the
    user as written, so it should say what happened in plain words.
    """

    def __init__(self, message: str, status: StepStatus = "failed"):
        super().__init__(message)
        self.status = status
        self.step: Optional[StepName] = None  # set by the pipeline


@dataclass
class GeneratedStrategy:
    code: str
    class_name: Optional[str]
    version: str


@dataclass
class MarketData:
    symbol: str
    bars: list  # dicts with date/open/high/low/close/volume/adj_close, ascending by date
    warnings: list = field(default_factory=list)  # gaps between the window asked for and the bars returned

    @property
    def period(self) -> dict:
        if not self.bars:
            return {}
        return {"start": self.bars[0]["date"], "end": self.bars[-1]["date"],
                "trading_days": len(self.bars)}


@dataclass
class SummaryReport:
    report: dict  # executiveSummary / detailedAnalysis / concernsAndRecommendations
    version: str
