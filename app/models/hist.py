from pydantic import AliasGenerator, BaseModel, ConfigDict
from pydantic.alias_generators import to_camel, to_snake

from app.utils.models import Fmt, config, f


class SymbolBar(BaseModel):
    model_config = ConfigDict(
        alias_generator=AliasGenerator(
            validation_alias=to_snake,
            serialization_alias=to_camel,
        ),
        extra='allow',
    )
    date: str
    iso: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    vwap: float
    volume: float
    pct_return: float | None = None


class DailyAgg(BaseModel):
    model_config = config()
    date: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    vwap: float
    volume: float
    pct_return: float | None = None


class HistBase(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    template: str


class HistStats(BaseModel):
    model_config = config()
    end_date: str
    end_price: float = f(Fmt.price)
    start_date: str
    prev_date: str
    prev_close: float = f(Fmt.price)
    range_vwap: float | None = f(Fmt.price, default=None)
    range_pct_return: float | None = f(Fmt.change, default=None)


class SymbolHist(HistBase):
    timespan: str
    multiplier: int
    scale: int
    stats: dict[int, HistStats]
    daily_aggs: list[DailyAgg] | None = None
    bars: list[SymbolBar]


class TrackingBar(BaseModel):
    """Bar-over-bar return. First bar = 0 (no prior)."""

    model_config = config()
    date: str
    timestamp: int | None = None
    pct_return: float


class BasketHist(HistBase):
    """Tracking series for one basket scenario."""

    basket: str
    stats: dict[int, HistStats]
    weighted: list[TrackingBar]
    symbols: dict[str, list[TrackingBar]]
