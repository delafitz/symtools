import math

from app.models.alerts import Alert
from app.services.alerts import (
    AlertContext,
    _level,
    _scale,
    rule,
)

_WINDOWS = [1, 3, 5]
_LABELS = {1: 'Sig1D', 3: 'Sig3D', 5: 'Sig5D'}
_HEDGE_LABELS = {1: 'HSig1D', 3: 'HSig3D', 5: 'HSig5D'}

_RAW_SIGMA = 1.0    # threshold multiplier for raw vol
_HEDGE_SIGMA = 1.5  # threshold multiplier for hedged vol


def _nday_return(closes, n: int) -> float | None:
    if len(closes) < n + 1:
        return None
    return closes[-1] / closes[-(n + 1)] - 1


@rule('moves')
def sigma_moves(ctx: AlertContext) -> Alert | None:
    if ctx.daily is None or ctx.daily.is_empty():
        return None
    if not ctx.analytics or ctx.analytics.vol <= 0:
        return None
    daily_sigma = ctx.analytics.vol / math.sqrt(252) / 100
    closes = ctx.daily.get_column('close')
    best: Alert | None = None
    for n in _WINDOWS:
        ret = _nday_return(closes, n)
        if ret is None:
            continue
        threshold = daily_sigma * math.sqrt(n) * _RAW_SIGMA
        if abs(ret) <= threshold:
            continue
        score = _scale(abs(ret), threshold)
        alert = Alert(
            rule=f'sigma_move_{n}d',
            category='moves',
            level=_level(score),
            score=score,
            label=_LABELS[n],
            desc=f'{n}d move > 1\u03c3',
            value=abs(ret),
            value_format='pct',
            threshold=threshold,
            threshold_format='pct',
        )
        if best is None or alert.score > best.score:
            best = alert
    return best


@rule('moves')
def hedged_sigma_moves(ctx: AlertContext) -> Alert | None:
    """N-day return > 1.5σ of hedged (residual) vol for any basket."""
    if ctx.daily is None or ctx.daily.is_empty():
        return None
    if not ctx.analytics or ctx.analytics.vol <= 0:
        return None
    if not ctx.baskets:
        return None
    raw_daily_sigma = ctx.analytics.vol / math.sqrt(252) / 100
    closes = ctx.daily.get_column('close')
    best: Alert | None = None
    for scenario, basket in ctx.baskets.baskets.items():
        vol_reduce = basket.stats.vol_reduce
        if vol_reduce <= 0:
            continue
        hedged_daily_sigma = raw_daily_sigma * (1 - vol_reduce)
        if hedged_daily_sigma <= 0:
            continue
        for n in _WINDOWS:
            ret = _nday_return(closes, n)
            if ret is None:
                continue
            threshold = hedged_daily_sigma * math.sqrt(n) * _HEDGE_SIGMA
            if abs(ret) <= threshold:
                continue
            score = _scale(abs(ret), threshold)
            alert = Alert(
                rule=f'hedged_sigma_move_{n}d',
                category='moves',
                level=_level(score),
                score=score,
                label=_HEDGE_LABELS[n],
                desc=f'{n}d move > 1.5\u03c3 hedged ({scenario})',
                value=abs(ret),
                value_format='pct',
                threshold=threshold,
                threshold_format='pct',
            )
            if best is None or alert.score > best.score:
                best = alert
    return best
