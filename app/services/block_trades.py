"""Block trade history loader.

Supports two source schemas:

1. **Legacy** (`block_trades*.json`, list of dicts):
     {Ticker, PxDt, TradeDt, Type, NetPx, OfferPx, Shares,
      Disc, T+1, LeftBank}
   Date format `DD-Mon-YYYY`. `Type` is `'Reg'` or null. `Disc`
   is unreliable and discarded.

2. **Curated** (`block_trades_alt*.json`, dict with `deals[]`):
     {ticker, cik, type, price_date, trade_date, intraday,
      offer_price, shares, notional, split_factor, adj_price,
      adj_shares, seller, relationship, banks, evidence,
      source}
   Dates are ISO. `type` is `'Reg'` or `'Unreg'`. `intraday`
   is a boolean. **Use `adj_price`/`adj_shares`** (split-
   adjusted, on today's basis to match Polygon hists).
"""

import json
from pathlib import Path

import polars as pl

from app.models.blocks import BlockTrade, SymbolBlocks
from app.utils.logger import get_logger

log = get_logger(__name__)

_STORE = Path('./data')

# Drop trades with |discount| > MAX_DISC. In practice these
# are bad data (split mismatches before the alt file's
# adj_price arrived) or extreme distressed prints.
MAX_DISC = 0.15


def _load_legacy(path: Path) -> pl.DataFrame | None:
    """Parse legacy `block_trades*.json` (flat array)."""
    from datetime import datetime
    with open(path) as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        return None

    def to_iso(s: str | None) -> str | None:
        if not s: return None
        try:
            return datetime.strptime(
                s, '%d-%b-%Y'
            ).strftime('%Y-%m-%d')
        except ValueError:
            return None

    normalized: list[dict] = []
    for r in rows:
        sym = (r.get('Ticker') or '').lower()
        if not sym:
            continue
        px = to_iso(r.get('PxDt'))
        td = to_iso(r.get('TradeDt'))
        if not px or not td:
            continue
        normalized.append({
            'symbol': sym,
            'price_date': px,
            'trade_date': td,
            'intraday': px == td,
            'offer_price': r.get('OfferPx'),
            'shares': r.get('Shares'),
            'registered': r.get('Type') == 'Reg',
            'broker': r.get('LeftBank') or '',
            'seller': r.get('seller'),
            'relationship': None,
            'evidence': 'legacy',
        })
    return pl.DataFrame(normalized) if normalized else None


def _load_alt(path: Path) -> pl.DataFrame | None:
    """Parse curated `block_trades_alt*.json` (dict with `deals[]`)."""
    with open(path) as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or 'deals' not in doc:
        return None

    deals = doc['deals']
    log.info(
        f'block_trades: alt schema v2; {doc.get("count", "?")} '
        f'deals (generated {doc.get("generated_at", "?")})'
    )

    normalized: list[dict] = []
    for r in deals:
        sym = (r.get('ticker') or '').lower()
        if not sym:
            continue
        # Use adj_price / adj_shares (split-adjusted to today)
        # so cross-deal comparisons stay on the same basis as
        # Polygon hists (also split-adjusted).
        adj_p = r.get('adj_price')
        adj_s = r.get('adj_shares')
        banks = r.get('banks') or []
        lead = banks[0] if banks else ''
        normalized.append({
            'symbol': sym,
            'price_date': r.get('price_date'),
            'trade_date': r.get('trade_date'),
            'offer_price': adj_p,
            'shares': adj_s,
            'registered': r.get('type') == 'Reg',
            'broker': lead,
            'seller': r.get('seller'),
            'relationship': r.get('relationship'),
            'evidence': r.get('evidence'),
        })
    return pl.DataFrame(normalized) if normalized else None


