"""Backtest baskets against historical block trades.

Emits two parquets:
  - data/backtest_trades.parquet: one row per trade. Trade meta
    + characteristics (vol_90d, adv_30d_usd, shares_pct_adv,
    shares_pct_float, days_to_cover) + combined-basket
    hedgeability (corr, beta, vol_reduce, n_hedges) + model
    discount + discount_delta.
  - data/backtest_scores.parquet: one row per
    (trade, scenario, period, window). period in {pre, post}.
    Holds raw_return, hedged_return = target - beta * basket.

Pragmatic point-in-time: returns are point-in-time correct
(filtered by date), but refs (mkt_cap, free_float, type) reflect
today's snapshot — survivorship-biased universe. Pre-trade
hedged returns apply the trade-date basket retrospectively,
which is fine as a stationary risk model but is NOT a
pre-positioning backtest.

Usage:
    uv run python tools/backtest.py
    uv run python tools/backtest.py --symbols pr,kntk
    uv run python tools/backtest.py --symbols AAPL,MSFT
    uv run python tools/backtest.py --limit 10
"""

import hashlib
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from app.models.baskets import Basket, BasketParams
from app.services.baskets import scenarios as bsk_scenarios
from app.services.baskets.barra import build_barra_model
from app.services.baskets.builder import build_baskets
from app.services.block_trades import load_block_trades
from app.services.cost import DAILY_ANN, get_discount
from app.utils import market as mkt
from app.utils.logger import get_logger

log = get_logger(__name__)

# Pre-trade scoring windows (close-to-close drift before T).
# Kept compact — the few cardinal horizons are sufficient for
# pre-trade signal analysis.
PRE_WINDOWS = (1, 5, 10, 20)
# Post-trade scoring windows. Daily granularity (1..20) lets
# us measure full P&L paths for dynamic-exit research and
# trailing-stop / take-profit rule design.
POST_WINDOWS = tuple(range(1, 21))
CACHE_PATH = Path('data/backtest_baskets.parquet')
TRADES_OUT = Path('data/backtest_trades.parquet')
SCORES_OUT = Path('data/backtest_scores.parquet')

VOL_WINDOW = 90    # days for trailing annualized vol
ADV_WINDOW = 30    # days for trailing avg daily volume
# Drop trades whose block size exceeds MAX_XADV days of typical
# volume. These are structurally different from overnight
# blocks — half-of-float / multi-month-volume crossings in
# thinly-traded names — and distort liquidity-bucket stats.
# At threshold=30, two trades are removed (CMPR 2024-02-06 at
# 56x and IAUX 2025-05-09 at 51x). Next-highest is SHC at 23x.
MAX_XADV = 30.0
# Drop trades below MIN_DEAL_SIZE. Source data has unit/typo
# errors at the low end (e.g. FWONK at $0.01M, WDC at $0.03M,
# NDAQ at $0.08M — all $20B+ mkt-cap names where micro-deals
# can't be real overnight blocks). Even legitimate sub-$100M
# trades are too small to size meaningfully under the
# $10M floor / pct_adv sizer (notional often exceeds deal).
MIN_DEAL_SIZE = 100_000_000
# Hedge ratio applied to the basket β when scoring hedged
# returns. Synced with DEFAULT_HEDGE_RATIO in
# app/services/portfolio/position.py so the score-time and
# sim-time hedge ratios always match. See that file for the
# rationale behind 0.60 (portfolio-Sharpe optimum on the
# cleaned 296-trade population).
from app.services.portfolio.position import (
    DEFAULT_HEDGE_RATIO as HEDGE_RATIO,
)
# Calendar days of history fed to Barra before the as-of week.
# Barra needs MOM_WINDOW (250 trading days) of pre-history to
# compute exposures, plus MIN_HIST (250) factor return
# observations. 750 calendar days ~= 510 trading days covers
# both with a small buffer.
BARRA_LOOKBACK_DAYS = 750
COMBINED = 'combined'


def latest(name: str, ext: str = 'parquet') -> Path | None:
    files = sorted(Path('data').glob(f'{name}.*.{ext}'))
    return files[-1] if files else None


