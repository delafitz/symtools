import polars as pl

from app.services.analytics.window import (
    get_all_windows,
)

DAILY_ANN = 252**0.5 * 100
VOL_WINDOW = 90
BETA_WINDOW = 200
CORR_WINDOW = 200
CORR_WINDOWS = [200, 30]
CORR_DELTAS: list[int] = []


def get_wtd_returns(
    target: str,
    weights: pl.DataFrame,
    returns: pl.DataFrame,
) -> pl.DataFrame:
    basket_weights = dict(
        zip(
            weights[target].to_list(),
            weights['weight'].to_list(),
        )
    )
    basket_syms = list(basket_weights.keys())
    total_weight = sum(basket_weights.values())
    wtd = pl.concat(
        [
            returns.select(['date', 'target']),
            (
                returns.select(basket_syms)
                .with_columns(
                    [
                        pl.col(symbol) * weight
                        for symbol, weight in basket_weights.items()
                    ]
                )
                .sum_horizontal()
                / total_weight
            )
            .alias('basket')
            .to_frame(),
        ],
        how='horizontal',
    ).with_columns(
        (pl.col('target') - pl.col('basket')).alias('hedged')
    )
    return wtd


def get_basket_series(
    wtd_returns: pl.DataFrame,
) -> pl.DataFrame:
    """Compute cumulative basket returns."""
    return wtd_returns.select(
        [
            'date',
            ((pl.col('basket') + 1).cum_prod()).alias('basket'),
        ]
    )


def calc_stats(
    target: str,
    weights: pl.DataFrame,
    all_returns: pl.DataFrame,
) -> dict:
    wtd_returns = get_wtd_returns(target, weights, all_returns)
    total_weight = sum(weights['weight'].to_list())

    corrs = get_all_windows(
        wtd_returns,
        pl.corr(pl.col('target'), pl.col('basket')),
        CORR_WINDOWS,
        CORR_DELTAS,
    )

    tail = wtd_returns.tail(CORR_WINDOW)
    corr = tail.select(pl.corr('target', 'basket')).item()

    beta = tail.select(
        pl.cov('target', 'basket') / pl.col('basket').var()
    ).item()

    wtd_returns = wtd_returns.with_columns(
        (pl.col('target') - beta * pl.col('basket')).alias('hedged')
    )

    vol_tail = wtd_returns.tail(VOL_WINDOW)
    tgt_vol = vol_tail.select(
        pl.col('target').std() * DAILY_ANN
    ).item()
    hdg_vol = vol_tail.select(
        pl.col('hedged').std() * DAILY_ANN
    ).item()
    vol_reduce = 1 - hdg_vol / tgt_vol

    series = get_basket_series(wtd_returns)

    return {
        'weights': dict(weights.rows()),
        'stats': {
            'weight': total_weight,
            'beta': beta,
            'corr': corr,
            'vol_reduce': vol_reduce,
        },
        'corrs': corrs,
        'series': series,
    }
