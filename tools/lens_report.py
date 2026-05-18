"""Consolidated lens report for block-trade backtest output.

Runs every cross-sectional cut we've explored:
  - Population summary
  - Discount bucket (<2%, 2-5%, >=5%)
  - Decile by post 20d hedged
  - Reg vs Unreg
  - Lead bank (GS / MS / JPM / BAML / Citi / Other)
  - GICS sector
  - Hedge-ratio sensitivity sweep

Operates on the combined scenario at one observation per
(symbol, trade_date) — multi-broker tranches collapsed.

Usage:
    uv run python tools/lens_report.py
    uv run python tools/lens_report.py --save
    uv run python tools/lens_report.py --scenario factors

`--save` writes markdown to data/lens_report.{stamp}.md for
easy diff between block-trade datasets.
"""

import sys
from datetime import datetime
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl


PCT = lambda x: f'{x:+.2%}' if x is not None else ''


def latest(name: str) -> Path | None:
    files = sorted(Path('data').glob(f'{name}.*.parquet'))
    return files[-1] if files else None


def fmt_md_table(
    df: pl.DataFrame, pct_cols: set[str] | None = None
) -> str:
    """Render a polars DF as a markdown table with optional
    percent formatting."""
    pct_cols = pct_cols or set()
    cols = df.columns
    rows = df.to_dicts()
    lines = ['| ' + ' | '.join(cols) + ' |']
    lines.append('|' + '|'.join('---' for _ in cols) + '|')
    for r in rows:
        cells = []
        for c in cols:
            v = r[c]
            if v is None:
                cells.append('')
            elif c in pct_cols and isinstance(v, float):
                cells.append(f'{v:+.2%}')
            elif isinstance(v, float):
                cells.append(f'{v:.3f}' if abs(v) < 100 else f'{v:.0f}')
            else:
                cells.append(str(v))
        lines.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def build_join(
    trades_df: pl.DataFrame,
    scores_df: pl.DataFrame,
    refs_df: pl.DataFrame | None,
    scenario: str,
) -> pl.DataFrame:
    """One row per (symbol, trade_date) with characteristics +
    pre/post raw and hedged returns at standard windows."""
    s = scores_df.filter(pl.col('scenario') == scenario)
    t = trades_df
    if refs_df is not None and 'g_sector' in refs_df.columns:
        t = t.join(
            refs_df.select(['symbol', 'g_sector']),
            on='symbol', how='left',
        )
        t = t.with_columns(
            pl.when(
                (pl.col('g_sector').is_null())
                | (pl.col('g_sector') == '')
            ).then(pl.lit('(unknown)'))
            .otherwise(pl.col('g_sector'))
            .alias('sector')
        )
    else:
        t = t.with_columns(pl.lit('(unknown)').alias('sector'))

    t_agg = t.group_by(['symbol', 'trade_date']).agg([
        pl.col('actual_discount').mean(),
        pl.col('shares_pct_adv').mean(),
        pl.col('vol_90d').first(),
        pl.col('combined_corr').first(),
        pl.col('registered').first().fill_null(False),
        pl.col('sector').first(),
    ])

    def winret(p: str, w: int, tag: str) -> pl.DataFrame:
        return s.filter(
            (pl.col('period') == p) & (pl.col('window_d') == w)
        ).group_by(['symbol', 'trade_date']).agg(
            pl.col('raw_return').mean().alias(f'r_{tag}'),
            pl.col('hedged_return').mean().alias(f'h_{tag}'),
        )

    j = t_agg
    for tag, (p, w) in [
        ('p1', ('post', 1)), ('p5', ('post', 5)),
        ('p10', ('post', 10)), ('p20', ('post', 20)),
        ('pre1', ('pre', 1)), ('pre5', ('pre', 5)),
        ('pre10', ('pre', 10)), ('pre20', ('pre', 20)),
    ]:
        j = j.join(winret(p, w, tag),
                   on=['symbol', 'trade_date'], how='inner')
    return j