def week_anchor(date_iso: str) -> str:
    """Monday of the ISO week containing date_iso.

    All trades in the same week share a cached basket built
    as-of this Monday. Worst-case staleness: 4 trading days
    (Fri trade scored with Mon basket). No look-ahead since
    `slice_hist(for_analytics=True)` ends at prev trading day.
    """
    d = datetime.strptime(date_iso, '%Y-%m-%d').date()
    monday = d - timedelta(days=d.weekday())
    return monday.strftime('%Y-%m-%d')


def params_hash(params: BasketParams | None = None) -> str:
    p = params or BasketParams()
    return hashlib.sha1(
        p.model_dump_json().encode()
    ).hexdigest()[:12]


def load_basket_cache() -> dict:
    """(symbol, week, params_hash) -> {state, baskets}."""
    if not CACHE_PATH.exists():
        return {}
    df = pl.read_parquet(CACHE_PATH)
    cache: dict = {}
    for row in df.iter_rows(named=True):
        key = (row['symbol'], row['week'], row['params_hash'])
        if key not in cache:
            cache[key] = {'state': row['state'], 'baskets': {}}
        if row['scenario'] and row['basket_json']:
            cache[key]['baskets'][row['scenario']] = (
                Basket.model_validate_json(row['basket_json'])
            )
    log.info(
        f'basket cache: loaded {len(cache)} entries '
        f'from {CACHE_PATH.name}'
    )
    return cache


def save_basket_cache(cache: dict) -> None:
    rows: list[dict] = []
    for (sym, wk, ph), v in cache.items():
        state = v['state']
        if state != 'ok' or not v['baskets']:
            rows.append({
                'symbol': sym, 'week': wk, 'params_hash': ph,
                'state': state, 'scenario': None,
                'basket_json': None,
            })
        else:
            for sc, b in v['baskets'].items():
                rows.append({
                    'symbol': sym, 'week': wk, 'params_hash': ph,
                    'state': 'ok', 'scenario': sc,
                    'basket_json': b.model_dump_json(),
                })
    if not rows:
        return
    pl.DataFrame(rows).write_parquet(
        CACHE_PATH, compression='zstd'
    )
    log.green(
        f'basket cache: wrote {len(rows)} rows '
        f'({len(cache)} entries) -> {CACHE_PATH.name}'
    )


@contextmanager
def as_of(d: date):
    """Patch market date anchors so slice_hist + friends treat
    `d` as today. Required for point-in-time backtest — without
    this, slice_hist anchors to wall-clock today and truncates
    pre-trade history."""
    orig_last = mkt.last_trading_day
    orig_prev = mkt.prev_trading_day
    mkt.last_trading_day = lambda: mkt.last_weekday(d)
    mkt.prev_trading_day = lambda: mkt.prev_weekday(mkt.last_weekday(d))
    try:
        yield
    finally:
        mkt.last_trading_day = orig_last
        mkt.prev_trading_day = orig_prev


def daily_for(
    symbol: str, hists: pl.DataFrame
) -> pl.DataFrame:
    return hists.filter(
        (pl.col('symbol') == symbol)
        & (pl.col('template') == 'Y')
    ).select(['date', 'close', 'volume']).sort('date')


def pre_return(
    daily: pl.DataFrame, anchor_date: str, n_days: int
) -> float | None:
    """Close-to-close return from n_days before anchor up to
    the last trading day STRICTLY BEFORE anchor's close.
    Measures drift INTO the print, with the anchor day excluded
    so intraday blocks (where the block's own price impact lands
    on the anchor date) don't pollute the pre-trade window.

    For pre_1d: close(anchor-1) / close(anchor-2) − 1.
    For pre_20d: close(anchor-1) / close(anchor-21) − 1.
    """
    bars = daily.filter(pl.col('date') < anchor_date).tail(
        n_days + 1
    )
    if len(bars) < 2:
        return None
    closes = bars.get_column('close').to_list()
    return closes[-1] / closes[0] - 1


