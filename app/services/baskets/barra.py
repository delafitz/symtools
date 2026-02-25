"""Barra-like risk factor model.

Builds structured covariance (B'FB + D) from
style + sector factors for use as a scikit-folio
prior in basket optimization.

Seven style factors (market, size, momentum, reversal,
beta, resvol, liquidity) + up to 11 sector factors.
Style factors use Q5-Q1 factor-mimicking portfolios;
sector factors use equal-weight sector returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl
from skfolio.prior import EmpiricalPrior, FactorModel

from app.services.baskets.config import (
    SECTOR_CAP_PCT,
    SECTOR_FLOOR_PCT,
)
from app.utils.logger import get_logger

log = get_logger(__name__)

# Universe filters (same as factors.py)
MIN_STOCKS = 50
MIN_BARS = 250
MIN_COVERAGE = 0.8

# Factor windows
BETA_WINDOW = 250
MOM_SKIP = 21
MOM_WINDOW = 250
REV_WINDOW = 21
RESVOL_WINDOW = 90
ADV_WINDOW = 30

# Rebalance frequency (trading days)
REBAL_PERIOD = 21

# Minimum stocks per sector
MIN_SECTOR_STOCKS = 5

# Number of quintiles
N_QUINTILES = 5

SPY = 'spy'


@dataclass
class BarraExposure:
    size: float
    momentum: float
    reversal: float
    beta: float
    resvol: float
    liquidity: float
    sector: str


@dataclass
class BarraModel:
    factor_returns: pl.DataFrame
    exposures: dict[str, BarraExposure] = field(default_factory=dict)
    sectors: list[str] = field(default_factory=list)
    n_stocks: int = 0
    n_factors: int = 0


def build_barra_model(
    refs: pl.DataFrame,
    hists: pl.DataFrame,
) -> BarraModel | None:
    """Build Barra factor model from refs + Y hists.

    Returns None if insufficient data.
    """
    # 1. Filter universe
    stocks = refs.filter(
        (pl.col('type') == 'stock') & (pl.col('mkt_cap') > 0)
    ).select('symbol', 'mkt_cap', 'g_sector')

    stock_syms = set(stocks.get_column('symbol').to_list())

    y_hists = hists.filter(
        (pl.col('template') == 'Y')
        & pl.col('symbol').is_in(stock_syms)
    )

    # Count bars per symbol
    counts = y_hists.group_by('symbol').agg(pl.len().alias('n'))
    valid = (
        counts.filter(pl.col('n') >= MIN_BARS)
        .get_column('symbol')
        .to_list()
    )

    if len(valid) < MIN_STOCKS:
        log.warning(
            f'barra: only {len(valid)} stocks with >={MIN_BARS} bars'
        )
        return None

    # Filter to >=80% date coverage
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
            f'barra: only {len(dense_syms)} stocks '
            f'with >={MIN_COVERAGE:.0%} coverage'
        )
        return None

    dense_hists = y_hists.filter(pl.col('symbol').is_in(dense_syms))

    # 2. Build returns matrix (date x symbol)
    returns_long = (
        dense_hists.sort('symbol', 'date')
        .with_columns(
            pl.col('close').pct_change().over('symbol').alias('ret')
        )
        .select('date', 'symbol', 'ret')
    )

    wide = returns_long.pivot(
        on='symbol',
        index='date',
        values='ret',
    ).sort('date')

    syms = [c for c in wide.columns if c != 'date']
    dates = wide.get_column('date').to_list()
    mat = wide.select(syms).fill_null(0.0).to_numpy()
    n_days, n_syms = mat.shape

    if n_days < MIN_BARS:
        log.warning(f'barra: only {n_days} dates')
        return None

    # 3. SPY returns
    spy_idx = syms.index(SPY) if SPY in syms else None
    if spy_idx is not None:
        spy_ret = mat[:, spy_idx].copy()
    else:
        spy_hists = hists.filter(
            (pl.col('template') == 'Y') & (pl.col('symbol') == SPY)
        )
        if spy_hists.is_empty():
            log.warning('barra: no SPY data')
            return None
        spy_returns = (
            spy_hists.sort('date')
            .with_columns(pl.col('close').pct_change().alias('ret'))
            .select('date', 'ret')
        )
        spy_joined = wide.select('date').join(
            spy_returns, on='date', how='left'
        )
        spy_ret = (
            spy_joined.get_column('ret').fill_null(0.0).to_numpy()
        )

    # 4. Metadata maps
    cap_map: dict[str, float] = dict(
        zip(
            stocks.get_column('symbol').to_list(),
            stocks.get_column('mkt_cap').to_list(),
        )
    )
    sector_map: dict[str, str] = dict(
        zip(
            stocks.get_column('symbol').to_list(),
            stocks.get_column('g_sector').to_list(),
        )
    )

    # Close prices for momentum/reversal
    close_long = dense_hists.sort('symbol', 'date').select(
        'date', 'symbol', 'close'
    )
    close_wide = close_long.pivot(
        on='symbol',
        index='date',
        values='close',
    ).sort('date')
    close_mat = (
        close_wide.select(syms)
        .fill_null(strategy='forward')
        .fill_null(strategy='backward')
        .to_numpy()
    )

    # Volume for liquidity
    vol_long = dense_hists.sort('symbol', 'date').select(
        'date', 'symbol', 'volume'
    )
    vol_wide = vol_long.pivot(
        on='symbol',
        index='date',
        values='volume',
    ).sort('date')
    vol_mat = vol_wide.select(syms).fill_null(0.0).to_numpy()

    # 5. Compute factor returns at each rebalance
    rebal_dates = list(range(MOM_WINDOW, n_days, REBAL_PERIOD))
    if not rebal_dates or rebal_dates[-1] != n_days - 1:
        rebal_dates.append(n_days - 1)

    # Pre-compute rolling stats for all symbols
    # Assignments will be held constant between rebalances
    style_names = [
        'size',
        'momentum',
        'reversal',
        'beta',
        'resvol',
        'liquidity',
    ]

    # Initialize factor return arrays
    factor_market = spy_ret.copy()
    factor_style = {name: np.zeros(n_days) for name in style_names}

    # Active sectors
    sector_counts: dict[str, int] = {}
    for s in syms:
        sec = sector_map.get(s, '')
        if sec:
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
    active_sectors = sorted(
        s for s, c in sector_counts.items() if c >= MIN_SECTOR_STOCKS
    )
    factor_sector = {sec: np.zeros(n_days) for sec in active_sectors}

    # Build sector membership (static)
    sector_members: dict[str, list[int]] = {
        sec: [] for sec in active_sectors
    }
    for i, s in enumerate(syms):
        sec = sector_map.get(s, '')
        if sec in sector_members:
            sector_members[sec].append(i)

    # Sector returns (equal-weight daily)
    for sec, members in sector_members.items():
        if members:
            factor_sector[sec] = mat[:, members].mean(axis=1)

    # Last exposure snapshot (for the model output)
    last_exposures: dict[str, BarraExposure] = {}

    # Quintile assignments (n_days x n_syms)
    quintile_assignments: dict[str, np.ndarray] = {
        name: np.zeros((n_days, n_syms), dtype=int)
        for name in style_names
    }

    prev_rebal = MOM_WINDOW
    for ri, rebal_t in enumerate(rebal_dates):
        # Compute exposures at rebal_t
        exposures: dict[str, np.ndarray] = {}

        # Size: log(mkt_cap), z-scored
        caps = np.array([cap_map.get(s, 1.0) for s in syms])
        log_caps = np.log(np.maximum(caps, 1.0))
        exposures['size'] = _zscore(log_caps)

        # Momentum: cumret t-250 to t-21
        if rebal_t >= MOM_WINDOW:
            t_start = max(0, rebal_t - MOM_WINDOW)
            t_skip = max(0, rebal_t - MOM_SKIP)
            p_start = close_mat[t_start]
            p_end = close_mat[t_skip]
            with np.errstate(divide='ignore', invalid='ignore'):
                mom_ret = np.where(
                    p_start > 0,
                    p_end / p_start - 1,
                    0.0,
                )
            exposures['momentum'] = _zscore(mom_ret)
        else:
            exposures['momentum'] = np.zeros(n_syms)

        # Reversal: 21d return
        if rebal_t >= REV_WINDOW:
            p_rev_start = close_mat[rebal_t - REV_WINDOW]
            p_rev_end = close_mat[rebal_t]
            with np.errstate(divide='ignore', invalid='ignore'):
                rev_ret = np.where(
                    p_rev_start > 0,
                    p_rev_end / p_rev_start - 1,
                    0.0,
                )
            exposures['reversal'] = _zscore(rev_ret)
        else:
            exposures['reversal'] = np.zeros(n_syms)

        # Beta: 250d rolling cov/var
        beta_start = max(0, rebal_t - BETA_WINDOW)
        spy_slice = spy_ret[beta_start : rebal_t + 1]
        spy_var = np.var(spy_slice)
        betas = np.zeros(n_syms)
        if spy_var > 0:
            for j in range(n_syms):
                stock_slice = mat[beta_start : rebal_t + 1, j]
                betas[j] = (
                    np.cov(stock_slice, spy_slice)[0, 1] / spy_var
                )
        exposures['beta'] = _zscore(betas)

        # Resvol: 90d residual std
        rv_start = max(0, rebal_t - RESVOL_WINDOW)
        spy_rv = spy_ret[rv_start : rebal_t + 1]
        resvols = np.zeros(n_syms)
        for j in range(n_syms):
            stock_rv = mat[rv_start : rebal_t + 1, j]
            resid = stock_rv - betas[j] * spy_rv
            resvols[j] = np.std(resid)
        exposures['resvol'] = _zscore(resvols)

        # Liquidity: log(ADV30 / mkt_cap)
        adv_start = max(0, rebal_t - ADV_WINDOW)
        adv30 = vol_mat[adv_start : rebal_t + 1].mean(axis=0)
        with np.errstate(divide='ignore', invalid='ignore'):
            liq_ratio = np.where(caps > 0, adv30 / caps, 0.0)
            log_liq = np.where(
                liq_ratio > 0,
                np.log(liq_ratio),
                0.0,
            )
        exposures['liquidity'] = _zscore(log_liq)

        # Assign quintiles and compute Q5-Q1 returns
        quintiles: dict[str, np.ndarray] = {}
        for name in style_names:
            q = _assign_quintiles(exposures[name])
            quintiles[name] = q

        # Determine period for factor returns
        period_start = prev_rebal
        period_end = (
            rebal_dates[ri + 1]
            if ri + 1 < len(rebal_dates)
            else n_days
        )

        for name in style_names:
            q = quintiles[name]
            q5_mask = q == 5
            q1_mask = q == 1
            if q5_mask.any() and q1_mask.any():
                for t in range(period_start, period_end):
                    q5_ret = mat[t, q5_mask].mean()
                    q1_ret = mat[t, q1_mask].mean()
                    factor_style[name][t] = q5_ret - q1_ret
            quintile_assignments[name][period_start:period_end] = q

        prev_rebal = period_end

        # Save last exposures
        if ri == len(rebal_dates) - 1:
            for i, s in enumerate(syms):
                last_exposures[s] = BarraExposure(
                    size=float(exposures['size'][i]),
                    momentum=float(exposures['momentum'][i]),
                    reversal=float(exposures['reversal'][i]),
                    beta=float(exposures['beta'][i]),
                    resvol=float(exposures['resvol'][i]),
                    liquidity=float(exposures['liquidity'][i]),
                    sector=sector_map.get(s, ''),
                )

    # 6. Combine into factor_returns DataFrame
    factor_cols: dict[str, list[float]] = {
        'date': dates,
        'market': factor_market.tolist(),
    }
    for name in style_names:
        factor_cols[name] = factor_style[name].tolist()
    for sec in active_sectors:
        col = f'sector_{sec}'.replace(' ', '_')
        factor_cols[col] = factor_sector[sec].tolist()

    factor_df = pl.DataFrame(factor_cols).slice(1)

    n_factors = 1 + len(style_names) + len(active_sectors)

    model = BarraModel(
        factor_returns=factor_df,
        exposures=last_exposures,
        sectors=active_sectors,
        n_stocks=n_syms,
        n_factors=n_factors,
    )

    _log_summary(model, sector_members)
    return model


def get_prior() -> FactorModel:
    """Return configured skfolio FactorModel prior."""
    return FactorModel(
        factor_prior_estimator=EmpiricalPrior(),
        residual_variance=True,
    )


def get_factor_returns(
    model: BarraModel,
    dates: list[str],
) -> pl.DataFrame:
    """Slice factor returns to match a date range.

    Returns DataFrame with factor columns (no date)
    aligned to the provided dates.
    """
    fr = model.factor_returns
    matched = fr.filter(pl.col('date').is_in(dates))
    return matched.drop('date')


def _sector_key(name: str) -> str:
    """Sector name → skfolio group key (no spaces)."""
    return name.replace(' ', '_')


def build_sector_constraints(
    model: BarraModel,
    columns: list[str],
    target_sector: str,
    max_budget: float,
    floor_pct: float = SECTOR_FLOOR_PCT,
    cap_pct: float = SECTOR_CAP_PCT,
) -> tuple[dict[str, list[str]], list[str]] | None:
    """Build skfolio groups + linear_constraints.

    Returns None if no symbols have sectors (ETF-only).
    """
    groups: dict[str, list[str]] = {}
    sectors_present: set[str] = set()

    for sym in columns:
        exp = model.exposures.get(sym)
        sec = exp.sector if exp else ''
        if not sec:
            continue
        key = _sector_key(sec)
        groups[sym] = [key]
        sectors_present.add(sec)

    if not groups:
        return None

    constraints: list[str] = []
    target_key = _sector_key(target_sector)

    # Floor: target's sector must get >= floor_pct * budget
    if target_sector and target_sector in sectors_present:
        floor = floor_pct * max_budget
        constraints.append(f'{target_key} >= {floor}')

    # Cap: each off-sector <= cap_pct * budget
    cap = cap_pct * max_budget
    for sec in sorted(sectors_present):
        if sec == target_sector:
            continue
        constraints.append(f'{_sector_key(sec)} <= {cap}')

    return groups, constraints


def _zscore(arr: np.ndarray) -> np.ndarray:
    """Z-score an array, handling zero std."""
    std = np.std(arr)
    if std < 1e-10:
        return np.zeros_like(arr)
    return (arr - np.mean(arr)) / std


def _assign_quintiles(
    values: np.ndarray,
) -> np.ndarray:
    """Assign quintiles 1-5 based on values."""
    n = len(values)
    ranked = np.argsort(np.argsort(values))
    quintiles = np.minimum(ranked * N_QUINTILES // n + 1, N_QUINTILES)
    return quintiles


def _log_summary(
    model: BarraModel,
    sector_members: dict[str, list[int]],
) -> None:
    sector_str = ' '.join(
        f'{s}({len(m)})' for s, m in sector_members.items() if m
    )
    log.cyan(
        f'barra: {model.n_stocks} stocks, '
        f'{model.n_factors} factors, '
        f'{len(model.factor_returns)} dates'
    )
    log.cyan(f'barra sectors: {sector_str}')

    # Factor correlation matrix
    fr = model.factor_returns.drop('date')
    cols = fr.columns
    if len(cols) <= 20:
        corr_mat = fr.to_numpy()
        corr = np.corrcoef(corr_mat, rowvar=False)
        corr_df = pl.DataFrame(
            {
                'factor': cols,
                **{
                    cols[i]: np.round(corr[:, i], 2)
                    for i in range(len(cols))
                },
            }
        )
        log.cyan(f'barra factor correlations:\n{corr_df}')
