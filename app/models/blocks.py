from pydantic import BaseModel, Field

from app.utils.models import Fmt, config, f


class BlockTrade(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    price_date: str = f(Fmt.trade_dt)
    trade_date: str = f(Fmt.trade_dt)
    registered: bool = Field(default=False)
    seller: str | None = f(Fmt.attr, default=None)
    deal_size: float = f(Fmt.deal_size)
    shares: int = f(Fmt.shares)
    offer_price: float = f(Fmt.price)
    discount: float = f(Fmt.discount)
    perf_t1: float = f(Fmt.change)
    broker: str = f(Fmt.attr)


class SymbolBlocks(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    trades: list[BlockTrade]
