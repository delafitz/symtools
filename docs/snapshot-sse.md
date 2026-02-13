# Snapshot SSE Protocol

`GET /snapshot?symbol=AAPL`

Streams symbol data as server-sent events. Each event has a `type` and JSON `data` payload.

## Event Sequence

```
1. quote
2. hist (Y)
3. analytics
4. baskets
5. basket_hist (Y × scenarios)
6. [fetch missing basket symbol hists for M/W/D]
7. hist (M) → basket_hist (M × scenarios)
   hist (W) → basket_hist (W × scenarios)
   hist (D) → basket_hist (D × scenarios)
8. done
```

- Quote, `PriceService.create`, and analytics fire in parallel on entry
- `PriceService` owns all hist routing: Y from stored daily, M sliced from Y (no extra API call), W/D fetched via cache
- If daily data is stale (missing today's bar or older than 5 min TTL), PriceService refreshes before building responses
- `hist` events fire as templates resolve (Y first, then M, W, D); bars always sent at max scale, client slices via stats
- `baskets` depends on Y hist; `basket_hist` depends on baskets + that template's hist
- Step 6 pre-fetches M/W/D hists for basket constituent symbols not already cached (e.g. singles stocks only have Y from startup)
- `basket_hist` emits one event per scenario per template
- `analytics` and `baskets` may be absent if data is unavailable
- Stream terminates with `done` (or `error` + `done` on failure)

## Event Types

### `quote`

Real-time quote data. Session fields are present outside market hours.

```json
{
  "symbol": "aapl",
  "updated": "2025-02-10T21:00:00Z",
  "prev": 230.50,
  "close": 232.10,
  "last": 232.10,
  "volume": 45000000,
  "chg": 1.60,
  "pctChg": 0.0069,
  "session": "post",
  "sessionLast": 232.25,
  "sessionChg": 0.15
}
```

| Field | Description |
|---|---|
| `prev` | Previous day close |
| `close` | Today's close (or prev day if no data) |
| `last` | Last trade price |
| `session` | `"pre"`, `"post"`, or `"closed"`. Absent during market hours. |
| `sessionLast` | Last trade in extended session (pre/post only) |
| `sessionChg` | Change vs reference price (pre/post only). Pre: vs prev close. Post: vs today's close. |

### `hist`

OHLCV bars for one template with per-scale stats.

```json
{
  "symbol": "aapl",
  "template": "D",
  "timespan": "minute",
  "multiplier": 10,
  "scale": 5,
  "stats": {
    "1": {
      "endDate": "2025-02-10",
      "endPrice": 232.10,
      "startDate": "2025-02-10",
      "prevDate": "2025-02-07",
      "prevClose": 230.50,
      "rangeVwap": 231.80,
      "rangePctReturn": 0.0069
    },
    "5": {
      "endDate": "2025-02-10",
      "endPrice": 232.10,
      "startDate": "2025-02-04",
      "prevDate": "2025-02-03",
      "prevClose": 228.90,
      "rangeVwap": 230.45,
      "rangePctReturn": 0.0140
    }
  },
  "dailyAggs": [
    {
      "date": "2025-02-03",
      "timestamp": 1738540800000,
      "open": 228.00, "high": 229.50, "low": 227.80,
      "close": 228.90, "vwap": 228.60, "volume": 48000000,
      "pctReturn": -0.0021
    }
  ],
  "bars": [
    {
      "date": "2025-02-04",
      "iso": "2025-02-04T14:40:00Z",
      "timestamp": 1738680000000,
      "open": 229.00, "high": 229.30, "low": 228.90,
      "close": 229.10, "vwap": 229.05, "volume": 120000,
      "pctReturn": 0.0009
    }
  ]
}
```

**`stats`** — keyed by scale (1 through max). Every scale shares `endDate`/`endPrice`; `startDate`, `prevDate`, `prevClose` vary per scale.

| Field | Description |
|---|---|
| `template` | `Y`, `M`, `W`, `D` |
| `timespan` | Bar resolution: `day` or `minute` |
| `multiplier` | Bar size (1 for daily, 10 or 30 for intraday) |
| `scale` | Requested scale (default for template) |
| `stats[n].endDate` | Last trading day in range |
| `stats[n].endPrice` | Daily close on end date (from Y hist, or market close bar as fallback) |
| `stats[n].startDate` | First trading day in range (inclusive) |
| `stats[n].prevDate` | Trading day before start (return baseline) |
| `stats[n].prevClose` | Daily close on prev date |
| `stats[n].rangeVwap` | Volume-weighted avg price from start to end |
| `stats[n].rangePctReturn` | `endPrice / prevClose - 1` |
| `dailyAggs` | Daily OHLCV bars covering the range (intraday templates only, `null` for daily) |
| `bars` | OHLCV bars at template resolution. `pctReturn` is bar-over-bar. |

**Stats date logic:**

For intraday templates (W, D), dates are clock-driven:
- `endDate` = today if market has opened, else prior trading day
- `startDate` = N trading days back from `endDate` (inclusive, where N = scale for D, scale×5 for W)
- `prevDate` = trading day before `startDate`

For daily templates (Y, M), dates come from the actual bar data:
- `endDate` = last bar date
- `startDate` = first bar date for that scale
- `prevDate` = trading day before `startDate` (from daily data)

Note: bar data may include pre-session bars for the current day even when `endDate` is the prior trading day. The stats reflect finalized daily closes; bars reflect real-time availability.

**Templates:**

| Template | Timespan | Multiplier | Default Scale | Max Scale |
|---|---|---|---|---|
| Y | day | 1 | 1 year | 2 years |
| M | day | 1 | 3 months | 6 months |
| W | minute | 30 | 2 weeks | 4 weeks |
| D | minute | 10 | 5 days | 10 days |

### `analytics`

Volatility and volume analytics.

```json
{
  "symbol": "aapl",
  "vol": 0.32,
  "adv": 55000000,
  "histVol": { "20": { "value": 0.28, "meta": 0.35 }, "60": { "value": 0.31 } },
  "histAdv": { "20": { "value": 52000000, "meta": 58000000 } }
}
```

### `baskets`

Hedge basket optimizations. One event with all scenarios.

```json
{
  "symbol": "aapl",
  "baskets": {
    "indices": {
      "params": { "maxBudget": 0.2, "thresholdLong": 0.10, "cardinality": 5, "l1Coef": 1e-5 },
      "weights": { "spy": 0.15, "qqq": 0.05 },
      "returns": { "20": { "value": -0.02, "meta": 0.05 } },
      "corrs": { "200": { "value": 0.85 } },
      "vols": { "target": 0.32, "basket": 0.18, "hedged": 0.22, "reduction": 0.31 }
    },
    "factors": { },
    "singles": { },
    "combined": { }
  }
}
```

| Scenario | Hedge pool |
|---|---|
| `indices` | SPY, QQQ, IWM |
| `factors` | Sector/factor ETFs (XLK, XLF, SOXX, etc.) |
| `singles` | Factor-screened individual stocks |
| `combined` | All of the above |

### `basket_hist`

Tracking returns for one scenario on one template. Match to parent `hist` on `(symbol, template)`. Hedge weights, vol, and correlation stats are on the `baskets` event.

```json
{
  "symbol": "aapl",
  "template": "D",
  "basket": "indices",
  "stats": {
    "1": {
      "endDate": "2025-02-10",
      "endPrice": 231.85,
      "startDate": "2025-02-10",
      "prevDate": "2025-02-07",
      "prevClose": 230.50,
      "rangeVwap": null,
      "rangePctReturn": 0.0059
    },
    "5": {
      "endDate": "2025-02-10",
      "endPrice": 231.85,
      "startDate": "2025-02-04",
      "prevDate": "2025-02-03",
      "prevClose": 228.90,
      "rangeVwap": null,
      "rangePctReturn": 0.0129
    }
  },
  "bars": [
    { "date": "2025-02-04", "pctReturn": -0.0021 },
    { "date": "2025-02-04", "timestamp": 1738680000000, "pctReturn": 0.0015 },
    { "date": "2025-02-05", "timestamp": 1738683600000, "pctReturn": -0.0008 }
  ]
}
```

| Field | Description |
|---|---|
| `basket` | Scenario name: `indices`, `factors`, `singles`, `combined` |
| `stats` | Per-scale stats, same keys as parent `hist.stats`. Dates/prevClose copied from parent; `endPrice` and `rangePctReturn` derived from cumulated basket bar returns. `rangeVwap` is always `null`. |
| `bars[].pctReturn` | Bar-over-bar weighted basket return |
| `bars[].timestamp` | Present for intraday templates (W, D) |

**First-bar return:** Tracking is rebased against `prevClose` from `hist.stats`, so the first bar generally has a non-zero return reflecting the overnight/prior-day move. This aligns the tracking series with the symbol's return window.

**Reconstructing the tracking line for chart overlay:**

```js
// anchor to any visible window start
const anchor = symbolBars[visibleStart].close;
let cum = anchor;
for (const bar of trackingBars.slice(visibleStart)) {
  cum *= (1 + bar.pctReturn);
  drawPoint(bar.date, cum);
}
```

The tracking line and symbol line share the same anchor price, so divergence between them shows hedge P&L. Works at any zoom level without refetching — just re-anchor to the new visible start.

For intraday templates (W, D), `pctReturn` between the last bar of day N and first bar of day N+1 captures the overnight gap.

### `done`

Terminal event. No payload.

### `error`

```json
{ "error": "message" }
```

Always followed by `done`.
