from __future__ import annotations

import polars as pl

from app.models.analytics import SymbolAnalytics
from app.services.analytics.window import get_all_windows

DAILY_ANN = 252**0.5 * 100

VOL_DEFAULT = 30
VOL_WINDOWS = [30, 90]
VOL_DELTAS = [5]
PCT_CHG = 'pct_chg'

ADV_DEFAULT = 30
ADV_WINDOWS = [10, 30, 90]
ADV_DELTAS = [5]
VOLUME = 'volume'


def build_analytics(
    symbol: str,
    hist: pl.DataFrame,
) -> SymbolAnalytics:
    """Build analytics from hist data."""
    vol, vol_table = get_vols(hist)
    adv, adv_table = get_advs(hist)
    sigma = vol / DAILY_ANN
    return SymbolAnalytics.model_validate(
        {
            'symbol': symbol,
            'vol': vol,
            'sigma': sigma,
            'adv': adv,
            'hist_vol': vol_table,
            'hist_adv': adv_table,
        }
    )


def get_vols(hist, windows=VOL_WINDOWS, deltas=VOL_DELTAS):
    returns = hist.select(
        pl.col(('date', 'close')),
        pl.col('close').pct_change().alias(PCT_CHG),
    )
    expr = pl.col(PCT_CHG).std() * DAILY_ANN
    vol = returns.tail(VOL_DEFAULT).select(expr).item()
    table = get_all_windows(
        returns,
        expr,
        windows,
        deltas,
    )
    return vol, table


def get_advs(hist, windows=ADV_WINDOWS, deltas=ADV_DELTAS):
    daily_volume = hist.select(
        pl.col(('date', VOLUME)),
    )
    expr = pl.col(VOLUME).mean()
    adv = daily_volume.tail(ADV_DEFAULT).select(expr).item()
    table = get_all_windows(
        daily_volume, expr, windows, deltas, 'pct'
    )
    return adv, table
