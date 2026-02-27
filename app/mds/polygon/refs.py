import polars as pl

from app.utils.corp import strip_name
from app.utils.logger import get_logger
from app.utils.sic_gics import sic_to_sector

log = get_logger(__name__)

CHUNK_LIMIT = 1000
MIN_MKT_CAP = 1e9

# Minimal schema for fast startup (from list_tickers)
TICKER_SCHEMA = {
    'symbol': pl.String,
    'exch': pl.String,
    'name': pl.String,
    'curr': pl.String,
}

# Full schema with details
REF_SCHEMA = {
    'symbol': pl.String,
    'exch': pl.String,
    'name': pl.String,
    'curr': pl.String,
    'cik': pl.String,
    'sic': pl.String,
    'shares_out': pl.Int64,
    'mkt_cap': pl.Float64,
    'free_float': pl.Int64,
    'free_float_pct': pl.Float64,
    'free_float_date': pl.String,
    'short_interest': pl.Int64,
    'days_to_cover': pl.Float64,
    'short_avg_vol': pl.Int64,
    'short_interest_date': pl.String,
    'g_sector': pl.String,
}


def list_tickers(client, max_tickers: int):
    """Fast: get ticker list without details."""
    log.info(f'list_tickers: fetching up to {max_tickers} tickers...')
    tickers = client.list_tickers(
        market='stocks', type='CS', active=True, limit=CHUNK_LIMIT
    )

    rows = []
    for ticker in tickers:
        if len(rows) >= max_tickers:
            log.info(f'list_tickers: reached limit of {max_tickers}')
            break
        rows.append(
            [
                ticker.ticker.lower(),
                ticker.primary_exchange[1:],
                strip_name(ticker.name),
                ticker.currency_name.upper(),
            ]
        )
        if len(rows) % 500 == 0:
            log.info(f'list_tickers: {len(rows)} tickers so far...')

    log.info(f'list_tickers: returning {len(rows)} tickers')
    return pl.DataFrame(data=rows, schema=TICKER_SCHEMA, orient='row')


def fetch_ticker_details(client, symbol: str):
    """Fetch details for a single ticker. Returns None if below market cap threshold."""
    details = client.get_ticker_details(symbol.upper())
    if details.market_cap and details.market_cap > MIN_MKT_CAP:
        return {
            'cik': details.cik or '',
            'sic': details.sic_description or '',
            'shares_out': details.share_class_shares_outstanding or 0,
            'mkt_cap': details.market_cap,
            'g_sector': sic_to_sector(details.sic_description or ''),
        }
    return None
