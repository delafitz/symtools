from __future__ import annotations

from pydantic import BaseModel

from app.models.alerts import Alert
from app.utils.models import Fmt, config, f, fp


class VolTimeCalcs(BaseModel):
    model_config = config()
    notional: float = f(Fmt.notional)
    shares: float = f(Fmt.shares)
    adv: float = f(Fmt.volume)
    vol: float = f(Fmt.volatility)
    discount: float = f(Fmt.discount)


class VolTimeStats(BaseModel):
    model_config = config()
    pct_mkt_cap: float = f(Fmt.pct, 'MktCap')
    pct_float: float = f(Fmt.pct, 'Float')
    xadv: float = f(Fmt.mult, 'xADV')
    sigma: float = f(Fmt.sigma)


class CostSensitivity(BaseModel):
    model_config = config()
    vol_1pct: float = f(Fmt.bps, 'Vol +1%')
    adv_10pct: float = f(Fmt.bps, 'ADV -10%')


class SymbolCostCalcs(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    discount: VolTimeCalcs = fp(title='Discount')
    stats: VolTimeStats = fp(title='Stats')
    sensitivity: CostSensitivity = fp(title='Sensitivity')
    alerts: list[Alert] = []
