from pydantic import BaseModel

from app.models.analytics import TermStruct
from app.services.baskets.config import (
    CARDINALITY,
    L1_COEF,
    MAX_BUDGET,
    THRESHOLD_LONG,
)
from app.utils.models import Fmt, config, f, fp


class BasketParams(BaseModel):
    model_config = config()
    max_budget: float = MAX_BUDGET
    threshold_long: float = THRESHOLD_LONG
    cardinality: int = CARDINALITY
    l1_coef: float = L1_COEF


class VolStats(BaseModel):
    model_config = config()
    target: float = f(Fmt.vol)
    basket: float = f(Fmt.vol)
    hedged: float = f(Fmt.vol)
    reduction: float = f(Fmt.ratio)


class Basket(BaseModel):
    model_config = config()
    params: BasketParams = fp('Params')
    weights: dict[str, float] = fp('Weights', Fmt.ratio)
    returns: dict[str, TermStruct] = fp(
        'Hedged', Fmt.pct, 'Outright', Fmt.meta
    )
    corrs: dict[str, TermStruct] = fp('Corrs', Fmt.ratio)
    vols: VolStats = fp('Vols')


class SymbolBaskets(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    baskets: dict[str, Basket]
