"""
Provider registry.

Selecting a vendor is a one-line change here (or the MARKET_DATA_PROVIDER env
var) — nothing downstream of the provider boundary knows a vendor name.
"""

from __future__ import annotations

import os

from .base import (
    Bar,
    BarSeries,
    MarketDataProvider,
    NewsItem,
    NewsProvider,
    NotEntitledError,
    ProviderError,
)
from .massive import MassiveProvider

_PROVIDERS = {
    "massive": MassiveProvider,
}


def get_market_data_provider(name: str | None = None) -> MarketDataProvider:
    """Return the configured market data provider (default: massive)."""
    key = (name or os.getenv("MARKET_DATA_PROVIDER", "massive")).lower()
    try:
        return _PROVIDERS[key]()
    except KeyError:
        raise ProviderError(
            f"Unknown provider {key!r}. Available: {', '.join(sorted(_PROVIDERS))}"
        ) from None


def get_news_provider(name: str | None = None) -> NewsProvider:
    """Return the configured news provider (default: massive).

    Separate from the market-data factory on purpose: news and bars are free to
    come from different vendors.
    """
    key = (name or os.getenv("NEWS_PROVIDER", "massive")).lower()
    try:
        return _PROVIDERS[key]()
    except KeyError:
        raise ProviderError(
            f"Unknown provider {key!r}. Available: {', '.join(sorted(_PROVIDERS))}"
        ) from None


__all__ = [
    "Bar",
    "BarSeries",
    "NewsItem",
    "MarketDataProvider",
    "NewsProvider",
    "ProviderError",
    "NotEntitledError",
    "MassiveProvider",
    "get_market_data_provider",
    "get_news_provider",
]
