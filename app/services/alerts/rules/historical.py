import datetime

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
_HIGH_MOM = 0.50       # 12M-1M momentum > 50% (top ~25%)
_LOW_MOM = -0.20       # 12M-1M momentum < -20% (bottom ~14%)
_MIN_HIST_DAYS = 365   # calendar days of required history


@rule('historical')
def short_history(ctx: AlertContext) -> Alert | None:
    """Less than 365 calendar days of price history."""
    if ctx.daily is None or ctx.daily.is_empty():
        return None
    dates = ctx.daily['date']
    if len(dates) < 2:
        return None
    d0 = datetime.date.fromisoformat(dates[0])
    d1 = datetime.date.fromisoformat(dates[-1])
    days = (d1 - d0).days
    if days >= _MIN_HIST_DAYS:
        return None
    score = _scale(days, _MIN_HIST_DAYS, above=False)
    return Alert(
        rule='short_history',
        category='historical',
        level=_level(score),
        score=score,
        label='ShortHist',
        desc='Less than 1Y of price history',
        value=float(days),
        value_format='days',
        threshold=float(_MIN_HIST_DAYS),
        threshold_format='days',
    )


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


@rule('historical')
def high_momentum(ctx: AlertContext) -> Alert | None:
    """Strong positive 12M-1M momentum (top ~25% of universe)."""
    if not ctx.analytics or not ctx.analytics.historical:
        return None
    mom = ctx.analytics.historical.momentum
    if mom is None or mom <= _HIGH_MOM:
        return None
    score = _scale(mom, _HIGH_MOM)
    return Alert(
        rule='high_momentum',
        category='historical',
        level=_level(score),
        score=score,
        label='HighMom',
        desc='12M-1M momentum > 50%',
        value=mom,
        value_format='pct',
        threshold=_HIGH_MOM,
        threshold_format='pct',
    )


@rule('historical')
def low_momentum(ctx: AlertContext) -> Alert | None:
    """Strong negative 12M-1M momentum (bottom ~14% of universe)."""
    if not ctx.analytics or not ctx.analytics.historical:
        return None
    mom = ctx.analytics.historical.momentum
    if mom is None or mom >= _LOW_MOM:
        return None
    # flip signs: larger magnitude below zero → higher severity
    score = _scale(-mom, -_LOW_MOM)
    return Alert(
        rule='low_momentum',
        category='historical',
        level=_level(score),
        score=score,
        label='LowMom',
        desc='12M-1M momentum < -20%',
        value=mom,
        value_format='pct',
        threshold=_LOW_MOM,
        threshold_format='pct',
    )
