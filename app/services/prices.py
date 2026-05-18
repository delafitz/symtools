"""Price lookups backed by daily (Y) hist data."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import polars as pl

from app.models.hist import SymbolHist
from app.models.results import SymbolQuote
from app.utils.market import (
    DT_FMT,
    ET,
    MARKET_OPEN,
    is_weekday,
    last_trading_day,
    last_weekday,
    prev_weekday,
    slice_hist,
    weekdays_back,
)
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.server.cache import Cache

log = get_logger(__name__)

# key -> (timespan, multiplier, unit, defaultScale, maxScale)
# Y maxScale=5 because point-in-time Barra needs MOM_WINDOW
# (~250 trading days) of pre-history beyond the factor-return
# window. To get ~250 real factor returns at an as-of date, the
# parquet must reach ~500 trading days before it; 5y depth
# covers the oldest block trades (~2024) with room to spare.
# All other consumers slice down to defaultScale.
HIST_TEMPLATES = {
    'Y': ('day', 1, 'years', 1, 5),
    'M': ('day', 1, 'months', 3, 6),
    'W': ('minute', 30, 'weeks', 2, 4),
    'D': ('minute', 10, 'days', 5, 10),
}

HIST_TEMPLATE_DEFAULT = 'Y'


def end_price_from_quote(
    quote: SymbolQuote,
) -> float:
    """Quote close is the single source of truth
    for end_price."""
    return quote.close


# --- module-level price helpers ---


def _close(daily: pl.DataFrame, date_str: str) -> float | None:
    """Daily close for a date string."""
    row = daily.filter(
        pl.col('date').cast(pl.String) == str(date_str)
    )
    if row.is_empty():
        return None
    return row.select('close').item()


def _prev_close(
    daily: pl.DataFrame, before: str
) -> tuple[str, float] | None:
    """Last daily bar before a date."""
    prior = daily.filter(pl.col('date') < before)
    if prior.is_empty():
        return None
    return (
        prior.select('date').tail(1).item(),
        prior.select('close').tail(1).item(),
    )


def _vwap(daily: pl.DataFrame, start: str, end: str) -> float | None:
    """Volume-weighted avg price over a date range.

    Caps range to available daily data so intraday
    end dates beyond the last daily bar still return
    a value.
    """
    last = daily.select('date').tail(1).item()
    end = min(end, last)
    start = min(start, end)
    sl = daily.filter(
        (pl.col('date') >= start) & (pl.col('date') <= end)
    )
    total_vol = sl.select(pl.col('volume').sum()).item()
    if total_vol > 0:
        return round(
            sl.select(
                (pl.col('vwap') * pl.col('volume')).sum()
            ).item()
            / total_vol,
            4,
        )
    return None


def _daily_aggs(
    daily: pl.DataFrame, start: str, end: str
) -> list[dict] | None:
    """Daily-bar slice for intraday overlay.

    Includes one bar before start (prev close
    anchor) through end. Appends a stub row when
    intraday data extends beyond the last daily bar.
    """
    prev_days = daily.filter(pl.col('date') < start)
    prev_d = (
        prev_days.select('date').tail(1).item()
        if not prev_days.is_empty()
        else start
    )
    daily_slice = daily.filter(
        (pl.col('date') >= prev_d) & (pl.col('date') <= end)
    )
    aggs = daily_slice.to_dicts()
    if aggs:
        daily_last = aggs[-1]['date']
        if end > daily_last:
            aggs.append(
                {
                    'date': end,
                    'timestamp': 0,
                    'open': 0.0,
                    'high': 0.0,
                    'low': 0.0,
                    'close': 0.0,
                    'vwap': 0.0,
                    'volume': 0.0,
                    'pct_return': None,
                }
            )
    return aggs


class PriceService:
    """Daily-bar lookups, template hist routing,
    and SymbolHist response building."""

    def __init__(
        self,
        cache: Cache,
        y_hist: pl.DataFrame,
    ) -> None:
        self._cache = cache
        self._daily = y_hist

    @classmethod
    async def create(
        cls, cache: Cache, symbol: str
    ) -> PriceService | None:
        """Load Y hist, return service or None."""
        y_hist = await cache.get_hist_async(symbol, 'Y')
        if y_hist is None or y_hist.is_empty():
            return None
        return cls(cache, y_hist)

    @property
    def daily(self) -> pl.DataFrame:
        return self._daily

    # --- today-bar helpers ---

    def append_quote_bar(self, end_price: float) -> None:
        """Append synthetic today bar (OHLC=close, vol=0)."""
        today = last_trading_day().strftime(DT_FMT)
        last_date = self._daily.select('date').tail(1).item()
        if today <= last_date:
            return
        prev_close = self._daily.select('close').tail(1).item()
        pct_ret = (
            round(end_price / prev_close - 1, 4)
            if prev_close and prev_close > 0
            else None
        )
        row = pl.DataFrame(
            [
                {
                    'date': today,
                    'iso': '',
                    'timestamp': 0,
                    'open': end_price,
                    'high': end_price,
                    'low': end_price,
                    'close': end_price,
                    'vwap': 0.0,
                    'volume': 0.0,
                    'pct_return': pct_ret,
                }
            ],
            schema=self._daily.schema,
        )
        self._daily = pl.concat([self._daily, row])

    def replace_today_bar(self, bar: pl.DataFrame) -> None:
        """Replace synthetic today bar with real data."""
        today = last_trading_day().strftime(DT_FMT)
        prev = self._daily.filter(pl.col('date') < today)
        if prev.is_empty():
            return
        prev_close = prev.select('close').tail(1).item()
        close = bar.select('close').item()
        pct_ret = (
            round(close / prev_close - 1, 4)
            if prev_close and prev_close > 0
            else None
        )
        bar = bar.with_columns(pl.lit(pct_ret).alias('pct_return'))
        self._daily = pl.concat([prev, bar])

    # --- hist routing ---

    async def hist(
        self, symbol: str, template: str
    ) -> pl.DataFrame | None:
        """Get hist bars for a template.

        Y -> stored daily, M -> sliced from Y,
        W/D -> fetched via cache.
        """
        if template == 'Y':
            return self._daily
        if template == 'M':
            _, _, unit, _, max_scale = HIST_TEMPLATES['M']
            return slice_hist(self._daily, unit, max_scale)
        return await self._cache.get_hist_async(symbol, template)

    # --- response building ---

    async def build_response(
        self,
        symbol: str,
        template: str,
        end_price: float,
        scale: int | None = None,
    ) -> SymbolHist | None:
        """Fetch hist and build SymbolHist response."""
        hist = await self.hist(symbol, template)
        if hist is None or hist.is_empty():
            return None
        return self._build_hist(
            symbol, hist, template, end_price, scale
        )

    def _build_hist(
        self,
        symbol: str,
        hist: pl.DataFrame,
        template: str,
        end_price: float,
        scale: int | None,
    ) -> SymbolHist:
        """Build SymbolHist response."""
        t, m, unit, default_scale, max_scale = HIST_TEMPLATES[
            template
        ]
        if scale is None:
            scale = default_scale
        bars_hist = slice_hist(hist, unit, max_scale)

        if bars_hist.is_empty():
            log.warning(
                f'hist: {symbol} {template} scale={scale} is empty'
            )
            return SymbolHist.model_validate(
                {
                    'symbol': symbol,
                    'template': template,
                    'timespan': t,
                    'multiplier': m,
                    'scale': scale,
                    'stats': {},
                    'daily_aggs': None,
                    'bars': [],
                }
            )

        is_intraday = t != 'day'
        first_date = bars_hist.select('date').head(1).item()
        last_date = bars_hist.select('date').tail(1).item()

        daily_aggs = (
            _daily_aggs(self._daily, first_date, last_date)
            if is_intraday
            else None
        )

        if is_intraday:
            now_et = datetime.now(ET)
            today_d = last_weekday(now_et.date())
            end_d = (
                prev_weekday(today_d)
                if is_weekday(now_et.date())
                and now_et.time() < MARKET_OPEN
                else today_d
            )
            end_date = end_d.strftime(DT_FMT)
        else:
            end_date = last_date

        stats: dict[int, dict] = {}
        for s in range(1, max_scale + 1):
            s_hist = slice_hist(hist, unit, s)
            if s_hist.is_empty():
                continue

            if is_intraday:
                n_days = s if unit == 'days' else s * 5
                start_d = weekdays_back(end_d, n_days - 1)
                start_date = start_d.strftime(DT_FMT)
                prev_d = prev_weekday(start_d)
                prev_date = prev_d.strftime(DT_FMT)
                prev_close = _close(self._daily, prev_date)
                if prev_close is None:
                    prev_close = s_hist.select('close').head(1).item()
            elif s == max_scale:
                start_date = s_hist.select('date').head(1).item()
                prev_date = start_date
                prev_close = s_hist.select('close').head(1).item()
            else:
                start_date = s_hist.select('date').head(1).item()
                pc = _prev_close(self._daily, start_date)
                if pc is not None:
                    prev_date, prev_close = pc
                else:
                    prev_date = start_date
                    prev_close = s_hist.select('close').head(1).item()

            range_vwap = _vwap(self._daily, start_date, end_date)
            if range_vwap is None and not is_intraday:
                vwap_slice = hist.filter(
                    (pl.col('date') >= start_date)
                    & (pl.col('date') <= end_date)
                )
                total_vol = vwap_slice.select(
                    pl.col('volume').sum()
                ).item()
                if total_vol > 0:
                    range_vwap = round(
                        vwap_slice.select(
                            (pl.col('vwap') * pl.col('volume')).sum()
                        ).item()
                        / total_vol,
                        4,
                    )

            range_pct_return = (
                round(end_price / prev_close - 1, 4)
                if prev_close and prev_close > 0
                else None
            )

            stats[s] = {
                'end_date': end_date,
                'end_price': end_price,
                'start_date': start_date,
                'prev_date': prev_date,
                'prev_close': prev_close,
                'range_vwap': range_vwap,
                'range_pct_return': range_pct_return,
            }

        ts_abbr = 'd' if t == 'day' else 'm'
        label = f'{unit[0].upper()} ({m}{ts_abbr})'
        rows = [
            {
                'scale': f'{s}{label}',
                'prev': v['prev_date'],
                'start': v['start_date'],
                'end': v['end_date'],
                'prev_close': v['prev_close'],
                'end_price': v['end_price'],
                'vwap': v['range_vwap'],
                'pct_return': v['range_pct_return'],
            }
            for s, v in stats.items()
        ]
        if rows:
            log.blue(f'{symbol}\n{pl.DataFrame(rows)}')

        if is_intraday and stats:
            max_end = stats[max(stats)]['end_date']
            overflow = bars_hist.filter(pl.col('date') > max_end)
            log.yellow(
                f'{symbol} {template} pre-session: '
                f'end_date={max_end} '
                f'total_bars={len(bars_hist)} '
                f'overflow={len(overflow)}'
            )
            if not overflow.is_empty():
                log.yellow(
                    f'{symbol} {template} overflow bars:\n{overflow}'
                )

        return SymbolHist.model_validate(
            {
                'symbol': symbol,
                'template': template,
                'timespan': t,
                'multiplier': m,
                'scale': scale,
                'stats': stats,
                'daily_aggs': daily_aggs,
                'bars': bars_hist.to_dicts(),
            }
        )
