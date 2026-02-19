import polars as pl

from app.mds.bloomberg.session import create_session
from app.mds.bloomberg.hist import (
    fetch_hist,
    fetch_hist_template,
)
from app.mds.bloomberg.quote import fetch_quote
from app.mds.bloomberg.refs import (
    fetch_details,
    fetch_float,
    fetch_short_interest,
    list_tickers,
)
from app.models.results import SymbolQuote
from app.services.prices import HIST_TEMPLATE_DEFAULT


class BloombergProvider:
    """Bloomberg market data provider via blpapi."""

    def __init__(self) -> None:
        self._session = create_session()

    def list_tickers(self, max_count: int) -> pl.DataFrame:
        return list_tickers(self._session, max_count)

    def get_details(self, symbol: str) -> dict | None:
        return fetch_details(self._session, symbol)

    def get_quote(self, symbol: str) -> SymbolQuote:
        return fetch_quote(self._session, symbol)

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
            self._session,
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
            self._session,
            symbol,
            template,
            quiet=quiet,
        )

    def get_float(
        self,
        symbol: str,
        quiet: bool = False,
    ) -> dict | None:
        return fetch_float(self._session, symbol, quiet)

    def get_short_interest(
        self,
        symbol: str,
        quiet: bool = False,
    ) -> dict | None:
        return fetch_short_interest(self._session, symbol, quiet)
