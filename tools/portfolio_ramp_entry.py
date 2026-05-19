"""Research: 3-day ramp entry on the long target leg.

Hypothesis: low-quality blocks (bad-bank, pre-1d panic, high
xADV, bad sector) drift further after T. Spreading entry over
T, T+1, T+2 (1/3 each at close) should improve entry price for
these cohorts vs taking full size at T (offer_price).

This first pass keeps the hedge leg unchanged (full hedge
entered at T close as in production). It isolates the
"target-side post-T drift" question. A follow-up can ramp the
hedge leg too if the long-side signal is real.

Quality buckets (from block-alpha-drivers.md):
  bad_bank      : broker ∈ {MS, BAC, BAML, JPM}
  panic         : r_pre1 ≤ −5%
  high_xadv     : shares_pct_adv > 5
  bad_sector    : sector ∈ {Energy, Real Estate}
  any_low_qual  : OR of the above

Usage:
    uv run python tools/portfolio_ramp_entry.py
    uv run python tools/portfolio_ramp_entry.py --window 5
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from app.utils.logger import get_logger

log = get_logger(__name__)


BAD_BANKS = {'MS', 'BAC', 'BAML', 'JPM'}
BAD_SECTORS = {'Energy', 'Real Estate'}
COST_BPS = 10.0  # per side, matches production


def latest(glob: str) -> Path:
    return sorted(Path('data').glob(glob))[-1]


def load_inputs() -> tuple[pl.DataFrame, pl.DataFrame]:
    pos = pl.read_parquet(latest('portfolio_trades.*.parquet'))

    # Add quality features: shares_pct_adv, r_pre1, sector
    trades = pl.read_parquet('data/backtest_trades.parquet')
    tr_unique = trades.group_by(['symbol', 'trade_date']).agg(
        pl.col('shares_pct_adv').first()
    )
    pos = pos.join(
        tr_unique, on=['symbol', 'trade_date'], how='left',
    )

    scores = pl.read_parquet('data/backtest_scores.parquet').filter(
        (pl.col('scenario') == 'combined')
        & (pl.col('period') == 'pre')
        & (pl.col('window_d') == 1)
    )
    pre1 = scores.group_by(['symbol', 'trade_date']).agg(
        pl.col('raw_return').mean().alias('r_pre1')
    )
    pos = pos.join(pre1, on=['symbol', 'trade_date'], how='left')

    refs = pl.read_parquet(latest('refs.*.parquet')).select([
        pl.col('symbol'),
        pl.col('g_sector').alias('refs_sector'),
    ])
    if 'sector' in pos.columns:
        pos = pos.drop('sector')
    pos = pos.join(refs, on='symbol', how='left').rename(
        {'refs_sector': 'sector'}
    )

    hists = pl.read_parquet(latest('hists.*.parquet')).filter(
        pl.col('template') == 'Y'
    ).select(['symbol', 'date', 'close']).sort(['symbol', 'date'])

    return pos, hists


def add_next_closes(
    pos: pl.DataFrame, hists: pl.DataFrame,
) -> pl.DataFrame:
    """For each (symbol, trade_date), look up close on T+1 and
    T+2 trading days from hists. Returns pos with extra cols
    `close_t1` and `close_t2` (null if hist is missing or
    the trade is at the tail of available data)."""

    # Per-symbol indexed list of dates → fast lookup
    per_sym = (
        hists.group_by('symbol').agg(
            pl.col('date').alias('dates'),
            pl.col('close').alias('closes'),
        )
    )
    lookup: dict[str, tuple[list[str], list[float]]] = {
        r['symbol']: (r['dates'], r['closes'])
        for r in per_sym.iter_rows(named=True)
    }

    t1, t2 = [], []
    for r in pos.iter_rows(named=True):
        sym, tdate = r['symbol'], r['trade_date']
        rec = lookup.get(sym)
        if rec is None:
            t1.append(None); t2.append(None); continue
        dates, closes = rec
        # binary-ish: locate tdate; we'll just walk
        try:
            i = dates.index(tdate)
        except ValueError:
            t1.append(None); t2.append(None); continue
        c1 = closes[i + 1] if i + 1 < len(dates) else None
        c2 = closes[i + 2] if i + 2 < len(dates) else None
        t1.append(c1); t2.append(c2)

    return pos.with_columns([
        pl.Series('close_t1', t1, dtype=pl.Float64),
        pl.Series('close_t2', t2, dtype=pl.Float64),
    ])


def recompute_ramp_entry(pos: pl.DataFrame) -> pl.DataFrame:
    """Compute ramped-entry variant of target P&L.

    avg_entry_px = (offer + close_t1 + close_t2) / 3
    target_pnl_ramp = shares × (target_avg_exit_px − avg_entry_px)
                      − cost_target  (unchanged)
    pnl_hedged_ramp = target_pnl_ramp + hedge_pnl_usd  (hedge unchanged)
    """
    df = pos.filter(
        pl.col('close_t1').is_not_null()
        & pl.col('close_t2').is_not_null()
    ).with_columns([
        (
            (pl.col('offer_price') + pl.col('close_t1')
             + pl.col('close_t2')) / 3
        ).alias('avg_entry_px'),
    ]).with_columns([
        (
            pl.col('shares') * (
                pl.col('target_avg_exit_px')
                - pl.col('avg_entry_px')
            ) - pl.col('cost_target_usd')
        ).alias('target_pnl_ramp_usd'),
    ]).with_columns([
        (
            pl.col('target_pnl_ramp_usd')
            + pl.col('hedge_pnl_usd')
        ).alias('pnl_hedged_ramp_usd'),
        (
            (pl.col('avg_entry_px') / pl.col('offer_price') - 1)
        ).alias('entry_drift'),  # negative = ramp got worse price
    ])
    return df


def add_buckets(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns([
        pl.col('broker').is_in(list(BAD_BANKS)).alias('q_bad_bank'),
        (pl.col('r_pre1') <= -0.05).fill_null(False).alias('q_panic'),
        (pl.col('shares_pct_adv') > 5.0).fill_null(False).alias(
            'q_high_xadv'
        ),
        pl.col('sector').is_in(list(BAD_SECTORS)).alias(
            'q_bad_sector'
        ),
    ]).with_columns(
        (
            pl.col('q_bad_bank')
            | pl.col('q_panic')
            | pl.col('q_high_xadv')
            | pl.col('q_bad_sector')
        ).alias('q_any_low_qual')
    )


def bucket_stats(
    df: pl.DataFrame, flag: str, label: str,
) -> dict:
    sub = df.filter(pl.col(flag))
    rest = df.filter(~pl.col(flag))

    def _stats(d: pl.DataFrame, name: str) -> dict:
        if d.is_empty():
            return {'cohort': name, 'n': 0}
        hed = d['pnl_hedged_usd'].sum()
        hed_r = d['pnl_hedged_ramp_usd'].sum()
        n = d.height
        avg_drift = d['entry_drift'].mean()
        # per-trade avg return (hedged / notional)
        d2 = d.with_columns([
            (pl.col('pnl_hedged_usd') / pl.col('notional_usd'))
                .alias('ret_h'),
            (pl.col('pnl_hedged_ramp_usd') / pl.col('notional_usd'))
                .alias('ret_h_ramp'),
        ])
        return {
            'cohort': name,
            'n': n,
            'avg_drift_bps': (avg_drift or 0.0) * 10000.0,
            'pnl_h_M': hed / 1e6,
            'pnl_h_ramp_M': hed_r / 1e6,
            'pnl_delta_M': (hed_r - hed) / 1e6,
            'avg_ret_h_pct': float(d2['ret_h'].mean() or 0) * 100,
            'avg_ret_h_ramp_pct': float(
                d2['ret_h_ramp'].mean() or 0
            ) * 100,
        }

    return [_stats(sub, f'{label}=Y'), _stats(rest, f'{label}=N')]


def render(df: pl.DataFrame, window_d: int) -> None:
    sub = df.filter(pl.col('window_d') == window_d)
    if sub.is_empty():
        print(f'(no rows for window {window_d}d)')
        return

    print(
        f'\n## Ramp-entry research (window={window_d}d, '
        f'hedge unchanged, target ramps 1/3 over T,T+1,T+2)\n'
    )

    # All trades baseline
    n = sub.height
    drift_all = float(sub['entry_drift'].mean() or 0) * 10000
    pnl_h = float(sub['pnl_hedged_usd'].sum()) / 1e6
    pnl_h_ramp = float(sub['pnl_hedged_ramp_usd'].sum()) / 1e6
    delta = pnl_h_ramp - pnl_h
    print(
        f'All trades (n={n}): drift={drift_all:+.0f}bps  '
        f'pnl_h=${pnl_h:+.1f}M  '
        f'pnl_h_ramp=${pnl_h_ramp:+.1f}M  '
        f'Δ=${delta:+.1f}M'
    )
    print()

    flags = [
        ('q_bad_bank', 'bad_bank'),
        ('q_panic', 'panic'),
        ('q_high_xadv', 'high_xadv'),
        ('q_bad_sector', 'bad_sector'),
        ('q_any_low_qual', 'any_low_qual'),
    ]
    hdr = (
        f'{"cohort":<18} {"n":>4} {"drift":>8} '
        f'{"pnl_h":>9} {"pnl_h_ramp":>11} {"Δ":>8} '
        f'{"ret_h%":>8} {"ret_ramp%":>10}'
    )
    print(hdr)
    print('-' * len(hdr))
    for flag, label in flags:
        rows = bucket_stats(sub, flag, label)
        for r in rows:
            if r.get('n', 0) == 0:
                continue
            print(
                f'{r["cohort"]:<18} {r["n"]:>4d} '
                f'{r["avg_drift_bps"]:>+7.0f}b '
                f'{r["pnl_h_M"]:>+8.1f}M '
                f'{r["pnl_h_ramp_M"]:>+10.1f}M '
                f'{r["pnl_delta_M"]:>+7.1f}M '
                f'{r["avg_ret_h_pct"]:>+7.2f}% '
                f'{r["avg_ret_h_ramp_pct"]:>+9.2f}%'
            )


def main() -> None:
    args = sys.argv[1:]
    windows = [5, 10, 20]
    while args:
        f = args.pop(0)
        if f == '--window':
            windows = [int(args.pop(0))]
        else:
            print(f'unknown arg: {f}', file=sys.stderr)
            sys.exit(1)

    pos, hists = load_inputs()
    log.info(f'pos rows={len(pos)}; hists rows={len(hists)}')
    pos = add_next_closes(pos, hists)
    pos = recompute_ramp_entry(pos)
    pos = add_buckets(pos)

    for w in windows:
        render(pos, w)


if __name__ == '__main__':
    main()
