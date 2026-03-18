from __future__ import annotations

import polars as pl

from app.services.prices import (
    HIST_TEMPLATE_DEFAULT,
    HIST_TEMPLATES,
)
from app.services.baskets.barra import BarraModel
from app.services.baskets.config import (
    MIN_SINGLE_MKT_CAP,
    MIN_SINGLE_REL_CAP,
)
from app.services.baskets.factors import EmpModel
from app.utils.market import slice_hist
from app.utils.groups import (
    COMBINED,
    FACTORS,
    INDICES,
    SCENARIOS,
    SINGLES,
    TYPE_ETF_FACTOR,
    TYPE_ETF_INDEX,
    TYPE_STOCK,
)
from app.utils.logger import get_logger

log = get_logger(__name__)

# ETF group returns cache: (group, template, scale) -> returns
# Indices/factors returns are identical for all target symbols
_etf_returns_cache: dict[tuple[str, str, int], pl.DataFrame] = {}

# Minimum history length for optimizer input
MIN_HIST = 250
# Max singles to include after pre-screen
MAX_SINGLES = 30
# Pre-screen multiplier (factor distance pool before corr)
PRESCREEN_MULT = 3
# Composite score weights
FACTOR_WEIGHT = 0.7
CORR_WEIGHT = 0.3
# Barra distance bonus for same-sector candidates
SECTOR_BONUS = 0.5


def get_returns(hists: pl.DataFrame) -> pl.DataFrame:
    """Calculate returns from wide-format hist.

    Input: date, [timestamp], sym1, sym2, ...
    Output: date, sym1, sym2, ... (as pct changes)
    """
    cols = [
        c
        for c in hists.columns
        if c == 'date' or c not in ('iso', 'timestamp', 'iso_right')
    ]
    return (
        hists.select(cols)
        .with_columns(pl.all().exclude('date').pct_change())
        .drop_nulls()
    )


def _build_liquid_set(
    symbol: str,
    refs: pl.DataFrame | None,
) -> set[str] | None:
    """Symbols meeting relative + absolute mkt_cap floor.

    effective_floor = max(MIN_SINGLE_MKT_CAP,
                          MIN_SINGLE_REL_CAP * target_mkt_cap)

    Returns None if refs not available.
    """
    if refs is None or 'mkt_cap' not in refs.columns:
        return None

    target_cap = 0.0
    tc_row = refs.filter(pl.col('symbol') == symbol).select('mkt_cap')
    if not tc_row.is_empty():
        target_cap = float(tc_row.item() or 0.0)

    floor = max(MIN_SINGLE_MKT_CAP, MIN_SINGLE_REL_CAP * target_cap)

    liquid = set(
        refs.filter(pl.col('mkt_cap') >= floor)
        .get_column('symbol')
        .to_list()
    )
    log.info(
        f'singles: liquidity floor '
        f'${floor / 1e9:.1f}B '
        f'({len(liquid)} qualified)'
    )
    return liquid


def _filter_hists_wide(
    hists: pl.DataFrame,
    refs: pl.DataFrame,
    template: str,
    types: list[str],
    unit: str,
    scale: int,
) -> pl.DataFrame | None:
    """Filter hists by type and pivot to wide."""
    type_symbols = (
        refs.filter(pl.col('type').is_in(types))
        .get_column('symbol')
        .to_list()
    )
    if not type_symbols:
        return None

    filtered = hists.filter(
        (pl.col('symbol').is_in(type_symbols))
        & (pl.col('template') == template)
    )
    if filtered.is_empty():
        return None

    filtered = slice_hist(filtered, unit, scale, for_analytics=True)
    if filtered.is_empty():
        return None

    is_intraday = template in ('D', 'W')
    index_cols = ['date', 'timestamp'] if is_intraday else ['date']

    if not all(c in filtered.columns for c in index_cols + ['close']):
        return None

    wide = filtered.pivot(
        on='symbol',
        index=index_cols,
        values='close',
        aggregate_function='last',
    )

    return wide if not wide.is_empty() else None


