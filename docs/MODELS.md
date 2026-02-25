# Optimization Model Strategy

Basket optimization supports two factor models for candidate screening and covariance estimation. A `ModelChoice` toggle in `config.py` routes the entire pipeline through one model or the other.

## Models

### Empirical (`'emp'`)

PCA-based factor model built from the stock return matrix. Identifies two factors (SMB + turnover) by correlating principal components with sort-portfolio returns. Used for candidate pre-screening in the `singles` scenario.

- **Class**: `EmpModel` (`app/services/baskets/factors.py`)
- **Builder**: `build_emp_model(refs, hists)`
- **Prior**: skfolio `EmpiricalPrior` (default, no explicit prior passed to optimizer)
- **Candidate screen**: Factor loading distance (L1 norm on SMB + turnover loadings), refined by correlation composite score

### Barra (`'barra'`)

Structured multi-factor model with 7 style factors (market, size, momentum, reversal, beta, resvol, liquidity) plus sector factors. Factor returns are computed from Q5-Q1 factor-mimicking portfolios with periodic rebalancing. Provides a structured covariance prior (B'FB + D) for the optimizer.

- **Class**: `BarraModel` (`app/services/baskets/barra.py`)
- **Builder**: `build_barra_model(refs, hists)`
- **Prior**: skfolio `FactorModel` with `EmpiricalPrior` on factor returns and residual variance
- **Candidate screen**: L1 norm over 6 z-scored style exposures with same-sector bonus, refined by correlation composite score
- **Sector constraints**: Floor on target sector (30% of budget), cap on off-sectors (50% of budget) via skfolio `linear_constraints`

## Configuration

```python
# app/services/baskets/config.py
ModelChoice = Literal['emp', 'barra']
MODEL_CHOICE: ModelChoice = 'emp'
```

Change `MODEL_CHOICE` to switch the production pipeline. `BasketService` reads this default at init and only builds the selected model.

## Pipeline Flow

```
BasketService.__init__(model_choice)
  |
  +-- build_emp_model()   (if 'emp')
  +-- build_barra_model() (if 'barra')
  |
  v
BasketService.build(symbol)
  |
  v
build_baskets(symbol, ..., emp_model, barra_model, model_choice)
  |
  +-- get_scenarios(emp_model=...)   (if 'emp')
  +-- get_scenarios(barra_model=...) (if 'barra')
  |
  +-- run_opts(symbol, scenarios)                          (emp: EmpiricalPrior default)
  +-- run_opts(symbol, scenarios, prior, fr, groups, lin)  (barra: FactorModel prior + sector constraints)
  |
  v
calc_stats() -> Basket
```

## Key Files

| File | Role |
|------|------|
| `config.py` | `ModelChoice` type, `MODEL_CHOICE` default |
| `factors.py` | `EmpModel` dataclass, `build_emp_model()` |
| `barra.py` | `BarraModel` dataclass, `build_barra_model()`, `get_prior()`, `get_factor_returns()`, `build_sector_constraints()` |
| `scenarios.py` | `get_scenarios()` routes candidate screening by model; `_get_emp_candidates()` / `_get_barra_candidates()` |
| `builder.py` | `build_baskets()` orchestrates model-specific optimization (prior, factor returns, sector constraints) |
| `service.py` | `BasketService` owns model lifecycle and passes choice through the pipeline |
| `worker.py` | Multiprocess batch: pickles both models + choice into worker state |
| `opt.py` | Model-agnostic optimizer; accepts optional `prior_estimator`, `factor_returns`, `groups`, `linear_constraints` |

## Comparison Tool

`tools/barra.py` runs both models side-by-side for a given symbol. It builds scenarios independently for each model and compares weights, correlation, and vol reduction. Unlike the production pipeline, the tool calls `get_scenarios` and `run_opts` directly (not through `build_baskets`) to print detailed per-scenario stats.

```bash
uv run python tools/barra.py AAPL
uv run python tools/barra.py --top 5
```
