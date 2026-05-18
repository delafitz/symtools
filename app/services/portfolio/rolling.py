"""Rolling daily-exposure view of a portfolio.

Each position contributes to GMV and VaR each trading day it
is held. Aggregating by month gives average daily positions /
GMV / VaR (the risk that was carried that month) alongside
realized P&L (attributed to the exit date).

Returns are annualized by ×12 simple (consistent with the
monthly Sharpe annualization elsewhere).
"""

from __future__ import annotations

import polars as pl


ANNUAL_MONTHS = 12


def trading_calendar(hists: pl.DataFrame) -> list[str]:
    """Unique trading days from the Y template, sorted."""
    return (
        hists.filter(pl.col('template') == 'Y')
        .select('date').unique()
        .sort('date')
        .get_column('date')
        .to_list()
    )


def expand_to_daily(
    positions: pl.DataFrame, hists: pl.DataFrame,
) -> pl.DataFrame:
    """One row per (position, trading_day held).

    Conventions:
      - Notional: full from T+1 through exit_date inclusive
        (slight overstatement during the 3-day ramp).
      - VaR: scales by √(remaining_days / window_d) per day,
        where remaining_days is the count of trading days from
        the current day to the planned-exit day inclusive.
        On day 1: remaining = window_d → scale = 1.0.
        On the last day: remaining = 1 → scale = √(1/window_d).
        For a position that runs the full window the average
        daily VaR is ~67% of the entry-time horizon VaR.

    Adds:
      pnl_realized_today: full position P&L on exit_date, else 0.
    """
    cal = trading_calendar(hists)
    cal_idx = {d: i for i, d in enumerate(cal)}

    rows: list[dict] = []
    for p in positions.iter_rows(named=True):
        entry = p['trade_date']
        exit_date = p['exit_date']
        window_d = p['window_d']
        if entry not in cal_idx or exit_date not in cal_idx:
            continue
        # Held on every trading day in (entry, exit_date] inclusive
        i0 = cal_idx[entry] + 1
        i1 = cal_idx[exit_date]
        if i1 < i0:
            continue
        held = cal[i0:i1 + 1]
        var_h_horizon = p['var99_hedged_usd']
        var_u_horizon = p['var99_unhedged_usd']
        long_n = p['notional_usd']
        hedge_n = abs(p.get('hedge_notional_usd') or 0.0)
        gross_n = long_n + hedge_n
        for j, d in enumerate(held):
            # j=0 is the first holding day; remaining shrinks
            # by 1 per day. Capped at 1 so the last day still
            # carries a single day's risk, not zero.
            remaining = max(1, window_d - j)
            scale = (remaining / window_d) ** 0.5
            rows.append({
                'date': d,
                'symbol': p['symbol'],
                'trade_date': entry,
                'window_d': window_d,
                'long_notional_usd': long_n,
                'hedge_notional_usd': hedge_n,
                'gross_notional_usd': gross_n,
                'var99_hedged_usd': var_h_horizon * scale,
                'var99_unhedged_usd': var_u_horizon * scale,
                'pnl_realized_today': (
                    p['pnl_hedged_usd'] if d == exit_date else 0.0
                ),
                'pnl_unhedged_today': (
                    p['pnl_unhedged_usd'] if d == exit_date else 0.0
                ),
                'exp_pnl_today': (
                    p.get('expected_pnl_hedged_usd', 0.0)
                    if d == exit_date else 0.0
                ),
            })
    return pl.DataFrame(rows)


def rolling_monthly(daily_held: pl.DataFrame) -> pl.DataFrame:
    """Monthly rolling stats per (month, window_d).

    Columns:
      n_trading_days        — trading days in the month
      avg_daily_positions   — mean count of open positions
      avg_daily_gmv         — mean GMV across days
      avg_daily_var_hedged  — mean VaR99 (hedged)
      avg_daily_var_unhedged
      pnl_hedged            — sum of hedged P&L from exits in month
      pnl_unhedged
      ret_hedged            — pnl_hedged / avg_daily_gmv
      ret_unhedged
      annualized_hedged     — ret × 12
      annualized_unhedged
      var_pct_gmv           — avg_daily_var_hedged / avg_daily_gmv
    """
    if daily_held.is_empty():
        return pl.DataFrame()

    daily_held = daily_held.with_columns(
        pl.col('date').str.slice(0, 7).alias('month')
    )

    daily = (
        daily_held.group_by(['month', 'window_d', 'date'])
        .agg([
            pl.len().alias('n_positions'),
            pl.col('long_notional_usd').sum().alias('long_gmv'),
            pl.col('hedge_notional_usd').sum().alias('hedge_gmv'),
            pl.col('gross_notional_usd').sum().alias('gmv'),
            pl.col('var99_hedged_usd').sum().alias('var_h'),
            pl.col('var99_unhedged_usd').sum().alias('var_u'),
            pl.col('pnl_realized_today').sum().alias('pnl_h'),
            pl.col('pnl_unhedged_today').sum().alias('pnl_u'),
            pl.col('exp_pnl_today').sum().alias('exp_pnl_h'),
        ])
    )

    monthly = (
        daily.group_by(['month', 'window_d'])
        .agg([
            pl.len().alias('n_trading_days'),
            pl.col('n_positions').mean().alias(
                'avg_daily_positions'
            ),
            pl.col('n_positions').max().alias(
                'peak_daily_positions'
            ),
            pl.col('long_gmv').mean().alias('avg_daily_long_gmv'),
            pl.col('hedge_gmv').mean().alias('avg_daily_hedge_gmv'),
            pl.col('gmv').mean().alias('avg_daily_gmv'),
            pl.col('gmv').max().alias('peak_daily_gmv'),
            pl.col('var_h').mean().alias('avg_daily_var_hedged'),
            pl.col('var_u').mean().alias('avg_daily_var_unhedged'),
            pl.col('var_h').max().alias('peak_daily_var_hedged'),
            pl.col('pnl_h').sum().alias('pnl_hedged'),
            pl.col('pnl_u').sum().alias('pnl_unhedged'),
            pl.col('exp_pnl_h').sum().alias('exp_pnl_hedged'),
        ])
        .sort(['window_d', 'month'])
    )
    monthly = monthly.with_columns([
        (pl.col('pnl_hedged') / pl.col('avg_daily_gmv'))
            .alias('ret_hedged'),
        (pl.col('pnl_unhedged') / pl.col('avg_daily_gmv'))
            .alias('ret_unhedged'),
        (pl.col('exp_pnl_hedged') / pl.col('avg_daily_gmv'))
            .alias('exp_ret_hedged'),
        (
            pl.col('avg_daily_var_hedged')
            / pl.col('avg_daily_gmv')
        ).alias('var_pct_gmv'),
    ]).with_columns([
        (pl.col('ret_hedged') * ANNUAL_MONTHS)
            .alias('annualized_hedged'),
        (pl.col('ret_unhedged') * ANNUAL_MONTHS)
            .alias('annualized_unhedged'),
    ])
    return monthly
