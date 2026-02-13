from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.analytics import SymbolAnalytics
from app.models.cost import SymbolCostCalcs
from app.models.inputs import SymbolOverrides

if TYPE_CHECKING:
    from app.server.cache import Cache

DAILY_ANN = 252**0.5 * 100


def merge_overrides(
    overrides: SymbolOverrides,
    snap: SymbolAnalytics,
) -> tuple[float, float, float, float]:
    return (
        (
            overrides.volatility
            if overrides.volatility > 0
            else snap.vol
        ),
        (overrides.adv if overrides.adv > 0 else snap.adv),
        (
            overrides.notional * 1e6
            if overrides.notional > 0
            else overrides.shares * 1e6 * overrides.price
            if overrides.shares > 0
            else 0
        ),
        (
            overrides.shares * 1e6
            if overrides.shares > 0
            else overrides.notional * 1e6 / overrides.price
            if overrides.notional > 0
            else 0
        ),
    )


def get_discount(shares: float, adv: float, vol: float) -> float:
    xadv = shares / adv
    sigma = vol / DAILY_ANN
    return -1 * sigma * xadv**0.5 if shares > 0 else 0


async def calc_costs(
    cache: 'Cache',
    overrides: SymbolOverrides,
) -> SymbolCostCalcs | None:
    ref = cache.get_ref(overrides.symbol)
    if ref:
        snap = await cache.get_analytics(overrides.symbol)
        if snap:
            vol, adv, notional, shares = merge_overrides(
                overrides, snap
            )
            xadv = shares / adv
            sigma = vol / DAILY_ANN
            discount = get_discount(shares, adv, vol)
            pct_mkt_cap = notional / ref['mkt_cap']
            pct_float = shares / ref['shares_out']

            return SymbolCostCalcs.model_validate(
                {
                    'symbol': snap.symbol,
                    'discount': {
                        'notional': notional,
                        'shares': shares,
                        'vol': vol,
                        'adv': adv,
                        'discount': discount,
                    },
                    'stats': {
                        'pct_mkt_cap': pct_mkt_cap,
                        'pct_float': pct_float,
                        'xadv': xadv,
                        'sigma': sigma,
                    },
                }
            )
    return None
