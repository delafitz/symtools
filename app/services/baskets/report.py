"""Opt report: human-readable summary of a symbol build."""

from __future__ import annotations

from datetime import date

import polars as pl

from app.models.baskets import Basket
from app.services.baskets.barra import BarraModel
from app.utils.groups import get_all_etf_symbols


def _section(lines: list[str], title: str) -> None:
    lines.append(f'--- {title} ---')


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
    _section(lines, 'Barra Exposures')
    lines.append(
        f'  size:     {exp.size:+.3f}   '
        f'momentum: {exp.momentum:+.3f}'
    )
    lines.append(
        f'  reversal: {exp.reversal:+.3f}   '
        f'beta:     {exp.beta:+.3f}'
    )
    lines.append(
        f'  resvol:   {exp.resvol:+.3f}   '
        f'liquidity:{exp.liquidity:+.3f}'
    )
    sname = (
        barra_model.sector_names.get(exp.sector, str(exp.sector))
        if exp.sector
        else 'none'
    )
    lines.append(f'  sector:   {exp.sector} ({sname})')
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
    rankings: dict[str, list[str]],
) -> None:
    _section(lines, 'Candidate Pools')
    for name, opt in opts.items():
        cands = opt['population'] - 1
        days = opt['days']
        d0 = opt['date_start']
        d1 = opt['date_end']
        lines.append(
            f'  {name:<10} {cands:>3} cands, '
            f'{days} bars  ({d0} to {d1})'
        )
        returns = scenarios.get(name)
        if returns is None:
            continue
        # Compute corr for all candidates (used for display values)
        corr_map = dict(_candidate_corrs(returns))
        pre_ranked = rankings.get(name)
        if pre_ranked is not None:
            # Use pre-existing Barra composite ranking, show corr values
            top = [
                (s, corr_map[s])
                for s in pre_ranked[:10]
                if s in corr_map
            ]
        else:
            # No pre-ranking — sort by corr
            top = sorted(
                corr_map.items(), key=lambda x: x[1], reverse=True
            )[:10]
        if top:
            row = '  '.join(f'{s}:{c:+.2f}' for s, c in top)
            lines.append(f'    top: {row}')
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


def _add_opt_results(
    lines: list[str],
    opts: dict,
) -> None:
    _section(lines, 'Opt Results')
    for name, opt in opts.items():
        weights: pl.DataFrame = opt['weights']
        cands = opt['population'] - 1
        if weights.is_empty():
            lines.append(f'  [{name:<10}] no result')
            continue
        sym_col = weights.columns[0]
        wt_pairs = list(zip(
            weights[sym_col].to_list(),
            weights['weight'].to_list(),
        ))
        wt_str = '  '.join(
            f'{s}:{w:.1%}' for s, w in wt_pairs
        )
        lines.append(
            f'  [{name:<10}] {cands} cands -> {wt_str}'
        )
    lines.append('')


def _add_stats(
    lines: list[str],
    baskets: dict[str, Basket],
) -> None:
    if not baskets:
        return
    _section(lines, 'Basket Stats')
    hdr = (
        f'  {"scenario":<12} '
        f'{"corr":>6} {"beta":>7} {"vol_red":>8}'
    )
    lines.append(hdr)
    for name, b in baskets.items():
        s = b.stats
        lines.append(
            f'  {name:<12} '
            f'{s.corr:>6.3f} '
            f'{s.beta:>7.3f} '
            f'{s.vol_reduce:>8.3f}'
        )
    lines.append('')


def _add_summary(
    lines: list[str],
    baskets: dict[str, Basket],
    opts: dict,
) -> None:
    _section(lines, 'Summary')
    # Warnings
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
    # Best by corr
    best_name, best = max(
        baskets.items(), key=lambda x: x[1].stats.corr
    )
    lines.append(
        f'  best:   {best_name}  '
        f'corr={best.stats.corr:.3f}  '
        f'vol_red={best.stats.vol_reduce:.3f}'
    )
    # ETF anchor from combined
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
    rankings: dict[str, list[str]],
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
    _add_opt_results(lines, opts)
    _add_stats(lines, baskets)
    _add_summary(lines, baskets, opts)
    return '\n'.join(lines)
