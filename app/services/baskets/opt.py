"""Basket optimization — two-stage MIP solver.

## Overview

Each scenario (indices, factors, singles, combined) is optimized
independently via `run_opts`, which calls `run_opt` per scenario.

## Two-stage strategy

Large candidate pools (> STAGE2_MIN_COLS) use a two-stage approach:

  Stage 1 — CLARABEL continuous relaxation (EmpiricalPrior, fast):
    Solves the unconstrained problem to get approximate weights.
    Takes the top STAGE1_TOPN candidates by absolute weight.
    Filters sector groups/constraints to surviving columns.

  Stage 2 — SCIP mixed-integer program (Barra FactorModel prior):
    Enforces cardinality (max N non-zero weights), threshold_long
    (min weight for inclusion), and sector floor/cap constraints.
    Time-limited to SOLVER_TIME_LIMIT seconds.

Small pools (≤ STAGE2_MIN_COLS) skip stage 1 and go direct to SCIP.

## Inputs (run_opt)

  X               — wide returns DataFrame: date dropped, columns are
                    hedge candidates + 'target' (the stock being hedged)
  prior_estimator — skfolio BasePrior; FactorModel (Barra B'FB+D) for
                    indices/factors/singles/combined; falls back to
                    EmpiricalPrior if None
  factor_returns  — factor return series for FactorModel prior.
                    MUST be row-aligned to X (same dates, same order).
                    run_opts accepts a dict[scenario, DataFrame] and
                    slices per-scenario to guarantee alignment — passing
                    a single shared DataFrame across scenarios causes a
                    sklearn shape mismatch when scenarios have different
                    date ranges (e.g. short-history targets).
  groups          — skfolio sector group map: symbol → group label,
                    used to enforce sector floor/cap constraints
  linear_constraints — skfolio constraint strings, e.g.
                    'sector_11 >= 0.12', 'sector_7 <= 0.10'

## Output (run_opt)

  weights DataFrame: columns [<target_symbol>, 'weight'],
  rows are hedge instruments with weight > MIN_WEIGHT, sorted
  descending. The target column name is the symbol string (e.g.
  'aapl') — this is the skfolio transpose convention.

## Scenario constraints summary

  indices   — no sector constraints (only 3 ETFs, no sector labels)
  factors   — no sector constraints (ETFs have no sector membership)
  singles   — sector floor on target sector, caps on off-sectors
  combined  — no sector constraints (ETF anchors exposure; singles
               complement freely)
"""

import warnings
from time import perf_counter

import polars as pl
from skfolio.measures import RiskMeasure
from skfolio.optimization import MeanRisk, ObjectiveFunction
from skfolio.prior import BasePrior, EmpiricalPrior

from app.models.baskets import BasketParams
from app.services.baskets.config import (
    MIN_WEIGHT,
    SOLVER_TIME_LIMIT,
    STAGE1_TOPN,
    STAGE2_MIN_COLS,
)
from app.utils.logger import get_logger

# Suppress skfolio covariance warnings - clipping to nearest PD is expected
warnings.filterwarnings(
    'ignore',
    message='The covariance matrix is not positive definite',
    module='skfolio',
)

log = get_logger(__name__)

DEFAULT_PARAMS = BasketParams()


