"""Barra factor model basket optimization diagnostics.

Loads refs + hists from parquet, builds the Barra model,
then runs basket optimization for each symbol and prints
weights and stats for all scenarios.

Usage:
    uv run python tools/barra.py AAPL
    uv run python tools/barra.py AAPL MSFT NVDA
    uv run python tools/barra.py --top 5
"""

import sys
from pathlib import Path
from time import perf_counter

# Add project root to path so `app` package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from app.services.baskets.barra import (
    BarraExposure,
    BarraModel,
    build_barra_model,
    build_sector_constraints,
    get_factor_returns,
    get_prior,
)
from app.services.baskets.builder import (
    _build_combined,
    _pick_top_etf,
)
from app.services.baskets.opt import DEFAULT_PARAMS, run_opts
from app.services.baskets.risk import calc_stats
from app.services.baskets.scenarios import get_scenarios
from app.utils.groups import COMBINED
from app.utils.logger import get_logger
from app.utils.sic_gics import sic_to_sector

log = get_logger(__name__)


def load_data() -> tuple[pl.DataFrame, pl.DataFrame] | None:
    """Load refs + hists from the most recent parquet."""

    def latest(name: str) -> pl.DataFrame | None:
        files = sorted(Path('data').glob(f'{name}.*.parquet'))
        if not files:
            return None
        print(f'loading {files[-1].name}')
        return pl.read_parquet(files[-1])

    refs = latest('refs')
    hists = latest('hists')
    if refs is None:
        print('no refs parquet found')
        return None
    if hists is None:
        print('no hists parquet found')
        return None
    return refs, hists


def ensure_g_sector(
    refs: pl.DataFrame,
) -> pl.DataFrame:
    """Add g_sector column if missing."""
    if 'g_sector' in refs.columns:
        return refs
    if 'sic' not in refs.columns:
        return refs.with_columns(pl.lit('').alias('g_sector'))
    return refs.with_columns(
        pl.col('sic')
        .map_elements(sic_to_sector, pl.String)
        .alias('g_sector')
    )


def log_sector_dist(refs: pl.DataFrame) -> None:
    """Log sector distribution from refs."""
    if 'g_sector' not in refs.columns:
        return
    dist = (
        refs.filter(pl.col('g_sector') != '')
        .group_by('g_sector')
        .len()
        .sort('len', descending=True)
    )
    log.cyan(f'g_sector distribution:\n{dist}')
    unmapped = refs.filter(pl.col('g_sector') == '').height
    log.info(f'unmapped SIC descriptions: {unmapped}')


