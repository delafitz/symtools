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


class BasketStats(BaseModel):
    model_config = config()
    weight: float = f(Fmt.weight)
    beta: float = f(Fmt.mult)
    corr: float = f(Fmt.corr)
    vol_reduce: float = f(Fmt.pct, title='Reduce')


class Basket(BaseModel):
    model_config = config()
    params: BasketParams = fp('Params')
    weights: dict[str, float] = fp('Weights', Fmt.weight)
    stats: BasketStats = fp('Stats')
    corrs: dict[str, TermStruct] = fp('Corrs', Fmt.corr)


class SymbolBaskets(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    baskets: dict[str, Basket]
