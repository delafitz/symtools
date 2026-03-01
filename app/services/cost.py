from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.analytics import SymbolAnalytics
from app.models.cost import SymbolCostCalcs
from app.models.inputs import SymbolOverrides
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.server.cache import Cache

log = get_logger(__name__)

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
    sym = overrides.symbol.lower()
    ref = cache.get_ref(sym)
    if not ref:
        log.warning(f'cost: {sym} not in refs')
        return None

    snap = await cache.get_analytics(sym)
    if not snap:
        log.warning(f'cost: {sym} no analytics')
        return None

    # Resolve price from last close if not provided
    if overrides.price <= 0:
        hist = cache.get_hist(sym)
        if hist is not None and not hist.is_empty():
            overrides = overrides.model_copy(
                update={
                    'price': hist['close'][-1],
                }
            )
        else:
            log.warning(f'cost: {sym} no price')
            return None

    vol, adv, notional, shares = merge_overrides(overrides, snap)

    if adv <= 0:
        log.warning(
            f'cost: {sym} adv=0'
            f' (override={overrides.adv}'
            f' snap={snap.adv})'
        )
        return None

    mkt_cap = ref['mkt_cap'] or 0
    free_float = ref['free_float'] or 0

    if mkt_cap <= 0 or free_float <= 0:
        log.warning(
            f'cost: {sym} mkt_cap={mkt_cap} free_float={free_float}'
        )
        return None

    xadv = shares / adv
    sigma = vol / DAILY_ANN
    discount = get_discount(shares, adv, vol)
    pct_mkt_cap = notional / mkt_cap
    pct_float = shares / free_float

    vol_up = get_discount(shares, adv, vol + 1)
    adv_down = get_discount(shares, adv * 0.9, vol)
    vol_1pct_bps = round((vol_up - discount) * 10000, 1)
    adv_10pct_bps = round((adv_down - discount) * 10000, 1)

    result = SymbolCostCalcs.model_validate(
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
            'sensitivity': {
                'vol_1pct': vol_1pct_bps,
                'adv_10pct': adv_10pct_bps,
            },
        }
    )
    from app.services.alerts import (
        AlertContext,
        evaluate,
    )

    ctx = AlertContext(
        symbol=sym,
        ref=ref,
        analytics=snap,
        costs=result,
        overrides=overrides,
    )
    alert_result = evaluate(ctx, categories={'cost'})
    if alert_result:
        result.alerts = alert_result.alerts
    return result
