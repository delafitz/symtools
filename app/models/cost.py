from pydantic import BaseModel

from app.utils.models import Fmt, config, f, fp


class VolTimeCalcs(BaseModel):
    model_config = config()
    notional: float = f(Fmt.notional)
    shares: float = f(Fmt.shares)
    adv: float = f(Fmt.shares)
    vol: float = f(Fmt.vol)
    discount: float = f(Fmt.discount)


class VolTimeStats(BaseModel):
    model_config = config()
    pct_mkt_cap: float = f(Fmt.pct, '%MktCap')
    pct_float: float = f(Fmt.pct, '%Float')
    xadv: float = f(Fmt.ratio, 'xADV')
    sigma: float = f(Fmt.pct)


class SymbolCostCalcs(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    discount: VolTimeCalcs = fp(title='Discount')
    stats: VolTimeStats = fp(title='Stats')
