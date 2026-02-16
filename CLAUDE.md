# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build and Run Commands

```bash
# Run development server
uv run fastapi dev app/main.py

# Format code
uv run ruff format

# Lint code
uv run ruff check

# Type check
uv run basedpyright
```

## Environment Variables

- `POLYGON_API_KEY` - Required for market data access via Polygon.io API (using `massive` client)

## Architecture Overview

Symtools is a FastAPI-based financial analytics server providing portfolio optimization and risk analysis for block trading.

### Directory Structure

- `app/main.py` - FastAPI application entry point
- `app/server/` - HTTP layer (router.py endpoints, cache.py in-memory storage)
- `app/services/` - Business logic (prices, refs, hist, cost, baskets/, alerts/)
- `app/models/` - Pydantic request/response models
- `app/mds/` - Market data service (massive client, formerly polygon-api-client)
- `app/utils/` - Helpers (store.py for parquet caching, trie.py for symbol search, groups.py for ETF lists and scenario defs)
- `data/` - Parquet cache files (date-stamped: `*.YYYYMMDD.parquet`)

### Core Data Flow

1. **Startup** - Async loading with concurrency pool (see Startup Architecture below)
2. **Symbol snapshot** (`/snapshot`) - SSE stream via `stream_symbol()`. Fires quote, PriceService, and analytics in parallel; yields events as they resolve. See `docs/snapshot-sse.md`.
3. **Hedge optimization** - Uses scikit-folio MeanRisk optimizer with SCIP solver against four scenario sets:
   - `indices`: SPY, QQQ, IWM
   - `factors`: sector/factor ETFs (XLK, XLF, SOXX, etc.)
   - `singles`: factor-screened individual stocks
   - `combined`: all of the above

### Startup Architecture

On startup, the server loads reference data and pre-fetches historical data using an async pool with semaphore-based concurrency (`CONCURRENCY = 15` in `app/services/refs.py`).

**Cached Startup** (refs parquet exists):
- Load refs from `data/refs.YYYYMMDD.parquet`
- Load basket hists from cache
- Background task loads any missing baskets

**Fresh Startup** (no refs cache):
Runs 4 phases sequentially, each using the shared semaphore for concurrent API calls:

| Phase | Description | Cache File | Progress Log |
|-------|-------------|------------|--------------|
| 1. Details | Fetch ticker details, filter by mkt_cap >= $1B | `refs.parquet` | every 50 |
| 2. Floats+SI | Fetch free float + short interest (concurrent per symbol) | (in refs) | every 50 |
| 3. Hists | Prefetch Y template for top 1000 by mkt_cap | `hists_Y.parquet` | every 50 |
| 4. Baskets | Load basket hists for all groups/templates | `{group}_{template}.parquet` | every 4 |

**Startup Summary** (logged as Polars table in yellow):
```
┌─────────┬───────┬─────────┐
│ phase   ┆ count ┆ seconds │
├─────────┼───────┼─────────┤
│ tickers │ 2000  │ 0.0     │
│ details │ 1500  │ 120.0   │
│ floats  │ 1200  │ 90.0    │
│ hists   │ 1000  │ 150.0   │
│ baskets │ 8     │ 45.0    │
│ total   │ 1500  │ 405.0   │
└─────────┴───────┴─────────┘
```

**Key Functions** (`app/services/refs.py`):
- `load_refs_async()` - Main startup loader, orchestrates all phases

**Callbacks** (defined in `cache.py:_load_refs_background`):
- `on_refs_update(refs)` - Updates `cache.refs` and rebuilds Trie
- `on_hists_update(hists)` - Updates unified `cache.hists` DataFrame

### Key Patterns

- **Pydantic models with formatting metadata** - Uses `Fmt` enum and `fp()` decorator in `app/utils/models.py` to attach display hints (valueFormat, metaLabel) for UI rendering
- **Parquet caching** - `app/utils/store.py` handles date-stamped file persistence with zstd compression:
  - `refs.YYYYMMDD.parquet` - Reference data (symbol, name, mkt_cap, free_float, etc.)
  - `hists.YYYYMMDD.parquet` - Unified symbol hists with `symbol`/`template` columns
  - `baskets.YYYYMMDD.parquet` - Cached optimizer weights (symbol, scenario, hedge_symbol, weight)
- **Async services** - All endpoint handlers are async; services return Pydantic models or Polars DataFrames

### API Endpoints

All routes defined in `app/server/router.py`:
- `/search` - Symbol prefix search via Trie
- `/refs` - Security reference data (symbol, exch, name, curr, sic, shares_out, mkt_cap, free_float, free_float_pct, free_float_date, short_interest, days_to_cover, short_avg_vol, short_interest_date)
- `/snapshot` - SSE: streams quote, hist (Y/M/W/D), analytics, baskets, basket_hist, alerts. See `docs/snapshot-sse.md`.
- `/quote` - Real-time quote (with session fields for pre/post/closed)
- `/hist` - Historical OHLCV bars with per-scale stats
- `/baskets` - Cached basket optimizations
- `/optimize` - No-op stub (disabled)
- `/cost` - Transaction cost calculations (slippage, xADV, cost alerts)