def run_barra(
    symbol: str,
    refs: pl.DataFrame,
    hists: pl.DataFrame,
    barra: BarraModel,
) -> None:
    """Run Barra basket optimization for one symbol."""
    print(f'\n{"=" * 50}')
    print(f'  {symbol.upper()}')
    print(f'{"=" * 50}')

    exp = barra.exposures.get(symbol)
    if exp:
        print(
            f'Exposures: '
            f'size={exp.size:.2f} '
            f'mom={exp.momentum:.2f} '
            f'rev={exp.reversal:.2f} '
            f'beta={exp.beta:.2f} '
            f'resvol={exp.resvol:.2f} '
            f'liq={exp.liquidity:.2f}'
        )
        print(f'Sector: {exp.sector}')
    else:
        print('(no Barra exposures — not in universe)')

    sym_hist = hists.filter(
        (pl.col('symbol') == symbol) & (pl.col('template') == 'Y')
    ).drop('symbol', 'template')

    if sym_hist.is_empty():
        print(f'  no Y hist for {symbol}')
        return

    scenarios = get_scenarios(
        symbol,
        sym_hist,
        refs,
        hists,
        barra_model=barra,
    )

    if not scenarios:
        print(f'  no scenarios for {symbol}')
        return

    prior = get_prior()

    all_dates: list[str] = []
    for returns in scenarios.values():
        all_dates.extend(returns.get_column('date').to_list())
    fr = get_factor_returns(barra, sorted(set(all_dates)))

    target_sector = barra.exposures.get(
        symbol, BarraExposure(0, 0, 0, 0, 0, 0, 0)
    ).sector
    sc_groups: dict[str, dict[str, list[str]] | None] = {}
    sc_lin: dict[str, list[str] | None] = {}
    for name, returns in scenarios.items():
        columns = [
            c for c in returns.columns if c not in ('date', 'target')
        ]
        sc = build_sector_constraints(
            barra,
            columns,
            target_sector,
            DEFAULT_PARAMS.max_budget,
        )
        if sc:
            sc_groups[name] = sc[0]
            sc_lin[name] = sc[1]
            log.cyan(f'sector constraints [{name}]: {sc[1]}')
        else:
            sc_groups[name] = None
            sc_lin[name] = None
            log.info(
                f'sector constraints [{name}]: none (no sectors)'
            )

    t0 = perf_counter()
    opts = run_opts(
        symbol,
        scenarios,
        prior_estimator=prior,
        factor_returns=fr,
        groups=sc_groups,
        linear_constraints=sc_lin,
    )
    elapsed = perf_counter() - t0

    # Combined: top ETF from factors + singles, no sector constraints
    top_etf = _pick_top_etf(
        opts.get('factors', {}).get('weights', pl.DataFrame())
    )
    if top_etf:
        comb_data = _build_combined(top_etf, scenarios)
        if comb_data is not None:
            scenarios[COMBINED] = comb_data
            comb_fr = get_factor_returns(
                barra,
                sorted(set(comb_data['date'].to_list())),
            )
            comb_opts = run_opts(
                symbol,
                {COMBINED: comb_data},
                prior_estimator=prior,
                factor_returns=comb_fr,
            )
            opts.update(comb_opts)
            print(f'combined: etf={top_etf}')

    print(f'Timing: {elapsed:.2f}s')

    for name in sorted(opts.keys()):
        opt = opts[name]
        sc = scenarios.get(name)
        print(f'\n  {name}:')

        if opt['weights'].is_empty():
            print('    (no weights)')
            continue

        wts = dict(opt['weights'].rows())
        for sym, w in sorted(
            wts.items(), key=lambda x: x[1], reverse=True
        ):
            print(f'  {sym:>20s}  {w:>8.4f}')

        if sc is not None:
            stats = calc_stats(symbol, opt['weights'], sc)['stats']
            print(f'  {"corr":>20s}  {stats["corr"]:>8.4f}')
            print(f'  {"beta":>20s}  {stats["beta"]:>8.4f}')
            print(
                f'  {"vol_reduce":>20s}  '
                f'{stats["vol_reduce"]:>7.1%}'
            )


def main() -> None:
    args = sys.argv[1:]

    top_n: int | None = None
    if '--top' in args:
        idx = args.index('--top')
        top_n = int(args[idx + 1])
        args = args[:idx] + args[idx + 2 :]

    result = load_data()
    if result is None:
        sys.exit(1)
    refs, hists = result

    refs = ensure_g_sector(refs)
    log_sector_dist(refs)

    if args:
        symbols = [s.lower() for s in args]
    elif top_n:
        symbols = (
            refs.filter(pl.col('mkt_cap') > 0)
            .sort('mkt_cap', descending=True)
            .head(top_n)
            .get_column('symbol')
            .to_list()
        )
    else:
        print('usage: barra.py SYMBOL [SYMBOL...] | --top N')
        sys.exit(1)

    print('\n=== Building Barra Model ===')
    t0 = perf_counter()
    barra = build_barra_model(refs, hists)
    print(f'barra model: {perf_counter() - t0:.2f}s')

    if barra is None:
        print('failed to build Barra model')
        sys.exit(1)

    print(
        f'\nstocks: {barra.n_stocks}  '
        f'factors: {barra.n_factors}  '
        f'dates: {len(barra.factor_returns)}'
    )
    print(f'sectors: {", ".join(barra.sector_names.values())}')

    for sym in symbols:
        run_barra(sym, refs, hists, barra)


if __name__ == '__main__':
    main()
