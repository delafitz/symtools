from app.models.alerts import Alert
from app.services.alerts import (
    AlertContext,
    _level,
    _scale,
    rule,
)

_PCT_FLOAT_THRESHOLD = 0.10
_PCT_MKTCAP_THRESHOLD = 0.05
_XADV_THRESHOLD = 5.0
_DISCOUNT_THRESHOLD = 0.07  # 7% market impact


@rule('cost')
def size_pct_float(ctx: AlertContext) -> Alert | None:
    if not ctx.costs:
        return None
    pct = ctx.costs.stats.pct_float
    if pct <= _PCT_FLOAT_THRESHOLD:
        return None
    score = _scale(pct, _PCT_FLOAT_THRESHOLD)
    return Alert(
        rule='size_pct_float',
        category='cost',
        level=_level(score),
        score=score,
        label='PctFloat',
        desc='Shares > 10% of float',
        value=pct,
        value_format='pct',
        threshold=_PCT_FLOAT_THRESHOLD,
        threshold_format='pct',
    )


@rule('cost')
def size_pct_mktcap(ctx: AlertContext) -> Alert | None:
    if not ctx.costs:
        return None
    pct = ctx.costs.stats.pct_mkt_cap
    if pct <= _PCT_MKTCAP_THRESHOLD:
        return None
    score = _scale(pct, _PCT_MKTCAP_THRESHOLD)
    return Alert(
        rule='size_pct_mktcap',
        category='cost',
        level=_level(score),
        score=score,
        label='PctMktCap',
        desc='Deal > 5% of mkt cap',
        value=pct,
        value_format='pct',
        threshold=_PCT_MKTCAP_THRESHOLD,
        threshold_format='pct',
    )


@rule('cost')
def high_adv_multiple(ctx: AlertContext) -> Alert | None:
    if not ctx.costs:
        return None
    xadv = ctx.costs.stats.xadv
    if xadv <= _XADV_THRESHOLD:
        return None
    score = _scale(xadv, _XADV_THRESHOLD)
    return Alert(
        rule='high_adv_multiple',
        category='cost',
        level=_level(score),
        score=score,
        label='xADV',
        desc='xADV > 5',
        value=xadv,
        value_format='mult',
        threshold=_XADV_THRESHOLD,
        threshold_format='mult',
    )


@rule('cost')
def high_discount(ctx: AlertContext) -> Alert | None:
    if not ctx.costs:
        return None
    discount = abs(ctx.costs.discount.discount)
    if discount <= _DISCOUNT_THRESHOLD:
        return None
    score = _scale(discount, _DISCOUNT_THRESHOLD)
    return Alert(
        rule='high_discount',
        category='cost',
        level=_level(score),
        score=score,
        label='Discount',
        desc='Market impact > 7%',
        value=discount,
        value_format='pct',
        threshold=_DISCOUNT_THRESHOLD,
        threshold_format='pct',
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
    score = _scale(diff, 0.20)
    return Alert(
        rule='override_vol_mismatch',
        category='cost',
        level=_level(score),
        score=score,
        label='VolOver',
        desc='Override vol > 20% off',
        value=diff,
        value_format='pct',
        threshold=0.20,
        threshold_format='pct',
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
    score = _scale(diff, 0.20)
    return Alert(
        rule='override_adv_mismatch',
        category='cost',
        level=_level(score),
        score=score,
        label='ADVOver',
        desc='Override ADV > 20% off',
        value=diff,
        value_format='pct',
        threshold=0.20,
        threshold_format='pct',
    )
