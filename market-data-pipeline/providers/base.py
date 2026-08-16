"""
Vendor-agnostic market data and news interfaces.

The agent never sees these types. Data reaches the quant agent through the
existing Gateway -> Lambda -> Iceberg read path; this package is the *ingestion*
side, so the vendor abstraction belongs here rather than in the agent.

Swapping Massive for Polygon/FactSet later means adding one module that
satisfies these protocols and changing the factory in __init__.py.
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class Bar(BaseModel):
    """One daily OHLCV bar, normalized to the Iceberg table's vocabulary.

    Vendors disagree on field names (Massive returns single letters: o/h/l/c/v).
    Normalizing here means exactly one place in the codebase knows a vendor's
    dialect, and everything downstream speaks the table's language.
    """

    model_config = ConfigDict(frozen=True)

    date: dt.date
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    adj_close: float = Field(
        description=(
            "Split-adjusted close. NOT dividend-adjusted: Massive's adjusted=true "
            "applies splits only, so this equals `close`. Exact for non-dividend "
            "payers (AMZN/NVDA/TSLA); understates total return for SPY by roughly "
            "its yield."
        )
    )

    def to_row(self) -> dict:
        """Row shaped for the Iceberg schema (which uses *_price column names)."""
        return {
            "date": self.date,
            "symbol": self.symbol,
            "open_price": self.open,
            "high_price": self.high,
            "low_price": self.low,
            "close_price": self.close,
            "volume": self.volume,
            "adj_close": self.adj_close,
        }


class BarSeries(BaseModel):
    """Bars for one symbol, carrying what was *asked for* alongside what arrived.

    This distinction is the whole point. Massive silently clamps a request that
    straddles the plan's history limit -- it returns 200 OK with data quietly
    starting late and no warning field. Without comparing requested vs actual,
    a 10-year backfill on a 5-year plan looks like a success.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    bars: list[Bar]
    requested_start: dt.date
    requested_end: dt.date

    @property
    def actual_start(self) -> dt.date | None:
        return self.bars[0].date if self.bars else None

    @property
    def actual_end(self) -> dt.date | None:
        return self.bars[-1].date if self.bars else None

    @property
    def truncated(self) -> bool:
        """True when the provider returned less history than was requested.

        Uses a 5-day tolerance so weekends and market holidays around the
        requested boundary don't read as truncation.
        """
        if not self.bars:
            return True
        return (self.actual_start - self.requested_start).days > 5

    def coverage_note(self) -> str:
        """One-line human-readable coverage summary, for ingest logs and run records."""
        if not self.bars:
            return f"{self.symbol}: NO DATA for {self.requested_start}..{self.requested_end}"
        note = (
            f"{self.symbol}: {len(self.bars):,} bars "
            f"{self.actual_start}..{self.actual_end}"
        )
        if self.truncated:
            short = (self.actual_start - self.requested_start).days
            note += f"  ⚠️ TRUNCATED — requested from {self.requested_start} ({short} days short)"
        return note


class NewsItem(BaseModel):
    """A news article with the vendor's per-ticker sentiment already attached."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    published_utc: dt.datetime
    article_url: str
    publisher: str | None = None
    description: str | None = None
    tickers: list[str] = Field(default_factory=list)
    sentiment: str | None = Field(
        default=None, description="positive | negative | neutral, for the queried ticker"
    )
    sentiment_reasoning: str | None = None


class ProviderError(RuntimeError):
    """Provider call failed (transport, auth, or entitlement)."""


class NotEntitledError(ProviderError):
    """The requested window or symbol is outside the subscription's entitlement.

    Raised on an explicit vendor refusal. Note that silent clamping does NOT
    raise -- it surfaces as BarSeries.truncated instead, which is why callers
    must check both.
    """


@runtime_checkable
class MarketDataProvider(Protocol):
    """Anything that can supply daily bars for a symbol."""

    name: str

    def get_daily_bars(
        self, symbol: str, start: dt.date, end: dt.date
    ) -> BarSeries: ...


@runtime_checkable
class NewsProvider(Protocol):
    """Anything that can supply news articles for a symbol."""

    name: str

    def get_news(
        self, symbol: str, start: dt.date, end: dt.date, limit: int = 50
    ) -> list[NewsItem]: ...
