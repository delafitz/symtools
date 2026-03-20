"""Basket construction pipeline.

## Full opt flow

  1. get_scenarios() — builds return matrices for each static scenario
     (indices, factors, singles) using Barra candidate pre-screening
     and the cached ETF hists. Combined is registered in SCENARIOS but
     has no static groups, so get_scenarios() skips it.

  2. Barra prior + factor returns — get_prior() returns a skfolio
     FactorModel (B'FB + D covariance). get_factor_returns() slices
     the Barra model's pre-computed factor returns to the union of
     dates across all scenario return matrices.

  3. Sector constraints — build_sector_constraints() produces skfolio
     groups + linear_constraints for each scenario that has stock
     symbols with known Barra sector memberships:
       - floor: target sector >= SECTOR_FLOOR_PCT × max_budget
       - cap:   each off-sector  <= SECTOR_CAP_PCT  × max_budget
     indices/factors have no sector labels so get no constraints.
     combined is intentionally unconstrained (see step 5).

  4. run_opts() — runs the two-stage SCIP optimizer (see opt.py) for
     indices, factors, and singles simultaneously.

  5. _run_combined() — two-pass combined scenario:
       a. _pick_top_etf(): reads the factors weights from step 4,
          filters to ETF symbols, returns the highest-weight one.
          This is the model-selected liquid hedge anchor.
       b. _build_combined(): slices that ETF's return column from
          scenarios['factors'] and concatenates with scenarios['singles']
          to form the combined candidate pool.
       c. Runs a separate Barra opt on combined with no sector
          constraints — the ETF already anchors market/sector exposure;
          singles complement freely.
       d. Updates scenarios[COMBINED] and opts in-place.

  6. Basket assembly — for each opt result with non-empty weights,
     calc_stats() computes beta, corr, vol_reduce against the scenario
     returns. Basket.model_validate() wraps the result.

## Startup cache path

  rebuild_from_weights() bypasses steps 1–5. It reconstructs basket
  stats from persisted weights (loaded from baskets.parquet) without
  re-running SCIP, by joining hedge symbol hists and calling calc_stats
  directly. Works for all scenarios including combined.
"""

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
from app.services.baskets.opt import DEFAULT_PARAMS, run_opts
from app.services.baskets.report import build_report
from app.services.baskets.risk import calc_stats
from app.services.baskets.scenarios import (
    MIN_HIST,
    get_returns,
    get_scenarios,
)
from app.utils.groups import COMBINED, get_all_etf_symbols
from app.utils.logger import get_logger
from app.utils.market import slice_hist

log = get_logger(__name__)


def _pick_top_etf(
    factors_weights: pl.DataFrame,
) -> str | None:
    """Top-weight ETF from the factors optimization result.

    Uses the factors opt output directly — it already ran with
    the Barra prior, so the highest-weight ETF is the
    model-selected hedge candidate for combined.
    """
    if factors_weights.is_empty():
        return None
    sym_col = factors_weights.columns[0]
    etf_syms = set(get_all_etf_symbols())
    etf_rows = factors_weights.filter(
        pl.col(sym_col).is_in(etf_syms)
    )
    if etf_rows.is_empty():
        return None
    return etf_rows.sort('weight', descending=True).get_column(
        sym_col
    )[0]


def _build_combined(
    top_etf: str,
    scenarios: dict[str, pl.DataFrame],
) -> pl.DataFrame | None:
    """Build combined returns: top_etf column + singles.

    ETF column is taken from scenarios['factors'] (already
    computed). Singles provide single-stock candidates.
    The combined opt runs unconstrained by sector — the ETF
    anchors market/sector exposure; singles complement freely.
    """
    if 'factors' not in scenarios:
        return None
    if top_etf not in scenarios['factors'].columns:
        return None
    if 'singles' not in scenarios:
        return None

    etf_col = scenarios['factors'].select(['date', top_etf])
    comb = pl.concat(
        [etf_col, scenarios['singles']],
        how='align_left',
    ).drop_nulls()

    if len(comb) > MIN_HIST:
        comb = comb.tail(MIN_HIST)
    return comb if len(comb) >= MIN_HIST else None


def _run_combined(
    symbol: str,
    scenarios: dict[str, pl.DataFrame],
    opts: dict,
    params: dict[str, BasketParams] | None,
    barra_model: BarraModel,
    prior,
) -> None:
    """Build and run combined scenario, updating scenarios +
    opts in-place.

    Picks the top-weight ETF from the factors optimization,
    builds combined returns (ETF + singles), and runs a
    separate Barra opt with no sector constraints.
    """
    top_etf = _pick_top_etf(
        opts.get('factors', {}).get('weights', pl.DataFrame())
    )
    if not top_etf:
        return

    comb_data = _build_combined(top_etf, scenarios)
    if comb_data is None:
        return

    scenarios[COMBINED] = comb_data
    comb_fr = get_factor_returns(
        barra_model,
        sorted(set(comb_data['date'].to_list())),
    )
    comb_opts = run_opts(
        symbol,
        {COMBINED: comb_data},
        params,
        prior_estimator=prior,
        factor_returns=comb_fr,
    )
    opts.update(comb_opts)
    log.info(
        f'combined: etf={top_etf} '
        f'cols={comb_data.width} '
        f'rows={comb_data.height}'
    )


def build_baskets(
    symbol: str,
    hist: pl.DataFrame,
    refs: pl.DataFrame,
    hists: pl.DataFrame,
    params: dict[str, BasketParams] | None = None,
    barra_model: BarraModel | None = None,
) -> tuple[dict[str, Basket], str] | None:
    """Build basket optimizations for a symbol."""
    scenarios, rankings = get_scenarios(
        symbol,
        hist,
        refs,
        hists,
        barra_model=barra_model,
    )

    if not scenarios:
        return None

    prior = get_prior()

    all_dates: list[str] = []
    for returns in scenarios.values():
        all_dates.extend(returns.get_column('date').to_list())
    fr = get_factor_returns(barra_model, sorted(set(all_dates)))

    target_sector = barra_model.exposures.get(
        symbol,
        BarraExposure(0, 0, 0, 0, 0, 0, 0),
    ).sector if barra_model else 0
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
        ) if barra_model else None
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

    # Combined: top ETF from factors result + singles,
    # no sector constraints (ETF anchors market exposure;
    # singles complement freely).
    if barra_model:
        _run_combined(
            symbol, scenarios, opts, params, barra_model, prior
        )

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

    report = build_report(
        symbol, barra_model, scenarios, rankings, opts, baskets, sc_lin
    )
    return (baskets, report) if baskets else None


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