### Basket Optimization

**BasketParams** (`app/models/baskets.py`):
- `max_budget`: float = 0.2 (max total weight)
- `threshold_long`: float = 0.10 (min weight for inclusion)
- `cardinality`: int = 4 (max non-zero weights)
- `l1_coef`: float = 1e-5 (L1 regularization)

### PriceService (`app/services/prices.py`)

Owns daily (Y) hist data, template routing, SymbolHist response building, and price lookups. Single entry point for all hist-related operations during a snapshot or `/hist` request.

**Factory**: `PriceService.create(cache, symbol)` — async classmethod that loads Y hist from cache, constructs the service, and refreshes stale data if needed. Returns `None` if no Y data exists.

**Template routing** via `prices.hist(symbol, template)`:
- `Y` → stored daily bars (`self._daily`)
- `M` → sliced from Y (`slice_hist(daily, 'months', 6)`) — no separate API fetch
- `W`/`D` → delegated to `cache.get_hist_async`

**Response building**: `prices.build_response(symbol, template, scale)` → `SymbolHist`. Calls `hist()` then `_build_hist()` internally. Computes per-scale stats (1 through max_scale), daily_aggs for intraday, and bar data at max_scale resolution. Client slices visible bars via stats lookups.

**Daily refresh**: On market hours, `needs_refresh` checks two conditions:
1. Today's bar missing from `_daily` (data not yet available when loaded pre-open)
2. `_loaded_at` older than `DAILY_DEFAULT_TTL` (300s / 5 min)

When stale, `refresh_today(symbol)` fetches today's daily bar via Polygon, merges into `_daily`, and recomputes `pct_return`.

**Intraday refresh**: W/D hists are re-fetched by `cache.get_hist_async` when age > `INTRADAY_TTL` (120s). This is transparent — callers get fresh data without any code change. The stream pre-fetch (step 6) also refreshes stale basket-constituent W/D hists.

**Price lookups** (all operate on `_daily`):
- `close(date_str)` — daily close for a date
- `prev_close(before)` → `(prev_date, prev_close)` for last bar before a date
- `vwap(start, end)` — volume-weighted avg price; caps range to available daily data so intraday end dates beyond the last daily bar still return a value
- `daily_aggs(start, end)` — daily-bar slice for intraday overlay with prev close anchor
- `end_price(end_str, hist)` — best end price: daily close → market-close bar → last bar

### Hist Features

**Templates** (`app/services/prices.py:HIST_TEMPLATES`):
| Key | Timespan | Multiplier | Unit | Default Scale | Max Scale |
|-----|----------|------------|------|---------------|-----------|
| Y | day | 1 | years | 1 | 2 |
| M | day | 1 | months | 3 | 6 |
| W | minute | 30 | weeks | 2 | 4 |
| D | minute | 10 | days | 5 | 10 |

**Response** (`SymbolHist` in `app/models/hist.py`):
- `stats`: `dict[int, HistStats]` keyed by scale (1 through max). Per-scale date range and return stats.
- `daily_aggs`: Daily OHLCV bars covering the intraday range (for prior close/VWAP). `null` for daily templates.
- `bars`: OHLCV bars at template resolution with `pct_return` (bar-over-bar).

**HistStats** — per-scale range metadata:
- `end_date` / `end_price`: Session-aware end of range. Shared across all scales.
- `start_date`: First trading day in range (inclusive). For intraday: clock-driven (N trading days back from end). For daily: from actual bar data.
- `prev_date` / `prev_close`: Trading day before start. Anchors return calculation.
- `range_vwap`: Volume-weighted avg price from start to end (from daily data).
- `range_pct_return`: `end_price / prev_close - 1`.

**Intraday stats date logic** (`PriceService._intraday_end`):
- `end_date` = today if market has opened (>= 9:30 AM ET), else prior trading day
- `end_price` = daily close from Y hist, or market close bar as fallback
- `start_date` = `weekdays_back(end, scale - 1)` for D, `weekdays_back(end, scale * 5 - 1)` for W
- `prev_date` = `prev_weekday(start_date)`
- Bar data may include pre-session bars beyond `end_date`; stats reflect finalized daily closes.

**Market Sessions** (`app/utils/market.py`):
| Session | Window (ET) | Quote fields |
|---|---|---|
| `closed` | before 4:30 AM | `session="closed"`, no last/chg |
| `pre` | 4:30 AM – 9:30 AM | `session="pre"`, `sessionChg = last - prevClose` |
| `market` | 9:30 AM – 4:00 PM | session fields absent |
| `post` | 4:00 PM – 11:59 PM | `session="post"`, `sessionChg = last - todayClose` |

Session classification via `get_session(ts_ms)` on the Polygon quote timestamp. Hist responses have no session field — session awareness is embedded in date range logic (see intraday stats above).

