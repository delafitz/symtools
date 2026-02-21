from __future__ import annotations

from pydantic import BaseModel

from app.models.alerts import Alert
from app.utils.models import Fmt, config, f, fp


class VolTimeCalcs(BaseModel):
    model_config = config()
    notional: float = f(Fmt.notional)
    shares: float = f(Fmt.shares)
    adv: float = f(Fmt.volume)
    vol: float = f(Fmt.vol)
    discount: float = f(Fmt.discount)


class VolTimeStats(BaseModel):
    model_config = config()
    pct_mkt_cap: float = f(Fmt.ratio, 'MktCap')
    pct_float: float = f(Fmt.ratio, 'Float')
    xadv: float = f(Fmt.ratio, 'xADV')
    sigma: float = f(Fmt.ratio)


class SymbolCostCalcs(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    discount: VolTimeCalcs = fp(title='Discount')
    stats: VolTimeStats = fp(title='Stats')
    alerts: list[Alert] = []
