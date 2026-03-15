from __future__ import annotations

import datetime

import polars as pl

from app.models.analytics import (
    Historical,
    Liquidity,
    Ratios,
    SymbolAnalytics,
)
from app.services.analytics.window import get_all_windows

DAILY_ANN = 252**0.5 * 100

VOL_DEFAULT = 30
VOL_WINDOWS = [10, 30, 90]
VOL_DELTAS = [5]
PCT_CHG = 'pct_chg'

ADV_DEFAULT = 30
ADV_WINDOWS = [10, 30, 90]
ADV_DELTAS = [5]
VOLUME = 'volume'

BETA_WINDOW = 200
MOM_WINDOW = 250  # ~12 months
MOM_SKIP = 21    # skip last month (reversal)


def _beta_spy(
    hist: pl.DataFrame,
    spy: pl.DataFrame,
) -> float | None:
    """Beta of symbol vs SPY over BETA_WINDOW trading days."""
    sym_ret = (
        hist.tail(BETA_WINDOW)
        .select('date', 'close')
        .with_columns(pl.col('close').pct_change().alias('r'))
    )
    spy_ret = (
        spy.tail(BETA_WINDOW)
        .select('date', 'close')
        .with_columns(pl.col('close').pct_change().alias('r'))
    )
    joined = sym_ret.join(
        spy_ret, on='date', suffix='_spy'
    ).drop_nulls()
    if joined.height < 10:
        return None
    cov = joined.select(pl.cov('r', 'r_spy')).item()
    var = joined.select(pl.col('r_spy').var()).item()
    return cov / var if var else None


def build_analytics(
    symbol: str,
    hist: pl.DataFrame,
    ref: dict | None = None,
    spy_hist: pl.DataFrame | None = None,
) -> SymbolAnalytics:
    """Build analytics from hist data."""
    vol, vol_table = get_vols(hist)
    adv, adv_table = get_advs(hist)
    one_sigma = vol / DAILY_ANN * 100

    liquidity: Liquidity | None = None
    ratios: Ratios | None = None
    if ref:
        mkt_cap = float(ref.get('mkt_cap', 0.0))
        shares_out = int(ref.get('shares_out', 0))
        float_shares = int(ref.get('free_float', 0))
        short_int = int(ref.get('short_interest', 0))
        liquidity = Liquidity(
            mkt_cap=mkt_cap,
            shares_out=shares_out,
            float_shares=float_shares,
            short_int=short_int,
        )
        ratios = Ratios(
            float_out=(
                float_shares / shares_out if shares_out else 0.0
            ),
            float_short=(
                short_int / float_shares if float_shares else 0.0
            ),
            cover_days=(short_int / adv if adv else 0.0),
        )

    historical: Historical | None = None
    if not hist.is_empty():
        end = hist['close'].tail(1).item()
        end_date = hist['date'].tail(1).item()
        one_year_ago = end_date - datetime.timedelta(days=365)
        start_row = hist.filter(
            pl.col('date') >= one_year_ago
        ).head(1)
        start_1y = (
            start_row['close'].item()
            if not start_row.is_empty()
            else None
        )
        return_1y = (
            (end / start_1y - 1) if start_1y else 0.0
        )
        high_1y = hist['high'].max() or end
        high_pct = end / high_1y if high_1y else 1.0
        low_1y = hist['low'].min() or end
        low_pct = end / low_1y if low_1y else 1.0
        closes = hist['close']
        n = len(closes)
        momentum: float | None = None
        if n > MOM_SKIP:
            skip_close = closes[-(MOM_SKIP + 1)]
            start_close = closes[-min(n, MOM_WINDOW + 1)]
            if start_close > 0:
                momentum = float(skip_close / start_close - 1)
        beta = (
            _beta_spy(hist, spy_hist)
            if spy_hist is not None and not spy_hist.is_empty()
            else None
        )
        historical = Historical(
            beta=beta,
            one_sigma=one_sigma,
            return_1y=return_1y,
            high_pct=high_pct,
            low_pct=low_pct,
            momentum=momentum,
        )

    return SymbolAnalytics.model_validate(
        {
            'symbol': symbol,
            'vol': vol,
            'adv': adv,
            'hist_vol': vol_table,
            'hist_adv': adv_table,
            'liquidity': liquidity,
            'ratios': ratios,
            'historical': historical,
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
