from __future__ import annotations

import math
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
    if score >= 0.75:
        return 'alert'
    if score >= 0.50:
        return 'warn'
    return 'info'


# Sigmoid steepness. At k=2.2:
#   severity 1.0x (at threshold) → score 0.50
#   severity 1.5x               → score 0.75
#   severity 2.0x               → score 0.90
#   severity 3.0x               → score 0.98
_K = 2.2


def _scale(
    value: float,
    threshold: float,
    above: bool = True,
) -> float:
    """Sigmoid score in (0, 1) based on distance past threshold.

    above=True: value > threshold is bad (most rules)
    above=False: value < threshold is bad

    Returns 0.5 exactly at threshold, approaches 1.0 for
    extreme values. Rules should only call this after
    confirming the threshold is crossed (score will be ≥ 0.5).
    """
    if above:
        severity = value / threshold
    else:
        severity = threshold / max(value, 1e-9)
    return 1 / (1 + math.exp(-_K * (severity - 1)))


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