def forward_close(
    daily: pl.DataFrame, anchor_date: str, n_days: int
) -> float | None:
    """Close on the n-th trading day after anchor_date.
    Returns None if fewer than n bars available."""
    bars = daily.filter(pl.col('date') > anchor_date).head(n_days)
    if len(bars) < n_days:
        return None
    return bars.get_column('close').tail(1).item()


def close_on_or_before(
    daily: pl.DataFrame, anchor_date: str
) -> float | None:
    rows = daily.filter(pl.col('date') <= anchor_date).tail(1)
    return rows.get_column('close').item() if not rows.is_empty() else None


def basket_close_series(
    weights: dict[str, float],
    hist_all: pl.DataFrame,
) -> pl.DataFrame | None:
    """Synthetic weighted basket close, rebased to 1.0 at
    first available bar."""
    hedge_syms = list(weights.keys())
    b_hist = hist_all.filter(
        (pl.col('symbol').is_in(hedge_syms))
        & (pl.col('template') == 'Y')
    ).select(['date', 'symbol', 'close'])
    if b_hist.is_empty():
        return None

    wide = b_hist.pivot(
        on='symbol', index='date', values='close',
        aggregate_function='last',
    ).sort('date').drop_nulls()
    if wide.is_empty():
        return None

    first = wide.row(0, named=True)
    present = [s for s in hedge_syms if s in wide.columns]
    wide = wide.with_columns([
        (pl.col(s) / first[s] * weights[s]).alias(s)
        for s in present
    ])
    return wide.select([
        'date',
        pl.sum_horizontal([pl.col(s) for s in present]).alias('close'),
    ])


def score_trade(
    symbol: str,
    trade_date: str,
    offer_price: float,
    hist_all: pl.DataFrame,
    baskets: dict,
) -> list[dict]:
    """One row per (scenario, period, window).

    pre period: close-to-close target/basket move INTO the
        print (T-N close → T close).
    post period: buyer's P&L from offer_price entry to close
        at T+N. Basket leg enters at T close (post-block hedge
        placement) and uses close-to-close.
    """
    target_daily = daily_for(symbol, hist_all)
    if target_daily.is_empty() or offer_price <= 0:
        return []

    rows: list[dict] = []
    for scenario, basket in baskets.items():
        beta = basket.stats.beta
        bc = basket_close_series(basket.weights, hist_all)
        if bc is None:
            continue
        bc_entry = close_on_or_before(bc, trade_date)
        if bc_entry is None or bc_entry <= 0:
            continue

        # PRE: close-to-close drift into the trade
        for w in PRE_WINDOWS:
            pr = pre_return(target_daily, trade_date, w)
            pb = pre_return(bc, trade_date, w)
            if pr is not None and pb is not None:
                rows.append({
                    'symbol': symbol,
                    'trade_date': trade_date,
                    'scenario': scenario,
                    'period': 'pre',
                    'window_d': w,
                    'raw_return': pr,
                    'basket_return': pb,
                    'hedged_return': pr - HEDGE_RATIO * beta * pb,
                })

        # POST: daily-granularity path from offer_price.
        # target: close(T+N) / offer_price - 1
        # basket: close(T+N) / close(T) - 1
        for w in POST_WINDOWS:
            tgt_n = forward_close(target_daily, trade_date, w)
            b_n = forward_close(bc, trade_date, w)
            if tgt_n is None or b_n is None:
                continue
            raw = tgt_n / offer_price - 1
            b_ret = b_n / bc_entry - 1
            rows.append({
                'symbol': symbol,
                'trade_date': trade_date,
                'scenario': scenario,
                'period': 'post',
                'window_d': w,
                'raw_return': raw,
                'basket_return': b_ret,
                'hedged_return': raw - HEDGE_RATIO * beta * b_ret,
            })
    return rows


