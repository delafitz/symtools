"""Sweep position-sizing parameters and compare portfolio
return vs scale.

Re-scales an existing `portfolio_trades.parquet` for each
(pct_adv, floor, cap) combination — dollar fields scale
linearly with notional, percent fields are invariant — then
re-aggregates monthly to compute average GMV, peak GMV, total
P&L, monthly Sharpe, and annualized return on gross.

Usage:
    uv run python tools/portfolio_sizing_sweep.py
    uv run python tools/portfolio_sizing_sweep.py --window 20
    uv run python tools/portfolio_sizing_sweep.py --no-save

Always writes `data/portfolio_sizing_sweep.{stamp}.parquet`
unless `--no-save` is passed.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from app.services.portfolio.rolling import (
    expand_to_daily,
    rolling_monthly,
)
from app.utils.logger import get_logger

log = get_logger(__name__)

# (pct_adv, floor_usd_M, cap_usd_M)
DEFAULT_COMBOS: list[tuple[float, int, int]] = [
    (0.10, 5, 50),
    (0.15, 10, 50),
    (0.15, 10, 100),
    (0.20, 5, 100),
    (0.20, 10, 100),   # current
    (0.20, 10, 50),
    (0.25, 10, 100),
    (0.25, 20, 100),
    (0.30, 20, 100),   # legacy
    (0.30, 20, 150),
    (0.40, 20, 200),
]

DOLLAR_COLS = [
    'notional_usd',
    'shares',
    'hedge_notional_usd',
    'target_pnl_usd',
    'hedge_pnl_usd',
    'pnl_unhedged_usd',
    'pnl_hedged_usd',
    'expected_pnl_unhedged_usd',
    'expected_pnl_hedged_usd',
    'var99_unhedged_usd',
    'var99_hedged_usd',
]


def rescale(
    positions: pl.DataFrame,
    pct_adv: float, floor_usd: float, cap_usd: float,
) -> pl.DataFrame:
    """Re-size each position with new (pct, floor, cap), then
    scale all dollar columns by new_notional / old_notional."""
    raw = pct_adv * pl.col('adv_usd_30d').fill_null(0)
    new_notional = (
        raw.clip(lower_bound=floor_usd, upper_bound=cap_usd)
    )
    df = positions.with_columns(new_notional.alias('_new_n'))
    df = df.with_columns(
        (pl.col('_new_n') / pl.col('notional_usd'))
        .alias('_scale')
    )
    for c in DOLLAR_COLS:
        if c not in df.columns:
            continue
        df = df.with_columns(
            (pl.col(c) * pl.col('_scale')).alias(c)
        )
    return df.drop(['_new_n', '_scale'])


def stats(monthly: pl.DataFrame, window_d: int) -> dict:
    m = monthly.filter(pl.col('window_d') == window_d)
    if m.is_empty():
        return {}
    rh = m['ret_hedged'].drop_nulls().to_list()
    mean_h = sum(rh) / len(rh)
    std_h = (
        sum((x - mean_h) ** 2 for x in rh) / max(len(rh) - 1, 1)
    ) ** 0.5
    return {
        'avg_gmv': float(m['avg_daily_gmv'].mean()),
        'peak_gmv': float(m['peak_daily_gmv'].max()),
        'avg_var_hed': float(m['avg_daily_var_hedged'].mean()),
        'total_pnl_hed': float(m['pnl_hedged'].sum()),
        'mean_monthly_ret': mean_h,
        'std_monthly_ret': std_h,
        'sharpe_annual': (
            (mean_h / std_h) * math.sqrt(12) if std_h > 0 else None
        ),
        'annualized_ret': mean_h * 12,
    }


def fmt_money(x: float | None) -> str:
    if x is None:
        return ''
    a = abs(x)
    if a >= 1e9:
        return f'{x/1e9:+.2f}B'
    if a >= 1e6:
        return f'{x/1e6:+.0f}M'
    if a >= 1e3:
        return f'{x/1e3:+.0f}K'
    return f'{x:+.0f}'


def main() -> None:
    args = sys.argv[1:]
    window = 20
    save = True
    while args:
        flag = args.pop(0)
        if flag == '--window':
            window = int(args.pop(0))
        elif flag == '--no-save':
            save = False
        else:
            print(f'unknown arg: {flag}', file=sys.stderr)
            sys.exit(1)

    pos_path = sorted(
        Path('data').glob('portfolio_trades.*.parquet')
    )[-1]
    hists_path = sorted(
        Path('data').glob('hists.*.parquet')
    )[-1]
    log.info(f'reading {pos_path.name}')
    positions = pl.read_parquet(pos_path)
    hists = pl.read_parquet(hists_path)

    print(
        f'\n## Sizing sweep (window={window}d, '
        f'rescaled from {pos_path.name})\n'
    )
    hdr = (
        f'{"pct":>5} {"floor":>7} {"cap":>7} '
        f'{"avg_size":>9} {"avg_GMV":>10} {"peak_GMV":>10} '
        f'{"avg_VaR_h":>10} {"V/G":>6} '
        f'{"PnL_hed":>10} {"mo_ret":>8} {"ann_ret":>8} '
        f'{"Sharpe":>7}'
    )
    print(hdr)
    print('-' * len(hdr))

    rows: list[dict] = []
    for pct, fl, cap in DEFAULT_COMBOS:
        rs = rescale(positions, pct, fl * 1e6, cap * 1e6)
        daily = expand_to_daily(rs, hists)
        monthly = rolling_monthly(daily)
        s = stats(monthly, window)
        if not s:
            continue
        avg_size = (
            rs.filter(pl.col('window_d') == window)
            ['notional_usd'].mean()
        )
        v_g = (
            s['avg_var_hed'] / s['avg_gmv']
            if s['avg_gmv'] else 0
        )
        row = {
            'pct_adv': pct,
            'floor_M': fl,
            'cap_M': cap,
            'avg_size': avg_size,
            **s,
            'var_pct_gmv': v_g,
        }
        rows.append(row)
        print(
            f'{pct:>5.2f} ${fl}M{"":<3} ${cap}M{"":<2} '
            f'{fmt_money(avg_size):>9} '
            f'{fmt_money(s["avg_gmv"]):>10} '
            f'{fmt_money(s["peak_gmv"]):>10} '
            f'{fmt_money(s["avg_var_hed"]):>10} '
            f'{v_g*100:>5.1f}% '
            f'{fmt_money(s["total_pnl_hed"]):>10} '
            f'{s["mean_monthly_ret"]*100:>+7.2f}% '
            f'{s["annualized_ret"]*100:>+7.1f}% '
            f'{s["sharpe_annual"] or 0:>+7.2f}'
        )

    if save and rows:
        df = pl.DataFrame(rows).with_columns(
            pl.lit(window).alias('window_d'),
            pl.lit(pos_path.name).alias('source_parquet'),
        )
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = (
            Path('data') / f'portfolio_sizing_sweep.{stamp}.parquet'
        )
        df.write_parquet(out, compression='zstd')
        log.green(f'wrote {len(df)} rows -> {out.name}')


if __name__ == '__main__':
    main()
