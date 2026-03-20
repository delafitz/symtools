"""Basket optimization report for one or more symbols.

Loads refs + hists from parquet, builds the model, runs
optimization, and prints the report.

Usage:
    uv run python tools/opt.py AAPL
    uv run python tools/opt.py AAPL MSFT NVDA
    uv run python tools/opt.py --top 5
"""

import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from app.models.baskets import Basket
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
from app.services.baskets.report import build_report
from app.services.baskets.risk import calc_stats
from app.services.baskets.scenarios import get_scenarios
from app.utils.groups import COMBINED


def load_data() -> tuple[pl.DataFrame, pl.DataFrame] | None:
    def latest(name: str) -> pl.DataFrame | None:
        files = sorted(Path('data').glob(f'{name}.*.parquet'))
        if not files:
            return None
        return pl.read_parquet(files[-1])

    refs = latest('refs')
    hists = latest('hists')
    if refs is None or hists is None:
        print('no parquet data found in data/', file=sys.stderr)
        return None
    return refs, hists


def run_report(
    symbol: str,
    refs: pl.DataFrame,
    hists: pl.DataFrame,
    barra: BarraModel,
) -> None:
    sym_hist = hists.filter(
        (pl.col('symbol') == symbol)
        & (pl.col('template') == 'Y')
    ).drop('symbol', 'template')

    if sym_hist.is_empty():
        print(f'{symbol}: no Y hist', file=sys.stderr)
        return

    scenarios, rankings = get_scenarios(
        symbol, sym_hist, refs, hists, barra_model=barra
    )
    if not scenarios:
        print(f'{symbol}: no scenarios', file=sys.stderr)
        return

    prior = get_prior()

    all_dates: list[str] = []
    for returns in scenarios.values():
        all_dates.extend(returns.get_column('date').to_list())
    fr = get_factor_returns(barra, sorted(set(all_dates)))

    target_sector = barra.exposures.get(
        symbol, BarraExposure(0, 0, 0, 0, 0, 0, 0)
    ).sector
    sc_groups: dict = {}
    sc_lin: dict = {}
    for name, returns in scenarios.items():
        columns = [
            c for c in returns.columns
            if c not in ('date', 'target')
        ]
        sc = build_sector_constraints(
            barra, columns, target_sector, DEFAULT_PARAMS.max_budget
        )
        if sc:
            sc_groups[name] = sc[0]
            sc_lin[name] = sc[1]
        else:
            sc_groups[name] = None
            sc_lin[name] = None

    opts = run_opts(
        symbol,
        scenarios,
        prior_estimator=prior,
        factor_returns=fr,
        groups=sc_groups,
        linear_constraints=sc_lin,
    )

    top_etf = _pick_top_etf(
        opts.get('factors', {}).get('weights', pl.DataFrame())
    )
    if top_etf:
        comb_data = _build_combined(top_etf, scenarios)
        if comb_data is not None:
            scenarios[COMBINED] = comb_data
            comb_fr = get_factor_returns(
                barra, sorted(set(comb_data['date'].to_list()))
            )
            comb_opts = run_opts(
                symbol,
                {COMBINED: comb_data},
                prior_estimator=prior,
                factor_returns=comb_fr,
            )
            opts.update(comb_opts)

    baskets: dict[str, Basket] = {}
    for name, opt in opts.items():
        sc = scenarios.get(name)
        if opt['weights'].is_empty() or sc is None:
            continue
        raw = {
            'params': opt['params'],
            **calc_stats(symbol, opt['weights'], sc),
        }
        baskets[name] = Basket.model_validate(raw)

    report = build_report(
        symbol, barra, scenarios, rankings, opts, baskets, sc_lin
    )
    print(report)


def main() -> None:
    args = sys.argv[1:]

    top_n: int | None = None
    if '--top' in args:
        idx = args.index('--top')
        top_n = int(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    result = load_data()
    if result is None:
        sys.exit(1)
    refs, hists = result

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
        print('usage: opt.py SYMBOL [SYMBOL...] | --top N')
        sys.exit(1)

    barra = build_barra_model(refs, hists)
    if barra is None:
        print('failed to build Barra model', file=sys.stderr)
        sys.exit(1)

    for sym in symbols:
        run_report(sym, refs, hists, barra)


if __name__ == '__main__':
    main()