def _refine_by_correlation(
    prescreen: list[tuple[str, float]],
    hists: pl.DataFrame,
    target_returns: pl.DataFrame,
    template: str,
    max_count: int,
) -> list[str]:
    """Stage 2: correlation refinement + composite score.

    Takes pre-screened (symbol, distance) pairs sorted by
    distance, computes correlation with target returns,
    and returns top max_count by composite score.
    """
    prescreen_syms = [s for s, _ in prescreen]
    factor_dists = {s: d for s, d in prescreen}

    min_fd = prescreen[0][1]
    max_fd = prescreen[-1][1]
    fd_range = max_fd - min_fd

    cand_hists = hists.filter(
        (pl.col('symbol').is_in(prescreen_syms))
        & (pl.col('template') == template)
    )
    if cand_hists.is_empty():
        return prescreen_syms[:max_count]

    wide = cand_hists.pivot(
        on='symbol',
        index='date',
        values='close',
        aggregate_function='last',
    )
    wide = wide.with_columns(
        pl.all().exclude('date').pct_change()
    ).slice(1)

    joined = target_returns.select(['date', 'target']).join(
        wide, on='date', how='inner'
    )

    avail = [s for s in prescreen_syms if s in joined.columns]
    corr_row = joined.select(
        [pl.corr('target', s).alias(s) for s in avail]
    )
    corrs: dict[str, float] = {}
    if not corr_row.is_empty():
        for s in avail:
            c = corr_row.get_column(s).item()
            if c is not None:
                corrs[s] = c

    scored = []
    for s in prescreen_syms:
        fd = factor_dists[s]
        norm_fd = (fd - min_fd) / fd_range if fd_range > 0 else 0.0
        corr = corrs.get(s)
        if corr is not None:
            score = FACTOR_WEIGHT * norm_fd + CORR_WEIGHT * (
                1.0 - corr
            )
        else:
            score = norm_fd
        scored.append((s, score))

    scored.sort(key=lambda x: x[1])
    return [s for s, _ in scored[:max_count]]


def _get_emp_candidates(
    symbol: str,
    emp_model: EmpModel,
    hists: pl.DataFrame,
    target_returns: pl.DataFrame,
    template: str,
    refs: pl.DataFrame | None = None,
    max_count: int = MAX_SINGLES,
) -> list[str]:
    """Pre-screen by factor distance, refine with corr.

    Stage 1: Rank liquid stocks by factor loading distance,
    take top PRESCREEN_MULT * max_count.
    Stage 2: Correlation refinement via composite score.
    """
    target_smb = emp_model.smb.exposures.get(symbol)
    target_tv = emp_model.turnover.exposures.get(symbol)
    if not target_smb or not target_tv:
        return []

    liquid = _build_liquid_set(symbol, refs)

    prescreen_count = max_count * PRESCREEN_MULT
    all_dists: list[tuple[str, float]] = []
    for s in emp_model.smb.exposures:
        if s == symbol:
            continue
        if liquid is not None and s not in liquid:
            continue
        smb_exp = emp_model.smb.exposures[s]
        tv_exp = emp_model.turnover.exposures.get(s)
        if not tv_exp:
            continue
        dist = abs(smb_exp.loading - target_smb.loading) + abs(
            tv_exp.loading - target_tv.loading
        )
        all_dists.append((s, dist))

    all_dists.sort(key=lambda x: x[1])
    prescreen = all_dists[:prescreen_count]
    if not prescreen:
        return []

    return _refine_by_correlation(
        prescreen, hists, target_returns, template, max_count
    )


def _get_barra_candidates(
    symbol: str,
    barra_model: BarraModel,
    hists: pl.DataFrame,
    target_returns: pl.DataFrame,
    template: str,
    refs: pl.DataFrame | None = None,
    max_count: int = MAX_SINGLES,
) -> list[str]:
    """Pre-screen by Barra exposure distance.

    Stage 1: L1 norm over 6 z-scored style factors,
    restricted to liquid stocks only.
    Same-sector bonus subtracted from distance.
    Stage 2: Correlation refinement via composite score.
    """
    target_exp = barra_model.exposures.get(symbol)
    if not target_exp:
        return []

    target_sector = target_exp.sector
    liquid = _build_liquid_set(symbol, refs)

    prescreen_count = max_count * PRESCREEN_MULT
    all_dists: list[tuple[str, float]] = []
    for s, exp in barra_model.exposures.items():
        if s == symbol:
            continue
        if liquid is not None and s not in liquid:
            continue
        dist = (
            abs(exp.size - target_exp.size)
            + abs(exp.momentum - target_exp.momentum)
            + abs(exp.reversal - target_exp.reversal)
            + abs(exp.beta - target_exp.beta)
            + abs(exp.resvol - target_exp.resvol)
            + abs(exp.liquidity - target_exp.liquidity)
        )
        if (
            target_sector
            and exp.sector
            and exp.sector == target_sector
        ):
            dist -= SECTOR_BONUS
        all_dists.append((s, dist))

    all_dists.sort(key=lambda x: x[1])
    prescreen = all_dists[:prescreen_count]
    if not prescreen:
        return []

    return _refine_by_correlation(
        prescreen, hists, target_returns, template, max_count
    )


