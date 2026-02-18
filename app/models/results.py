from pydantic import BaseModel

from app.utils.models import Fmt, config, f


class SearchResult(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    score: float = f(Fmt.score)


class RefData(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    exch: str = f(Fmt.attr)
    name: str = f(Fmt.attr)
    curr: str = f(Fmt.attr)
    cik: str = f(Fmt.attr, default='')
    sic: str = f(Fmt.attr)
    shares_out: int = f(Fmt.shares, title='ShrsOut')
    mkt_cap: float = f(Fmt.notional, title='MktCap')
    free_float: int = f(Fmt.shares, title='Float')
    free_float_pct: float = f(Fmt.ratio, title='FloatPct')
    free_float_date: str = f(Fmt.date, default='')
    short_interest: int = f(Fmt.shares, title='SI', default=0)
    days_to_cover: float = f(Fmt.ratio, title='DTC', default=0.0)
    short_avg_vol: int = f(Fmt.volume, title='SIADV', default=0)
    short_interest_date: str = f(Fmt.date, default='')


class SymbolQuote(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    updated: str = f(Fmt.iso)
    prev: float = f(Fmt.price)
    close: float = f(Fmt.price)
    last: float = f(Fmt.price)
    volume: float = f(Fmt.volume)
    chg: float = f(Fmt.price)
    pct_chg: float = f(Fmt.pct)
    session: str | None = f(Fmt.attr, default=None)
    session_last: float | None = f(Fmt.price, default=None)
    session_chg: float | None = f(Fmt.price, default=None)
    session_volume: float | None = f(Fmt.volume, default=None)
