from app.models.alerts import Alert
from app.services.alerts import AlertContext, _level, rule


@rule('volatility')
def high_vol(ctx: AlertContext) -> Alert | None:
    if not ctx.analytics:
        return None
    vol = ctx.analytics.vol
    if vol > 100:
        score = 0.8
        return Alert(
            rule='high_vol',
            category='volatility',
            level=_level(score),
            score=score,
            label='Vol > 100%',
            value=vol / 100,
            threshold=1.0,
        )
    if vol > 50:
        score = 0.5
        return Alert(
            rule='high_vol',
            category='volatility',
            level=_level(score),
            score=score,
            label='Vol > 50%',
            value=vol / 100,
            threshold=0.5,
        )
    return None


@rule('volatility')
def vol_discord(ctx: AlertContext) -> Alert | None:
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
    score = 0.4
    return Alert(
        rule='vol_discord',
        category='volatility',
        level=_level(score),
        score=score,
        label='30d/90d vol divergence > 20%',
        value=diff,
        threshold=0.20,
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
    score = 0.5
    return Alert(
        rule='vol_change',
        category='volatility',
        level=_level(score),
        score=score,
        label='30d vol > 1.3x 90d vol',
        value=ratio,
        threshold=1.3,
    )
