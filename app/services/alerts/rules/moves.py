import math

from app.models.alerts import Alert
from app.services.alerts import AlertContext, _level, rule

_WINDOWS = [1, 3, 5]


@rule('moves')
def recent_moves(ctx: AlertContext) -> Alert | None:
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
        threshold_warn = daily_sigma * math.sqrt(n)
        threshold_alert = 2 * daily_sigma * math.sqrt(n)
        if abs(ret) > threshold_alert:
            score = 0.8
            alert = Alert(
                rule=f'recent_move_{n}d',
                category='moves',
                level=_level(score),
                score=score,
                label=f'{n}d move > 2 sigma',
                value=abs(ret),
                threshold=threshold_alert,
            )
        elif abs(ret) > threshold_warn:
            score = 0.4
            alert = Alert(
                rule=f'recent_move_{n}d',
                category='moves',
                level=_level(score),
                score=score,
                label=f'{n}d move > 1 sigma',
                value=abs(ret),
                threshold=threshold_warn,
            )
        else:
            continue
        if best is None or alert.score > best.score:
            best = alert
    return best
