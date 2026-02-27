import asyncio
from datetime import date
from time import perf_counter
from typing import TYPE_CHECKING, Callable

import polars as pl

from app.mds.polygon.refs import REF_SCHEMA
from app.services.prices import (
    HIST_TEMPLATE_DEFAULT,
    HIST_TEMPLATES,
)
from app.utils.groups import (
    TYPE_STOCK,
    get_all_etf_symbols,
    get_symbol_type,
)
from app.utils.logger import get_logger
from app.utils.store import get_store, write_store

if TYPE_CHECKING:
    from app.mds.provider import MarketDataProvider

log = get_logger(__name__)

CONCURRENCY = 15

# Extended schema with type column
REFS_SCHEMA = {
    **REF_SCHEMA,
    'type': pl.String,
}


def get_cached_refs() -> pl.DataFrame | None:
    """Load refs from today's cache if available."""
    return get_store('refs')


def get_cached_hists() -> pl.DataFrame | None:
    """Load unified hists from today's cache if available."""
    return get_store('hists')


async def load_refs_async(
    mds: 'MarketDataProvider',
    tickers: pl.DataFrame,
    on_refs_update: Callable[[pl.DataFrame], None],
    on_hists_update: Callable[[pl.DataFrame], None],
    top_hists: int = 5000,
):
    """
    Load refs with details, floats, and hists using async pool.

    Steps:
    1. Merge ETF symbols into refs with type column
    2. Fetch details for stock tickers (filters by mkt_cap)
    3. Fetch floats for filtered refs
    4. Fetch hists for all symbols x all templates
    5. Save and report summary
    """
    # Get ETF symbols first
    etf_symbols = get_all_etf_symbols()
    etf_set = set(etf_symbols)

    # Get stock symbols from tickers (excluding ETFs)
    stock_symbols = [
        s
        for s in tickers.get_column('symbol').to_list()
        if s not in etf_set
    ]
    ticker_dicts = {row['symbol']: row for row in tickers.to_dicts()}

    semaphore = asyncio.Semaphore(CONCURRENCY)

    # Phase 1: Fetch details for stocks
    details_done = 0
    details_found = 0
    total_tickers = len(stock_symbols)

    async def fetch_detail(symbol: str) -> dict | None:
        nonlocal details_done, details_found
        async with semaphore:
            try:
                details = await asyncio.wait_for(
                    asyncio.to_thread(mds.get_details, symbol),
                    timeout=10.0,
                )
                details_done += 1
                if details_done % 50 == 0:
                    log.info(
                        f'  details: {details_done}/{total_tickers} '
                        f'({details_found} found)'
                    )
                if details:
                    details_found += 1
                    ticker = ticker_dicts[symbol]
                    return {
                        'symbol': ticker['symbol'],
                        'exch': ticker['exch'],
                        'name': ticker['name'],
                        'curr': ticker['curr'],
                        'cik': details['cik'],
                        'sic': details['sic'],
                        'shares_out': details['shares_out'],
                        'mkt_cap': details['mkt_cap'],
                        'free_float': 0,
                        'free_float_pct': 0.0,
                        'free_float_date': '',
                        'short_interest': 0,
                        'days_to_cover': 0.0,
                        'short_avg_vol': 0,
                        'short_interest_date': '',
                        'g_sector': details.get('g_sector', ''),
                        'type': TYPE_STOCK,
                    }
            except asyncio.TimeoutError:
                details_done += 1
                log.debug(f'timeout fetching details for {symbol}')
            except Exception:
                details_done += 1
            return None

    details_start = perf_counter()
    log.info(f'fetching details for {total_tickers} tickers...')

    tasks = [fetch_detail(sym) for sym in stock_symbols]
    results = await asyncio.gather(*tasks)
    refs_rows = [r for r in results if r is not None]

    details_elapsed = perf_counter() - details_start
    filtered_count = total_tickers - len(refs_rows)
    log.info(
        f'details: {len(refs_rows)} loaded, '
        f'{filtered_count} filtered by mkt_cap, '
        f'{details_elapsed:.1f}s'
    )

    # Write refs CSV (symbol, name, cik)
    if refs_rows:
        csv_date = date.today().strftime('%Y%m%d')
        csv_path = f'./data/refs.{csv_date}.csv'
        pl.DataFrame(refs_rows).select(
            'symbol', 'name', 'cik', 'mkt_cap'
        ).write_csv(csv_path)
        log.info(f'wrote {csv_path}')

    # Add ETF symbols to refs (with placeholder data)
    log.info(f'adding {len(etf_symbols)} ETF symbols to refs...')
    for etf_sym in etf_symbols:
        refs_rows.append(
            {
                'symbol': etf_sym,
                'exch': 'ETF',
                'name': etf_sym,
                'curr': 'USD',
                'cik': '',
                'sic': 0,
                'shares_out': 0,
                'mkt_cap': 0.0,
                'free_float': 0,
                'free_float_pct': 0.0,
                'free_float_date': '',
                'short_interest': 0,
                'days_to_cover': 0.0,
                'short_avg_vol': 0,
                'short_interest_date': '',
                'g_sector': '',
                'type': get_symbol_type(etf_sym),
            }
        )

    # Update cache with refs (stocks + ETFs)
    log.info(f'creating refs DataFrame with {len(refs_rows)} rows...')
    if refs_rows:
        refs = pl.DataFrame(refs_rows, schema=REFS_SCHEMA)
        log.info('updating cache with refs...')
        on_refs_update(refs)

    log.info('starting Phase 2...')
    # Phase 2: Fetch floats + short interest for stocks
    stock_rows = [r for r in refs_rows if r['type'] == TYPE_STOCK]
    phase2_total = len(stock_rows)
    phase2_done = 0
    floats_found = 0
    si_found = 0

    async def fetch_float_and_si(row: dict) -> None:
        nonlocal phase2_done, floats_found, si_found
        async with semaphore:
            try:
                float_data, si_data = await asyncio.wait_for(
                    asyncio.gather(
                        asyncio.to_thread(
                            mds.get_float,
                            row['symbol'],
                            True,
                        ),
                        asyncio.to_thread(
                            mds.get_short_interest,
                            row['symbol'],
                            True,
                        ),
                    ),
                    timeout=10.0,
                )
                if float_data:
                    floats_found += 1
                    row['free_float'] = float_data['free_float']
                    row['free_float_pct'] = float_data[
                        'free_float_percent'
                    ]
                    row['free_float_date'] = (
                        float_data['effective_date'] or ''
                    )
                if si_data:
                    si_found += 1
                    row['short_interest'] = (
                        si_data['short_interest'] or 0
                    )
                    row['days_to_cover'] = (
                        si_data['days_to_cover'] or 0.0
                    )
                    row['short_avg_vol'] = (
                        si_data['avg_daily_volume'] or 0
                    )
                    row['short_interest_date'] = (
                        si_data['settlement_date'] or ''
                    )
            except (asyncio.TimeoutError, Exception):
                pass
            finally:
                phase2_done += 1
                if phase2_done % 50 == 0:
                    log.info(
                        f'  phase2: {phase2_done}'
                        f'/{phase2_total} '
                        f'(floats={floats_found} '
                        f'si={si_found})'
                    )

    floats_start = perf_counter()
    log.info(
        f'fetching floats + short interest for {phase2_total} refs...'
    )

    tasks = [fetch_float_and_si(row) for row in stock_rows]
    await asyncio.gather(*tasks)

    floats_elapsed = perf_counter() - floats_start
    log.info(
        f'phase2: floats={floats_found} si={si_found}, '
        f'{floats_elapsed:.1f}s'
    )

    # Phase 3: Fetch hists for all symbols x all templates
    # Prioritize: ETFs first, then top stocks by mkt_cap
    sorted_stocks = sorted(
        stock_rows, key=lambda r: r['mkt_cap'], reverse=True
    )
    symbols_to_fetch = etf_symbols + [
        r['symbol'] for r in sorted_stocks[:top_hists]
    ]

    all_hist_rows: list[pl.DataFrame] = []
    hists_total = len(symbols_to_fetch)
    hists_done = 0
    hists_found = 0

    async def fetch_hist(symbol: str) -> bool:
        nonlocal hists_done, hists_found
        async with semaphore:
            try:
                hist = await asyncio.wait_for(
                    asyncio.to_thread(
                        mds.get_hist_template,
                        symbol,
                        HIST_TEMPLATE_DEFAULT,
                        True,
                    ),
                    timeout=30.0,
                )
                if not hist.is_empty():
                    hists_found += 1
                    hist_with_meta = hist.with_columns(
                        pl.lit(symbol).alias('symbol'),
                        pl.lit(HIST_TEMPLATE_DEFAULT).alias(
                            'template'
                        ),
                    )
                    all_hist_rows.append(hist_with_meta)
                    return True
            except (asyncio.TimeoutError, Exception):
                pass
            finally:
                hists_done += 1
                if hists_done % 50 == 0:
                    log.info(
                        f'  hists: {hists_done}/{hists_total} '
                        f'({hists_found} loaded)'
                    )
            return False

    hists_start = perf_counter()
    log.info(
        f'fetching Y hists for {len(symbols_to_fetch)} symbols...'
    )

    tasks = [fetch_hist(sym) for sym in symbols_to_fetch]
    await asyncio.gather(*tasks)

    hists_elapsed = perf_counter() - hists_start
    log.info(f'hists: {hists_found} loaded, {hists_elapsed:.1f}s')

    # Phase 4: Fetch remaining templates for ETF symbols
    other_templates = [
        t for t in HIST_TEMPLATES if t != HIST_TEMPLATE_DEFAULT
    ]
    etf_templates_total = len(etf_symbols) * len(other_templates)
    etf_templates_done = 0
    etf_templates_found = 0

    async def fetch_etf_template(symbol: str, template: str) -> bool:
        nonlocal etf_templates_done, etf_templates_found
        async with semaphore:
            try:
                hist = await asyncio.wait_for(
                    asyncio.to_thread(
                        mds.get_hist_template,
                        symbol,
                        template,
                        True,
                    ),
                    timeout=30.0,
                )
                if not hist.is_empty():
                    etf_templates_found += 1
                    all_hist_rows.append(
                        hist.with_columns(
                            pl.lit(symbol).alias('symbol'),
                            pl.lit(template).alias('template'),
                        )
                    )
                    return True
            except (asyncio.TimeoutError, Exception):
                pass
            finally:
                etf_templates_done += 1
                if etf_templates_done % 20 == 0:
                    log.info(
                        f'  etf hists: '
                        f'{etf_templates_done}'
                        f'/{etf_templates_total} '
                        f'({etf_templates_found} loaded)'
                    )
            return False

    etf_start = perf_counter()
    log.info(
        f'fetching {len(other_templates)} templates '
        f'for {len(etf_symbols)} ETFs...'
    )

    tasks = [
        fetch_etf_template(sym, tmpl)
        for sym in etf_symbols
        for tmpl in other_templates
    ]
    await asyncio.gather(*tasks)

    etf_elapsed = perf_counter() - etf_start
    log.info(
        f'etf hists: {etf_templates_found} loaded, {etf_elapsed:.1f}s'
    )

    # Combine and save hists
    if all_hist_rows:
        combined_hists = pl.concat(all_hist_rows)
        write_store(combined_hists, 'hists')
        on_hists_update(combined_hists)

    # Save final refs
    if refs_rows:
        refs = pl.DataFrame(refs_rows, schema=REFS_SCHEMA)
        on_refs_update(refs)
        write_store(refs, 'refs')

    # Return stats for summary logging
    return {
        'details_count': len(refs_rows),
        'details_elapsed': details_elapsed,
        'floats_count': floats_found,
        'floats_elapsed': floats_elapsed,
        'hists_count': hists_found,
        'hists_elapsed': hists_elapsed,
    }