def trade_characteristics(
    symbol: str,
    trade_date: str,
    hist_all: pl.DataFrame,
    refs_row: dict,
    shares: float,
    offer_price: float,
) -> dict:
    """Trade-date target characteristics. None for fields that
    can't be computed (insufficient history)."""
    daily = daily_for(symbol, hist_all)
    daily_pit = daily.filter(pl.col('date') <= trade_date)

    out: dict = {
        'vol_90d': None,
        'adv_30d_shares': None,
        'adv_usd_30d': None,
        'shares_pct_adv': None,
        'shares_pct_float': None,
        'days_to_cover': refs_row.get('days_to_cover'),
        'mkt_cap': refs_row.get('mkt_cap'),
    }

    if len(daily_pit) >= VOL_WINDOW + 1:
        last = daily_pit.tail(VOL_WINDOW + 1)
        rets = last.select(
            pl.col('close').pct_change()
        ).drop_nulls()
        std = rets.std().item()
        if std is not None:
            # vol stored as annualized percent, matching cost svc
            out['vol_90d'] = std * DAILY_ANN

    if len(daily_pit) >= ADV_WINDOW:
        last = daily_pit.tail(ADV_WINDOW)
        adv_sh = last.get_column('volume').mean()
        adv_usd = (
            last.get_column('close') * last.get_column('volume')
        ).mean()
        out['adv_30d_shares'] = adv_sh
        out['adv_usd_30d'] = adv_usd
        if adv_sh and adv_sh > 0:
            out['shares_pct_adv'] = shares / adv_sh

    free_float = refs_row.get('free_float') or 0
    if free_float > 0:
        out['shares_pct_float'] = shares / free_float

    return out


def build_trade_row(
    trade: dict,
    chars: dict,
    baskets: dict | None,
) -> dict:
    """Combine trade meta + characteristics + combined-basket
    hedgeability + model discount."""
    shares = trade.get('shares') or 0
    actual_disc = trade.get('discount') or 0.0
    adv = chars.get('adv_30d_shares')
    vol = chars.get('vol_90d')
    model_disc = (
        get_discount(shares, adv, vol)
        if shares and adv and vol
        else None
    )

    row: dict = {
        'symbol': trade['symbol'],
        'trade_date': trade['trade_date_iso'],
        'price_date': trade.get('price_date'),
        'broker': trade.get('broker'),
        'registered': trade.get('registered'),
        'shares': shares,
        'offer_price': trade.get('offer_price'),
        'deal_size': (
            (trade.get('offer_price') or 0) * shares
        ),
        'actual_discount': actual_disc,
        'model_discount': model_disc,
        'discount_delta': (
            actual_disc - model_disc
            if model_disc is not None else None
        ),
        'perf_t1': trade.get('perf_t1'),
        **chars,
    }

    combined = (baskets or {}).get(COMBINED)
    if combined is not None:
        row.update({
            'combined_beta': combined.stats.beta,
            'combined_corr': combined.stats.corr,
            'combined_vol_reduce': combined.stats.vol_reduce,
            'combined_n_hedges': len(combined.weights),
        })
    else:
        row.update({
            'combined_beta': None,
            'combined_corr': None,
            'combined_vol_reduce': None,
            'combined_n_hedges': None,
        })
    return row