def run_opt(
    target: str,
    scenario: str,
    X,
    params: BasketParams = DEFAULT_PARAMS,
    prior_estimator: BasePrior | None = None,
    factor_returns=None,
    groups: dict[str, list[str]] | None = None,
    linear_constraints: list[str] | None = None,
):
    """Optimize one scenario. See module docstring for full details."""
    start = perf_counter()
    cols = X.shape[1]

    if cols > STAGE2_MIN_COLS:
        # Stage 1: always EmpiricalPrior (fast pre-screen)
        stage1 = MeanRisk(
            solver='CLARABEL',
            prior_estimator=EmpiricalPrior(),
            objective_function=ObjectiveFunction.MINIMIZE_RISK,
            risk_measure=RiskMeasure.VARIANCE,
            max_weights={'target': -1},
            min_weights=-1,
            max_short=1.0,
            budget=None,
            max_budget=params.max_budget,
            l1_coef=params.l1_coef,
        )
        stage1.fit(X)
        top = sorted(
            [
                (c, w)
                for c, w in zip(X.columns, stage1.weights_)
                if c != 'target' and abs(w) > MIN_WEIGHT
            ],
            key=lambda x: abs(x[1]),
            reverse=True,
        )[:STAGE1_TOPN]
        X = X[['target'] + [c for c, _ in top]]
        # Re-filter groups/constraints to surviving cols
        if groups is not None:
            kept = set(X.columns)
            groups = {s: g for s, g in groups.items() if s in kept}
            if not groups:
                groups = None
                linear_constraints = None
            elif linear_constraints is not None:
                active = {g for gs in groups.values() for g in gs}
                linear_constraints = [
                    c
                    for c in linear_constraints
                    if c.split()[0] in active
                ]
                if not linear_constraints:
                    linear_constraints = None

    # Stage 2 (or direct): MIP solve
    prior = prior_estimator or EmpiricalPrior()
    stage2_kw: dict = {}
    if groups is not None:
        stage2_kw['groups'] = groups
    if linear_constraints is not None:
        stage2_kw['linear_constraints'] = linear_constraints
    model = MeanRisk(
        solver='SCIP',
        solver_params={'limits/time': SOLVER_TIME_LIMIT},
        prior_estimator=prior,
        objective_function=ObjectiveFunction.MINIMIZE_RISK,
        risk_measure=RiskMeasure.VARIANCE,
        max_weights={'target': -1},
        min_weights=-1,
        max_short=1.0,
        threshold_short=-1.0,
        budget=None,
        max_budget=params.max_budget,
        threshold_long=params.threshold_long,
        l1_coef=params.l1_coef,
        cardinality=params.cardinality,
        **stage2_kw,
    )
    if factor_returns is not None:
        model.fit(X, factor_returns)
    else:
        model.fit(X)
    elapsed = perf_counter() - start

    weights = (
        pl.DataFrame(
            data=[model.weights_],
            schema=list(X.columns),
            orient='row',
        )
        .transpose(
            include_header=True,
            header_name=target,
            column_names=['weight'],
        )
        .filter(pl.col('weight') > MIN_WEIGHT)
        .sort(by='weight', descending=True)
    )

    # Log optimization summary
    rows = X.shape[0]
    stage = '2-stage' if cols > STAGE2_MIN_COLS else 'direct'
    log.cyan(
        f'opt {scenario}: X={rows}x{cols} ({stage}) '
        f'budget={params.max_budget} card={params.cardinality} '
        f'thresh={params.threshold_long} -> {elapsed:.2f}s'
    )
    if not weights.is_empty():
        log.cyan(f'{weights}')

    return weights


def run_opts(
    symbol: str,
    scenarios: dict[str, pl.DataFrame],
    params: dict[str, BasketParams] | None = None,
    prior_estimator: BasePrior | None = None,
    factor_returns=None,
    groups: dict[str, dict[str, list[str]] | None] | None = None,
    linear_constraints: dict[str, list[str] | None] | None = None,
):
    """Run optimization for each scenario.

    Args:
        symbol: Target symbol
        scenarios: Dict of scenario name -> returns DataFrame
        params: Optional dict of scenario name -> BasketParams overrides
        prior_estimator: Optional prior (e.g. skfolio FactorModel)
        factor_returns: Factor return series for prior
        groups: Optional dict of scenario -> groups
        linear_constraints: Optional dict of scenario -> constraints
    """
    params = params or {}
    groups = groups or {}
    linear_constraints = linear_constraints or {}
    results = {}

    for name, returns in scenarios.items():
        scenario_params = params.get(name, DEFAULT_PARAMS)
        sc_fr = (
            factor_returns.get(name)
            if isinstance(factor_returns, dict)
            else factor_returns
        )
        weights = run_opt(
            symbol,
            name,
            returns.drop('date'),
            scenario_params,
            prior_estimator=prior_estimator,
            factor_returns=sc_fr,
            groups=groups.get(name),
            linear_constraints=linear_constraints.get(name),
        )
        results[name] = {
            'params': scenario_params,
            'population': returns.width - 1,
            'days': returns.height,
            'date_start': returns.select(pl.col('date').min()).item(),
            'date_end': returns.select(pl.col('date').max()).item(),
            'weights': weights,
        }

    return results
