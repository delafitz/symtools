from app.models.alerts import Alert
from app.services.alerts import (
    AlertContext,
    _level,
    _scale,
    rule,
)


@rule('baskets')
def poor_index_hedge(ctx: AlertContext) -> Alert | None:
    if not ctx.baskets:
        return None
    indices = ctx.baskets.baskets.get('indices')
    if not indices:
        return None
    corr_200 = indices.corrs.get('200d')
    if not corr_200:
        return None
    if corr_200.value >= 0.2:
        return None
    score = _scale(corr_200.value, 0.2, above=False)
    return Alert(
        rule='poor_index_hedge',
        category='baskets',
        level=_level(score),
        score=score,
        label='LowIndex',
        desc='Index 200d corr < 0.2',
        value=corr_200.value,
        value_format='ratio',
        threshold=0.2,
        threshold_format='ratio',
    )


@rule('baskets')
def no_good_hedges(ctx: AlertContext) -> Alert | None:
    if not ctx.baskets:
        return None
    best_corr: float | None = None
    for basket in ctx.baskets.baskets.values():
        corr_200 = basket.corrs.get('200d')
        if corr_200 and corr_200.value > 0.5:
            return None
        if corr_200:
            if best_corr is None or corr_200.value > best_corr:
                best_corr = corr_200.value
    score = (
        _scale(best_corr, 0.5, above=False)
        if best_corr is not None
        else 0.6
    )
    return Alert(
        rule='no_good_hedges',
        category='baskets',
        level=_level(score),
        score=score,
        label='LowBaskets',
        desc='No scenario corr > 0.5',
        value=best_corr,
        value_format='ratio' if best_corr is not None else None,
        threshold=0.5,
        threshold_format='ratio',
    )
