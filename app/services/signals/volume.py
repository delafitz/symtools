"""Volume estimator — intraday volume vs historical curve.

Uses 10-minute bars (D template, 9 prior trading days) to
build an average volume curve per time bucket across regular
trading hours (9:30–16:10 ET, 40 buckets). The 16:00 bar
captures the closing auction (~9% of daily lit volume).

**Lit/dark ratio**: Polygon aggs only report lit-exchange
volume. Daily bars include dark pool / off-exchange prints.
The ratio varies by name — ~92% for retail-heavy (TSLA),
~68% for institutional (JPM). We compute a per-symbol
median ratio from the 9 hist days (median is robust to
quad-witching outliers) and scale the projection up.

**Outputs**:
- `pct_of_avg`: today's cumulative lit volume / average
  cumulative lit volume at the same point in the day.
  Lerped within the current 10-min bucket.
- `projected_volume`: extrapolated total daily volume
  (lit + dark). Remaining curve volume is scaled by
  `pct_of_avg`, then divided by `lit_ratio`.

**Time boundaries**:
- Before 9:40 ET → (0, 0): not enough signal
- 9:40–16:10 ET → lerped curve projection
- After 16:10 ET → actuals, no extrapolation
"""

from __future__ import annotations

from datetime import datetime, time

import polars as pl

from app.models.signals import VolumeEstimate
from app.utils.market import ET, last_trading_day

RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 10)
BAR_MINUTES = 10


def _add_bar_time(df: pl.DataFrame) -> pl.DataFrame:
    """Add bar_time (ET time-of-day) from timestamp."""
    return df.with_columns(
        pl.col('timestamp')
        .map_elements(
            lambda ts: datetime.fromtimestamp(
                ts / 1000, tz=ET
            ).time(),
            return_dtype=pl.Time,
        )
        .alias('bar_time')
    )


def _rth(df: pl.DataFrame) -> pl.DataFrame:
    """Filter to regular trading hours (9:30–16:10)."""
    return df.filter(
        (pl.col('bar_time') >= RTH_OPEN)
        & (pl.col('bar_time') < RTH_CLOSE)
    )


def estimate_volume(
    symbol: str,
    intraday: pl.DataFrame,
    daily: pl.DataFrame,
) -> VolumeEstimate | None:
    """Estimate today's volume from intraday curve.

    Args:
        symbol: ticker
        intraday: D template bars (10-min, ~10 days)
        daily: Y template bars (daily, >= 10 days)

    Returns VolumeEstimate or None if insufficient data.
    """
    if intraday.is_empty() or daily.is_empty():
        return None

    today = last_trading_day().strftime('%Y-%m-%d')

    bars = _rth(_add_bar_time(intraday))
    if bars.is_empty():
        return None

    today_bars = bars.filter(pl.col('date') == today)
    hist_bars = bars.filter(pl.col('date') != today)

    hist_dates = hist_bars['date'].unique()
    if len(hist_dates) < 3:
        return None

    # --- lit ratio (median) ---
    lit_by_day = hist_bars.group_by('date').agg(
        pl.col('volume').sum().alias('lit_vol'),
    )
    daily_match = daily.filter(
        pl.col('date').is_in(hist_dates)
    ).select('date', pl.col('volume').alias('daily_vol'))

    ratios = lit_by_day.join(
        daily_match, on='date', how='inner'
    ).with_columns(
        (pl.col('lit_vol') / pl.col('daily_vol')).alias('ratio')
    )
    if ratios.is_empty():
        return None
    lit_ratio = float(ratios['ratio'].median())
    if lit_ratio <= 0:
        return None

    # --- volume curve (avg per bucket) ---
    n_hist_days = len(hist_dates)
    curve = (
        hist_bars.group_by('bar_time')
        .agg(pl.col('volume').sum().alias('total_vol'))
        .with_columns(
            (pl.col('total_vol') / n_hist_days).alias('avg_vol')
        )
        .sort('bar_time')
    )
    avg_full_day_lit = float(curve['avg_vol'].sum())
    if avg_full_day_lit <= 0:
        return None

    # --- today's progress ---
    now_et = datetime.now(ET).time()

    # before first full bar: no signal yet
    if now_et < time(9, 40) or today_bars.is_empty():
        return VolumeEstimate(
            symbol=symbol,
            pct_of_avg=0.0,
            projected_volume=0.0,
            lit_ratio=lit_ratio,
        )

    today_cum = float(today_bars['volume'].sum())

    # after close: no projection needed
    if now_et >= RTH_CLOSE:
        pct_of_avg = (
            today_cum / avg_full_day_lit
            if avg_full_day_lit > 0
            else 0.0
        )
        projected_total = today_cum / lit_ratio
        return VolumeEstimate(
            symbol=symbol,
            pct_of_avg=pct_of_avg,
            projected_volume=round(projected_total),
            lit_ratio=lit_ratio,
        )

    # --- intraday lerp ---
    last_bucket = today_bars['bar_time'].max()

    # fraction of current bucket elapsed
    bucket_start_min = last_bucket.hour * 60 + last_bucket.minute
    now_min = now_et.hour * 60 + now_et.minute
    bucket_frac = (
        max(0, min(BAR_MINUTES, now_min - bucket_start_min))
        / BAR_MINUTES
    )

    # avg cumulative through completed buckets + lerped
    completed = curve.filter(pl.col('bar_time') < last_bucket)
    current = curve.filter(pl.col('bar_time') == last_bucket)
    avg_cum = float(completed['avg_vol'].sum())
    current_avg = (
        float(current['avg_vol'][0])
        if not current.is_empty()
        else 0.0
    )
    avg_cum += current_avg * bucket_frac

    pct_of_avg = today_cum / avg_cum if avg_cum > 0 else 0.0

    # project full day: remaining avg curve volume
    avg_remaining = avg_full_day_lit - avg_cum
    projected_lit = today_cum + avg_remaining * pct_of_avg
    projected_total = projected_lit / lit_ratio

    return VolumeEstimate(
        symbol=symbol,
        pct_of_avg=pct_of_avg,
        projected_volume=round(projected_total),
        lit_ratio=lit_ratio,
    )
