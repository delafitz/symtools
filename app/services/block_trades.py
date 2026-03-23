"""Block trade history loader."""

from pathlib import Path

import polars as pl

from app.models.blocks import BlockTrade, SymbolBlocks
from app.utils.logger import get_logger

log = get_logger(__name__)

_STORE = Path('./data')

# Raw column name → normalized name
_COL_MAP = {
    'PxDt': 'price_date',
    'TradeDt': 'trade_date',
    'Type': 'registered',
    'OfferPx': 'offer_price',
    'Shares': 'shares',
    'Disc': 'discount',
    'T+1': 'perf_t1',
    'LeftBank': 'broker',
}


def load_block_trades(
    refs: pl.DataFrame,
) -> pl.DataFrame | None:
    """Find and load latest block_trades file from data/.

    Supports .json and .csv (auto-detected by extension).
    Normalizes column names, symbol to lowercase,
    cross-checks against refs — logs warnings for unknown
    symbols and drops them.
    """
    candidates = sorted(
        list(_STORE.glob('block_trades*.json'))
        + list(_STORE.glob('block_trades*.csv'))
    )
    if not candidates:
        log.info('block_trades: no file found in data/')
        return None

    path = candidates[-1]
    log.info(f'block_trades: loading {path.name}')

    try:
        if path.suffix == '.csv':
            df = pl.read_csv(path)
        else:
            df = pl.read_json(path)
    except Exception as e:
        log.warning(f'block_trades: load failed: {e}')
        return None

    # Normalize common ticker column names → symbol
    for alias in ('Ticker', 'ticker', 'Symbol', 'TICKER', 'SYMBOL'):
        if alias in df.columns:
            df = df.rename({alias: 'symbol'})
            break

    if 'symbol' not in df.columns:
        log.warning(
            f'block_trades: no symbol column in {path.name}'
        )
        return None

    # Rename known columns
    rename = {k: v for k, v in _COL_MAP.items() if k in df.columns}
    if rename:
        df = df.rename(rename)

    # Normalize symbol to lowercase
    # registered: "Reg" → True, else False
    exprs: list = [pl.col('symbol').str.to_lowercase()]
    if 'registered' in df.columns:
        exprs.append(
            (pl.col('registered') == 'Reg').alias('registered')
        )
    df = df.with_columns(exprs)

    # Cross-check against refs
    known = set(refs.get_column('symbol').to_list())
    unknown = [
        s for s in df['symbol'].unique().to_list()
        if s not in known
    ]
    if unknown:
        log.warning(
            f'block_trades: {len(unknown)} unknown symbols '
            f'(dropping): {unknown[:20]}'
            f'{"..." if len(unknown) > 20 else ""}'
        )
        df = df.filter(pl.col('symbol').is_in(known))

    if df.is_empty():
        log.warning('block_trades: no valid trades after filter')
        return None

    n_trades = len(df)
    n_syms = df['symbol'].n_unique()
    log.info(
        f'block_trades: {n_trades} trades, {n_syms} symbols'
    )
    log.green(f'block_trades head:\n{df.head()}')
    return df


def get_symbol_blocks(
    symbol: str,
    df: pl.DataFrame,
) -> SymbolBlocks | None:
    """Filter block trades for a symbol and build SymbolBlocks."""
    rows = df.filter(pl.col('symbol') == symbol)
    if rows.is_empty():
        return None

    trades: list[BlockTrade] = []
    for row in rows.to_dicts():
        offer_price = row.get('offer_price') or 0.0
        shares = row.get('shares') or 0
        trades.append(
            BlockTrade(
                symbol=symbol,
                price_date=row.get('price_date') or '',
                trade_date=row.get('trade_date') or '',
                registered=row.get('registered') or False,
                seller=row.get('seller'),
                deal_size=offer_price * shares,
                shares=shares,
                offer_price=offer_price,
                discount=row.get('discount') or 0.0,
                perf_t1=row.get('perf_t1') or 0.0,
                broker=row.get('broker') or '',
            )
        )
    return SymbolBlocks(symbol=symbol, trades=trades)
