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
    float_shares: int = f(Fmt.shares)
    short_int: int = f(Fmt.shares)


class Ratios(BaseModel):
    model_config = config()
    float_out: float = f(Fmt.pct)
    float_short: float = f(Fmt.pct)
    cover_days: float = f(Fmt.days)


class Historical(BaseModel):
    model_config = config()
    beta: Optional[float] = f(Fmt.mult, default=None)
    vol: float = f(Fmt.vol)
    sigma: float = f(Fmt.sigma)
    return_1y: float = f(Fmt.change)
    high_pct: float = f(Fmt.change)


class SymbolAnalytics(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    vol: float = f(Fmt.vol)
    sigma: float = f(Fmt.sigma)
    adv: float = f(Fmt.volume)
    hist_vol: dict[str, TermStruct] = fp(
        'HistVol', Fmt.vol, '5d', Fmt.meta
    )
    hist_adv: dict[str, TermStruct] = fp(
        'ADV', Fmt.volume, '5d', Fmt.meta
    )
    liquidity: Optional[Liquidity] = None
    ratios: Optional[Ratios] = None
    historical: Optional[Historical] = None
