import os

import polars as pl
from massive import RESTClient

from .float import fetch_free_float
from .hist import fetch_hist, fetch_hist_template
from .quote import fetch_quote
from .refs import fetch_ticker_details, list_tickers
from .short_interest import fetch_short_interest
from app.models.results import SymbolQuote
from app.services.prices import HIST_TEMPLATE_DEFAULT

API_KEY = os.getenv('POLYGON_API_KEY')


class PolygonProvider:
    """Polygon.io market data provider."""

    def __init__(self) -> None:
        self._client = RESTClient(API_KEY)

    def list_tickers(self, max_count: int) -> pl.DataFrame:
        return list_tickers(self._client, max_count)

    def get_details(self, symbol: str) -> dict | None:
        return fetch_ticker_details(self._client, symbol)

    def get_quote(self, symbol: str) -> SymbolQuote:
        return fetch_quote(self._client, symbol)

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
    ) -> pl.DataFrame:
        return fetch_hist(
            self._client,
            symbol,
            timespan,
            multiplier,
            unit,
            scale,
            close_only=close_only,
            open_close_only=open_close_only,
            quiet=quiet,
        )

    def get_hist_template(
        self,
        symbol: str,
        template: str = HIST_TEMPLATE_DEFAULT,
        quiet: bool = False,
    ) -> pl.DataFrame:
        return fetch_hist_template(
            self._client,
            symbol,
            template,
            quiet=quiet,
        )

    def get_float(
        self,
        symbol: str,
        quiet: bool = False,
    ) -> dict | None:
        return fetch_free_float(self._client, symbol, quiet)

    def get_short_interest(
        self,
        symbol: str,
        quiet: bool = False,
    ) -> dict | None:
        return fetch_short_interest(self._client, symbol, quiet)
