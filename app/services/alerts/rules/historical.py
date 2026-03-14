from app.models.alerts import Alert
from app.services.alerts import (
    AlertContext,
    _level,
    _scale,
    rule,
)

_HIGH_BETA = 1.5       # SPY beta
_NEAR_HIGH_PCT = 0.05  # within 5% of 52W high
_NEAR_LOW_PCT = 0.05   # within 5% of 52W low


@rule('volatility')
def high_beta(ctx: AlertContext) -> Alert | None:
    if not ctx.analytics or not ctx.analytics.historical:
        return None
    beta = ctx.analytics.historical.beta
    if beta is None or beta <= _HIGH_BETA:
        return None
    score = _scale(beta, _HIGH_BETA)
    return Alert(
        rule='high_beta',
        category='volatility',
        level=_level(score),
        score=score,
        label='HighBeta',
        desc='Beta > 1.5x SPY',
        value=beta,
        value_format='mult',
        threshold=_HIGH_BETA,
        threshold_format='mult',
    )


@rule('volatility')
def near_52w_high(ctx: AlertContext) -> Alert | None:
    if not ctx.analytics or not ctx.analytics.historical:
        return None
    high_pct = ctx.analytics.historical.high_pct
    dist = 1.0 - high_pct
    if dist >= _NEAR_HIGH_PCT:
        return None
    score = _scale(dist, _NEAR_HIGH_PCT, above=False)
    return Alert(
        rule='near_52w_high',
        category='volatility',
        level=_level(score),
        score=score,
        label='Near52H',
        desc='Within 5% of 52W high',
        value=high_pct,
        value_format='pct',
        threshold=1.0 - _NEAR_HIGH_PCT,
        threshold_format='pct',
    )


@rule('volatility')
def near_52w_low(ctx: AlertContext) -> Alert | None:
    if not ctx.analytics or not ctx.analytics.historical:
        return None
    low_pct = ctx.analytics.historical.low_pct
    # low_pct = end / 52W_low; at low = 1.0, above low > 1.0
    dist = low_pct - 1.0
    if dist >= _NEAR_LOW_PCT:
        return None
    score = _scale(dist, _NEAR_LOW_PCT, above=False)
    return Alert(
        rule='near_52w_low',
        category='volatility',
        level=_level(score),
        score=score,
        label='Near52L',
        desc='Within 5% of 52W low',
        value=low_pct,
        value_format='pct',
        threshold=1.0 + _NEAR_LOW_PCT,
        threshold_format='pct',
    )
