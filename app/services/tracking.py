"""Tracking series computation and caching."""

from dataclasses import dataclass

import polars as pl

from app.models.baskets import SymbolBaskets
from app.services.prices import HIST_TEMPLATES
from app.utils.market import slice_hist
from app.utils.groups import SCENARIOS
from app.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class TrackingResult:
    series: pl.DataFrame
    scenarios: list[str]
    symbol_series: dict[str, pl.DataFrame]


def _get_symbol_closes(
    sym: str,
    template: str,
    is_intraday: bool,
    hists: pl.DataFrame,
) -> pl.DataFrame | None:
    """Get closes for a symbol from unified hists."""
    # M is a subset of Y daily data — use Y if M not cached
    lookup = 'Y' if template == 'M' else template
    filtered = hists.filter(
        (pl.col('symbol') == sym) & (pl.col('template') == lookup)
    )
    if filtered.is_empty():
        return None

    cols = ['date']
    if is_intraday and 'timestamp' in filtered.columns:
        cols.append('timestamp')

    return filtered.select(cols + ['close']).rename({'close': sym})


def compute_tracking_for_template(
    symbol: str,
    hist: pl.DataFrame,
    template: str,
    scale: int,
    baskets: SymbolBaskets,
    hists: pl.DataFrame,
    prev_date: str | None = None,
) -> TrackingResult | None:
    """Compute tracking returns for a single template.

    Returns dict with:
        - 'series': DataFrame with date, [timestamp],
          per-scenario bar-over-bar pct returns
        - 'scenarios': list of scenario names present

    If prev_date is provided, the slice is extended back
    to include it so pct_change() produces a real first-bar
    return, then the extra leading rows are trimmed.
    """
    basket_data = baskets.baskets
    if not basket_data:
        return None

    _, _, unit, _, _ = HIST_TEMPLATES[template]
    raw_hist = hist
    hist = slice_hist(hist, unit, scale)

    if hist.is_empty():
        return None

    start_date = hist.select('date').head(1).item()

    # Extend slice back to prev_date for real first-bar return
    if prev_date and prev_date < start_date:
        hist = raw_hist.filter(pl.col('date') >= prev_date).filter(
            pl.col('date') <= hist.select('date').tail(1).item()
        )

    is_intraday = template in ('D', 'W')
    join_on = ['date', 'timestamp'] if is_intraday else 'date'

    # Build tracking series
    series_cols = ['date']
    if is_intraday:
        series_cols.append('timestamp')

    tracking_series = hist.select(series_cols)
    scenarios: list[str] = []
    symbol_series: dict[str, pl.DataFrame] = {}

    # Pre-fetch all basket symbol closes for this template
    all_syms: set[str] = set()
    for name in basket_data:
        all_syms.update(basket_data[name].weights.keys())

    closes_cache: dict[str, pl.DataFrame] = {}
    missing_syms: list[str] = []
    for sym in all_syms:
        closes = _get_symbol_closes(sym, template, is_intraday, hists)
        if closes is not None:
            closes_cache[sym] = closes
        else:
            missing_syms.append(sym)
    if missing_syms:
        log.yellow(
            f'{symbol} {template} tracking: '
            f'missing closes for {missing_syms}'
        )

    for name, (_label, _groups) in SCENARIOS.items():
        if name not in basket_data:
            continue
        weights = basket_data[name].weights
        if not weights:
            continue

        # Join cached closes for this scenario's symbols
        basket_syms = list(weights.keys())
        hist_with_baskets = hist.select(series_cols + ['close'])
        for sym in basket_syms:
            if sym in closes_cache:
                hist_with_baskets = hist_with_baskets.join(
                    closes_cache[sym],
                    on=join_on,
                    how='left',
                )

        available_syms = [
            s for s in basket_syms if s in hist_with_baskets.columns
        ]
        if not available_syms:
            continue

        # Forward-fill, backward-fill, and pct_change
        hist_with_baskets = hist_with_baskets.with_columns(
            pl.col(sym).forward_fill().backward_fill()
            for sym in available_syms
        ).with_columns(
            pl.col(sym).pct_change().fill_null(0).alias(f'{sym}_ret')
            for sym in available_syms
        )

        # Weighted average return per bar
        total_weight = sum(weights.get(s, 0) for s in available_syms)
        if total_weight == 0:
            continue

        weighted_expr = pl.sum_horizontal(
            pl.col(f'{sym}_ret') * weights.get(sym, 0)
            for sym in available_syms
            if weights.get(sym, 0) > 0
        )
        hist_with_baskets = hist_with_baskets.with_columns(
            (weighted_expr / total_weight).alias(name)
        )

        # Capture per-symbol returns
        sym_df = hist_with_baskets.select(
            series_cols + [f'{sym}_ret' for sym in available_syms]
        ).rename({f'{sym}_ret': sym for sym in available_syms})
        symbol_series[name] = sym_df

        # Add to tracking series
        basket_series = hist_with_baskets.select(series_cols + [name])
        tracking_series = tracking_series.join(
            basket_series, on=join_on, how='left'
        )
        tracking_series = tracking_series.with_columns(
            pl.col(name).fill_null(0)
        )
        scenarios.append(name)

    if not scenarios:
        return None

    # Trim leading rows from prev_date extension
    if prev_date and prev_date < start_date:
        tracking_series = tracking_series.filter(
            pl.col('date') >= start_date
        )
        for k in symbol_series:
            symbol_series[k] = symbol_series[k].filter(
                pl.col('date') >= start_date
            )

    return TrackingResult(
        series=tracking_series,
        scenarios=scenarios,
        symbol_series=symbol_series,
    )
