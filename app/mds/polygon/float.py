from massive import RESTClient

from app.utils.logger import get_logger

log = get_logger(__name__)


def fetch_free_float(
    client: RESTClient,
    symbol: str,
    quiet: bool = False,
) -> dict | None:
    """Fetch free float data for a symbol.

    Returns dict with:
        - ticker: str
        - effective_date: str
        - free_float: int (shares)
        - free_float_percent: float
    """
    try:
        r = next(
            client.list_stocks_floats(ticker=symbol.upper(), limit=1),
            None,
        )
        if r:
            if not quiet:
                log.info(
                    f'{symbol.upper()} free_float: '
                    f'{r.free_float_percent:.1f}% '
                    f'({r.free_float:,} shares) '
                    f'as of {r.effective_date}'
                )
            return {
                'ticker': r.ticker,
                'effective_date': (r.effective_date),
                'free_float': r.free_float,
                'free_float_percent': (r.free_float_percent),
            }
        return None
    except Exception as e:
        log.warning(f'{symbol.upper()} free_float error: {e}')
        return None
