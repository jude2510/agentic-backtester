"""
Massive (massive.com) adapter — implements MarketDataProvider and NewsProvider.

Massive is Polygon.io rebranded, so the surface is the familiar /v2/aggs and
/v2/reference/news shape. Written on raw httpx rather than the official client:
at one request per symbol the client buys nothing, and a thin adapter keeps the
provider boundary honest for a later vendor swap.

Auth: Authorization: Bearer $MASSIVE_API_KEY
"""

from __future__ import annotations

import datetime as dt
import os
import zoneinfo

import httpx

from .base import (
    Bar,
    BarSeries,
    NewsItem,
    NotEntitledError,
    ProviderError,
)

BASE_URL = "https://api.massive.com"

# Daily bar timestamps are midnight *Eastern* expressed as UTC milliseconds
# (e.g. 1785729600000 -> 2026-08-03 04:00 UTC == 2026-08-03 00:00 EDT).
# Converting explicitly documents the convention and stays correct if Massive
# ever changes it or intraday bars get added.
_ET = zoneinfo.ZoneInfo("America/New_York")


def _bar_date(ts_ms: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts_ms / 1000, dt.timezone.utc).astimezone(_ET).date()


class MassiveProvider:
    """Daily bars and news from Massive.

    Args:
        api_key: defaults to the MASSIVE_API_KEY environment variable.
        timeout: per-request timeout in seconds.
    """

    name = "massive"

    def __init__(self, api_key: str | None = None, timeout: float = 30.0):
        key = api_key or os.getenv("MASSIVE_API_KEY")
        if not key:
            raise ProviderError(
                "MASSIVE_API_KEY is not set. Get a key at "
                "https://massive.com/dashboard/keys and export it."
            )
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers={"Authorization": f"Bearer {key}"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MassiveProvider":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ HTTP

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            resp = self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise ProviderError(f"{path}: transport error: {e}") from e

        if resp.status_code in (401, 403):
            raise NotEntitledError(
                f"{path}: {resp.status_code} — key rejected or window/symbol "
                f"outside plan entitlement. {resp.text[:200]}"
            )
        if resp.status_code != 200:
            raise ProviderError(f"{path}: HTTP {resp.status_code}: {resp.text[:200]}")

        payload = resp.json()

        # Massive signals entitlement problems in-band as well as by status code.
        status = str(payload.get("status", "")).upper()
        if status in {"NOT_ENTITLED", "NOT_AUTHORIZED"}:
            raise NotEntitledError(
                f"{path}: {status} — {payload.get('message', 'not included in your plan')}"
            )
        if status == "ERROR":
            raise ProviderError(f"{path}: {payload.get('error') or payload.get('message')}")

        return payload

    def _paginate(self, path: str, params: dict) -> list[dict]:
        """Follow next_url until exhausted, returning the concatenated results.

        Daily bars over a 5-year window fit in a single response, so this is
        insurance rather than a hot path — but news queries do page.
        """
        out: list[dict] = []
        payload = self._get(path, params)
        out.extend(payload.get("results") or [])

        next_url = payload.get("next_url")
        while next_url:
            # next_url is absolute; httpx handles it against the same client.
            payload = self._get(next_url)
            out.extend(payload.get("results") or [])
            next_url = payload.get("next_url")

        return out

    # ------------------------------------------------------- MarketDataProvider

    def get_daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> BarSeries:
        """Fetch daily OHLCV bars for one symbol.

        `adjusted=true` (the API default, set explicitly here) applies split
        adjustments, so AMZN's 2022 20:1 split is handled. It does NOT apply
        dividend adjustments, hence adj_close == close.

        A request reaching past the plan's history limit does not fail — Massive
        silently returns a later start. Inspect BarSeries.truncated on the result.
        """
        symbol = symbol.upper()
        path = f"/v2/aggs/ticker/{symbol}/range/1/day/{start.isoformat()}/{end.isoformat()}"

        try:
            raw = self._paginate(
                path, {"adjusted": "true", "sort": "asc", "limit": 50000}
            )
        except NotEntitledError:
            # Entirely outside the entitlement window. Report as empty coverage
            # rather than exploding, so a multi-symbol backfill can continue and
            # summarize what it could not reach.
            return BarSeries(
                symbol=symbol, bars=[], requested_start=start, requested_end=end
            )

        bars = [
            Bar(
                date=_bar_date(r["t"]),
                symbol=symbol,
                open=float(r["o"]),
                high=float(r["h"]),
                low=float(r["l"]),
                close=float(r["c"]),
                volume=int(round(float(r["v"]))),  # returned as a float
                adj_close=float(r["c"]),
            )
            for r in raw
        ]
        bars.sort(key=lambda b: b.date)

        return BarSeries(
            symbol=symbol, bars=bars, requested_start=start, requested_end=end
        )

    # ------------------------------------------------------------ NewsProvider

    def get_news(
        self, symbol: str, start: dt.date, end: dt.date, limit: int = 50
    ) -> list[NewsItem]:
        """Fetch news articles mentioning `symbol`, newest first.

        Each article carries vendor-computed per-ticker sentiment; the insight
        matching `symbol` is lifted onto the NewsItem.
        """
        symbol = symbol.upper()
        raw = self._paginate(
            "/v2/reference/news",
            {
                "ticker": symbol,
                "published_utc.gte": start.isoformat(),
                "published_utc.lte": end.isoformat(),
                "order": "desc",
                "sort": "published_utc",
                "limit": min(limit, 1000),
            },
        )

        items: list[NewsItem] = []
        for r in raw[:limit]:
            insight = next(
                (i for i in (r.get("insights") or []) if i.get("ticker") == symbol),
                None,
            )
            items.append(
                NewsItem(
                    id=str(r.get("id", "")),
                    title=r.get("title", ""),
                    published_utc=r["published_utc"],
                    article_url=r.get("article_url", ""),
                    publisher=(r.get("publisher") or {}).get("name"),
                    description=r.get("description"),
                    tickers=r.get("tickers") or [],
                    sentiment=(insight or {}).get("sentiment"),
                    sentiment_reasoning=(insight or {}).get("sentiment_reasoning"),
                )
            )
        return items
