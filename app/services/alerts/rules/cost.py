from app.models.alerts import Alert
from app.services.alerts import AlertContext, _level, rule


@rule('cost')
def size_pct_float(ctx: AlertContext) -> Alert | None:
    if not ctx.costs:
        return None
    pct = ctx.costs.stats.pct_float
    if pct <= 0.10:
        return None
    score = 0.6
    return Alert(
        rule='size_pct_float',
        category='cost',
        level=_level(score),
        score=score,
        label='Shares > 10% of float',
        value=pct,
        threshold=0.10,
    )


@rule('cost')
def high_adv_multiple(ctx: AlertContext) -> Alert | None:
    if not ctx.costs:
        return None
    xadv = ctx.costs.stats.xadv
    if xadv <= 5:
        return None
    score = 0.5
    return Alert(
        rule='high_adv_multiple',
        category='cost',
        level=_level(score),
        score=score,
        label='xADV > 5',
        value=xadv,
        threshold=5.0,
    )


@rule('cost')
def override_vol_mismatch(
    ctx: AlertContext,
) -> Alert | None:
    if not ctx.costs or not ctx.overrides:
        return None
    if not ctx.analytics:
        return None
    if ctx.overrides.volatility <= 0:
        return None
    hist = ctx.analytics.vol
    if hist <= 0:
        return None
    diff = abs(ctx.overrides.volatility - hist) / hist
    if diff <= 0.20:
        return None
    score = 0.3
    return Alert(
        rule='override_vol_mismatch',
        category='cost',
        level=_level(score),
        score=score,
        label='Override vol differs > 20%',
        value=diff,
        threshold=0.20,
    )


@rule('cost')
def override_adv_mismatch(
    ctx: AlertContext,
) -> Alert | None:
    if not ctx.costs or not ctx.overrides:
        return None
    if not ctx.analytics:
        return None
    if ctx.overrides.adv <= 0:
        return None
    hist = ctx.analytics.adv
    if hist <= 0:
        return None
    diff = abs(ctx.overrides.adv - hist) / hist
    if diff <= 0.20:
        return None
    score = 0.3
    return Alert(
        rule='override_adv_mismatch',
        category='cost',
        level=_level(score),
        score=score,
        label='Override ADV differs > 20%',
        value=diff,
        threshold=0.20,
    )
