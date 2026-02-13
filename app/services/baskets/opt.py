import warnings
from time import perf_counter

import polars as pl
from skfolio.measures import RiskMeasure
from skfolio.optimization import MeanRisk, ObjectiveFunction
from skfolio.prior import EmpiricalPrior

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
):
    start = perf_counter()
    cols = X.shape[1]

    if cols > STAGE2_MIN_COLS:
        # Stage 1: continuous relaxation (no cardinality/threshold)
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

    # Stage 2 (or direct): MIP solve
    model = MeanRisk(
        solver='SCIP',
        solver_params={'limits/time': SOLVER_TIME_LIMIT},
        prior_estimator=EmpiricalPrior(),
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
    )
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
):
    """Run optimization for each scenario.

    Args:
        symbol: Target symbol
        scenarios: Dict of scenario name -> returns DataFrame
        params: Optional dict of scenario name -> BasketParams overrides
    """
    params = params or {}
    results = {}

    for name, returns in scenarios.items():
        scenario_params = params.get(name, DEFAULT_PARAMS)
        weights = run_opt(
            symbol,
            name,
            returns.drop('date'),
            scenario_params,
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
