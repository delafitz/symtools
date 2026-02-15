import asyncio

from app.utils.logger import get_logger

log = get_logger(__name__)


def fetch_free_float(
    client, symbol: str, quiet: bool = False
) -> dict | None:
    """Fetch free float data for a symbol.

    Returns dict with:
        - ticker: str
        - effective_date: str
        - free_float: int (shares)
        - free_float_percent: float
    """
    try:
        path = f'/stocks/vX/float?ticker={symbol.upper()}'
        data = client._get(path)
        if data.get('results'):
            result = data['results'][0]
            if not quiet:
                log.info(
                    f'{symbol.upper()} free_float: '
                    f'{result["free_float_percent"]:.1f}% '
                    f'({result["free_float"]:,} shares) '
                    f'as of {result["effective_date"]}'
                )
            return {
                'ticker': result['ticker'],
                'effective_date': result['effective_date'],
                'free_float': result['free_float'],
                'free_float_percent': result['free_float_percent'],
            }
        return None
    except Exception as e:
        log.warning(f'{symbol.upper()} free_float error: {e}')
        return None


async def get_free_float(client, symbol: str) -> dict | None:
    """Async wrapper for fetch_free_float."""
    return await asyncio.to_thread(fetch_free_float, client, symbol)
