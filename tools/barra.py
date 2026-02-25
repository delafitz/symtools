"""Compare empirical vs Barra factor model optimization.

Loads refs + hists from parquet, builds the Barra model,
then runs both EmpiricalPrior and Barra FactorModel optimizations
side-by-side for each symbol.

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
from app.services.baskets.factors import (
    build_emp_model,
)
from app.services.baskets.opt import run_opts
from app.services.baskets.risk import calc_stats
from app.services.baskets.scenarios import (
    get_scenarios,
)
from app.utils.logger import get_logger
from app.utils.sic_gics import sic_to_sector
from app.utils.store import get_store

log = get_logger(__name__)


def load_data() -> tuple[pl.DataFrame, pl.DataFrame] | None:
    """Load refs + hists from today's parquet."""
    refs = get_store('refs')
    hists = get_store('hists')
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


def run_comparison(
    symbol: str,
    refs: pl.DataFrame,
    hists: pl.DataFrame,
    barra: BarraModel,
    emp_model,
) -> None:
    """Run empirical vs Barra for one symbol."""
    print(f'\n{"=" * 50}')
    print(f'  {symbol.upper()}')
    print(f'{"=" * 50}')

    # Log Barra exposures
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

    # Get symbol hist
    sym_hist = hists.filter(
        (pl.col('symbol') == symbol) & (pl.col('template') == 'Y')
    ).drop('symbol', 'template')

    if sym_hist.is_empty():
        print(f'  no Y hist for {symbol}')
        return

    # Build scenarios independently for each model
    emp_scenarios = get_scenarios(
        symbol,
        sym_hist,
        refs,
        hists,
        emp_model=emp_model,
    )
    barra_scenarios = get_scenarios(
        symbol,
        sym_hist,
        refs,
        hists,
        barra_model=barra,
    )

    if not emp_scenarios and not barra_scenarios:
        print(f'  no scenarios for {symbol}')
        return

    # Run empirical on factor-screened scenarios
    t0 = perf_counter()
    emp_opts = (
        run_opts(symbol, emp_scenarios) if emp_scenarios else {}
    )
    emp_time = perf_counter() - t0

    # Get factor returns aligned to barra scenario dates
    from app.services.baskets.opt import DEFAULT_PARAMS

    all_dates: list[str] = []
    for returns in barra_scenarios.values():
        all_dates.extend(returns.get_column('date').to_list())
    unique_dates = sorted(set(all_dates))
    fr = get_factor_returns(barra, unique_dates)

    # Build sector constraints per barra scenario
    target_sector = barra.exposures.get(
        symbol, BarraExposure(0, 0, 0, 0, 0, 0, '')
    ).sector
    sc_groups: dict[str, dict[str, list[str]] | None] = {}
    sc_lin: dict[str, list[str] | None] = {}
    for name, returns in barra_scenarios.items():
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

    # Run Barra on barra-screened scenarios
    t0 = perf_counter()
    barra_prior = get_prior()
    barra_opts = (
        run_opts(
            symbol,
            barra_scenarios,
            prior_estimator=barra_prior,
            factor_returns=fr,
            groups=sc_groups,
            linear_constraints=sc_lin,
        )
        if barra_scenarios
        else {}
    )
    barra_time = perf_counter() - t0

    print(
        f'\nTiming: empirical={emp_time:.2f}s barra={barra_time:.2f}s'
    )

    # Compare results
    all_scenarios = sorted(
        set(emp_opts.keys()) | set(barra_opts.keys())
    )
    for name in all_scenarios:
        emp = emp_opts.get(name)
        bar = barra_opts.get(name)
        print(f'\n  {name}:')

        header = f'{"":>20s} {"Empirical":>12s} {"Barra":>12s}'
        print(header)
        print(f'  {"-" * 46}')

        # Weights
        emp_w = (
            dict(emp['weights'].rows())
            if emp and not emp['weights'].is_empty()
            else {}
        )
        bar_w = (
            dict(bar['weights'].rows())
            if bar and not bar['weights'].is_empty()
            else {}
        )
        all_syms = sorted(set(emp_w.keys()) | set(bar_w.keys()))
        for s in all_syms:
            ew = emp_w.get(s, 0)
            bw = bar_w.get(s, 0)
            print(f'  {s:>20s} {ew:>12.4f} {bw:>12.4f}')

        # Stats comparison (each vs its own scenarios)
        emp_sc = emp_scenarios.get(name)
        bar_sc = barra_scenarios.get(name)
        if (
            emp
            and not emp['weights'].is_empty()
            and emp_sc is not None
            and bar
            and not bar['weights'].is_empty()
            and bar_sc is not None
        ):
            emp_stats = calc_stats(
                symbol,
                emp['weights'],
                emp_sc,
            )
            bar_stats = calc_stats(
                symbol,
                bar['weights'],
                bar_sc,
            )

            ev = emp_stats['vols']
            bv = bar_stats['vols']
            print(
                f'  {"vol_reduction":>20s} '
                f'{ev["reduction"]:>11.1%} '
                f'{bv["reduction"]:>11.1%}'
            )

            ec = emp_stats['corrs']
            bc = bar_stats['corrs']
            for window in ec:
                ecv = ec[window]['value']
                bcv = bc[window]['value']
                print(
                    f'  {window + "_corr":>20s} '
                    f'{ecv:>12.4f} '
                    f'{bcv:>12.4f}'
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

    # Ensure g_sector exists
    refs = ensure_g_sector(refs)
    log_sector_dist(refs)

    # Determine symbols
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

    # Build models
    print('\n=== Building Emp Model ===')
    t0 = perf_counter()
    emp_model = build_emp_model(refs, hists)
    print(f'emp model: {perf_counter() - t0:.2f}s')

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
    print(f'sectors: {", ".join(barra.sectors)}')

    # Run comparisons
    for sym in symbols:
        run_comparison(sym, refs, hists, barra, emp_model)


if __name__ == '__main__':
    main()
