"""Monthly portfolio aggregation.

Trades are independently sized (no shared capital base). Monthly
roll-up groups by entry month (trade_date). For each month:
  GMV       = sum of position notionals
  pnl_*     = sum of position pnls (USD)
  ret_*     = pnl / GMV (return on gross)
  hit_rate  = share of trades with pnl > 0

Sharpe is computed across months as monthly returns annualized
by sqrt(12).
"""

import math

import polars as pl


ANNUALIZER = math.sqrt(12)


def monthly_aggregate(
    positions: pl.DataFrame, window_d: int
) -> pl.DataFrame:
    """One row per month for the given tradeout window.

    Expected columns on positions:
      trade_date, window_d, notional_usd,
      pnl_unhedged_usd, pnl_hedged_usd,
      var99_unhedged_usd, var99_hedged_usd,
      expected_pnl_hedged_usd
    """
    df = positions.filter(pl.col('window_d') == window_d)
    if df.is_empty():
        return pl.DataFrame()

    df = df.with_columns(
        pl.col('trade_date').str.slice(0, 7).alias('month')
    )

    monthly = (
        df.group_by('month').agg([
            pl.len().alias('n_trades'),
            pl.col('notional_usd').sum().alias('gmv_usd'),
            pl.col('pnl_unhedged_usd').sum().alias('pnl_unhedged'),
            pl.col('pnl_hedged_usd').sum().alias('pnl_hedged'),
            pl.col('expected_pnl_hedged_usd').sum()
                .alias('exp_pnl_hedged'),
            pl.col('var99_hedged_usd').sum()
                .alias('var99_hedged_sum'),
            (pl.col('pnl_hedged_usd') > 0).mean().alias('hit_rate'),
        ])
        .sort('month')
    )
    monthly = monthly.with_columns([
        (pl.col('pnl_unhedged') / pl.col('gmv_usd'))
            .alias('ret_unhedged'),
        (pl.col('pnl_hedged') / pl.col('gmv_usd'))
            .alias('ret_hedged'),
        (pl.col('exp_pnl_hedged') / pl.col('gmv_usd'))
            .alias('exp_ret_hedged'),
    ])
    return monthly


def portfolio_summary(monthly: pl.DataFrame) -> dict:
    """Top-level stats over the monthly series."""
    if monthly.is_empty():
        return {}
    rh = monthly.get_column('ret_hedged').to_list()
    ru = monthly.get_column('ret_unhedged').to_list()
    mean_h = sum(rh) / len(rh)
    mean_u = sum(ru) / len(ru)
    std_h = (
        sum((x - mean_h) ** 2 for x in rh) / max(len(rh) - 1, 1)
    ) ** 0.5
    std_u = (
        sum((x - mean_u) ** 2 for x in ru) / max(len(ru) - 1, 1)
    ) ** 0.5
    sharpe_h = (mean_h / std_h) * ANNUALIZER if std_h > 0 else None
    sharpe_u = (mean_u / std_u) * ANNUALIZER if std_u > 0 else None
    return {
        'n_months': len(monthly),
        'n_trades': int(monthly['n_trades'].sum()),
        'gmv_total': float(monthly['gmv_usd'].sum()),
        'pnl_unhedged_total': float(monthly['pnl_unhedged'].sum()),
        'pnl_hedged_total': float(monthly['pnl_hedged'].sum()),
        'avg_monthly_ret_unhedged': mean_u,
        'avg_monthly_ret_hedged': mean_h,
        'sharpe_unhedged_annual': sharpe_u,
        'sharpe_hedged_annual': sharpe_h,
    }
