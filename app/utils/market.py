"""Market hours and trading day utilities."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import polars as pl
from dateutil.relativedelta import relativedelta

ET = ZoneInfo('America/New_York')
DT_FMT = '%Y-%m-%d'

# Market sessions (all times ET)
PRE_MARKET_OPEN = time(4, 30, 0)
MARKET_OPEN = time(9, 30, 0)
MARKET_CLOSE = time(16, 0, 0)
POST_MARKET_CLOSE = time(23, 59, 59)


def is_weekday(d: date) -> bool:
    """Check if date is a weekday (Mon-Fri)."""
    return d.weekday() < 5


def prev_weekday(d: date) -> date:
    """Get the previous weekday (skips weekends)."""
    d = d - timedelta(days=1)
    while not is_weekday(d):
        d = d - timedelta(days=1)
    return d


def last_weekday(d: date) -> date:
    """Get last weekday on or before date d."""
    while not is_weekday(d):
        d = d - timedelta(days=1)
    return d


def last_trading_day() -> date:
    """Get last trading day with potential data.

    Before pre-market open (4:30 AM ET), returns previous trading day.
    On weekends, returns Friday.
    """
    now_et = datetime.now(ET)
    today = now_et.date()

    if now_et.time() < PRE_MARKET_OPEN:
        today = today - timedelta(days=1)

    return last_weekday(today)


def prev_trading_day() -> date:
    """Get previous trading day (for daily close data).

    Always returns the previous weekday since today's close isn't
    available until after market close.
    """
    now_et = datetime.now(ET)
    today = now_et.date()
    return prev_weekday(last_weekday(today))


def weekdays_back(d: date, n: int) -> date:
    """Go back n weekdays from date d."""
    weeks, rem = divmod(n, 5)
    days_back = weeks * 7 + rem
    if d.weekday() - rem < 0:
        days_back += 2
    return d - timedelta(days=days_back)


def get_session_ts(date_str: str, boundary: time) -> int:
    """Get ms timestamp for a time boundary on a date."""
    dt = datetime.strptime(date_str, DT_FMT)
    dt_et = datetime.combine(dt.date(), boundary, tzinfo=ET)
    return int(dt_et.timestamp() * 1000)


def get_close_ts(date_str: str) -> int:
    """Get 4:00 PM ET timestamp in milliseconds."""
    return get_session_ts(date_str, MARKET_CLOSE)


def get_session() -> str:
    """Classify current market session.

    Uses current time, not the quote timestamp — session
    answers 'what is open now', not 'when was the last tick'.
    Returns 'closed' on weekends regardless of time.
    """
    now = datetime.now(ET)
    if not is_weekday(now.date()):
        return 'closed'
    t = now.time()
    if t < PRE_MARKET_OPEN:
        return 'closed'
    if t < MARKET_OPEN:
        return 'pre'
    if t < MARKET_CLOSE:
        return 'market'
    return 'post'


def slice_hist(
    hist: pl.DataFrame,
    unit: str,
    scale: int,
    for_analytics: bool = False,
) -> pl.DataFrame:
    """Slice hist to a given date range.

    For intraday templates (days/weeks units), includes previous day's
    post-market (from 4pm close) plus N trading days.
    For daily templates (months/years units), uses calendar-based cutoff.

    Uses last_trading_day() which accounts for pre-market hours
    (before 4:30 AM ET, uses previous trading day).

    When for_analytics=True, uses prev_trading_day() as end date since
    daily close prices aren't available until after market close.
    """
    if for_analytics:
        # Analytics uses daily close prices, end at prev trading day
        end = prev_trading_day()
        if unit == 'days':
            cutoff = weekdays_back(end, scale)
        elif unit == 'weeks':
            cutoff = weekdays_back(end, scale * 5)
        else:
            cutoff = last_weekday(
                end - relativedelta(**{unit: scale})
            )
        return hist.filter(
            (pl.col('date') >= cutoff.strftime(DT_FMT))
            & (pl.col('date') <= end.strftime(DT_FMT))
        )

    today = last_trading_day()

    if unit == 'days':
        # D template: include prev day post-market + scale days
        # scale=1 → prev day 4pm + today
        cutoff = weekdays_back(today, scale)
        cutoff_str = cutoff.strftime(DT_FMT)
        close_ts = get_close_ts(cutoff_str)
        return hist.filter(
            (pl.col('date') > cutoff_str)
            | (
                (pl.col('date') == cutoff_str)
                & (pl.col('timestamp') >= close_ts)
            )
        )
    elif unit == 'weeks':
        # W template: include prev day post-market + scale weeks
        cutoff = weekdays_back(today, scale * 5)
        cutoff_str = cutoff.strftime(DT_FMT)
        close_ts = get_close_ts(cutoff_str)
        return hist.filter(
            (pl.col('date') > cutoff_str)
            | (
                (pl.col('date') == cutoff_str)
                & (pl.col('timestamp') >= close_ts)
            )
        )
    else:
        # Y/M templates: calendar-based
        cutoff = last_weekday(today - relativedelta(**{unit: scale}))
        return hist.filter(pl.col('date') >= cutoff.strftime(DT_FMT))
