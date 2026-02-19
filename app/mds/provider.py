from typing import Protocol

import polars as pl

from app.models.results import SymbolQuote


class MarketDataProvider(Protocol):
    def list_tickers(self, max_count: int) -> pl.DataFrame: ...

    def get_details(self, symbol: str) -> dict | None: ...

    def get_quote(self, symbol: str) -> SymbolQuote: ...

    def get_hist(
        self,
        symbol: str,
        timespan: str,
        multiplier: int,
        unit: str,
        scale: int,
        close_only: bool = False,
        open_close_only: bool = False,
        quiet: bool = False,
    ) -> pl.DataFrame: ...

    def get_hist_template(
        self,
        symbol: str,
        template: str,
        quiet: bool = False,
    ) -> pl.DataFrame: ...

    def get_float(
        self,
        symbol: str,
        quiet: bool = False,
    ) -> dict | None: ...

    def get_short_interest(
        self,
        symbol: str,
        quiet: bool = False,
    ) -> dict | None: ...
