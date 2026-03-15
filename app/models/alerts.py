from pydantic import BaseModel

from app.utils.models import Fmt, config, f


class Alert(BaseModel):
    model_config = config()
    rule: str = f(Fmt.attr)
    category: str = f(Fmt.attr)
    level: str = f(Fmt.attr)
    score: float = f(Fmt.score)
    label: str = f(Fmt.attr)
    desc: str = f(Fmt.longtext)
    value: float | None = f(Fmt.dynamic, default=None)
    value_format: str | None = f(Fmt.attr, default=None)
    threshold: float | None = f(Fmt.dynamic, default=None)
    threshold_format: str | None = f(Fmt.attr, default=None)


class SymbolAlerts(BaseModel):
    model_config = config()
    symbol: str = f(Fmt.symbol)
    alerts: list[Alert] = []
