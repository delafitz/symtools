from app.models.alerts import Alert
from app.services.alerts import AlertContext, _level, rule


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
    score = 0.5
    return Alert(
        rule='poor_index_hedge',
        category='baskets',
        level=_level(score),
        score=score,
        label='Index 200d corr < 0.2',
        value=corr_200.value,
        threshold=0.2,
    )


@rule('baskets')
def no_good_hedges(ctx: AlertContext) -> Alert | None:
    if not ctx.baskets:
        return None
    for basket in ctx.baskets.baskets.values():
        corr_200 = basket.corrs.get('200d')
        if corr_200 and corr_200.value > 0.5:
            return None
    score = 0.6
    return Alert(
        rule='no_good_hedges',
        category='baskets',
        level=_level(score),
        score=score,
        label='No scenario with 200d corr > 0.5',
        value=None,
        threshold=0.5,
    )