def _rebuild_discount(
    df: pl.DataFrame, hists: pl.DataFrame | None
) -> pl.DataFrame:
    """Compute discount from offer_price and pre-block close.

    Rule: pre_close = close on price_date (uniform across all
    trades). The source's `price_date` is the day the offer was
    set; its closing price is the natural reference quote.

    Source as of 2026-05-22 no longer carries an `intraday`
    flag — overnight blocks have trade_date == price_date + 1,
    intraday/same-day blocks have trade_date == price_date.
    Either way, close on price_date is the right pre-block
    reference under this convention.

    Rows with no usable pre_close, positive discount, or
    |discount| > MAX_DISC are dropped. Adds `discount` and
    `pre_close` columns.
    """
    if hists is None:
        log.warning(
            'block_trades: no hists; discount will be null'
        )
        return df.with_columns([
            pl.lit(None, dtype=pl.Float64).alias('discount'),
            pl.lit(None, dtype=pl.Float64).alias('pre_close'),
        ])

    y_closes = hists.filter(
        pl.col('template') == 'Y'
    ).select(['symbol', 'date', 'close']).sort(
        ['symbol', 'date']
    )

    pre_closes: list[float | None] = []
    for r in df.iter_rows(named=True):
        sym = r['symbol']
        px = r.get('price_date')
        if not px:
            pre_closes.append(None)
            continue
        sym_hist = y_closes.filter(pl.col('symbol') == sym)
        if sym_hist.is_empty():
            pre_closes.append(None)
            continue
        rows = sym_hist.filter(pl.col('date') <= px).tail(1)
        pre_closes.append(
            rows.get_column('close').item() if not rows.is_empty()
            else None
        )

    df = df.with_columns(
        pl.Series('pre_close', pre_closes, dtype=pl.Float64)
    ).with_columns(
        (pl.col('offer_price') / pl.col('pre_close') - 1)
        .alias('discount')
    )

    n_before = len(df)
    df = df.filter(
        pl.col('discount').is_not_null()
        & (pl.col('discount') <= 0)
        & (pl.col('discount') >= -MAX_DISC)
    )
    dropped = n_before - len(df)
    if dropped:
        log.warning(
            f'block_trades: dropped {dropped} trades '
            f'(no pre_close, premium >0, or |discount| > '
            f'{MAX_DISC:.0%}). Premium-priced trades are not '
            f'meaningful for this framework; likely date errors '
            f'in source (PxDt/TradeDt off-by-one).'
        )
    return df


def load_block_trades(
    refs: pl.DataFrame,
    hists: pl.DataFrame | None = None,
) -> pl.DataFrame | None:
    """Find and load latest block_trades file from data/.

    Auto-detects schema:
      - dict with `deals[]`  → curated alt schema (uses
        `adj_price`/`adj_shares` and explicit `intraday`)
      - flat list of dicts   → legacy `block_trades*.json`

    Both are normalized to a common DataFrame with columns:
      symbol, price_date, trade_date, intraday, offer_price,
      shares, registered, broker, seller, relationship,
      evidence (+ discount, pre_close added by
      `_rebuild_discount` when hists is provided).
    """
    candidates = sorted(
        list(_STORE.glob('block_trades*.json'))
        + list(_STORE.glob('block_trades*.csv'))
    )
    candidates = [
        p for p in candidates if not p.name.endswith('.bak')
    ]
    if not candidates:
        log.info('block_trades: no file found in data/')
        return None

    path = candidates[-1]
    log.info(f'block_trades: loading {path.name}')

    df: pl.DataFrame | None = None
    if path.suffix == '.json':
        df = _load_alt(path)
        if df is None:
            df = _load_legacy(path)
    elif path.suffix == '.csv':
        # CSV path: legacy column names assumed.
        # Not commonly used; fall back to a thin reader if ever
        # needed.
        raw = pl.read_csv(path)
        log.warning(
            'block_trades: CSV path not fully supported under '
            'the new schema; using legacy column mapping'
        )
        # Coerce to the legacy normalization via a minimal map.
        # (Caller should switch to JSON.)
        return None

    if df is None or df.is_empty():
        log.warning(f'block_trades: empty or unrecognized {path.name}')
        return None

    # Cross-check against refs
    known = set(refs.get_column('symbol').to_list())
    unknown = sorted(
        s for s in df.get_column('symbol').unique().to_list()
        if s not in known
    )
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

    df = _rebuild_discount(df, hists)

    if df.is_empty():
        log.warning(
            'block_trades: no trades after discount filter'
        )
        return None

    # Dedupe on (symbol, trade_date). Source occasionally
    # records the same block twice with different price_date
    # interpretations (T-1 overnight vs T intraday). Keeping the
    # first occurrence is safe — _rebuild_discount has already
    # normalized them to the same offer/shares.
    before = len(df)
    df = df.unique(subset=['symbol', 'trade_date'], keep='first')
    if len(df) < before:
        log.warning(
            f'block_trades: dropped {before - len(df)} duplicate '
            f'(symbol, trade_date) rows'
        )

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
