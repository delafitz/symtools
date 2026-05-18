"""Sweep stop-loss levels and compare portfolio performance.

For each stop threshold, re-runs the position-scoring loop
(scoring is path-dependent, so we can't just rescale a saved
parquet the way the sizing sweep does), aggregates to monthly,
and reports hedged + unhedged returns, Sharpe, and stop
diagnostics.

Usage:
    uv run python tools/portfolio_stop_sweep.py
    uv run python tools/portfolio_stop_sweep.py --window 20
    uv run python tools/portfolio_stop_sweep.py --stops 0,-2,-3,-5,-7

`--stops` accepts a comma-separated list of percent values
(0 disables the stop). Default: 0,-3,-5,-7.
"""

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
from app.services.portfolio.sizer import SizeParams
from app.utils.logger import get_logger
from tools.portfolio_backtest import run as run_backtest

log = get_logger(__name__)


DEFAULT_STOPS = [0.0, -0.03, -0.05, -0.07]


def fmt_money(x: float | None) -> str:
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


def stats(monthly: pl.DataFrame, window_d: int) -> dict:
    m = monthly.filter(pl.col('window_d') == window_d)
    if m.is_empty():
        return {}
    rh = m['ret_hedged'].drop_nulls().to_list()
    ru = m['ret_unhedged'].drop_nulls().to_list()

    def _ms(xs: list[float]) -> tuple[float, float, float | None]:
        if not xs:
            return 0.0, 0.0, None
        mean = sum(xs) / len(xs)
        std = (
            sum((x - mean) ** 2 for x in xs)
            / max(len(xs) - 1, 1)
        ) ** 0.5
        sharpe = (mean / std) * math.sqrt(12) if std > 0 else None
        return mean, std, sharpe

    mh, sh, sharpe_h = _ms(rh)
    mu, su, sharpe_u = _ms(ru)
    return {
        'n_months': len(m),
        'avg_daily_gmv': float(m['avg_daily_gmv'].mean()),
        'avg_daily_var_h': float(m['avg_daily_var_hedged'].mean()),
        'pnl_hedged_total': float(m['pnl_hedged'].sum()),
        'pnl_unhedged_total': float(m['pnl_unhedged'].sum()),
        'pnl_hedged_avg_mo': float(m['pnl_hedged'].mean()),
        'pnl_unhedged_avg_mo': float(m['pnl_unhedged'].mean()),
        'mean_monthly_ret_hed': mh,
        'mean_monthly_ret_unh': mu,
        'sharpe_h': sharpe_h,
        'sharpe_u': sharpe_u,
        'annualized_hed': mh * 12,
        'annualized_unh': mu * 12,
    }


def main() -> None:
    args = sys.argv[1:]
    window = 20
    stops = DEFAULT_STOPS
    save = True
    while args:
        flag = args.pop(0)
        if flag == '--window':
            window = int(args.pop(0))
        elif flag == '--stops':
            stops = [
                float(x) / 100.0 if abs(float(x)) > 1 else float(x)
                for x in args.pop(0).split(',')
            ]
        elif flag == '--no-save':
            save = False
        else:
            print(f'unknown arg: {flag}', file=sys.stderr)
            sys.exit(1)

    # Load hists once (used by rolling expansion across all stops)
    hists_path = sorted(
        Path('data').glob('hists.*.parquet')
    )[-1]
    hists = pl.read_parquet(hists_path)

    size_params = SizeParams()  # use the current default sizing

    print(
        f'\n## Stop-loss sweep (window={window}d, '
        f'sizing pct={size_params.pct_adv} '
        f'floor=${int(size_params.floor_usd/1e6)}M '
        f'cap=${int(size_params.cap_usd/1e6)}M)\n'
    )
    hdr = (
        f'{"stop":>6} {"n_stops":>8} '
        f'{"avg_GMV":>10} {"avg_VaR":>9} '
        f'{"PnL_unh_mo":>11} {"PnL_hed_mo":>11} '
        f'{"ann_unh":>8} {"ann_hed":>8} '
        f'{"sharpe_u":>9} {"sharpe_h":>9}'
    )
    print(hdr)
    print('-' * len(hdr))

    rows: list[dict] = []
    for stop in stops:
        # "0" or "no stop" → use a deeply impossible threshold
        eff_stop = stop if stop < 0 else -10.0
        log.info(
            f'\n=== stop = {("none" if stop >= 0 else f"{stop*100:.0f}%")} ==='
        )
        positions, _ = run_backtest(
            mode='all',
            strategy=None,
            n=None,
            seed=42,
            size_params=size_params,
            hedge_ratio=0.85,
            stop_pct=eff_stop,
        )
        if positions.is_empty():
            continue

        n_stops = int(
            positions.filter(
                (pl.col('window_d') == window)
                & (pl.col('stop_triggered') == True)
            ).height
        )

        daily = expand_to_daily(positions, hists)
        monthly = rolling_monthly(daily)
        s = stats(monthly, window)
        s.update({
            'stop_pct': stop if stop < 0 else None,
            'n_stops': n_stops,
        })
        rows.append(s)

        stop_label = (
            'none' if stop >= 0 else f'{stop*100:+.0f}%'
        )
        print(
            f'{stop_label:>6} {n_stops:>8d} '
            f'{fmt_money(s["avg_daily_gmv"]):>10} '
            f'{fmt_money(s["avg_daily_var_h"]):>9} '
            f'{fmt_money(s["pnl_unhedged_avg_mo"]):>11} '
            f'{fmt_money(s["pnl_hedged_avg_mo"]):>11} '
            f'{s["annualized_unh"]*100:>+7.1f}% '
            f'{s["annualized_hed"]*100:>+7.1f}% '
            f'{(s["sharpe_u"] or 0):>+9.2f} '
            f'{(s["sharpe_h"] or 0):>+9.2f}'
        )

    if save and rows:
        df = pl.DataFrame(rows).with_columns(
            pl.lit(window).alias('window_d'),
        )
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = (
            Path('data')
            / f'portfolio_stop_sweep.{stamp}.parquet'
        )
        df.write_parquet(out, compression='zstd')
        log.green(f'wrote {len(df)} rows -> {out.name}')


if __name__ == '__main__':
    main()