**Basket Tracking** (`app/services/tracking.py:compute_tracking_for_template`):
1. For each scenario, join basket symbol closes from unified `cache.hists` to symbol hist on date/timestamp
2. Forward-fill + backward-fill nulls
3. Compute pct_change per basket symbol
4. Weighted average return per bar → returns `TrackingResult(series, scenarios)`
5. `build_basket_hists` splits into `BasketHist` per scenario with per-scale `stats` (dates from parent, `end_price`/`range_pct_return` cumulated from bar returns, `range_vwap` = null)
6. Series is rebased against `prev_close` from hist stats, so first bar has a non-zero return

**Timestamp Alignment**: Intraday timestamps rounded to bar boundaries (`round_ts` in `app/utils/dates.py`) for consistent joins across symbols.

### Alerts Service (`app/services/alerts/`)

Rule-based signals evaluated from symbol data. Decorator-based registry: `@rule(category)` auto-registers functions `(AlertContext) -> Alert | None`. `evaluate(ctx, categories?)` runs all (or filtered) rules, returns `SymbolAlerts | None`.

**AlertContext** — single data bag with optional fields (`ref`, `analytics`, `baskets`, `daily`, `costs`, `overrides`). Rules check for required data and return `None` if unavailable.

**Rule categories** (`app/services/alerts/rules/`):
- `liquidity` — `low_liquidity` (ADV < 1% float), `high_turnover` (ADV > 5% float)
- `volatility` — `high_vol` (>50%), `vol_disperse` (30d/90d divergence), `vol_change` (30d > 1.3x 90d)
- `moves` — `sigma_move_{1,3,5}d` (return vs sigma)
- `baskets` — `poor_index_hedge` (200d corr < 0.2), `no_good_hedges` (no scenario > 0.5 corr)
- `cost` — `size_pct_float`, `high_adv_multiple`, `override_vol_mismatch`, `override_adv_mismatch`

**Integration**:
- SSE stream: emitted as step 8 (after all hists + basket_hists), excludes cost category
- `/cost` endpoint: cost-category alerts included in `SymbolCostCalcs.alerts`

**Cache** (`app/server/cache.py`):
- Unified hists: `self.hists` — single Polars DataFrame with `symbol`/`template` columns, all templates concatenated
- `get_hist(symbol, template)` — sync, filters unified hists, drops metadata columns
- `get_hist_async(symbol, template)` — async, fetches from API if not cached or stale, adds to unified hists
- Intraday TTL: W/D hists tracked via `_hist_loaded_at` timestamps; re-fetched when age > `INTRADAY_TTL` (120s). Double-checked inside the per-symbol lock to avoid redundant concurrent fetches. Empty API responses preserve old cached data.
- Parquet: `data/hists.YYYYMMDD.parquet` — persisted on startup, loaded on cached startup
- Baskets: `data/baskets.YYYYMMDD.parquet` — cached optimizer weights, rebuilt on load
- During snapshot stream, M/W/D hists for basket constituent symbols (singles) are fetched on-demand if not already cached or stale

## Code Style

- Line length: 70 characters
- Quote style: single quotes
- Uses Polars for DataFrame operations (some Pandas in legacy paths)

### Service Return Types

Services return Pydantic models directly — no `model_validate` at call sites:
- `build_analytics()` → `SymbolAnalytics`
- `fetch_quote()` → `SymbolQuote`
- `calc_costs()` → `SymbolCostCalcs | None`
- `BasketService.build/get()` → `SymbolBaskets | None`
- `cache.search_token()` → `list[SearchResult]`
- `cache.get_analytics()` → `SymbolAnalytics | None`
- `cache.get_quote()` → `SymbolQuote`
- `cache.get_costs()` → `SymbolCostCalcs | None`
- `cache.get_baskets()` → `SymbolBaskets | None`
- `evaluate()` → `SymbolAlerts | None`

The basket pipeline passes `SymbolBaskets`/`Basket` models end-to-end (builder → service → cache → stream → tracking). `calc_stats()` in `baskets/risk.py` returns a dict internally; `Basket.model_validate()` happens in `builder.py`.

### SSE Serialization

SSE events use `model_dump(by_alias=True)` → camelCase, matching REST endpoint serialization. All models use `config()` with `serialization_alias=to_camel`.

### Cache Type Annotation

`Cache` is imported via `TYPE_CHECKING` in services that accept it (e.g. `cost.py`, `stream.py`). `get_ref()` / `get_refs()` still return dicts (RefData model lacks fields present in tickers fallback).

## Known Gaps

### Remaining Untyped Functions

- `app/utils/trie.py` - `insert()`, `prefix_search()` lack types
- `app/utils/corp.py` - `strip_name()` lacks types
- `app/utils/timing.py` - `timeit()` decorator lacks types
- `app/mds/refs.py` - `list_tickers()`, `fetch_ticker_details()` partial types
- `app/server/cache.py` - `get_refs()` → `list[dict]`, `get_ref()` → `dict | None` (not yet models)