def main() -> None:
    args = sys.argv[1:]

    symbols: set[str] | None = None
    limit: int | None = None
    while args:
        flag = args.pop(0)
        if flag == '--symbols':
            symbols = {s.lower() for s in args.pop(0).split(',')}
        elif flag == '--limit':
            limit = int(args.pop(0))
        else:
            print(f'unknown arg: {flag}', file=sys.stderr)
            sys.exit(1)

    refs_path = latest('refs')
    hists_path = latest('hists')
    if refs_path is None or hists_path is None:
        print('no parquet data in data/', file=sys.stderr)
        sys.exit(1)

    refs = pl.read_parquet(refs_path)
    hists = pl.read_parquet(hists_path)
    log.info(f'loaded refs={refs_path.name} hists={hists_path.name}')

    trades = load_block_trades(refs, hists)
    if trades is None or trades.is_empty():
        print('no block trades found', file=sys.stderr)
        sys.exit(1)

    # block_trades loader normalizes dates to ISO already
    trades = trades.with_columns(
        pl.col('trade_date').alias('trade_date_iso')
    ).sort('trade_date_iso', descending=True)

    # Drop trades outside Y hist range (need >= MIN_TARGET_HIST bars
    # before trade_date and at least max(POST_WINDOWS)+1 bars after).
    hist_min = hists.get_column('date').min()
    hist_max = hists.get_column('date').max()
    max_window = max(POST_WINDOWS)
    n_before = len(trades)
    trades = trades.filter(
        (pl.col('trade_date_iso') > hist_min)
        & (pl.col('trade_date_iso') <= hist_max)
    )
    dropped = n_before - len(trades)
    if dropped:
        log.warning(
            f'dropped {dropped} trades outside '
            f'hist range [{hist_min}, {hist_max}]; '
            f'forward window {max_window}d may truncate near end'
        )

    if symbols:
        trades = trades.filter(pl.col('symbol').is_in(symbols))
    if limit:
        trades = trades.head(limit)

    log.info(
        f'backtesting {len(trades)} trades, '
        f'pre_windows={PRE_WINDOWS}, post_windows=range(1,21)'
    )

    cache = load_basket_cache()
    ph = params_hash(None)
    n_hits = n_builds = 0

    # In-memory per-week caches. Barra and ETF returns
    # don't depend on the target symbol, so they're shared
    # across all trades in the same week.
    barra_by_week: dict = {}
    etf_anchor_week: str | None = None

    results_trades: list[dict] = []
    results_scores: list[dict] = []
    try:
        for i, trade in enumerate(trades.to_dicts(), 1):
            sym = trade['symbol']
            td = trade['trade_date_iso']
            wk = week_anchor(td)
            cache_key = (sym, wk, ph)
            t0 = perf_counter()

            ref_match = refs.filter(pl.col('symbol') == sym)
            refs_row = (
                ref_match.to_dicts()[0]
                if not ref_match.is_empty() else {}
            )
            shares = trade.get('shares') or 0
            offer_price = trade.get('offer_price') or 0.0
            deal_size = shares * offer_price
            if deal_size < MIN_DEAL_SIZE:
                log.warning(
                    f'{i}/{len(trades)} {sym}@{td}: '
                    f'deal=${deal_size/1e6:.1f}M < '
                    f'${MIN_DEAL_SIZE/1e6:.0f}M — dropping'
                )
                continue

            chars = trade_characteristics(
                sym, td, hists, refs_row, shares, offer_price
            )

            xadv = chars.get('shares_pct_adv')
            if xadv is not None and xadv > MAX_XADV:
                log.warning(
                    f'{i}/{len(trades)} {sym}@{td}: '
                    f'xADV={xadv:.1f} > {MAX_XADV} — dropping'
                )
                continue

            baskets: dict | None = None
            cache_tag = ''

            if cache_key in cache:
                entry = cache[cache_key]
                if entry['state'] == 'ok':
                    baskets = entry['baskets']
                    n_hits += 1
                    cache_tag = 'hit'
                else:
                    cache_tag = f'cached:{entry["state"]}'
            else:
                wk_d = datetime.strptime(wk, '%Y-%m-%d').date()
                lookback_start = (
                    wk_d - timedelta(days=BARRA_LOOKBACK_DAYS)
                ).strftime('%Y-%m-%d')
                hists_pit = hists.filter(
                    (pl.col('date') <= wk)
                    & (pl.col('date') >= lookback_start)
                )
                target_y = hists_pit.filter(
                    (pl.col('symbol') == sym)
                    & (pl.col('template') == 'Y')
                ).drop('symbol', 'template')

                if target_y.is_empty():
                    cache[cache_key] = {
                        'state': 'no_hist', 'baskets': {}
                    }
                    cache_tag = 'no_hist'
                else:
                    if etf_anchor_week != wk:
                        bsk_scenarios._etf_returns_cache.clear()
                        etf_anchor_week = wk

                    barra = barra_by_week.get(wk)
                    if barra is None and wk not in barra_by_week:
                        with as_of(wk_d):
                            barra = build_barra_model(
                                refs, hists_pit
                            )
                        barra_by_week[wk] = barra

                    if barra is None:
                        cache[cache_key] = {
                            'state': 'no_barra', 'baskets': {}
                        }
                        cache_tag = 'no_barra'
                    else:
                        try:
                            with as_of(wk_d):
                                out = build_baskets(
                                    sym, target_y, refs, hists_pit,
                                    barra_model=barra,
                                )
                        except Exception as e:
                            # e.g. skfolio NonPositiveVarianceError
                            # when factor model is degenerate due to
                            # insufficient point-in-time history.
                            log.warning(
                                f'{i}/{len(trades)} {sym}@{td}: '
                                f'opt failed: {type(e).__name__}'
                            )
                            out = None
                        if out is None:
                            cache[cache_key] = {
                                'state': 'no_baskets',
                                'baskets': {},
                            }
                            cache_tag = 'no_baskets'
                        else:
                            baskets, _ = out
                            cache[cache_key] = {
                                'state': 'ok', 'baskets': baskets
                            }
                            n_builds += 1
                            cache_tag = 'build'

            results_trades.append(
                build_trade_row(trade, chars, baskets)
            )

            score_rows: list[dict] = []
            if baskets:
                score_rows = score_trade(
                    sym, td, offer_price, hists, baskets
                )
                results_scores.extend(score_rows)

            log.info(
                f'{i}/{len(trades)} {sym}@{td} '
                f'[{cache_tag}] scores={len(score_rows)} '
                f'({perf_counter() - t0:.1f}s)'
            )
    finally:
        save_basket_cache(cache)
        log.info(
            f'cache: hits={n_hits} builds={n_builds} '
            f'total={n_hits + n_builds}'
        )

    if not results_trades:
        print('no trades produced', file=sys.stderr)
        sys.exit(1)

    trades_df = pl.DataFrame(results_trades)
    scores_df = (
        pl.DataFrame(results_scores)
        if results_scores else None
    )

    trades_df.write_parquet(TRADES_OUT, compression='zstd')
    log.green(
        f'wrote {len(trades_df)} trade rows -> {TRADES_OUT.name}'
    )
    if scores_df is not None:
        scores_df.write_parquet(SCORES_OUT, compression='zstd')
        log.green(
            f'wrote {len(scores_df)} score rows '
            f'-> {SCORES_OUT.name}'
        )

    print_summary(trades_df, scores_df)