def section_population(j: pl.DataFrame) -> str:
    out = ['## Population\n']
    out.append(f'n={len(j)} unique (symbol, trade_date)\n')
    summary = pl.DataFrame([{
        'avg_disc': j['actual_discount'].mean(),
        'avg_xadv': j['shares_pct_adv'].mean(),
        'avg_vol_90d': j['vol_90d'].mean(),
        'avg_combined_corr': j['combined_corr'].mean(),
        'pct_reg': j['registered'].mean(),
    }])
    out.append(fmt_md_table(
        summary, pct_cols={'avg_disc', 'pct_reg'}
    ))
    return '\n'.join(out)


def section_discount_bucket(j: pl.DataFrame) -> str:
    j = j.with_columns(
        pl.when(pl.col('actual_discount') > -0.02)
        .then(pl.lit('<2%'))
        .when(pl.col('actual_discount') > -0.05)
        .then(pl.lit('2-5%'))
        .otherwise(pl.lit('>=5%'))
        .alias('bucket')
    )
    prof = j.group_by('bucket').agg([
        pl.len().alias('n'),
        pl.col('actual_discount').mean().alias('disc'),
        pl.col('shares_pct_adv').mean().alias('xadv'),
        pl.col('vol_90d').mean().alias('vol'),
        pl.col('r_pre20').mean().alias('pre20'),
        pl.col('r_pre1').mean().alias('pre1'),
        pl.col('r_p1').mean().alias('rp1'),
        pl.col('r_p5').mean().alias('rp5'),
        pl.col('r_p10').mean().alias('rp10'),
        pl.col('r_p20').mean().alias('rp20'),
        pl.col('h_p1').mean().alias('hp1'),
        pl.col('h_p5').mean().alias('hp5'),
        pl.col('h_p10').mean().alias('hp10'),
        pl.col('h_p20').mean().alias('hp20'),
        (pl.col('h_p20') > 0).mean().alias('hed_hit'),
    ]).sort('bucket')
    pct_cols = {
        'disc', 'pre20', 'pre1',
        'rp1', 'rp5', 'rp10', 'rp20',
        'hp1', 'hp5', 'hp10', 'hp20', 'hed_hit',
    }
    return '## Discount bucket\n\n' + fmt_md_table(prof, pct_cols)


def section_decile(j: pl.DataFrame) -> str:
    j = j.with_columns(
        pl.col('h_p20').qcut(
            10, labels=[f'D{i+1}' for i in range(10)]
        ).alias('d')
    )
    dec = j.group_by('d').agg([
        pl.len().alias('n'),
        pl.col('h_p20').mean().alias('mean_h20'),
        pl.col('h_p20').median().alias('med_h20'),
        pl.col('r_p20').mean().alias('mean_r20'),
        pl.col('actual_discount').mean().alias('disc'),
        pl.col('shares_pct_adv').mean().alias('xadv'),
        pl.col('vol_90d').mean().alias('vol'),
        pl.col('combined_corr').mean().alias('corr'),
        pl.col('r_pre1').mean().alias('pre1'),
        pl.col('r_pre20').mean().alias('pre20'),
    ]).sort('mean_h20', descending=True)
    pct_cols = {
        'mean_h20', 'med_h20', 'mean_r20',
        'disc', 'pre1', 'pre20',
    }
    return '## Decile by post 20d hedged\n\n' + fmt_md_table(
        dec, pct_cols
    )


def section_reg(j: pl.DataFrame) -> str:
    j = j.with_columns(
        pl.when(pl.col('registered'))
        .then(pl.lit('Reg'))
        .otherwise(pl.lit('Unreg'))
        .alias('type')
    )
    prof = j.group_by('type').agg([
        pl.len().alias('n'),
        pl.col('actual_discount').mean().alias('disc'),
        pl.col('shares_pct_adv').mean().alias('xadv'),
        pl.col('vol_90d').mean().alias('vol'),
        pl.col('combined_corr').mean().alias('corr'),
        pl.col('r_pre20').mean().alias('pre20'),
        pl.col('r_pre1').mean().alias('pre1'),
        pl.col('r_p20').mean().alias('rp20'),
        pl.col('h_p1').mean().alias('hp1'),
        pl.col('h_p5').mean().alias('hp5'),
        pl.col('h_p10').mean().alias('hp10'),
        pl.col('h_p20').mean().alias('hp20'),
        (pl.col('h_p20') > 0).mean().alias('hed_hit'),
    ]).sort('type')
    pct_cols = {
        'disc', 'pre20', 'pre1', 'rp20',
        'hp1', 'hp5', 'hp10', 'hp20', 'hed_hit',
    }
    return '## Registered vs Unregistered\n\n' + fmt_md_table(
        prof, pct_cols
    )


