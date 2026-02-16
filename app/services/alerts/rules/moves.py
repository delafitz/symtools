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
        if len(closes) < n + 1:
            continue
        ret = closes[-1] / closes[-(n + 1)] - 1
        threshold = daily_sigma * math.sqrt(n)
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