def print_summary(
    trades_df: pl.DataFrame,
    scores_df: pl.DataFrame | None,
) -> None:
    print('\n== trade population ==')
    n = len(trades_df)
    n_hedged = (
        trades_df.filter(
            pl.col('combined_corr').is_not_null()
        ).height
    )
    print(f'trades: {n}  hedgeable: {n_hedged}')
    print(
        trades_df.select([
            pl.col('actual_discount').mean().alias(
                'avg_actual_disc'
            ),
            pl.col('model_discount').mean().alias(
                'avg_model_disc'
            ),
            pl.col('discount_delta').mean().alias(
                'avg_delta'
            ),
            pl.col('vol_90d').median().alias('med_vol_90d'),
            pl.col('combined_corr').median().alias(
                'med_corr'
            ),
        ])
    )

    if scores_df is None or scores_df.is_empty():
        return

    print('\n== returns by scenario x period x window ==')
    summary = (
        scores_df.group_by(['scenario', 'period', 'window_d'])
        .agg([
            pl.len().alias('n'),
            pl.col('raw_return').mean().alias('avg_raw'),
            pl.col('hedged_return').mean().alias('avg_hedged'),
            (
                pl.col('raw_return').abs()
                - pl.col('hedged_return').abs()
            ).mean().alias('avg_mag_reduce'),
            (
                (
                    pl.col('raw_return').abs()
                    - pl.col('hedged_return').abs()
                )
                > 0
            ).mean().alias('hit_rate'),
        ])
        .sort(['scenario', 'period', 'window_d'])
    )
    with pl.Config(tbl_rows=-1, tbl_cols=-1):
        print(summary)


if __name__ == '__main__':
    main()
