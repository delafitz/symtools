"""Trade-sampling modes for the portfolio backtest.

Modes:
  all:      every trade in the input.
  random:   uniform sample without replacement (--n controls
            count, --seed controls reproducibility).
  strategy: predefined preset filters keyed by name.

Strategy presets:
  d10:        top-decile-archetype filter (strong pre 20d
              run-up + mild pre 1d drawdown).
  reg_only:   keep only registered offerings.
  citi:       keep only Citi-led blocks.
  combined:   reg_only AND d10-archetype.
"""

from __future__ import annotations

import polars as pl


# D10 archetype thresholds (from the lens analysis)
D10_PRE20D_MIN = 0.05    # ≥ +5% over 20d
D10_PRE1D_MIN = -0.025   # ≥ −2.5% on T-1 (mild drawdown)


def sample_trades(
    trades: pl.DataFrame,
    mode: str = 'all',
    strategy: str | None = None,
    n: int | None = None,
    seed: int = 42,
) -> pl.DataFrame:
    """Return a filtered/sampled subset of trades.

    `trades` is the join of backtest_trades.parquet with the
    pre-window returns (caller supplies r_pre20 and r_pre1
    columns when filtering on strategies that need them)."""
    if mode == 'all':
        return trades
    if mode == 'random':
        if n is None or n <= 0 or n >= len(trades):
            return trades
        return trades.sample(n=n, seed=seed, shuffle=True)
    if mode == 'strategy':
        if not strategy:
            return trades
        return _strategy_filter(trades, strategy)
    raise ValueError(f'unknown sampling mode: {mode}')


def _strategy_filter(
    trades: pl.DataFrame, strategy: str
) -> pl.DataFrame:
    s = strategy.lower()
    if s == 'd10':
        return _d10(trades)
    if s == 'reg_only':
        return trades.filter(pl.col('registered') == True)
    if s == 'citi':
        # Bank column may come from a join with trades_agg by
        # broker; just check the broker field.
        return trades.filter(
            (pl.col('broker') == 'C')
            | (pl.col('broker') == 'Citi')
        )
    if s == 'combined':
        d10 = _d10(trades)
        return d10.filter(pl.col('registered') == True)
    raise ValueError(f'unknown strategy: {strategy}')


def _d10(trades: pl.DataFrame) -> pl.DataFrame:
    """D10-archetype: strong 20d run-up + mild same-day
    drawdown. Requires `r_pre20` and `r_pre1` columns."""
    missing = [
        c for c in ('r_pre20', 'r_pre1')
        if c not in trades.columns
    ]
    if missing:
        raise ValueError(
            f'strategy=d10 requires {missing} on trades frame'
        )
    return trades.filter(
        (pl.col('r_pre20') >= D10_PRE20D_MIN)
        & (pl.col('r_pre1') >= D10_PRE1D_MIN)
    )
