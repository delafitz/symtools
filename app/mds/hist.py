import asyncio
from time import perf_counter

import polars as pl

from app.utils.dates import (
    get_delta,
    get_dt_span,
    iso_ts,
    round_ts,
    ts_to_date,
)
from app.utils.logger import get_logger

from app.services.prices import (
    HIST_TEMPLATE_DEFAULT,
    HIST_TEMPLATES,
)

log = get_logger(__name__)

OHLCV_BASE_SCHEMA = {
    'date': pl.String,
    'iso': pl.String,
    'timestamp': pl.Int64,
    'open': pl.Float64,
    'high': pl.Float64,
    'low': pl.Float64,
    'close': pl.Float64,
    'vwap': pl.Float64,
    'volume': pl.Float64,
}

CLOSE_SCHEMA = {
    'date': pl.String,
    'iso': pl.String,
    'timestamp': pl.Int64,
    'close': pl.Float64,
}

OPEN_CLOSE_SCHEMA = {
    'date': pl.String,
    'iso': pl.String,
    'timestamp': pl.Int64,
    'open': pl.Float64,
    'close': pl.Float64,
}


def fetch_hist(
    client,
    symbol: str,
    timespan: str,
    multiplier: int,
    unit: str,
    scale: int,
    close_only: bool = False,
    open_close_only: bool = False,
) -> pl.DataFrame:
    """Fetch OHLCV hist for a symbol."""
    start = perf_counter()
    from_dt, to_dt = get_dt_span(unit, scale)
    delta = get_delta(timespan, multiplier)
    aggs = list(
        client.list_aggs(
            symbol.upper(),
            multiplier,
            timespan,
            from_dt,
            to_dt,
            adjusted='true',
            sort='asc',
            limit=50000,
        )
    )

    if close_only:
        hist = [
            [
                ts_to_date(a.timestamp + delta),
                iso_ts(a.timestamp + delta),
                round_ts(a.timestamp + delta, timespan, multiplier),
                round(a.close, 4),
            ]
            for a in aggs
        ]
        df = pl.DataFrame(hist, schema=CLOSE_SCHEMA, orient='row')
    elif open_close_only:
        hist = [
            [
                ts_to_date(a.timestamp + delta),
                iso_ts(a.timestamp + delta),
                round_ts(a.timestamp + delta, timespan, multiplier),
                round(a.open, 4),
                round(a.close, 4),
            ]
            for a in aggs
        ]
        df = pl.DataFrame(
            hist, schema=OPEN_CLOSE_SCHEMA, orient='row'
        )
    else:
        hist = [
            [
                ts_to_date(a.timestamp + delta),
                iso_ts(a.timestamp + delta),
                round_ts(a.timestamp + delta, timespan, multiplier),
                round(a.open, 4),
                round(a.high, 4),
                round(a.low, 4),
                round(a.close, 4),
                round(a.vwap, 4) if a.vwap else 0.0,
                a.volume,
            ]
            for a in aggs
        ]
        df = pl.DataFrame(
            hist, schema=OHLCV_BASE_SCHEMA, orient='row'
        ).with_columns(
            pl.col('close').pct_change().round(4).alias('pct_return')
        )

    elapsed = perf_counter() - start
    log.blue(
        f'{symbol.upper()} {scale}{unit[0]} '
        f'{from_dt}->{to_dt} '
        f'{timespan}/{multiplier} '
        f'{len(df)} rows {elapsed:.2f}s'
    )

    return df


def fetch_hist_template(
    client,
    symbol: str,
    template: str = HIST_TEMPLATE_DEFAULT,
) -> pl.DataFrame:
    """Fetch hist at maxScale for a template."""
    timespan, multiplier, unit, _, max_scale = HIST_TEMPLATES[
        template
    ]
    return fetch_hist(
        client, symbol, timespan, multiplier, unit, max_scale
    )


async def fetch_hist_async(
    client,
    symbol: str,
    template: str = HIST_TEMPLATE_DEFAULT,
) -> pl.DataFrame:
    """Async wrapper for fetch_hist_template."""
    return await asyncio.to_thread(
        fetch_hist_template, client, symbol, template
    )


async def fetch_symbol_series(
    client,
    symbol: str,
    templates: list[str] | None = None,
) -> dict[str, pl.DataFrame]:
    """Fetch all templates for a symbol concurrently."""
    templates = templates or list(HIST_TEMPLATES.keys())
    tasks = {
        tmpl: asyncio.to_thread(
            fetch_hist_template, client, symbol, tmpl
        )
        for tmpl in templates
    }
    results = {}
    for tmpl, task in tasks.items():
        results[tmpl] = await task
    return results


def fetch_basket_hist(
    client,
    symbol: str,
    template: str = HIST_TEMPLATE_DEFAULT,
) -> pl.DataFrame:
    """Fetch prices for basket analysis (template-aware)."""
    timespan, multiplier, unit, _, max_scale = HIST_TEMPLATES[
        template
    ]
    is_intraday = timespan != 'day'
    df = fetch_hist(
        client,
        symbol,
        timespan,
        multiplier,
        unit,
        max_scale,
        close_only=not is_intraday,
        open_close_only=is_intraday,
    )
    if is_intraday:
        return df.rename(
            {
                'open': f'{symbol}_open',
                'close': symbol,
            }
        )
    return df.rename({'close': symbol})
