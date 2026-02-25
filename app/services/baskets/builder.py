from __future__ import annotations

import polars as pl

from app.services.prices import HIST_TEMPLATE_DEFAULT, HIST_TEMPLATES
from app.models.baskets import Basket, BasketParams
from app.services.baskets.barra import (
    BarraExposure,
    BarraModel,
    build_sector_constraints,
    get_factor_returns,
    get_prior,
)
from app.services.baskets.config import ModelChoice
from app.services.baskets.factors import EmpModel
from app.services.baskets.opt import DEFAULT_PARAMS, run_opts
from app.services.baskets.risk import calc_stats
from app.services.baskets.scenarios import (
    MIN_HIST,
    get_returns,
    get_scenarios,
)
from app.utils.market import slice_hist


def build_baskets(
    symbol: str,
    hist: pl.DataFrame,
    refs: pl.DataFrame,
    hists: pl.DataFrame,
    params: dict[str, BasketParams] | None = None,
    emp_model: EmpModel | None = None,
    barra_model: BarraModel | None = None,
    model_choice: ModelChoice = 'emp',
) -> dict[str, Basket] | None:
    """Build basket optimizations for a symbol."""
    if model_choice == 'barra':
        scenarios = get_scenarios(
            symbol,
            hist,
            refs,
            hists,
            barra_model=barra_model,
        )
    else:
        scenarios = get_scenarios(
            symbol,
            hist,
            refs,
            hists,
            emp_model=emp_model,
        )

    if not scenarios:
        return None

    # Barra: compute prior + sector constraints
    if model_choice == 'barra' and barra_model:
        prior = get_prior()

        all_dates: list[str] = []
        for returns in scenarios.values():
            all_dates.extend(returns.get_column('date').to_list())
        fr = get_factor_returns(barra_model, sorted(set(all_dates)))

        target_sector = barra_model.exposures.get(
            symbol,
            BarraExposure(0, 0, 0, 0, 0, 0, ''),
        ).sector
        p = (params or {}).get(next(iter(scenarios)), DEFAULT_PARAMS)

        sc_groups: dict[str, dict[str, list[str]] | None] = {}
        sc_lin: dict[str, list[str] | None] = {}
        for name, returns in scenarios.items():
            columns = [
                c
                for c in returns.columns
                if c not in ('date', 'target')
            ]
            sc = build_sector_constraints(
                barra_model,
                columns,
                target_sector,
                p.max_budget,
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
            params,
            prior_estimator=prior,
            factor_returns=fr,
            groups=sc_groups,
            linear_constraints=sc_lin,
        )
    else:
        opts = run_opts(symbol, scenarios, params)

    baskets: dict[str, Basket] = {}
    for name, opt in opts.items():
        if opt['weights'].is_empty():
            continue
        raw = {
            'params': opt['params'],
            **calc_stats(
                symbol,
                opt['weights'],
                scenarios[name],
            ),
        }
        baskets[name] = Basket.model_validate(raw)
    return baskets if baskets else None


def rebuild_from_weights(
    symbol: str,
    hist: pl.DataFrame,
    hists: pl.DataFrame,
    weights_by_scenario: dict[str, dict[str, float]],
) -> dict[str, Basket] | None:
    """Rebuild basket stats from cached weights.

    Skips SCIP — constructs minimal returns from
    the known hedge symbols and runs calc_stats.
    """
    _, _, unit, scale, _ = HIST_TEMPLATES[HIST_TEMPLATE_DEFAULT]

    symbol_hist = (
        slice_hist(hist, unit, scale, for_analytics=True)
        .select(['date', 'close'])
        .rename({'close': 'target'})
    )
    if len(symbol_hist) < MIN_HIST:
        return None
    target_returns = get_returns(symbol_hist)

    baskets: dict[str, Basket] = {}
    for scenario, wts in weights_by_scenario.items():
        hedge_syms = list(wts.keys())

        hedge_hists = hists.filter(
            (pl.col('symbol').is_in(hedge_syms))
            & (pl.col('template') == HIST_TEMPLATE_DEFAULT)
        )
        hedge_hists = slice_hist(
            hedge_hists,
            unit,
            scale,
            for_analytics=True,
        )
        if hedge_hists.is_empty():
            continue

        wide = hedge_hists.pivot(
            on='symbol',
            index='date',
            values='close',
            aggregate_function='last',
        )
        hedge_returns = get_returns(wide)

        combined = pl.concat(
            [hedge_returns, target_returns],
            how='align_left',
        ).drop_nulls()

        if len(combined) < MIN_HIST:
            continue
        combined = combined.tail(MIN_HIST)

        weights_df = pl.DataFrame(
            {
                symbol: hedge_syms,
                'weight': [wts[s] for s in hedge_syms],
            }
        )
        raw = {
            'params': BasketParams(),
            **calc_stats(symbol, weights_df, combined),
        }
        baskets[scenario] = Basket.model_validate(raw)

    return baskets if baskets else None
