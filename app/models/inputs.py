from pydantic import BaseModel


class SymbolOverrides(BaseModel):
    symbol: str
    price: float = 0.0
    notional: float = 0.0
    shares: float = 0.0
    volatility: float = 0.0
    adv: float = 0.0
