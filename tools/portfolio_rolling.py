"""Rolling monthly view of a saved portfolio_trades parquet.

Expands each position to its per-trading-day footprint, then
averages by calendar month: avg daily positions / GMV / VaR /
P&L / monthly return / annualized return.

Usage:
    uv run python tools/portfolio_rolling.py
    uv run python tools/portfolio_rolling.py --file <path>
    uv run python tools/portfolio_rolling.py --window 20
"""

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


def latest_portfolio() -> Path | None:
    files = sorted(
        Path('data').glob('portfolio_trades.*.parquet')
    )
    return files[-1] if files else None


def latest_hists() -> Path | None:
    files = sorted(Path('data').glob('hists.*.parquet'))
    return files[-1] if files else None


def fmt_money(x: float) -> str:
    if x is None:
        return ''
    a = abs(x)
    if a >= 1e9:
        return f'{x/1e9:+.2f}B'
    if a >= 1e6:
        return f'{x/1e6:+.1f}M'
    if a >= 1e3:
        return f'{x/1e3:+.0f}K'
    return f'{x:+.0f}'


def render_monthly(monthly: pl.DataFrame, window_d: int) -> None:
    m = monthly.filter(pl.col('window_d') == window_d)
    if m.is_empty():
        print(f'(no data for window {window_d}d)')
        return

    print(f'\n## Rolling monthly (window={window_d}d, GMV=gross)\n')
    hdr = (
        f'{"month":<8} {"days":>5} {"pos":>5} {"pkpos":>5} '
        f'{"avgGMV":>10} {"peakGMV":>10} '
        f'{"VaR_hed":>10} {"V/Gross":>8} '
        f'{"PnL_hed":>10} {"exp_PnL":>10} '
        f'{"ret_hed":>8} {"annual_h":>9}'
    )
    print(hdr)
    print('-' * len(hdr))
    for r in m.iter_rows(named=True):
        v_g = (
            r['avg_daily_var_hedged'] / r['avg_daily_gmv']
            if r['avg_daily_gmv'] else 0
        )
        print(
            f'{r["month"]:<8} '
            f'{r["n_trading_days"]:>5d} '
            f'{r["avg_daily_positions"]:>5.1f} '
            f'{r["peak_daily_positions"]:>5d} '
            f'{fmt_money(r["avg_daily_gmv"]):>10} '
            f'{fmt_money(r["peak_daily_gmv"]):>10} '
            f'{fmt_money(r["avg_daily_var_hedged"]):>10} '
            f'{v_g*100:>7.2f}% '
            f'{fmt_money(r["pnl_hedged"]):>10} '
            f'{fmt_money(r["exp_pnl_hedged"]):>10} '
            f'{(r["ret_hedged"] or 0)*100:>+7.2f}% '
            f'{(r["annualized_hedged"] or 0)*100:>+7.1f}%'
        )

    # Window-level summary
    avg_h = m['ret_hedged'].drop_nulls().mean()
    avg_u = m['ret_unhedged'].drop_nulls().mean()
    sum_pnl_h = m['pnl_hedged'].sum()
    sum_pnl_u = m['pnl_unhedged'].sum()
    avg_gmv = m['avg_daily_gmv'].drop_nulls().mean()
    n_months = len(m)
    print(
        f'\nsummary (window={window_d}d): '
        f'n_months={n_months}  '
        f'avg_daily_gmv={fmt_money(avg_gmv)}  '
        f'total_pnl_unh={fmt_money(sum_pnl_u)}  '
        f'total_pnl_hed={fmt_money(sum_pnl_h)}  '
        f'avg_monthly_ret_hed={avg_h*100:+.2f}%  '
        f'annualized_hed={avg_h*12*100:+.1f}%'
    )


def main() -> None:
    args = sys.argv[1:]
    path: Path | None = None
    windows = [5, 10, 20]
    save = False
    while args:
        flag = args.pop(0)
        if flag == '--file':
            path = Path(args.pop(0))
        elif flag == '--window':
            windows = [int(args.pop(0))]
        elif flag == '--save':
            save = True
        else:
            print(f'unknown arg: {flag}', file=sys.stderr)
            sys.exit(1)

    if path is None:
        path = latest_portfolio()
        if path is None:
            print(
                'no portfolio_trades parquet found in data/',
                file=sys.stderr,
            )
            sys.exit(1)

    hists_path = latest_hists()
    if hists_path is None:
        print('no hists parquet found', file=sys.stderr)
        sys.exit(1)

    log.info(f'reading {path.name}')
    positions = pl.read_parquet(path)
    hists = pl.read_parquet(hists_path)

    daily = expand_to_daily(positions, hists)
    monthly = rolling_monthly(daily)

    for w in windows:
        render_monthly(monthly, w)

    if save:
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = Path('data') / f'portfolio_rolling.{stamp}.parquet'
        monthly.write_parquet(out, compression='zstd')
        print(f'\nwrote {out}', file=sys.stderr)


if __name__ == '__main__':
    main()
