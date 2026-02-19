import os
from collections.abc import Iterator

import blpapi

from app.utils.logger import get_logger

log = get_logger(__name__)

BLOOMBERG_HOST = os.getenv('BLOOMBERG_HOST', 'localhost')
BLOOMBERG_PORT = int(os.getenv('BLOOMBERG_PORT', '8194'))
REFDATA_SVC = '//blp/refdata'


def create_session() -> blpapi.Session:
    """Create and start a Bloomberg session."""
    opts = blpapi.SessionOptions()
    opts.setServerHost(BLOOMBERG_HOST)
    opts.setServerPort(BLOOMBERG_PORT)
    session = blpapi.Session(opts)
    if not session.start():
        raise RuntimeError('Failed to start Bloomberg session')
    if not session.openService(REFDATA_SVC):
        raise RuntimeError(f'Failed to open {REFDATA_SVC}')
    return session


def collect(
    session: blpapi.Session,
) -> Iterator[dict]:
    """Yield toPy() dicts from response events."""
    while True:
        event = session.nextEvent(5000)
        et = event.eventType()
        if et in (
            blpapi.Event.PARTIAL_RESPONSE,
            blpapi.Event.RESPONSE,
        ):
            for msg in event:
                yield msg.toPy()
        if et == blpapi.Event.RESPONSE:
            return


def sec(symbol: str) -> str:
    """Bare ticker -> Bloomberg security ID.

    'aapl' -> 'AAPL US Equity'
    """
    return f'{symbol.upper()} US Equity'
