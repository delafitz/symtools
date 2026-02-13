import polars as pl

from app.services.analytics.window import (
    get_all_windows,
)

DAILY_ANN = 252**0.5 * 100
BASKET_VOL_WINDOW = 90
RETURN_WINDOWS = [1, 5, 30, 200]
CORR_WINDOWS = [200, 30]
CORR_DELTAS: list[int] = []


def get_window_return(returns: pl.DataFrame, days: int) -> dict:
    base = (
        returns.tail(days)
        .drop('date')
        .with_columns((pl.all() + 1).cum_prod() - 1)
        .tail(1)
    )
    return {
        'value': base.select('hedged').item(),
        'meta': base.select('target').item(),
    }


def get_return_windows(
    returns: pl.DataFrame, windows: list[int]
) -> dict[str, dict]:
    all_windows = {
        f'{day_count}d': get_window_return(returns, day_count)
        for day_count in windows
    }
    return all_windows


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

    returns = get_return_windows(wtd_returns, RETURN_WINDOWS)

    corrs = get_all_windows(
        wtd_returns,
        pl.corr(pl.col('target'), pl.col('basket')),
        CORR_WINDOWS,
        CORR_DELTAS,
    )

    vols = {
        col: get_all_windows(
            wtd_returns,
            pl.col(col).std() * DAILY_ANN,
            [BASKET_VOL_WINDOW],
            [],
        )[f'{BASKET_VOL_WINDOW}d']['value']
        for col in ['target', 'basket', 'hedged']
    }
    vols['reduction'] = 1 - vols['hedged'] / vols['target']

    series = get_basket_series(wtd_returns)

    return {
        'weights': dict(weights.rows()),
        'corrs': corrs,
        'vols': vols,
        'returns': returns,
        'series': series,
    }
