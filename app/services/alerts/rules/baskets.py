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
    corr = indices.stats.corr
    if corr >= 0.2:
        return None
    score = _scale(corr, 0.2, above=False)
    return Alert(
        rule='poor_index_hedge',
        category='baskets',
        level=_level(score),
        score=score,
        label='LowIndex',
        desc='Index 200d corr < 0.2',
        value=corr,
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
        corr = basket.stats.corr
        if corr > 0.5:
            return None
        if best_corr is None or corr > best_corr:
            best_corr = corr
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
