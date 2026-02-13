import asyncio

from polygon import RESTClient

from app.models.results import SymbolQuote
from app.utils.dates import ns_to_dt
from app.utils.market import get_session


async def get_symbol_quote(
    client: RESTClient, symbol: str
) -> SymbolQuote:
    return await asyncio.to_thread(fetch_quote, client, symbol)


def fetch_quote(client: RESTClient, symbol: str) -> SymbolQuote:
    # API expects uppercase
    s = client.get_snapshot_ticker('stocks', symbol.upper())
    close = s.day.close if s.day.close > 0 else s.prev_day.close
    last = s.last_trade.price if s.last_trade else s.prev_day.close
    quote: dict = {
        'symbol': symbol,
        'updated': ns_to_dt(s.updated).isoformat(),
        'prev': s.prev_day.close,
        'close': close,
        'last': last,
        'volume': (
            s.day.volume if s.day.volume > 0 else s.prev_day.volume
        ),
        'chg': s.todays_change,
        'pct_chg': s.todays_change_percent,
    }

    session = get_session(s.updated // 1_000_000)
    if session in ('pre', 'post'):
        ref = s.prev_day.close if session == 'pre' else close
        quote['session'] = session
        if s.last_trade:
            quote['session_last'] = last
            quote['session_chg'] = last - ref
    elif session == 'closed':
        quote['session'] = session

    return SymbolQuote.model_validate(quote)
