"""Quote caching with TTL."""

from __future__ import annotations

import asyncio
from time import monotonic

from app.mds.provider import MarketDataProvider
from app.models.results import SymbolQuote

QUOTE_TTL = 300  # 5 min


class QuoteService:
    """Owns all quote fetching and caching."""

    def __init__(self, mds: MarketDataProvider) -> None:
        self._mds = mds
        self._quotes: dict[str, SymbolQuote] = {}
        self._loaded_at: dict[str, float] = {}

    async def get(self, symbol: str) -> SymbolQuote:
        """Single quote, cached behind TTL."""
        now = monotonic()
        loaded = self._loaded_at.get(symbol, 0.0)
        if symbol in self._quotes and now - loaded <= QUOTE_TTL:
            return self._quotes[symbol]
        quote = await asyncio.to_thread(self._mds.get_quote, symbol)
        self._quotes[symbol] = quote
        self._loaded_at[symbol] = monotonic()
        return quote

    async def get_many(
        self, symbols: set[str]
    ) -> dict[str, SymbolQuote]:
        """Batch fetch, parallel for stale symbols."""
        now = monotonic()
        stale = {
            s
            for s in symbols
            if s not in self._quotes
            or now - self._loaded_at.get(s, 0.0) > QUOTE_TTL
        }
        if stale:
            tasks = [
                asyncio.to_thread(self._mds.get_quote, s)
                for s in stale
            ]
            results = await asyncio.gather(*tasks)
            for s, q in zip(stale, results):
                self._quotes[s] = q
                self._loaded_at[s] = monotonic()
        return {
            s: self._quotes[s] for s in symbols if s in self._quotes
        }
