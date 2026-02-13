from __future__ import annotations

import polars as pl

from app.services.prices import HIST_TEMPLATE_DEFAULT, HIST_TEMPLATES
from app.models.baskets import Basket, BasketParams
from app.services.baskets.factors import FactorModel
from app.services.baskets.opt import run_opts
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
    factor_model: FactorModel | None = None,
) -> dict[str, Basket] | None:
    """Build basket optimizations for a symbol."""
    scenarios = get_scenarios(symbol, hist, refs, hists, factor_model)
    if scenarios:
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
    return None


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
