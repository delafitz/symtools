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
from app.services.portfolio.caps import (
    DEFAULT_MAX_GMV_USD,
    DEFAULT_MAX_POS,
    apply_caps_scaled,
)
from app.services.portfolio.expected import compute_expected
from app.services.portfolio.filters import bank_filter, sector_filter
from app.services.portfolio.position import (
    DEFAULT_COST_BPS,
    DEFAULT_HEDGE_RATIO,
    DEFAULT_R0_STOP_PCT,
    DEFAULT_STOP_BASIS,
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
    r0_stop_pct: float | None = None,
    profit_take_pct: float | None = 0.20,
    max_gmv_usd: float | None = None,
    max_pos: int | None = None,
    cost_bps_per_side: float = DEFAULT_COST_BPS,
    stop_basis: str = DEFAULT_STOP_BASIS,
    bank_filter_fn=bank_filter,
    sector_filter_fn=sector_filter,
    flow_filter_fn=None,
) -> tuple[pl.DataFrame, dict[int, pl.DataFrame]]:
    trades = pl.read_parquet('data/backtest_trades.parquet')
    scores = pl.read_parquet('data/backtest_scores.parquet')
    baskets = load_basket_lookup()
    hists = pl.read_parquet(latest('hists'))

    trades = join_pre_returns(trades, scores)
    trades = attach_combined_beta(trades, baskets)

    # Attach GICS sector (post-overrides) for strategy filters.
    refs = pl.read_parquet(latest('refs')).select([
        pl.col('symbol'),
        pl.col('g_sector').alias('sector'),
    ])
    trades = trades.join(refs, on='symbol', how='left')

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

        # Strategy filters: per-broker and per-sector
        # multipliers. Passed to size_position as a pre-clip
        # multiplier so the hard caps (position $cap, deal_pct,
        # VaR cap) still bind on upsized trades.
        bank_mult = bank_filter_fn(tr.get('broker'))
        if sector_filter_fn is not None:
            bank_mult *= sector_filter_fn(tr.get('sector'))
        if flow_filter_fn is not None:
            bank_mult *= flow_filter_fn(tr)
        notional = size_position(
            adv_usd or 0,
            size_params,
            vol_90d_annual_pct=vol,
            corr=rho,
            deal_size_usd=tr.get('deal_size'),
            pre_clip_mult=bank_mult,
        )
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
                r0_stop_pct=r0_stop_pct,
                profit_take_pct=profit_take_pct,
                cost_bps_per_side=cost_bps_per_side,
                stop_basis=stop_basis,
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
            row['sector'] = tr.get('sector')
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

    # Portfolio-level caps (chronological, per window). Applied
    # AFTER per-trade scoring — scales down dollar fields of new
    # trades that would breach the GMV cap; binary-skips trades
    # that would breach the position cap. See app/services/
    # portfolio/caps.py for the policy.
    if max_gmv_usd is not None or max_pos is not None:
        capped_parts = []
        stats_log: list[tuple[int, dict]] = []
        for w in WINDOWS:
            sub = positions.filter(pl.col('window_d') == w)
            kept, stats = apply_caps_scaled(
                sub, max_pos=max_pos, max_gmv_usd=max_gmv_usd,
            )
            capped_parts.append(kept)
            stats_log.append((w, stats))
        positions = pl.concat(capped_parts)
        for w, s in stats_log:
            log.info(
                f'caps w={w}: full={s["full"]} '
                f'partial={s["partial"]} zero={s["zero_capacity"]} '
                f'skipped_pos={s["skipped_pos"]} '
                f'(of {s["total"]})'
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
    cap_usd = 75_000_000
    var_cap_usd: float | None = 50_000_000  # soft cap; ~5% of trades clipped
    hedge_ratio = DEFAULT_HEDGE_RATIO
    stop_pct = DEFAULT_STOP_PCT  # -0.10 by default
    r0_stop_pct: float | None = DEFAULT_R0_STOP_PCT
    max_gmv_usd: float | None = DEFAULT_MAX_GMV_USD
    max_pos: int | None = DEFAULT_MAX_POS
    cost_bps = DEFAULT_COST_BPS  # 10 bps/side
    stop_basis = DEFAULT_STOP_BASIS  # 'hedged'
    bank_filter_fn = bank_filter
    sector_filter_fn = sector_filter

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
        elif flag == '--var-cap':
            var_cap_usd = float(args.pop(0))
        elif flag == '--hedge-ratio':
            hedge_ratio = float(args.pop(0))
        elif flag == '--stop':
            stop_pct = float(args.pop(0))
        elif flag == '--r0-stop':
            v = args.pop(0)
            r0_stop_pct = None if v == 'none' else float(v)
        elif flag == '--gmv-cap':
            v = args.pop(0)
            max_gmv_usd = None if v == 'none' else float(v)
        elif flag == '--pos-cap':
            v = args.pop(0)
            max_pos = None if v == 'none' else int(v)
        elif flag == '--cost-bps':
            cost_bps = float(args.pop(0))
        elif flag == '--stop-basis':
            stop_basis = args.pop(0)
        elif flag == '--no-bank-filter':
            from app.services.portfolio.filters import no_filter
            bank_filter_fn = no_filter
        elif flag == '--no-sector-filter':
            from app.services.portfolio.filters import (
                no_sector_filter,
            )
            sector_filter_fn = no_sector_filter
        else:
            print(f'unknown arg: {flag}', file=sys.stderr)
            sys.exit(1)

    size_params = SizeParams(
        pct_adv=pct_adv,
        floor_usd=floor_usd,
        cap_usd=cap_usd,
        var_cap_usd=var_cap_usd,
    )

    positions, monthly = run(
        mode=mode,
        strategy=strategy,
        n=n,
        seed=seed,
        size_params=size_params,
        hedge_ratio=hedge_ratio,
        stop_pct=stop_pct,
        r0_stop_pct=r0_stop_pct,
        max_gmv_usd=max_gmv_usd,
        max_pos=max_pos,
        cost_bps_per_side=cost_bps,
        stop_basis=stop_basis,
        bank_filter_fn=bank_filter_fn,
        sector_filter_fn=sector_filter_fn,
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
