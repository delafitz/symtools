from app.models.alerts import Alert
from app.services.alerts import (
    AlertContext,
    _level,
    _scale,
    rule,
)


@rule('volatility')
def high_vol(ctx: AlertContext) -> Alert | None:
    if not ctx.analytics:
        return None
    vol = ctx.analytics.vol
    if vol <= 50:
        return None
    score = _scale(vol / 100, 0.5)
    return Alert(
        rule='high_vol',
        category='volatility',
        level=_level(score),
        score=score,
        label='HighVol',
        desc='Vol > 50%',
        value=vol / 100,
        value_format='pct',
        threshold=0.5,
        threshold_format='pct',
    )


@rule('volatility')
def vol_disperse(ctx: AlertContext) -> Alert | None:
    if not ctx.analytics:
        return None
    hv = ctx.analytics.hist_vol
    v30 = hv.get('30d')
    v90 = hv.get('90d')
    if not v30 or not v90 or v90.value <= 0:
        return None
    diff = abs(v30.value - v90.value) / v90.value
    if diff <= 0.20:
        return None
    score = _scale(diff, 0.20)
    return Alert(
        rule='vol_disperse',
        category='volatility',
        level=_level(score),
        score=score,
        label='VolTerm',
        desc='30d/90d vol diverge > 20%',
        value=diff,
        value_format='pct',
        threshold=0.20,
        threshold_format='pct',
    )


@rule('volatility')
def vol_change(ctx: AlertContext) -> Alert | None:
    if not ctx.analytics:
        return None
    hv = ctx.analytics.hist_vol
    v30 = hv.get('30d')
    v90 = hv.get('90d')
    if not v30 or not v90 or v90.value <= 0:
        return None
    ratio = v30.value / v90.value
    if ratio <= 1.3:
        return None
    score = _scale(ratio, 1.3)
    return Alert(
        rule='vol_change',
        category='volatility',
        level=_level(score),
        score=score,
        label='VolMove',
        desc='30d vol > 1.3x 90d',
        value=ratio,
        value_format='ratio',
        threshold=1.3,
        threshold_format='ratio',
    )
