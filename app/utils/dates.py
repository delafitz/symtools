from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta

from app.utils.market import (
    ET,
    last_trading_day,
    last_weekday,
    weekdays_back,
)

UTC = ZoneInfo('UTC')
DT_FMT = '%Y-%m-%d'


def get_delta(timespan, multiplier):
    if timespan == 'minute':
        delta = timedelta(minutes=multiplier)
    elif timespan == 'hour':
        delta = timedelta(hours=multiplier)
    elif timespan == 'day':
        delta = timedelta(days=multiplier) + timedelta(seconds=-1)
    else:
        return 0
    return int(delta.total_seconds() * 1e3)


def round_ts(ts: int, timespan: str, multiplier: int) -> int:
    """Round timestamp to bar boundary for consistent joins."""
    if timespan == 'minute':
        bar_ms = multiplier * 60 * 1000
    elif timespan == 'hour':
        bar_ms = multiplier * 60 * 60 * 1000
    else:
        return ts
    return (ts // bar_ms) * bar_ms


def ts_to_date(ts):
    """Date label for a bar timestamp in ET.

    Bars are labeled by their ET trading day so date joins
    against external dates (block trades, market.py helpers)
    work correctly. The `iso` column still shows the absolute
    instant in UTC.
    """
    return datetime.fromtimestamp(ts * 1e-3, tz=ET).strftime(DT_FMT)


def ns_to_dt(ts):
    return datetime.fromtimestamp(ts / 1e9, tz=UTC)


def iso_ts(ts):
    return datetime.fromtimestamp(ts * 1e-3, tz=UTC).isoformat()


def get_dt_span(unit, scale):
    """Get date range for fetching hist data.

    Uses last_trading_day() which accounts for pre-market hours
    (before 4:30 AM ET, uses previous trading day).
    """
    end = last_trading_day()
    if unit == 'days':
        start = weekdays_back(end, scale)
    else:
        start = last_weekday(end - relativedelta(**{unit: scale}))
    return start.strftime(DT_FMT), end.strftime(DT_FMT)
