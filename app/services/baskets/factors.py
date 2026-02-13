from dataclasses import dataclass, field

import numpy as np
import polars as pl
from sklearn.decomposition import PCA

from app.utils.logger import get_logger

log = get_logger(__name__)

MIN_STOCKS = 50
MIN_BARS = 250
MIN_COVERAGE = 0.8
N_COMPONENTS = 10
ADV_WINDOW = 30


@dataclass
class FactorExposure:
    loading: float
    quintile: int


@dataclass
class Factor:
    name: str
    pc_index: int
    pc_corr: float
    explained_var: float
    exposures: dict[str, FactorExposure] = field(default_factory=dict)


@dataclass
class FactorModel:
    smb: Factor
    turnover: Factor
    n_stocks: int


def build_factor_model(
    refs: pl.DataFrame,
    hists: pl.DataFrame,
) -> FactorModel | None:
    stocks = refs.filter(
        (pl.col('type') == 'stock') & (pl.col('mkt_cap') > 0)
    ).select('symbol', 'mkt_cap')

    stock_syms = set(stocks.get_column('symbol').to_list())

    y_hists = hists.filter(
        (pl.col('template') == 'Y')
        & pl.col('symbol').is_in(stock_syms)
    )

    # Count bars per symbol, keep those with enough
    counts = y_hists.group_by('symbol').agg(pl.len().alias('n'))
    valid = (
        counts.filter(pl.col('n') >= MIN_BARS)
        .get_column('symbol')
        .to_list()
    )

    if len(valid) < MIN_STOCKS:
        log.warning(
            f'factors: only {len(valid)} stocks '
            f'with >={MIN_BARS} bars, need {MIN_STOCKS}'
        )
        return None

    # Filter to symbols covering >=80% of dates
    valid_hists = y_hists.filter(pl.col('symbol').is_in(valid))
    n_dates = valid_hists.select('date').n_unique()
    date_threshold = int(n_dates * MIN_COVERAGE)

    date_counts = valid_hists.group_by('symbol').agg(
        pl.col('date').n_unique().alias('n_dates')
    )
    dense_syms = (
        date_counts.filter(pl.col('n_dates') >= date_threshold)
        .get_column('symbol')
        .to_list()
    )

    if len(dense_syms) < MIN_STOCKS:
        log.warning(
            f'factors: only {len(dense_syms)} stocks '
            f'with >={MIN_COVERAGE:.0%} date coverage'
        )
        return None

    dense_hists = y_hists.filter(pl.col('symbol').is_in(dense_syms))

    # Build returns matrix
    returns = (
        dense_hists.sort('symbol', 'date')
        .with_columns(
            pl.col('close').pct_change().over('symbol').alias('ret')
        )
        .select('date', 'symbol', 'ret')
    )

    wide = returns.pivot(
        on='symbol',
        index='date',
        values='ret',
    ).sort('date')

    syms = [c for c in wide.columns if c != 'date']
    mat = wide.select(syms).fill_null(0.0).to_numpy()

    # Orthogonalize: subtract equal-weight market
    mkt = mat.mean(axis=1, keepdims=True)
    resid = mat - mkt

    # Symbol metadata
    cap_map = dict(
        zip(
            stocks.get_column('symbol').to_list(),
            stocks.get_column('mkt_cap').to_list(),
        )
    )
    caps = np.array([cap_map[s] for s in syms])

    # ADV per symbol (trailing window mean volume)
    adv_df = (
        dense_hists.sort('symbol', 'date')
        .group_by('symbol')
        .agg(pl.col('volume').tail(ADV_WINDOW).mean().alias('adv'))
    )
    adv_map = dict(
        zip(
            adv_df.get_column('symbol').to_list(),
            adv_df.get_column('adv').to_list(),
        )
    )

    # Turnover ratio: ADV / mkt_cap
    turnover = np.array(
        [adv_map.get(s, 0.0) / cap_map[s] for s in syms]
    )

    # PCA on residuals
    n_comp = min(N_COMPONENTS, resid.shape[1])
    pca = PCA(n_components=n_comp)
    pca.fit(resid)

    # Raw factor series
    median_cap = np.median(caps)
    small_mask = caps <= median_cap
    big_mask = ~small_mask
    smb_raw = resid[:, small_mask].mean(axis=1) - resid[
        :, big_mask
    ].mean(axis=1)

    median_turnover = np.median(turnover)
    high_mask = turnover >= median_turnover
    low_mask = ~high_mask
    turnover_raw = resid[:, high_mask].mean(axis=1) - resid[
        :, low_mask
    ].mean(axis=1)

    # Match each factor to its best PC
    smb_factor = _match_pc(
        'smb', pca, resid, smb_raw, syms, caps, n_comp
    )
    turnover_factor = _match_pc(
        'turnover',
        pca,
        resid,
        turnover_raw,
        syms,
        turnover,
        n_comp,
        exclude=smb_factor.pc_index,
    )

    model = FactorModel(
        smb=smb_factor,
        turnover=turnover_factor,
        n_stocks=len(syms),
    )
    _log_factor_summary(model, cap_map, adv_map)
    return model


