import asyncio
from typing import TYPE_CHECKING, Callable

import polars as pl

from app.models.hist import BasketHist, HistStats
from app.services.prices import HIST_TEMPLATES
from app.services.tracking import TrackingResult
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.mds.provider import MarketDataProvider

log = get_logger(__name__)


def _basket_stats(
    parent_stats: dict[int, HistStats],
    bars: list[dict],
    is_intraday: bool,
) -> dict[int, HistStats]:
    """Compute per-scale stats for a basket.

    Reuses parent date anchors; cumulates bar
    pct_return values to derive end_price and
    range_pct_return.
    """
    if not bars:
        return {}
    stats: dict[int, HistStats] = {}
    for scale, ps in parent_stats.items():
        prev_close = ps.prev_close
        cum = prev_close
        for b in bars:
            if is_intraday:
                ts = b.get('timestamp')
                if ts is None:
                    continue
                # use date for start, timestamp
                # for end boundary
                if b['date'] < ps.start_date:
                    continue
                if b['date'] > ps.end_date:
                    break
            else:
                if b['date'] < ps.start_date:
                    continue
                if b['date'] > ps.end_date:
                    break
            cum *= 1 + b['pct_return']
        end_price = round(cum, 6)
        rr = (end_price / prev_close - 1) if prev_close else None
        stats[scale] = HistStats(
            end_date=ps.end_date,
            end_price=end_price,
            start_date=ps.start_date,
            prev_date=ps.prev_date,
            prev_close=prev_close,
            range_vwap=None,
            range_pct_return=rr,
        )
    return stats


def build_basket_hists(
    symbol: str,
    template: str,
    tracking: TrackingResult,
    parent_stats: dict[int, HistStats],
) -> list[BasketHist]:
    """Split tracking data into one BasketHist
    per scenario."""
    series = tracking.series
    scenarios = tracking.scenarios
    is_intraday = template in ('D', 'W')

    results: list[BasketHist] = []
    for name in scenarios:
        bars: list[dict] = []
        if name in series.columns:
            cols = ['date']
            if is_intraday and 'timestamp' in series.columns:
                cols.append('timestamp')
            bars = (
                series.select(cols + [name])
                .rename({name: 'pct_return'})
                .to_dicts()
            )
        stats = _basket_stats(parent_stats, bars, is_intraday)
        results.append(
            BasketHist.model_validate(
                {
                    'symbol': symbol,
                    'template': template,
                    'basket': name,
                    'stats': stats,
                    'bars': bars,
                }
            )
        )
    return results


async def load_symbol_series(
    mds: 'MarketDataProvider',
    symbol: str,
    existing: dict[str, pl.DataFrame],
    on_update: Callable[[str, str, pl.DataFrame], None],
):
    """Load all hist templates for a symbol,
    skipping already cached.
    Calls on_update(symbol, template, hist)
    as each completes.
    """
    for template in HIST_TEMPLATES:
        if template in existing:
            continue
        hist = await asyncio.to_thread(
            mds.get_hist_template,
            symbol,
            template,
        )
        on_update(symbol, template, hist)


async def load_symbol_template(
    mds: 'MarketDataProvider',
    symbol: str,
    template: str,
) -> pl.DataFrame:
    """Load a single template for a symbol."""
    return await asyncio.to_thread(
        mds.get_hist_template,
        symbol,
        template,
    )
