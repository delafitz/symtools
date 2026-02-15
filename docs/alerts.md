# Alerts Service

Rule-based signals for quantitative analysis. Rules evaluate symbol data and produce scored alerts grouped by category.

## Architecture

```
app/services/alerts/
    __init__.py           # AlertContext, @rule(), evaluate()
    rules/
        __init__.py       # imports all rule modules
        liquidity.py      # ADV vs float
        volatility.py     # vol levels + term structure
        moves.py          # recent returns vs sigma
        baskets.py        # hedge quality
        cost.py           # size, xADV, override mismatches
```

### Models (`app/models/alerts.py`)

```python
class Alert(BaseModel):
    rule: str          # rule identifier
    category: str      # rule category
    level: str         # 'info' | 'warn' | 'alert'
    score: float       # 0.0-1.0
    label: str         # human-readable description
    value: float       # measured value
    threshold: float   # threshold exceeded

class SymbolAlerts(BaseModel):
    symbol: str
    score: float       # max of alert scores
    alerts: list[Alert]
```

Level is derived from score: `alert` (>0.66), `warn` (0.34-0.66), `info` (<0.34).

### AlertContext

Single data bag passed to every rule. Rules check for required fields and return `None` if data is unavailable.

```python
@dataclass
class AlertContext:
    symbol: str
    ref: dict | None = None
    analytics: SymbolAnalytics | None = None
    baskets: SymbolBaskets | None = None
    daily: pl.DataFrame | None = None
    costs: SymbolCostCalcs | None = None
    overrides: SymbolOverrides | None = None
```

### Rule Registration

Decorator-based. `@rule(category)` auto-registers a function `(AlertContext) -> Alert | None`.

```python
@rule('volatility')
def high_vol(ctx: AlertContext) -> Alert | None:
    ...
```

`evaluate(ctx, categories?)` runs all registered rules (or a filtered subset), collects non-`None` results, and returns `SymbolAlerts | None`.

## Rules

### Liquidity

Uses `analytics.adv` and `ref['free_float']`.

| Rule | Condition | Score | Level |
|------|-----------|-------|-------|
| `low_liquidity` | ADV < 1% of float | 0.5 | warn |
| `high_turnover` | ADV > 5% of float | 0.5 | warn |

### Volatility

Uses `analytics.vol` and `analytics.hist_vol` (30d, 90d term structure).

| Rule | Condition | Score | Level |
|------|-----------|-------|-------|
| `high_vol` | vol > 100% | 0.8 | alert |
| `high_vol` | vol > 50% | 0.5 | warn |
| `vol_discord` | \|30d - 90d\| / 90d > 20% | 0.4 | warn |
| `vol_change` | 30d > 1.3x 90d | 0.5 | warn |

### Moves

Uses `daily` (Y daily bars) and `analytics.vol`. Computes N-day return from tail of daily bars. Daily sigma = `vol / sqrt(252) / 100`.

| Rule | Condition | Score | Level |
|------|-----------|-------|-------|
| `recent_move_{n}d` | \|return\| > 2 * sigma * sqrt(n) | 0.8 | alert |
| `recent_move_{n}d` | \|return\| > 1 * sigma * sqrt(n) | 0.4 | warn |

Windows: n = 1, 3, 5. Returns the highest-scoring alert across all windows.

### Baskets

Uses `baskets` (SymbolBaskets with per-scenario correlations).

| Rule | Condition | Score | Level |
|------|-----------|-------|-------|
| `poor_index_hedge` | indices 200d corr < 0.2 | 0.5 | warn |
| `no_good_hedges` | no scenario with 200d corr > 0.5 | 0.6 | warn |

### Cost

Uses `costs`, `overrides`, and `analytics`. Only evaluated from the `/cost` endpoint (filtered by `categories={'cost'}`).

| Rule | Condition | Score | Level |
|------|-----------|-------|-------|
| `size_pct_float` | shares > 10% of float | 0.6 | warn |
| `high_adv_multiple` | xADV > 5 | 0.5 | warn |
| `override_vol_mismatch` | \|override - hist\| / hist > 20% | 0.3 | info |
| `override_adv_mismatch` | \|override - hist\| / hist > 20% | 0.3 | info |

## Integration

### SSE Stream (`/snapshot`)

Emitted as step 8, after all hists and basket_hists. Evaluates all categories except cost (cost data is not available in the stream context).

```
event: alerts
data: {"symbol":"aapl","score":0.5,"alerts":[...]}
```

Only emitted if at least one rule fires.

### Cost Endpoint (`/cost`)

Cost-category alerts are evaluated after computing costs and included in the `SymbolCostCalcs` response:

```json
{
  "symbol": "aapl",
  "discount": { ... },
  "stats": { ... },
  "alerts": [
    {
      "rule": "size_pct_float",
      "category": "cost",
      "level": "warn",
      "score": 0.6,
      "label": "Shares > 10% of float",
      "value": 0.15,
      "threshold": 0.10
    }
  ]
}
```

## Adding Rules

1. Create a function in the appropriate `rules/*.py` module (or a new module)
2. Decorate with `@rule('category')`
3. Accept `AlertContext`, return `Alert | None`
4. If adding a new module, import it in `rules/__init__.py`
