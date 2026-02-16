from app.models.alerts import Alert
from app.services.alerts import (
    AlertContext,
    _level,
    _scale,
    rule,
)


@rule('liquidity')
def low_liquidity(ctx: AlertContext) -> Alert | None:
    if not ctx.analytics or not ctx.ref:
        return None
    ff = ctx.ref.get('free_float')
    if not ff or ff <= 0:
        return None
    adv = ctx.analytics.adv
    ratio = adv / ff
    if ratio >= 0.01:
        return None
    score = _scale(ratio, 0.01, above=False)
    return Alert(
        rule='low_liquidity',
        category='liquidity',
        level=_level(score),
        score=score,
        label='LowADV',
        desc='ADV < 1% of float',
        value=ratio,
        value_format='pct',
        threshold=0.01,
        threshold_format='pct',
    )


@rule('liquidity')
def high_turnover(ctx: AlertContext) -> Alert | None:
    if not ctx.analytics or not ctx.ref:
        return None
    ff = ctx.ref.get('free_float')
    if not ff or ff <= 0:
        return None
    adv = ctx.analytics.adv
    ratio = adv / ff
    if ratio <= 0.05:
        return None
    score = _scale(ratio, 0.05)
    return Alert(
        rule='high_turnover',
        category='liquidity',
        level=_level(score),
        score=score,
        label='HighADV',
        desc='ADV > 5% of float',
        value=ratio,
        value_format='pct',
        threshold=0.05,
        threshold_format='pct',
    )
