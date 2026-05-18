"""Portfolio-level block trade backtest.

Sizes each block trade based on a % of trailing ADV, sims a
realistic entry/exit ramp with stop-loss, and rolls up to a
monthly portfolio with realized vs expected scorecard.

Inputs (must exist; produced by `tools/backtest.py`):
  data/backtest_trades.parquet
  data/backtest_baskets.parquet
  data/backtest_scores.parquet
  data/hists.YYYYMMDD.parquet
  data/refs.YYYYMMDD.parquet

Outputs:
  data/portfolio_trades.{stamp}.parquet   per-trade-per-window
  data/portfolio_monthly.{stamp}.parquet  monthly aggregates

Usage:
    uv run python tools/portfolio_backtest.py
    uv run python tools/portfolio_backtest.py --mode random --n 100
    uv run python tools/portfolio_backtest.py --mode strategy --strategy d10
    uv run python tools/portfolio_backtest.py --pct-adv 0.4
"""

import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from app.services.portfolio.aggregator import (
    monthly_aggregate,
    portfolio_summary,
)
from app.services.portfolio.expected import compute_expected
from app.services.portfolio.position import (
    DEFAULT_HEDGE_RATIO,
    DEFAULT_STOP_PCT,
    score_position,
)
from app.services.portfolio.sampler import sample_trades
from app.services.portfolio.sizer import SizeParams, size_position
from app.utils.logger import get_logger

# Reuse helpers from tools/backtest.py
from tools.backtest import (
    basket_close_series,
    params_hash,
    week_anchor,
)

log = get_logger(__name__)

WINDOWS = (5, 10, 20)
SCENARIO = 'combined'


def latest(name: str) -> Path | None:
    files = sorted(Path('data').glob(f'{name}.*.parquet'))
    return files[-1] if files else None


def load_basket_lookup() -> dict:
    """Map (symbol, week, params_hash) → {scenario → basket dict}."""
    path = Path('data/backtest_baskets.parquet')
    if not path.exists():
        return {}
    df = pl.read_parquet(path).filter(pl.col('state') == 'ok')
    out: dict = {}
    for r in df.iter_rows(named=True):
        key = (r['symbol'], r['week'], r['params_hash'])
        if r['scenario'] and r['basket_json']:
            out.setdefault(key, {})[r['scenario']] = json.loads(
                r['basket_json']
            )
    return out


def build_target_daily(
    symbol: str, hists: pl.DataFrame
) -> pl.DataFrame:
    return (
        hists.filter(
            (pl.col('symbol') == symbol)
            & (pl.col('template') == 'Y')
        )
        .select(['date', 'close'])
        .sort('date')
    )


def join_pre_returns(
    trades: pl.DataFrame, scores: pl.DataFrame
) -> pl.DataFrame:
    """Attach pre-trade 1d and 20d returns (combined scenario)
    so strategy sampling can use them."""
    s = scores.filter(
        (pl.col('scenario') == SCENARIO)
        & (pl.col('period') == 'pre')
    )
    pre1 = (
        s.filter(pl.col('window_d') == 1)
        .group_by(['symbol', 'trade_date'])
        .agg(pl.col('raw_return').mean().alias('r_pre1'))
    )
    pre20 = (
        s.filter(pl.col('window_d') == 20)
        .group_by(['symbol', 'trade_date'])
        .agg(pl.col('raw_return').mean().alias('r_pre20'))
    )
    return trades.join(
        pre1, on=['symbol', 'trade_date'], how='left'
    ).join(pre20, on=['symbol', 'trade_date'], how='left')


def attach_combined_beta(
    trades: pl.DataFrame, baskets: dict
) -> pl.DataFrame:
    """Lookup combined-basket β for each (symbol, trade_date).
    Adds a `combined_beta_lookup` column. Note: trades parquet
    has `combined_beta` already, prefer that."""
    if 'combined_beta' in trades.columns:
        return trades  # already present
    ph = params_hash(None)
    betas: list[float | None] = []
    for r in trades.iter_rows(named=True):
        wk = week_anchor(r['trade_date'])
        entry = baskets.get((r['symbol'], wk, ph), {}).get('combined')
        betas.append(
            entry['stats']['beta'] if entry else None
        )
    return trades.with_columns(
        pl.Series('combined_beta', betas, dtype=pl.Float64)
    )


