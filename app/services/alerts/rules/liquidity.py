from app.models.alerts import Alert
from app.services.alerts import (
    AlertContext,
    _level,
    _scale,
    rule,
)

_LOW_ADV_DOLLAR = 20e6   # $20M daily dollar ADV
_HIGH_TURNOVER = 0.05    # 5% of float
_HIGH_SI = 0.05          # 5% of float
_HIGH_DTC = 5.0          # days to cover
_LOW_FLOAT = 0.60        # float / shares_out


@rule('liquidity')
def low_liquidity(ctx: AlertContext) -> Alert | None:
    """Dollar ADV below $20M — hard to execute a block."""
    if not ctx.analytics or ctx.daily is None:
        return None
    if ctx.daily.is_empty():
        return None
    price = ctx.daily['close'][-1]
    if price <= 0:
        return None
    adv_dollar = ctx.analytics.adv * price
    if adv_dollar >= _LOW_ADV_DOLLAR:
        return None
    score = _scale(adv_dollar, _LOW_ADV_DOLLAR, above=False)
    return Alert(
        rule='low_liquidity',
        category='liquidity',
        level=_level(score),
        score=score,
        label='LowADV',
        desc='ADV < $20M',
        value=adv_dollar,
        value_format='notional',
        threshold=_LOW_ADV_DOLLAR,
        threshold_format='notional',
    )


@rule('liquidity')
def high_turnover(ctx: AlertContext) -> Alert | None:
    """ADV > 5% of float — speculative/squeeze risk."""
    if not ctx.analytics or not ctx.ref:
        return None
    ff = ctx.ref.get('free_float')
    if not ff or ff <= 0:
        return None
    ratio = ctx.analytics.adv / ff
    if ratio <= _HIGH_TURNOVER:
        return None
    score = _scale(ratio, _HIGH_TURNOVER)
    return Alert(
        rule='high_turnover',
        category='liquidity',
        level=_level(score),
        score=score,
        label='HighTurn',
        desc='ADV > 5% of float',
        value=ratio,
        value_format='pct',
        threshold=_HIGH_TURNOVER,
        threshold_format='pct',
    )


@rule('liquidity')
def high_short_interest(ctx: AlertContext) -> Alert | None:
    if not ctx.ref:
        return None
    si = ctx.ref.get('short_interest')
    ff = ctx.ref.get('free_float')
    if not si or si <= 0 or not ff or ff <= 0:
        return None
    ratio = si / ff
    if ratio <= _HIGH_SI:
        return None
    score = _scale(ratio, _HIGH_SI)
    return Alert(
        rule='high_short_interest',
        category='liquidity',
        level=_level(score),
        score=score,
        label='ShortInt',
        desc='SI > 5% of float',
        value=ratio,
        value_format='pct',
        threshold=_HIGH_SI,
        threshold_format='pct',
    )


@rule('liquidity')
def low_float(ctx: AlertContext) -> Alert | None:
    """Float < 60% of shares out — insider/founder concentration."""
    if not ctx.ref:
        return None
    ff = ctx.ref.get('free_float')
    shares_out = ctx.ref.get('shares_out')
    if not ff or ff <= 0 or not shares_out or shares_out <= 0:
        return None
    ratio = ff / shares_out
    if ratio >= _LOW_FLOAT:
        return None
    score = _scale(ratio, _LOW_FLOAT, above=False)
    return Alert(
        rule='low_float',
        category='liquidity',
        level=_level(score),
        score=score,
        label='LowFloat',
        desc='Float < 60% of shares',
        value=ratio,
        value_format='pct',
        threshold=_LOW_FLOAT,
        threshold_format='pct',
    )


@rule('liquidity')
def high_days_to_cover(ctx: AlertContext) -> Alert | None:
    if not ctx.ref:
        return None
    dtc = ctx.ref.get('days_to_cover')
    if not dtc or dtc <= _HIGH_DTC:
        return None
    score = _scale(dtc, _HIGH_DTC)
    return Alert(
        rule='high_days_to_cover',
        category='liquidity',
        level=_level(score),
        score=score,
        label='DTC',
        desc='Days to cover > 5',
        value=dtc,
        value_format='days',
        threshold=_HIGH_DTC,
        threshold_format='days',
    )
