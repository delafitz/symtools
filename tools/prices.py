"""Fetch recent daily closes for one or more symbols.

Usage:
    uv run python tools/prices.py <days> <symbol> [symbol ...]

Prints a Polars table of date × symbol closes for the last N
trading days, including today (or the last trading day if
pre-open / weekend).

Examples:
    uv run python tools/prices.py 3 AAPL SPY
    uv run python tools/prices.py 5 NVDA MSFT AMZN GOOG
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import polars as pl

from app.mds.client import get_provider

CONCURRENCY = 10
# Fetch a wider window than requested so tail(N) always has
# enough rows even when holidays reduce trading days in range.
BUFFER = 10


async def _fetch(
    mds,
    symbol: str,
    days: int,
    sem: asyncio.Semaphore,
) -> pl.DataFrame | None:
    async with sem:
        df = await asyncio.to_thread(
            mds.get_hist,
            symbol,
            timespan='day',
            multiplier=1,
            unit='days',
            scale=days + BUFFER,
            close_only=True,
            quiet=True,
        )
    if df.is_empty():
        print(f'  {symbol.upper()}: no data', file=sys.stderr)
        return None
    return (
        df.with_columns(
            pl.col('close').shift(1).alias('prev_close'),
            (pl.col('close').pct_change() * 100)
            .round(4)
            .alias('chg_pct'),
        )
        .tail(days)
        .select('date', 'prev_close', 'close', 'chg_pct')
        .with_columns(pl.lit(symbol.upper()).alias('symbol'))
    )


async def run(days: int, symbols: list[str]) -> pl.DataFrame:
    mds = get_provider()
    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [_fetch(mds, sym, days, sem) for sym in symbols]
    frames = [
        r for r in await asyncio.gather(*tasks) if r is not None
    ]
    if not frames:
        return pl.DataFrame(
            schema={
                'date': pl.String,
                'symbol': pl.String,
                'prev_close': pl.Float64,
                'close': pl.Float64,
                'chg_pct': pl.Float64,
            }
        )
    return (
        pl.concat(frames)
        .select('date', 'symbol', 'prev_close', 'close', 'chg_pct')
        .sort(['date', 'symbol'], descending=[True, False])
    )


def main() -> None:
    if len(sys.argv) < 3:
        print('usage: prices.py <days> <symbol> [symbol ...]')
        sys.exit(1)

    days = int(sys.argv[1])
    symbols = [s.lower() for s in sys.argv[2:]]

    df = asyncio.run(run(days, symbols))
    print(df)


if __name__ == '__main__':
    main()
