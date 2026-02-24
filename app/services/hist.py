import asyncio
from typing import TYPE_CHECKING, Callable

import polars as pl

from app.models.baskets import SymbolBaskets
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


def _pct(v: float | None) -> str:
    return f'{v * 100:.2f}%' if v is not None else ''


def _sym_close(
    hists: pl.DataFrame,
    sym: str,
    date: str,
    intraday: bool = False,
) -> float | None:
    """Close for a symbol on or before date.

    For intraday=True, uses D template (latest
    minute bar). Falls back to Y daily close.
    """
    if intraday:
        rows = hists.filter(
            (pl.col('symbol') == sym)
            & (pl.col('template') == 'D')
            & (pl.col('date') <= date)
        ).sort('date')
        if not rows.is_empty():
            return rows['close'][-1]
    rows = hists.filter(
        (pl.col('symbol') == sym)
        & (pl.col('template') == 'Y')
        & (pl.col('date') <= date)
    ).sort('date')
    if rows.is_empty():
        return None
    return rows['close'][-1]


def _log_d_basket(
    symbol: str,
    name: str,
    weights: dict[str, float],
    parent_stats: dict[int, HistStats],
    stats: dict[int, HistStats],
    hists: pl.DataFrame,
) -> None:
    """Log D basket_hist details for debugging."""
    # Summary table: target vs basket per scale
    summary_rows: list[dict] = []
    log_scales = {1, 3}
    for scale in sorted(parent_stats):
        if scale not in log_scales:
            continue
        ps = parent_stats[scale]
        bs = stats.get(scale)
        summary_rows.append(
            {
                'scale': f'{scale}d',
                'tgt_prev': ps.prev_close,
                'tgt_end': ps.end_price,
                'tgt_ret': _pct(ps.range_pct_return),
                'bsk_prev': bs.prev_close if bs else None,
                'bsk_end': bs.end_price if bs else None,
                'bsk_ret': _pct(bs.range_pct_return if bs else None),
            }
        )

    # Per-symbol table: weight, prev, end, return per scale
    sym_rows: list[dict] = []
    for sym, w in weights.items():
        for scale in sorted(parent_stats):
            if scale not in log_scales:
                continue
            ps = parent_stats[scale]
            prev = _sym_close(hists, sym, ps.prev_date)
            end = _sym_close(hists, sym, ps.end_date, intraday=True)
            ret = (end / prev - 1) if prev and end else None
            sym_rows.append(
                {
                    'symbol': sym,
                    'weight': w,
                    'scale': f'{scale}d',
                    'prev': prev,
                    'end': end,
                    'sym_ret': _pct(ret),
                }
            )

    summary = pl.DataFrame(summary_rows)
    syms = pl.DataFrame(sym_rows)
    log.cyan(f'{symbol} D {name}\n{summary}\n{syms}')


def build_basket_hists(
    symbol: str,
    template: str,
    tracking: TrackingResult,
    parent_stats: dict[int, HistStats],
    baskets_model: SymbolBaskets | None = None,
    hists: pl.DataFrame | None = None,
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

        if (
            template == 'D'
            and name == 'indices'
            and baskets_model
            and hists is not None
        ):
            basket = baskets_model.baskets.get(name)
            weights = basket.weights if basket else {}
            _log_d_basket(
                symbol,
                name,
                weights,
                parent_stats,
                stats,
                hists,
            )

        # Extract per-symbol returns
        symbols: dict[str, list[dict]] = {}
        sym_df = tracking.symbol_series.get(name)
        if sym_df is not None:
            cols = ['date']
            if is_intraday and 'timestamp' in sym_df.columns:
                cols.append('timestamp')
            for col in sym_df.columns:
                if col in ('date', 'timestamp'):
                    continue
                symbols[col] = (
                    sym_df.select(cols + [col])
                    .rename({col: 'pct_return'})
                    .to_dicts()
                )

        results.append(
            BasketHist.model_validate(
                {
                    'symbol': symbol,
                    'template': template,
                    'basket': name,
                    'stats': stats,
                    'weighted': bars,
                    'symbols': symbols,
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