def _build_singles_wide(
    hists: pl.DataFrame,
    refs: pl.DataFrame,
    template: str,
    unit: str,
    scale: int,
    exclude_symbol: str,
    include_symbols: list[str] | None = None,
) -> pl.DataFrame | None:
    """Build wide-format hist for stock symbols.

    If include_symbols is provided (pre-screened by model),
    use those directly. Otherwise filter all stocks with an
    absolute mkt_cap floor.
    """
    if include_symbols is not None:
        stock_symbols = [
            s for s in include_symbols if s != exclude_symbol
        ]
    else:
        # No model pre-screen; apply absolute floor directly
        stock_filter = (
            pl.col('type') == TYPE_STOCK
        ) & (pl.col('symbol') != exclude_symbol)
        if MIN_SINGLE_MKT_CAP > 0 and 'mkt_cap' in refs.columns:
            stock_filter = stock_filter & (
                pl.col('mkt_cap') >= MIN_SINGLE_MKT_CAP
            )
        stock_symbols = (
            refs.filter(stock_filter).get_column('symbol').to_list()
        )

    if not stock_symbols:
        return None

    filtered = hists.filter(
        (pl.col('symbol').is_in(stock_symbols))
        & (pl.col('template') == template)
    )
    if filtered.is_empty():
        return None

    filtered = slice_hist(filtered, unit, scale, for_analytics=True)
    if filtered.is_empty():
        return None

    is_intraday = template in ('D', 'W')
    index_cols = ['date', 'timestamp'] if is_intraday else ['date']

    # Filter out sparse symbols
    symbol_counts = filtered.group_by('symbol').len()
    valid_symbols = (
        symbol_counts.filter(pl.col('len') >= MIN_HIST)
        .get_column('symbol')
        .to_list()
    )

    excluded_count = len(stock_symbols) - len(valid_symbols)
    if excluded_count > 0:
        log.info(
            f'singles: excluded {excluded_count} '
            f'symbols < {MIN_HIST} bars'
        )

    if not valid_symbols:
        return None

    filtered = filtered.filter(pl.col('symbol').is_in(valid_symbols))

    wide = filtered.pivot(
        on='symbol',
        index=index_cols,
        values='close',
        aggregate_function='last',
    )

    return wide if not wide.is_empty() else None


def _best_etf_col(
    etf_groups: list[pl.DataFrame],
    target_returns: pl.DataFrame,
) -> str | None:
    """ETF column most correlated (abs) with target returns."""
    tr = target_returns.select(['date', 'target'])
    corrs: dict[str, float] = {}
    for gr in etf_groups:
        etf_cols = [c for c in gr.columns if c != 'date']
        joined = tr.join(gr, on='date', how='inner').drop_nulls()
        if joined.is_empty():
            continue
        for col in etf_cols:
            if col not in joined.columns:
                continue
            c = joined.select(pl.corr('target', col)).item()
            if c is not None:
                corrs[col] = abs(c)
    if not corrs:
        return None
    return max(corrs, key=corrs.__getitem__)


