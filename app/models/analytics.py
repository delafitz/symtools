from typing import Optional

from pydantic import BaseModel

from app.utils.models import Fmt, config, f, fp


class TermStruct(BaseModel):
    model_config = config()
    value: float
    meta: Optional[float] = None


class SymbolAnalytics(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    vol: float = f(Fmt.vol)
    adv: float = f(Fmt.shares)
    hist_vol: dict[str, TermStruct] = fp(
        'HistVol', Fmt.vol, '5d', Fmt.meta
    )
    hist_adv: dict[str, TermStruct] = fp(
        'ADV', Fmt.shares, '5d', Fmt.meta
    )
