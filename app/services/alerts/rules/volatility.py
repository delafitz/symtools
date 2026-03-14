from app.models.alerts import Alert
from app.services.alerts import (
    AlertContext,
    _level,
    _scale,
    rule,
)

_HIGH_VOL = 0.50  # 50% annualized vol
_VOL_TERM_DIFF = 0.20  # |30d-90d|/90d divergence
_VOL_SPIKE_RATIO = 1.3  # 10d/30d ratio


@rule('volatility')
def high_vol(ctx: AlertContext) -> Alert | None:
    if not ctx.analytics:
        return None
    vol = ctx.analytics.vol
    if vol <= 50:
        return None
    score = _scale(vol / 100, _HIGH_VOL)
    return Alert(
        rule='high_vol',
        category='volatility',
        level=_level(score),
        score=score,
        label='HighVol',
        desc='Vol > 50%',
        value=vol / 100,
        value_format='pct',
        threshold=_HIGH_VOL,
        threshold_format='pct',
    )


@rule('volatility')
def vol_disperse(ctx: AlertContext) -> Alert | None:
    """30d/90d vol divergence — medium-term regime shift."""
    if not ctx.analytics:
        return None
    hv = ctx.analytics.hist_vol
    v30 = hv.get('30d')
    v90 = hv.get('90d')
    if not v30 or not v90 or v90.value <= 0:
        return None
    diff = abs(v30.value - v90.value) / v90.value
    if diff <= _VOL_TERM_DIFF:
        return None
    score = _scale(diff, _VOL_TERM_DIFF)
    return Alert(
        rule='vol_disperse',
        category='volatility',
        level=_level(score),
        score=score,
        label='VolTerm',
        desc='30d/90d vol diverge > 20%',
        value=diff,
        value_format='pct',
        threshold=_VOL_TERM_DIFF,
        threshold_format='pct',
    )


@rule('volatility')
def vol_change(ctx: AlertContext) -> Alert | None:
    """10d/30d vol spike — recent short-dated vol move."""
    if not ctx.analytics:
        return None
    hv = ctx.analytics.hist_vol
    v10 = hv.get('10d')
    v30 = hv.get('30d')
    if not v10 or not v30 or v30.value <= 0:
        return None
    ratio = v10.value / v30.value
    if ratio <= _VOL_SPIKE_RATIO:
        return None
    score = _scale(ratio, _VOL_SPIKE_RATIO)
    return Alert(
        rule='vol_change',
        category='volatility',
        level=_level(score),
        score=score,
        label='VolSpike',
        desc='10d vol > 1.3x 30d',
        value=ratio,
        value_format='ratio',
        threshold=_VOL_SPIKE_RATIO,
        threshold_format='ratio',
    )