def section_bank(
    trades_df: pl.DataFrame, scores_df: pl.DataFrame,
    refs_df: pl.DataFrame | None, scenario: str,
) -> str:
    # Bank lens preserves the broker dimension; group by
    # (sym, td, broker) so one bank gets one row per tranche.
    t = trades_df.group_by(
        ['symbol', 'trade_date', 'broker']
    ).agg([
        pl.col('actual_discount').mean(),
        pl.col('shares_pct_adv').mean(),
        pl.col('vol_90d').first(),
        pl.col('combined_corr').first(),
        pl.col('registered').first().fill_null(False),
    ])
    t = t.with_columns(
        pl.when(pl.col('broker').is_in(['BAML', 'BAC']))
        .then(pl.lit('BAC'))
        .when(pl.col('broker').is_in(['C', 'Citi']))
        .then(pl.lit('Citi'))
        .when(pl.col('broker').is_in(['GS', 'MS', 'JPM']))
        .then(pl.col('broker'))
        .otherwise(pl.lit('Other'))
        .alias('bank')
    )

    s = scores_df.filter(pl.col('scenario') == scenario)

    def winret(p: str, w: int, tag: str) -> pl.DataFrame:
        return s.filter(
            (pl.col('period') == p) & (pl.col('window_d') == w)
        ).group_by(['symbol', 'trade_date']).agg(
            pl.col('raw_return').mean().alias(f'r_{tag}'),
            pl.col('hedged_return').mean().alias(f'h_{tag}'),
        )

    j = t
    for tag, (p, w) in [
        ('p1', ('post', 1)), ('p5', ('post', 5)),
        ('p10', ('post', 10)), ('p20', ('post', 20)),
        ('pre1', ('pre', 1)), ('pre20', ('pre', 20)),
    ]:
        j = j.join(winret(p, w, tag),
                   on=['symbol', 'trade_date'], how='inner')

    prof = j.group_by('bank').agg([
        pl.len().alias('n'),
        pl.col('registered').mean().alias('pct_reg'),
        pl.col('actual_discount').mean().alias('disc'),
        pl.col('shares_pct_adv').mean().alias('xadv'),
        pl.col('vol_90d').mean().alias('vol'),
        pl.col('combined_corr').mean().alias('corr'),
        pl.col('r_pre20').mean().alias('pre20'),
        pl.col('r_pre1').mean().alias('pre1'),
        pl.col('r_p20').mean().alias('rp20'),
        pl.col('h_p1').mean().alias('hp1'),
        pl.col('h_p5').mean().alias('hp5'),
        pl.col('h_p10').mean().alias('hp10'),
        pl.col('h_p20').mean().alias('hp20'),
        (pl.col('h_p20') > 0).mean().alias('hed_hit'),
    ]).sort('hp20', descending=True)
    pct_cols = {
        'pct_reg', 'disc', 'pre20', 'pre1', 'rp20',
        'hp1', 'hp5', 'hp10', 'hp20', 'hed_hit',
    }
    return '## Lead bank\n\n' + fmt_md_table(prof, pct_cols)


def section_sector(j: pl.DataFrame) -> str:
    sec = j.group_by('sector').agg([
        pl.len().alias('n'),
        pl.col('actual_discount').mean().alias('disc'),
        pl.col('shares_pct_adv').mean().alias('xadv'),
        pl.col('vol_90d').mean().alias('vol'),
        pl.col('combined_corr').mean().alias('corr'),
        pl.col('r_pre20').mean().alias('pre20'),
        pl.col('r_pre1').mean().alias('pre1'),
        pl.col('r_p20').mean().alias('rp20'),
        pl.col('h_p1').mean().alias('hp1'),
        pl.col('h_p5').mean().alias('hp5'),
        pl.col('h_p10').mean().alias('hp10'),
        pl.col('h_p20').mean().alias('hp20'),
        (pl.col('h_p20') > 0).mean().alias('hed_hit'),
    ]).sort('hp20', descending=True)
    pct_cols = {
        'disc', 'pre20', 'pre1', 'rp20',
        'hp1', 'hp5', 'hp10', 'hp20', 'hed_hit',
    }
    return '## GICS sector\n\n' + fmt_md_table(sec, pct_cols)