def run(
    mode: str,
    strategy: str | None,
    n: int | None,
    seed: int,
    size_params: SizeParams,
    hedge_ratio: float,
    stop_pct: float,
) -> tuple[pl.DataFrame, dict[int, pl.DataFrame]]:
    trades = pl.read_parquet('data/backtest_trades.parquet')
    scores = pl.read_parquet('data/backtest_scores.parquet')
    baskets = load_basket_lookup()
    hists = pl.read_parquet(latest('hists'))

    trades = join_pre_returns(trades, scores)
    trades = attach_combined_beta(trades, baskets)

    # Sampling
    selected = sample_trades(
        trades, mode=mode, strategy=strategy, n=n, seed=seed,
    )
    log.info(
        f'portfolio: scoring {len(selected)} trades '
        f'(mode={mode}, strategy={strategy})'
    )

    ph = params_hash(None)
    rows: list[dict] = []
    skipped = {'no_basket': 0, 'no_size': 0, 'no_score': 0}

    for tr in selected.iter_rows(named=True):
        sym = tr['symbol']
        td = tr['trade_date']
        offer = tr.get('offer_price')
        adv_usd = tr.get('adv_usd_30d')
        vol = tr.get('vol_90d')
        rho = tr.get('combined_corr')
        beta = tr.get('combined_beta')
        disc = tr.get('actual_discount')

        if offer is None or beta is None:
            skipped['no_basket'] += 1
            continue

        notional = size_position(adv_usd or 0, size_params)
        if notional <= 0:
            skipped['no_size'] += 1
            continue

        # Basket close series
        wk = week_anchor(td)
        basket = baskets.get((sym, wk, ph), {}).get('combined')
        if not basket:
            skipped['no_basket'] += 1
            continue
        bc = basket_close_series(basket['weights'], hists)
        if bc is None:
            skipped['no_basket'] += 1
            continue
        td_daily = build_target_daily(sym, hists)
        if td_daily.is_empty():
            skipped['no_basket'] += 1
            continue

        for w in WINDOWS:
            res = score_position(
                symbol=sym,
                trade_date=td,
                offer_price=offer,
                notional_usd=notional,
                beta=beta,
                target_daily=td_daily,
                basket_close=bc,
                window_d=w,
                hedge_ratio=hedge_ratio,
                stop_pct=stop_pct,
            )
            if res is None:
                skipped['no_score'] += 1
                continue

            exp = compute_expected(
                actual_discount=disc or 0.0,
                vol_90d_annual_pct=vol or 0.0,
                basket_target_corr=rho or 0.0,
                notional_usd=notional,
                window_d=w,
            )

            row = asdict(res)
            row['actual_discount'] = disc
            row['adv_usd_30d'] = adv_usd
            row['vol_90d'] = vol
            row['combined_corr'] = rho
            row['registered'] = tr.get('registered')
            row['broker'] = tr.get('broker')
            row['sector'] = None  # joined later if needed
            if exp:
                row.update({
                    'expected_return_unhedged':
                        exp.expected_return_unhedged,
                    'expected_return_hedged':
                        exp.expected_return_hedged,
                    'expected_pnl_unhedged_usd':
                        exp.expected_pnl_unhedged_usd,
                    'expected_pnl_hedged_usd':
                        exp.expected_pnl_hedged_usd,
                    'var99_unhedged_usd': exp.var99_unhedged_usd,
                    'var99_hedged_usd': exp.var99_hedged_usd,
                    'sigma_daily': exp.sigma_daily,
                    'rho_used': exp.rho_used,
                })
            rows.append(row)

    if not rows:
        log.warning('portfolio: no scored trades')
        return pl.DataFrame(), {}

    positions = pl.DataFrame(rows)
    log.info(
        f'portfolio: {len(positions)} position-window rows; '
        f'skipped={skipped}'
    )

    monthly: dict[int, pl.DataFrame] = {}
    for w in WINDOWS:
        monthly[w] = monthly_aggregate(positions, window_d=w)

    return positions, monthly


