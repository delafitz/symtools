"""Apply per-trade size multipliers based on filter rules and
sweep strategy variants.

Reuses the rescale pattern from `portfolio_sizing_sweep.py`:
dollar fields scale linearly with notional, so we can adjust
notional per-trade and reaggregate without rerunning scoring.

Strategy primitives (each maps a trade row → multiplier):
  baseline      : 1.0 (no filter)
  half_bad_bank : 0.5 if broker in {MS, BAC, BAML, JPM} else 1.0
  skip_bad_bank : 0.0 if bad bank, else 1.0
  skip_panic    : 0.0 if pre_1d ≤ -5% (severe same-day drop)
  skip_deep     : 0.0 if discount ≤ -5%
  skip_xadv     : 0.0 if shares_pct_adv > 5.0
  chase_d10     : 1.5x if pre_20d > +5% AND pre_1d > -2%
  combo_*       : compositions of the above

Usage:
    uv run python tools/portfolio_strategy_sweep.py
    uv run python tools/portfolio_strategy_sweep.py --window 20
    uv run python tools/portfolio_strategy_sweep.py --no-save
"""

import math
import sys
from collections.abc import Callable
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

DOLLAR_COLS = [
    'notional_usd', 'shares', 'hedge_notional_usd',
    'target_pnl_usd', 'hedge_pnl_usd',
    'pnl_unhedged_usd', 'pnl_hedged_usd',
    'expected_pnl_unhedged_usd', 'expected_pnl_hedged_usd',
    'var99_unhedged_usd', 'var99_hedged_usd',
]

BAD_BANKS = {'MS', 'BAC', 'BAML', 'JPM'}


def baseline(r: dict) -> float:
    return 1.0


def half_bad_bank(r: dict) -> float:
    return 0.5 if r.get('broker') in BAD_BANKS else 1.0


def skip_bad_bank(r: dict) -> float:
    return 0.0 if r.get('broker') in BAD_BANKS else 1.0


def skip_panic(r: dict) -> float:
    """Skip trades with pre-1d return ≤ -5% (severe drop)."""
    pre1 = r.get('r_pre1')
    return 0.0 if (pre1 is not None and pre1 <= -0.05) else 1.0


def skip_deep_disc(r: dict) -> float:
    """Skip trades with discount ≤ -5%."""
    d = r.get('actual_discount')
    return 0.0 if (d is not None and d <= -0.05) else 1.0


def skip_high_xadv(r: dict) -> float:
    """Skip trades with shares_pct_adv > 5.0 (large vs typical
    daily volume)."""
    x = r.get('shares_pct_adv')
    return 0.0 if (x is not None and x > 5.0) else 1.0


def chase_d10(r: dict) -> float:
    """1.5x for D10 archetype: pre_20d > +5% AND pre_1d > -2%."""
    p20 = r.get('r_pre20')
    p1 = r.get('r_pre1')
    if p20 is not None and p1 is not None and p20 > 0.05 and p1 > -0.02:
        return 1.5
    return 1.0


def _compose(*fns: Callable[[dict], float]) -> Callable[[dict], float]:
    def composed(r: dict) -> float:
        m = 1.0
        for fn in fns:
            m *= fn(r)
        return m
    return composed


STRATEGIES: dict[str, Callable[[dict], float]] = {
    'baseline': baseline,
    'half_bad_bank': half_bad_bank,
    'skip_bad_bank': skip_bad_bank,
    'skip_panic': skip_panic,
    'skip_deep_disc': skip_deep_disc,
    'skip_high_xadv': skip_high_xadv,
    'chase_d10': chase_d10,
    'half_bank+skip_panic': _compose(half_bad_bank, skip_panic),
    'chase_d10+skip_panic': _compose(chase_d10, skip_panic),
    'chase_d10+half_bad_bank': _compose(chase_d10, half_bad_bank),
    'chase_d10+half_bank+skip_panic': _compose(
        chase_d10, half_bad_bank, skip_panic,
    ),
    'chase_d10+skip_bad_bank': _compose(chase_d10, skip_bad_bank),
    'all_skips': _compose(
        skip_bad_bank, skip_panic, skip_deep_disc, skip_high_xadv,
    ),
}


def load_with_features() -> pl.DataFrame:
    """Load portfolio_trades + join pre/post returns + raw trade
    features (shares_pct_adv, etc.)."""
    pos_path = sorted(
        Path('data').glob('portfolio_trades.*.parquet')
    )[-1]
    log.info(f'reading {pos_path.name}')
    pos = pl.read_parquet(pos_path)

    # Raw trades for shares_pct_adv
    trades = pl.read_parquet('data/backtest_trades.parquet')
    trades_unique = trades.group_by(['symbol', 'trade_date']).agg([
        pl.col('shares_pct_adv').first(),
    ])
    pos = pos.join(
        trades_unique, on=['symbol', 'trade_date'], how='left'
    )

    # Pre-1d and pre-20d from scores
    scores = pl.read_parquet('data/backtest_scores.parquet').filter(
        (pl.col('scenario') == 'combined')
        & (pl.col('period') == 'pre')
    )
    pre1 = scores.filter(pl.col('window_d') == 1).group_by(
        ['symbol', 'trade_date']
    ).agg(pl.col('raw_return').mean().alias('r_pre1'))
    pre20 = scores.filter(pl.col('window_d') == 20).group_by(
        ['symbol', 'trade_date']
    ).agg(pl.col('raw_return').mean().alias('r_pre20'))
    pos = pos.join(
        pre1, on=['symbol', 'trade_date'], how='left'
    ).join(
        pre20, on=['symbol', 'trade_date'], how='left'
    )
    return pos