def _match_pc(
    name: str,
    pca: PCA,
    resid: np.ndarray,
    raw_series: np.ndarray,
    syms: list[str],
    sort_values: np.ndarray,
    n_comp: int,
    exclude: int = -1,
) -> Factor:
    best_idx = 0
    best_corr = 0.0
    for i in range(n_comp):
        if i == exclude:
            continue
        pc = pca.components_[i] @ resid.T
        corr = np.corrcoef(pc, raw_series)[0, 1]
        if abs(corr) > abs(best_corr):
            best_corr = corr
            best_idx = i

    sign = 1.0 if best_corr > 0 else -1.0
    raw_loadings = sign * pca.components_[best_idx]

    # Quintiles by sort_values (1=lowest, 5=highest)
    ranked = sorted(enumerate(sort_values), key=lambda x: x[1])
    n = len(ranked)
    quintile_arr = [0] * n
    for rank, (idx, _) in enumerate(ranked):
        q = int(rank / n * 5) + 1
        quintile_arr[idx] = min(q, 5)

    exposures = {
        s: FactorExposure(
            loading=float(raw_loadings[i]),
            quintile=quintile_arr[i],
        )
        for i, s in enumerate(syms)
    }

    return Factor(
        name=name,
        pc_index=best_idx,
        pc_corr=float(best_corr * sign),
        explained_var=float(pca.explained_variance_ratio_[best_idx]),
        exposures=exposures,
    )


def _log_factor_summary(
    model: FactorModel,
    cap_map: dict[str, float],
    adv_map: dict[str, float],
) -> None:
    for factor in [model.smb, model.turnover]:
        rows = []
        for s, exp in factor.exposures.items():
            rows.append(
                (
                    s,
                    exp.loading,
                    exp.quintile,
                    cap_map.get(s, 0.0),
                    adv_map.get(s, 0.0),
                )
            )

        df = pl.DataFrame(
            {
                'symbol': [r[0] for r in rows],
                'beta': [r[1] for r in rows],
                'quintile': [r[2] for r in rows],
                'mkt_cap': [r[3] for r in rows],
                'adv': [r[4] for r in rows],
            }
        )

        summary = (
            df.group_by('quintile')
            .agg(
                pl.len().alias('n'),
                pl.col('beta').mean().round(4).alias('mean_beta'),
                pl.col('beta').std().round(4).alias('std_beta'),
                (pl.col('mkt_cap').mean() / 1e9)
                .round(1)
                .alias('avg_cap_B'),
                (pl.col('adv').mean() / 1e6)
                .round(2)
                .alias('avg_adv_M'),
            )
            .sort('quintile')
        )

        log.cyan(
            f'{factor.name}: {model.n_stocks} stocks, '
            f'PC{factor.pc_index} '
            f'corr={factor.pc_corr:.3f}, '
            f'var={factor.explained_var:.3f}\n'
            f'{summary}'
        )
