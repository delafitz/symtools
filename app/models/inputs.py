from pydantic import BaseModel


class SymbolOverrides(BaseModel):
    symbol: str
    price: float
    notional: float
    shares: float
    volatility: float
    adv: float
