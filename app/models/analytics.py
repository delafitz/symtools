from typing import Optional

from pydantic import BaseModel

from app.utils.models import Fmt, config, f, fp


class TermStruct(BaseModel):
    model_config = config()
    value: float
    meta: Optional[float] = None


class Liquidity(BaseModel):
    model_config = config()
    mkt_cap: float = f(Fmt.notional)
    shares_out: int = f(Fmt.shares)
    float_shares: int = f(Fmt.shares, title='Float')
    short_int: int = f(Fmt.shares, title='SI')


class Ratios(BaseModel):
    model_config = config()
    float_out: float = f(Fmt.pct, title='Float/SO')
    float_short: float = f(Fmt.pct, title='SI/Float')
    cover_days: float = f(Fmt.days, title='DaysToCov')


class Historical(BaseModel):
    model_config = config()
    one_sigma: float = f(Fmt.volatility, title='OneSig')
    beta: Optional[float] = f(Fmt.mult, default=None)
    return_1y: float = f(Fmt.change, title='1Y Ret', hidden=True)
    high_pct: float = f(Fmt.ratio, title='1Y Disc')
    low_pct: float = f(Fmt.ratio, title='1Y Low', hidden=True)
    momentum: Optional[float] = f(Fmt.change, default=None)


class SymbolAnalytics(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    vol: float = f(Fmt.volatility)
    adv: float = f(Fmt.volume)
    hist_vol: dict[str, TermStruct] = fp(
        'HistVol', Fmt.volatility, '5d', Fmt.meta
    )
    hist_adv: dict[str, TermStruct] = fp(
        'ADV', Fmt.volume, '5d', Fmt.meta
    )
    liquidity: Optional[Liquidity] = None
    ratios: Optional[Ratios] = None
    historical: Optional[Historical] = None