def get_scenarios(
    symbol: str,
    hist: pl.DataFrame,
    refs: pl.DataFrame,
    hists: pl.DataFrame,
    emp_model: EmpModel | None = None,
    barra_model: BarraModel | None = None,
    template: str = HIST_TEMPLATE_DEFAULT,
    scale: int | None = None,
) -> dict[str, pl.DataFrame]:
    """Build scenario return matrices for optimization.

    Each scenario is an independent candidate pool.
    Singles pre-screened by Barra exposures (preferred)
    or factor quintile proximity when available.

    Combined = single best-correlated ETF + singles.
    """
    _, _, unit, default_scale, _ = HIST_TEMPLATES[template]
    if scale is None:
        scale = default_scale

    symbol_hist = (
        slice_hist(hist, unit, scale, for_analytics=True)
        .select(['date', 'close'])
        .rename({'close': 'target'})
    )

    if len(symbol_hist) < MIN_HIST:
        log.warning(
            f'scenarios: {symbol} has '
            f'{len(symbol_hist)} bars '
            f'< {MIN_HIST} required'
        )
        return {}

    target_returns = get_returns(symbol_hist)

    # Build returns for each group
    group_returns: dict[str, pl.DataFrame] = {}

    # Indices (SPY, QQQ, IWM) — cached across symbols
    idx_key = (INDICES, template, scale)
    if idx_key in _etf_returns_cache:
        group_returns[INDICES] = _etf_returns_cache[idx_key]
    else:
        indices_wide = _filter_hists_wide(
            hists,
            refs,
            template,
            [TYPE_ETF_INDEX],
            unit,
            scale,
        )
        if indices_wide is not None and len(indices_wide) >= MIN_HIST:
            group_returns[INDICES] = get_returns(indices_wide)
            _etf_returns_cache[idx_key] = group_returns[INDICES]
        else:
            log.warning(f'scenarios: {INDICES} unavailable')

    # Factors (sector/factor ETFs) — cached across symbols
    fac_key = (FACTORS, template, scale)
    if fac_key in _etf_returns_cache:
        group_returns[FACTORS] = _etf_returns_cache[fac_key]
    else:
        factors_wide = _filter_hists_wide(
            hists,
            refs,
            template,
            [TYPE_ETF_FACTOR],
            unit,
            scale,
        )
        if factors_wide is not None and len(factors_wide) >= MIN_HIST:
            group_returns[FACTORS] = get_returns(factors_wide)
            _etf_returns_cache[fac_key] = group_returns[FACTORS]
        else:
            log.warning(f'scenarios: {FACTORS} unavailable')

    # Singles (pre-screened by Barra or factor model)
    include = None
    if barra_model:
        include = _get_barra_candidates(
            symbol,
            barra_model,
            hists,
            target_returns,
            template,
            refs=refs,
        )
        log.info(
            f'scenarios: {SINGLES} barra pre-screen '
            f'-> {len(include)} candidates'
        )
    elif emp_model:
        include = _get_emp_candidates(
            symbol,
            emp_model,
            hists,
            target_returns,
            template,
            refs=refs,
        )
        log.info(
            f'scenarios: {SINGLES} factor pre-screen '
            f'-> {len(include)} candidates'
        )

    singles_wide = _build_singles_wide(
        hists,
        refs,
        template,
        unit,
        scale,
        exclude_symbol=symbol,
        include_symbols=include,
    )
    if singles_wide is not None and len(singles_wide) >= MIN_HIST:
        n_syms = singles_wide.width - 1
        group_returns[SINGLES] = get_returns(singles_wide)
        log.info(f'scenarios: {SINGLES} {n_syms} symbols')

    # Log available groups
    avail = {k: len(v) for k, v in group_returns.items()}
    log.info(f'scenarios: {symbol} {template} groups={avail}')

    # Build indices / factors / singles scenarios
    scenarios: dict[str, pl.DataFrame] = {}
    for name, (label, groups) in SCENARIOS.items():
        missing = [g for g in groups if g not in group_returns]
        if missing:
            log.debug(f'scenarios: {name} missing {missing}')
            continue

        returns_list = [group_returns[g] for g in groups]
        returns_list.append(target_returns)

        combined = pl.concat(
            returns_list, how='align_left'
        ).drop_nulls()

        if len(combined) > MIN_HIST:
            combined = combined.tail(MIN_HIST)

        if len(combined) >= MIN_HIST:
            scenarios[name] = combined
            log.info(
                f'scenarios: {name} ({label}) '
                f'cols={combined.width} '
                f'rows={combined.height}'
            )
        else:
            log.warning(
                f'scenarios: {name} '
                f'{len(combined)} rows '
                f'< {MIN_HIST} after join'
            )

    # Combined: single best-correlated ETF + singles
    etf_avail = [
        group_returns[g]
        for g in (INDICES, FACTORS)
        if g in group_returns
    ]
    if etf_avail and SINGLES in group_returns:
        best = _best_etf_col(etf_avail, target_returns)
        if best:
            etf_col = next(
                (
                    gr.select(['date', best])
                    for gr in etf_avail
                    if best in gr.columns
                ),
                None,
            )
            if etf_col is not None:
                returns_list = [
                    etf_col,
                    group_returns[SINGLES],
                    target_returns,
                ]
                comb = pl.concat(
                    returns_list, how='align_left'
                ).drop_nulls()
                if len(comb) > MIN_HIST:
                    comb = comb.tail(MIN_HIST)
                if len(comb) >= MIN_HIST:
                    scenarios[COMBINED] = comb
                    log.info(
                        f'scenarios: {COMBINED} '
                        f'etf={best} '
                        f'cols={comb.width} '
                        f'rows={comb.height}'
                    )
                else:
                    log.warning(
                        f'scenarios: {COMBINED} '
                        f'{len(comb)} rows '
                        f'< {MIN_HIST} after join'
                    )

    return scenarios
