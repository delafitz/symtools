from datetime import datetime
from time import perf_counter

import blpapi
import polars as pl

from app.mds.bloomberg.session import (
    REFDATA_SVC,
    collect,
    sec,
)
from app.mds.polygon.hist import (
    CLOSE_SCHEMA,
    OHLCV_BASE_SCHEMA,
    OPEN_CLOSE_SCHEMA,
)
from app.services.prices import (
    HIST_TEMPLATE_DEFAULT,
    HIST_TEMPLATES,
)
from app.utils.dates import (
    get_dt_span,
    iso_ts,
    round_ts,
    ts_to_date,
)
from app.utils.logger import get_logger
from app.utils.market import ET

log = get_logger(__name__)

DAILY_FIELDS = [
    'OPEN',
    'HIGH',
    'LOW',
    'PX_LAST',
    'VOLUME',
    'VWAP',
]


def _daily_to_ohlcv(
    bars: list[dict],
    close_only: bool,
    open_close_only: bool,
) -> pl.DataFrame:
    """Bloomberg daily bars -> OHLCV schema."""
    rows = []
    for bar in bars:
        d = bar.get('date')
        if d is None:
            continue
        ds = d.strftime('%Y-%m-%d')
        ts_ms = int(
            datetime(d.year, d.month, d.day, tzinfo=ET).timestamp()
            * 1000
        )

        if close_only:
            rows.append(
                [
                    ds,
                    ds,
                    ts_ms,
                    round(bar.get('PX_LAST', 0.0), 4),
                ]
            )
        elif open_close_only:
            rows.append(
                [
                    ds,
                    ds,
                    ts_ms,
                    round(bar.get('OPEN', 0.0), 4),
                    round(bar.get('PX_LAST', 0.0), 4),
                ]
            )
        else:
            rows.append(
                [
                    ds,
                    ds,
                    ts_ms,
                    round(bar.get('OPEN', 0.0), 4),
                    round(bar.get('HIGH', 0.0), 4),
                    round(bar.get('LOW', 0.0), 4),
                    round(bar.get('PX_LAST', 0.0), 4),
                    round(bar.get('VWAP', 0.0), 4),
                    bar.get('VOLUME', 0),
                ]
            )

    if close_only:
        schema = CLOSE_SCHEMA
    elif open_close_only:
        schema = OPEN_CLOSE_SCHEMA
    else:
        schema = OHLCV_BASE_SCHEMA

    df = pl.DataFrame(rows, schema=schema, orient='row')
    if not close_only and not open_close_only:
        df = df.with_columns(
            pl.col('close').pct_change().round(4).alias('pct_return')
        )
    return df


def _intraday_to_ohlcv(
    bars: list[dict],
    timespan: str,
    multiplier: int,
    close_only: bool,
    open_close_only: bool,
) -> pl.DataFrame:
    """Bloomberg intraday bars -> OHLCV schema.

    Intraday bars have 'value' (turnover) field;
    vwap = value / volume.
    """
    rows = []
    for bar in bars:
        t = bar.get('time')
        if t is None:
            continue
        ts_ms = int(t.timestamp() * 1000)
        ds = ts_to_date(ts_ms)
        iso = iso_ts(ts_ms)
        rts = round_ts(ts_ms, timespan, multiplier)

        if close_only:
            rows.append(
                [
                    ds,
                    iso,
                    rts,
                    round(bar.get('close', 0.0), 4),
                ]
            )
        elif open_close_only:
            rows.append(
                [
                    ds,
                    iso,
                    rts,
                    round(bar.get('open', 0.0), 4),
                    round(bar.get('close', 0.0), 4),
                ]
            )
        else:
            vol = bar.get('volume', 0)
            cl = bar.get('close', 0.0)
            val = bar.get('value', 0.0)
            vwap = round(val / vol, 4) if vol > 0 else round(cl, 4)
            rows.append(
                [
                    ds,
                    iso,
                    rts,
                    round(bar.get('open', 0.0), 4),
                    round(bar.get('high', 0.0), 4),
                    round(bar.get('low', 0.0), 4),
                    round(cl, 4),
                    vwap,
                    vol,
                ]
            )

    if close_only:
        schema = CLOSE_SCHEMA
    elif open_close_only:
        schema = OPEN_CLOSE_SCHEMA
    else:
        schema = OHLCV_BASE_SCHEMA

    df = pl.DataFrame(rows, schema=schema, orient='row')
    if not close_only and not open_close_only:
        df = df.with_columns(
            pl.col('close').pct_change().round(4).alias('pct_return')
        )
    return df


def fetch_hist(
    session: blpapi.Session,
    symbol: str,
    timespan: str,
    multiplier: int,
    unit: str,
    scale: int,
    close_only: bool = False,
    open_close_only: bool = False,
    quiet: bool = False,
) -> pl.DataFrame:
    """Fetch OHLCV hist for a symbol."""
    start = perf_counter()
    from_dt, to_dt = get_dt_span(unit, scale)
    svc = session.getService(REFDATA_SVC)

    if timespan == 'day':
        fields = DAILY_FIELDS
        if close_only:
            fields = ['PX_LAST']
        elif open_close_only:
            fields = ['OPEN', 'PX_LAST']

        req = svc.createRequest('HistoricalDataRequest')
        req.fromPy(
            {
                'securities': [sec(symbol)],
                'fields': fields,
                'startDate': from_dt.replace('-', ''),
                'endDate': to_dt.replace('-', ''),
                'periodicitySelection': 'DAILY',
                'periodicityAdjustment': 'ACTUAL',
            }
        )
        session.sendRequest(req)

        bars: list[dict] = []
        for data in collect(session):
            sd = data['securityData']
            bars.extend(sd.get('fieldData', []))

        df = _daily_to_ohlcv(bars, close_only, open_close_only)
    else:
        # IntradayBarRequest: single security
        start_dt = datetime.strptime(from_dt, '%Y-%m-%d').replace(
            tzinfo=ET
        )
        end_dt = datetime.now(ET)

        req = svc.createRequest('IntradayBarRequest')
        req.set('security', sec(symbol))
        req.set('eventType', 'TRADE')
        req.set('interval', multiplier)
        req.set('startDateTime', start_dt)
        req.set('endDateTime', end_dt)
        session.sendRequest(req)

        bars = []
        for data in collect(session):
            bd = data.get('barData', {})
            bars.extend(bd.get('barTickData', []))

        df = _intraday_to_ohlcv(
            bars,
            timespan,
            multiplier,
            close_only,
            open_close_only,
        )

    elapsed = perf_counter() - start
    if not quiet:
        log.blue(
            f'{symbol.upper()} '
            f'{scale}{unit[0]} '
            f'{from_dt}->{to_dt} '
            f'{timespan}/{multiplier} '
            f'{len(df)} rows '
            f'{elapsed:.2f}s'
        )

    return df


def fetch_hist_template(
    session: blpapi.Session,
    symbol: str,
    template: str = HIST_TEMPLATE_DEFAULT,
    quiet: bool = False,
) -> pl.DataFrame:
    """Fetch hist at maxScale for a template."""
    ts, mult, unit, _, max_scale = HIST_TEMPLATES[template]
    return fetch_hist(
        session,
        symbol,
        ts,
        mult,
        unit,
        max_scale,
        quiet=quiet,
    )
