import asyncio

from massive import RESTClient

from app.utils.logger import get_logger

log = get_logger(__name__)


def fetch_short_interest(
    client: RESTClient,
    symbol: str,
    quiet: bool = False,
) -> dict | None:
    """Fetch short interest data for a symbol.

    Returns dict with:
        - ticker: str
        - settlement_date: str
        - short_interest: int
        - days_to_cover: float
        - avg_daily_volume: int
    """
    try:
        r = next(
            iter(
                client.list_short_interest(
                    ticker=symbol.upper(),
                    limit=1,
                    settlement_date_gte='2025-01-01',
                )
            ),
            None,
        )
        if r:
            if not quiet:
                log.info(
                    f'{symbol.upper()} short_interest: '
                    f'{r.short_interest:,} shares '
                    f'({r.days_to_cover:.1f} DTC) '
                    f'as of {r.settlement_date}'
                )
            return {
                'ticker': r.ticker,
                'settlement_date': r.settlement_date,
                'short_interest': r.short_interest,
                'days_to_cover': r.days_to_cover,
                'avg_daily_volume': r.avg_daily_volume,
            }
        return None
    except Exception as e:
        log.warning(f'{symbol.upper()} short_interest error: {e}')
        return None


async def get_short_interest(
    client: RESTClient, symbol: str
) -> dict | None:
    """Async wrapper for fetch_short_interest."""
    return await asyncio.to_thread(
        fetch_short_interest, client, symbol, True
    )
