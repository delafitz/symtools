import asyncio

import polars as pl
from massive import RESTClient

from app.models.results import SymbolQuote
from app.utils.dates import ns_to_dt
from app.utils.logger import get_logger
from app.utils.market import get_session

log = get_logger(__name__)


async def get_symbol_quote(
    client: RESTClient, symbol: str
) -> SymbolQuote:
    return await asyncio.to_thread(fetch_quote, client, symbol)


def fetch_quote(client: RESTClient, symbol: str) -> SymbolQuote:
    """Build quote from Polygon snapshot.

    Fields by session:

    | Field       | closed    | pre          | market     | post          |
    |-------------|-----------|--------------|------------|---------------|
    | prev        | prev close| prev close   | prev close | prev close    |
    | close       | =prev     | =prev        | today run  | today final   |
    | last        | =prev     | pre-mkt px   | ≈close     | post-mkt px   |
    | volume      | prev vol  | prev vol     | today vol  | today vol     |
    | chg/pctChg  | from API  | from API     | from API   | from API      |
    | session     | "closed"  | "pre"        | null       | "post"        |
    | sessionLast | null      | =last        | null       | =last         |
    | sessionChg  | null      | last - prev  | null       | last - close  |
    | sessionVol  | null      | min.accum_vol| null       | min.accum_vol |

    Price resolution:  last_trade > min.close > prev_day.close
    Volume resolution: day.volume > prev_day.volume
    Close resolution:  day.close > prev_day.close

    sessionLast duplicates last for convenience — lets clients
    grab all session-specific fields without checking session type.
    """  # noqa: E501
    s = client.get_snapshot_ticker('stocks', symbol.upper())
    close = s.day.close if s.day.close > 0 else s.prev_day.close
    # Best last price: last_trade > min bar > prev close
    if s.last_trade and s.last_trade.price > 0:
        last = s.last_trade.price
    elif s.min and s.min.close > 0:
        last = s.min.close
    else:
        last = s.prev_day.close

    # Volume: day if available, else prev day
    if s.day.volume > 0:
        volume = float(s.day.volume)
    else:
        volume = float(s.prev_day.volume)

    quote: dict = {
        'symbol': symbol,
        'updated': ns_to_dt(s.updated).isoformat(),
        'prev': s.prev_day.close,
        'close': close,
        'last': last,
        'volume': volume,
        'chg': s.todays_change,
        'pct_chg': s.todays_change_percent,
    }

    session = get_session(s.updated // 1_000_000)
    if session in ('pre', 'post'):
        ref = s.prev_day.close if session == 'pre' else close
        quote['session'] = session
        quote['session_last'] = last
        quote['session_chg'] = last - ref
        if s.min and s.min.accumulated_volume > 0:
            quote['session_volume'] = float(s.min.accumulated_volume)
    elif session == 'closed':
        quote['session'] = session

    min_close = float(s.min.close) if s.min else 0.0
    min_vol = float(s.min.accumulated_volume) if s.min else 0.0
    table = pl.DataFrame(
        data={
            'field': [
                'prev',
                'close',
                'last',
                'volume',
                'chg',
                'pctChg',
                'sessionLast',
                'sessionChg',
                'sessionVol',
            ],
            'prevDay': [
                float(s.prev_day.close),
                None,
                None,
                float(s.prev_day.volume),
                None,
                None,
                None,
                None,
                None,
            ],
            'day': [
                None,
                float(s.day.close),
                None,
                float(s.day.volume),
                float(s.todays_change),
                float(s.todays_change_percent),
                None,
                None,
                None,
            ],
            'min': [
                None,
                min_close,
                min_close,
                min_vol,
                None,
                None,
                None,
                None,
                min_vol,
            ],
            'lastTrade': [
                None,
                None,
                (float(s.last_trade.price) if s.last_trade else None),
                None,
                None,
                None,
                None,
                None,
                None,
            ],
            'result': [
                quote['prev'],
                close,
                last,
                volume,
                quote['chg'],
                quote['pct_chg'],
                quote.get('session_last'),
                quote.get('session_chg'),
                quote.get('session_volume'),
            ],
        }
    )
    log.cyan(f'quote {symbol.upper()} [{session}]\n{table}')

    return SymbolQuote.model_validate(quote)
