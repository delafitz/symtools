"""Opt report: human-readable summary of a symbol build."""

from __future__ import annotations

from datetime import date

import polars as pl

from app.models.baskets import Basket
from app.services.baskets.barra import BarraModel
from app.utils.groups import get_all_etf_symbols

_CFG = dict(tbl_rows=-1, tbl_width_chars=100, float_precision=3)


def _section(lines: list[str], title: str) -> None:
    lines.append(f'--- {title} ---')


def _df_str(df: pl.DataFrame) -> str:
    """Format DataFrame as string, drop the shape header line."""
    with pl.Config(**_CFG):
        raw = str(df)
    return '\n'.join(
        ln for ln in raw.splitlines() if not ln.startswith('shape:')
    )


def _add_barra(
    lines: list[str],
    symbol: str,
    barra_model: BarraModel | None,
) -> None:
    if not barra_model:
        return
    exp = barra_model.exposures.get(symbol)
    if not exp:
        return
    sname = (
        barra_model.sector_names.get(exp.sector, str(exp.sector))
        if exp.sector
        else 'none'
    )
    _section(lines, f'Barra Exposures  sector={exp.sector} ({sname})')
    df = pl.DataFrame({
        'factor': [
            'size', 'momentum', 'reversal',
            'beta', 'resvol', 'liquidity',
        ],
        'value': [
            exp.size, exp.momentum, exp.reversal,
            exp.beta, exp.resvol, exp.liquidity,
        ],
    })
    lines.append(_df_str(df))
    lines.append('')


def _candidate_corrs(
    returns: pl.DataFrame,
) -> list[tuple[str, float]]:
    """Correlation of each candidate with target, sorted desc."""
    candidates = [
        c for c in returns.columns if c not in ('date', 'target')
    ]
    corrs: list[tuple[str, float]] = []
    for c in candidates:
        val = returns.select(pl.corr(c, 'target')).item()
        if val is not None:
            corrs.append((c, val))
    corrs.sort(key=lambda x: x[1], reverse=True)
    return corrs


def _add_pools(
    lines: list[str],
    opts: dict,
    scenarios: dict[str, pl.DataFrame],
    rankings: dict[str, list[tuple[str, float, float]]],
) -> None:
    _section(lines, 'Candidate Pools + Results')
    for name, opt in opts.items():
        cands = opt['population'] - 1
        days = opt['days']
        d0 = opt['date_start']
        d1 = opt['date_end']
        pre_ranked = rankings.get(name)
        label = 'barra ranked' if pre_ranked else 'corr ranked'
        lines.append(
            f'  [{name}] {cands} cands, '
            f'{days} bars  ({d0} to {d1})  [{label}]'
        )
        returns = scenarios.get(name)
        if returns is None:
            continue
        if pre_ranked is not None:
            top = pre_ranked[:10]
            pool_df = pl.DataFrame({
                'symbol': [s for s, _, _ in top],
                'fd': [fd for _, fd, _ in top],
                'corr': [c for _, _, c in top],
            })
        else:
            top_corr = _candidate_corrs(returns)[:10]
            pool_df = pl.DataFrame({
                'symbol': [s for s, _ in top_corr],
                'corr': [c for _, c in top_corr],
            })
        weights: pl.DataFrame = opt['weights']
        if not weights.is_empty():
            sym_col = weights.columns[0]
            wt_df = pl.DataFrame({
                'symbol': weights[sym_col].to_list(),
                'weight': [
                    f'{w:.1%}'
                    for w in weights['weight'].to_list()
                ],
            })
            pool_df = pool_df.join(wt_df, on='symbol', how='left')
        lines.append(_df_str(pool_df))
    lines.append('')


def _add_constraints(
    lines: list[str],
    sc_lin: dict[str, list[str] | None],
    barra_model: BarraModel | None,
) -> None:
    active = {k: v for k, v in sc_lin.items() if v}
    if not active:
        return
    _section(lines, 'Sector Constraints')
    for name, lcs in active.items():
        lines.append(f'  [{name}]')
        for lc in lcs:
            parts = lc.split()
            if (
                barra_model
                and parts
                and parts[0].startswith('sector_')
            ):
                try:
                    sid = int(parts[0].split('_')[1])
                    sname = barra_model.sector_names.get(
                        sid, str(sid)
                    )
                    lines.append(f'    {lc}  ({sname})')
                except (ValueError, IndexError):
                    lines.append(f'    {lc}')
            else:
                lines.append(f'    {lc}')
    lines.append('')



def _add_stats(
    lines: list[str],
    baskets: dict[str, Basket],
) -> None:
    if not baskets:
        return
    _section(lines, 'Basket Stats')
    df = pl.DataFrame({
        'scenario': list(baskets.keys()),
        'corr': [b.stats.corr for b in baskets.values()],
        'beta': [b.stats.beta for b in baskets.values()],
        'vol_red': [b.stats.vol_reduce for b in baskets.values()],
    })
    lines.append(_df_str(df))
    lines.append('')


def _add_summary(
    lines: list[str],
    baskets: dict[str, Basket],
    opts: dict,
) -> None:
    _section(lines, 'Summary')
    for name, opt in opts.items():
        if opt['weights'].is_empty():
            lines.append(f'  WARN: {name} produced no weights')
    for name, b in baskets.items():
        if b.stats.corr < 0.5:
            lines.append(
                f'  WARN: {name} low corr={b.stats.corr:.3f}'
            )
    if not baskets:
        lines.append('  no baskets produced')
        return
    best_name, best = max(
        baskets.items(), key=lambda x: x[1].stats.corr
    )
    lines.append(
        f'  best:   {best_name}  '
        f'corr={best.stats.corr:.3f}  '
        f'vol_red={best.stats.vol_reduce:.3f}'
    )
    if 'combined' in baskets:
        etf_syms = set(get_all_etf_symbols())
        comb_wts = baskets['combined'].weights
        etf_wts = {
            s: w for s, w in comb_wts.items() if s in etf_syms
        }
        if etf_wts:
            anchor_sym, anchor_wt = max(
                etf_wts.items(), key=lambda x: x[1]
            )
            lines.append(
                f'  anchor: {anchor_sym} '
                f'(combined, weight={anchor_wt:.1%})'
            )


def build_report(
    symbol: str,
    barra_model: BarraModel | None,
    scenarios: dict[str, pl.DataFrame],
    rankings: dict[str, list[tuple[str, float, float]]],
    opts: dict,
    baskets: dict[str, Basket],
    sc_lin: dict[str, list[str] | None],
) -> str:
    """Build human-readable opt report for a symbol."""
    lines: list[str] = []
    today = date.today().isoformat()
    lines.append(f'=== {symbol.upper()} OPT REPORT {today} ===')
    lines.append('')
    _add_barra(lines, symbol, barra_model)
    _add_pools(lines, opts, scenarios, rankings)
    _add_constraints(lines, sc_lin, barra_model)
    _add_stats(lines, baskets)
    _add_summary(lines, baskets, opts)
    return '\n'.join(lines)
