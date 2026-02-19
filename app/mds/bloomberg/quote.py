import blpapi
import polars as pl

from app.mds.bloomberg.session import (
    REFDATA_SVC,
    collect,
    sec,
)
from app.models.results import SymbolQuote
from app.utils.logger import get_logger

log = get_logger(__name__)

QUOTE_FIELDS = [
    'PX_LAST',
    'PREV_CLOSE_VALUE_REALTIME',
    'VOLUME',
    'PREV_TOTAL_VOLUME',
    'CHG_NET_1D',
    'CHG_PCT_1D',
    'LAST_TRADE_DATE_TIME_REALTIME',
    'PRE_MKT_LAST_PRICE',
    'AFTER_MKT_LAST_PRICE',
]


def fetch_quote(
    session: blpapi.Session,
    symbol: str,
) -> SymbolQuote:
    """Build quote from Bloomberg snapshot.

    Uses ReferenceDataRequest for point-in-time
    snapshot (not streaming subscription).

    Session detection (pre/post) uses Bloomberg's
    extended-hours fields rather than the Polygon
    timestamp-based approach.
    """
    svc = session.getService(REFDATA_SVC)
    req = svc.createRequest('ReferenceDataRequest')
    req.fromPy(
        {
            'securities': [sec(symbol)],
            'fields': QUOTE_FIELDS,
        }
    )
    session.sendRequest(req)

    fd: dict = {}
    for data in collect(session):
        for s in data['securityData']:
            fd = s.get('fieldData', {})

    prev = fd.get('PREV_CLOSE_VALUE_REALTIME', 0.0)
    last = fd.get('PX_LAST', prev)
    close = last
    volume = fd.get('VOLUME', 0.0) or fd.get('PREV_TOTAL_VOLUME', 0.0)
    chg = fd.get('CHG_NET_1D', 0.0)
    pct_chg = fd.get('CHG_PCT_1D', 0.0)
    updated = fd.get('LAST_TRADE_DATE_TIME_REALTIME', '')

    quote: dict = {
        'symbol': symbol,
        'updated': str(updated),
        'prev': prev,
        'close': close,
        'last': last,
        'volume': float(volume),
        'chg': chg,
        'pct_chg': pct_chg,
    }

    # Extended hours — Bloomberg provides
    # separate fields for pre/post
    pre_px = fd.get('PRE_MKT_LAST_PRICE')
    post_px = fd.get('AFTER_MKT_LAST_PRICE')
    if post_px and post_px > 0:
        quote['session'] = 'post'
        quote['session_last'] = post_px
        quote['session_chg'] = post_px - close
    elif pre_px and pre_px > 0:
        quote['session'] = 'pre'
        quote['session_last'] = pre_px
        quote['session_chg'] = pre_px - prev

    table = pl.DataFrame(
        data={
            'field': [
                'prev',
                'close',
                'last',
                'volume',
                'chg',
                'pctChg',
            ],
            'value': [
                prev,
                close,
                last,
                volume,
                chg,
                pct_chg,
            ],
        }
    )
    log.cyan(f'quote {symbol.upper()}\n{table}')

    return SymbolQuote.model_validate(quote)
