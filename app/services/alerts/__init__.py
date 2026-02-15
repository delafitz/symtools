from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

from app.models.alerts import Alert, SymbolAlerts
from app.models.analytics import SymbolAnalytics
from app.models.baskets import SymbolBaskets
from app.models.cost import SymbolCostCalcs
from app.models.inputs import SymbolOverrides

_rules: list[tuple[str, Callable[[AlertContext], Alert | None]]] = []


def rule(category: str):
    def decorator(
        fn: Callable[[AlertContext], Alert | None],
    ) -> Callable[[AlertContext], Alert | None]:
        _rules.append((category, fn))
        return fn

    return decorator


def _level(score: float) -> str:
    if score > 0.66:
        return 'alert'
    if score >= 0.34:
        return 'warn'
    return 'info'


@dataclass
class AlertContext:
    symbol: str
    ref: dict | None = None
    analytics: SymbolAnalytics | None = None
    baskets: SymbolBaskets | None = None
    daily: pl.DataFrame | None = None
    costs: SymbolCostCalcs | None = None
    overrides: SymbolOverrides | None = None


def evaluate(
    ctx: AlertContext,
    categories: set[str] | None = None,
) -> SymbolAlerts | None:
    alerts: list[Alert] = []
    for cat, fn in _rules:
        if categories and cat not in categories:
            continue
        result = fn(ctx)
        if result is not None:
            alerts.append(result)
    if not alerts:
        return None
    return SymbolAlerts(
        symbol=ctx.symbol,
        score=max(a.score for a in alerts),
        alerts=alerts,
    )


# trigger rule registration
import app.services.alerts.rules  # noqa: E402, F401