def section_hedge_sweep(
    scores_df: pl.DataFrame, scenario: str,
) -> str:
    """For the given scenario, sweep hedge ratio k from 0..1.25
    on post-window returns. Reports mean / std / Sharpe / hit.

    Note: hedged_return in scores is already computed at
    HEDGE_RATIO (current default 0.85). The sweep recovers
    raw - β*basket and rescales, so k=1.0 here corresponds to
    raw - 1.0*β*basket (un-haircut)."""
    s = scores_df.filter(
        (pl.col('scenario') == scenario)
        & (pl.col('period') == 'post')
    )
    # Recover β*basket_return = raw - hedged_at_HR
    # but hedged_at_HR = raw - HR * β * basket
    # so β*basket = (raw - hedged_at_HR) / HR
    # We don't know HR here without importing from backtest.py;
    # the simpler path is: the ratio of (raw - hedged) is HR*β*b
    # so we sweep k where "k=1" means full HR*β*b applied. To
    # express in original-β terms we'd need HR.
    # Document the convention explicitly.
    s = s.with_columns(
        (pl.col('raw_return') - pl.col('hedged_return'))
        .alias('hr_beta_b')
    )

    rows = []
    for k in [0.0, 0.25, 0.5, 0.6, 0.7, 0.8, 0.85,
              0.9, 1.0, 1.1, 1.2]:
        for w in [1, 5, 10, 20]:
            sw = s.filter(pl.col('window_d') == w).with_columns(
                (pl.col('raw_return')
                 - k * pl.col('hr_beta_b')).alias('hk')
            )
            mean = sw['hk'].mean()
            std = sw['hk'].std()
            rows.append({
                'k': k, 'window_d': w,
                'mean': mean, 'std': std,
                'sharpe': (mean / std) if std else None,
                'hit': float((sw['hk'] > 0).mean()),
            })
    df = pl.DataFrame(rows).sort(['window_d', 'k'])
    return (
        '## Hedge ratio sensitivity\n\n'
        'k applied as multiplier on the *currently-scored* '
        'hedge contribution (k=1.0 reproduces stored hedged; '
        'k=0 is unhedged).\n\n'
        + fmt_md_table(df, pct_cols={'mean', 'std', 'hit'})
    )


def run_report(scenario: str = 'combined') -> str:
    trades_path = Path('data/backtest_trades.parquet')
    scores_path = Path('data/backtest_scores.parquet')
    if not trades_path.exists() or not scores_path.exists():
        print(
            'missing backtest outputs — run tools/backtest.py first',
            file=sys.stderr,
        )
        sys.exit(1)

    trades = pl.read_parquet(trades_path)
    scores = pl.read_parquet(scores_path)
    refs_path = latest('refs')
    refs = pl.read_parquet(refs_path) if refs_path else None

    j = build_join(trades, scores, refs, scenario)

    buf = StringIO()
    print(
        f'# Lens report — scenario={scenario}\n'
        f'generated {datetime.now():%Y-%m-%d %H:%M:%S}  '
        f'trades={trades_path.name}  '
        f'scores={scores_path.name}\n',
        file=buf,
    )
    for sec_fn, args in [
        (section_population, (j,)),
        (section_discount_bucket, (j,)),
        (section_decile, (j,)),
        (section_reg, (j,)),
        (section_bank, (trades, scores, refs, scenario)),
        (section_sector, (j,)),
        (section_hedge_sweep, (scores, scenario)),
    ]:
        print('\n' + sec_fn(*args) + '\n', file=buf)
    return buf.getvalue()


def main() -> None:
    args = sys.argv[1:]
    scenario = 'combined'
    save = False
    while args:
        flag = args.pop(0)
        if flag == '--scenario':
            scenario = args.pop(0)
        elif flag == '--save':
            save = True
        else:
            print(f'unknown arg: {flag}', file=sys.stderr)
            sys.exit(1)

    report = run_report(scenario)
    print(report)
    if save:
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = Path('data') / f'lens_report.{stamp}.md'
        out.write_text(report)
        print(f'wrote {out}', file=sys.stderr)


if __name__ == '__main__':
    main()