def print_summary(
    positions: pl.DataFrame, monthly: dict[int, pl.DataFrame]
) -> None:
    if positions.is_empty():
        print('no positions to summarize', file=sys.stderr)
        return

    print('\n## Per-trade summary (by window)\n')
    by_w = (
        positions.group_by('window_d').agg([
            pl.len().alias('n'),
            pl.col('notional_usd').mean().alias('avg_notional'),
            pl.col('return_unhedged').mean().alias('avg_ret_unhedged'),
            pl.col('return_hedged').mean().alias('avg_ret_hedged'),
            pl.col('expected_return_hedged').mean()
                .alias('avg_exp_ret_hedged'),
            (pl.col('return_hedged') > 0).mean().alias('hedged_hit'),
            pl.col('stop_triggered').sum().alias('n_stops'),
            pl.col('var99_hedged_usd').mean().alias('avg_var99_hed'),
        ]).sort('window_d')
    )
    with pl.Config(tbl_cols=-1, tbl_width_chars=200):
        print(by_w)

    for w in (5, 10, 20):
        m = monthly.get(w)
        if m is None or m.is_empty():
            continue
        print(f'\n## Monthly aggregate (window={w}d)\n')
        with pl.Config(tbl_cols=-1, tbl_width_chars=200, tbl_rows=-1):
            print(m)
        summ = portfolio_summary(m)
        print(f'\nsummary (window={w}d): {summ}\n')


def main() -> None:
    args = sys.argv[1:]
    mode = 'all'
    strategy: str | None = None
    n: int | None = None
    seed = 42
    pct_adv = 0.15
    floor_usd = 10_000_000
    cap_usd = 100_000_000
    hedge_ratio = DEFAULT_HEDGE_RATIO
    stop_pct = DEFAULT_STOP_PCT  # -0.08 by default

    while args:
        flag = args.pop(0)
        if flag == '--mode':
            mode = args.pop(0)
        elif flag == '--strategy':
            strategy = args.pop(0)
        elif flag == '--n':
            n = int(args.pop(0))
        elif flag == '--seed':
            seed = int(args.pop(0))
        elif flag == '--pct-adv':
            pct_adv = float(args.pop(0))
        elif flag == '--floor':
            floor_usd = float(args.pop(0))
        elif flag == '--cap':
            cap_usd = float(args.pop(0))
        elif flag == '--hedge-ratio':
            hedge_ratio = float(args.pop(0))
        elif flag == '--stop':
            stop_pct = float(args.pop(0))
        else:
            print(f'unknown arg: {flag}', file=sys.stderr)
            sys.exit(1)

    size_params = SizeParams(
        pct_adv=pct_adv, floor_usd=floor_usd, cap_usd=cap_usd
    )

    positions, monthly = run(
        mode=mode,
        strategy=strategy,
        n=n,
        seed=seed,
        size_params=size_params,
        hedge_ratio=hedge_ratio,
        stop_pct=stop_pct,
    )
    if positions.is_empty():
        sys.exit(1)

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_pos = Path('data') / f'portfolio_trades.{stamp}.parquet'
    positions.write_parquet(out_pos, compression='zstd')
    log.green(f'wrote {len(positions)} rows -> {out_pos.name}')

    # Concatenate per-window monthly into one parquet with a
    # `window_d` column for easy slicing.
    mlist = []
    for w, m in monthly.items():
        if m.is_empty():
            continue
        mlist.append(m.with_columns(pl.lit(w).alias('window_d')))
    if mlist:
        merged = pl.concat(mlist, how='diagonal_relaxed')
        out_m = Path('data') / f'portfolio_monthly.{stamp}.parquet'
        merged.write_parquet(out_m, compression='zstd')
        log.green(f'wrote monthly -> {out_m.name}')

    print_summary(positions, monthly)


if __name__ == '__main__':
    main()