def apply_strategy(
    positions: pl.DataFrame,
    rule_fn: Callable[[dict], float],
) -> pl.DataFrame:
    """Apply per-trade multipliers; drop zero-multiplier rows;
    rescale dollar columns by remaining multipliers."""
    mults = [
        rule_fn(r) for r in positions.iter_rows(named=True)
    ]
    df = positions.with_columns(
        pl.Series('_mult', mults, dtype=pl.Float64)
    )
    df = df.filter(pl.col('_mult') > 0)
    for c in DOLLAR_COLS:
        if c not in df.columns:
            continue
        df = df.with_columns(
            (pl.col(c) * pl.col('_mult')).alias(c)
        )
    return df.drop('_mult')


def stats(
    monthly: pl.DataFrame, window_d: int,
) -> dict:
    m = monthly.filter(pl.col('window_d') == window_d)
    if m.is_empty():
        return {}
    rh = m['ret_hedged'].drop_nulls().to_list()
    if not rh:
        return {}
    mean_h = sum(rh) / len(rh)
    std_h = (
        sum((x - mean_h) ** 2 for x in rh) / max(len(rh) - 1, 1)
    ) ** 0.5
    sharpe = (mean_h / std_h) * math.sqrt(12) if std_h > 0 else None
    return {
        'n_months': len(m),
        'avg_gmv': float(m['avg_daily_gmv'].mean()),
        'avg_var_h': float(m['avg_daily_var_hedged'].mean()),
        'pnl_hedged_total': float(m['pnl_hedged'].sum()),
        'pnl_hedged_avg_mo': float(m['pnl_hedged'].mean()),
        'pnl_unhedged_total': float(m['pnl_unhedged'].sum()),
        'mean_mo_ret_h': mean_h,
        'sharpe_h_annual': sharpe,
        'annualized_h': mean_h * 12,
    }


def fmt_money(x: float | None) -> str:
    if x is None:
        return ''
    a = abs(x)
    if a >= 1e9:
        return f'{x/1e9:+.2f}B'
    if a >= 1e6:
        return f'{x/1e6:+.0f}M'
    return f'{x:+.0f}'


def main() -> None:
    args = sys.argv[1:]
    window = 20
    save = True
    while args:
        f = args.pop(0)
        if f == '--window':
            window = int(args.pop(0))
        elif f == '--no-save':
            save = False
        else:
            print(f'unknown arg: {f}', file=sys.stderr)
            sys.exit(1)

    positions = load_with_features()
    hists = pl.read_parquet(
        sorted(Path('data').glob('hists.*.parquet'))[-1]
    )

    print(f'\n## Strategy sweep (window={window}d)\n')
    hdr = (
        f'{"strategy":<32} {"n":>5} {"avg_GMV":>10} '
        f'{"avg_VaR":>10} {"PnL_mo":>10} '
        f'{"mo_ret":>8} {"ann_ret":>8} {"sharpe_h":>9}'
    )
    print(hdr)
    print('-' * len(hdr))

    rows = []
    for name, rule in STRATEGIES.items():
        df = apply_strategy(positions, rule)
        if df.is_empty():
            continue
        n = df.filter(pl.col('window_d') == window).height
        daily = expand_to_daily(df, hists)
        monthly = rolling_monthly(daily)
        s = stats(monthly, window)
        if not s:
            continue
        rows.append({'strategy': name, 'n_trades': n, **s})
        print(
            f'{name:<32} {n:>5d} '
            f'{fmt_money(s["avg_gmv"]):>10} '
            f'{fmt_money(s["avg_var_h"]):>10} '
            f'{fmt_money(s["pnl_hedged_avg_mo"]):>10} '
            f'{s["mean_mo_ret_h"]*100:>+7.2f}% '
            f'{s["annualized_h"]*100:>+7.1f}% '
            f'{(s["sharpe_h_annual"] or 0):>+9.2f}'
        )

    if save and rows:
        df = pl.DataFrame(rows).with_columns(
            pl.lit(window).alias('window_d'),
        )
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = (
            Path('data')
            / f'portfolio_strategy_sweep.{stamp}.parquet'
        )
        df.write_parquet(out, compression='zstd')
        log.green(f'wrote {len(df)} rows -> {out.name}')


if __name__ == '__main__':
    main()
