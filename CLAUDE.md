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

**Polygon provider** (active):
- `POLYGON_API_KEY` - Required for Polygon.io API (using `massive` client)

**Bloomberg provider** (stub):
- `BLOOMBERG_HOST` - B-PIPE or Terminal host (default `localhost`)
- `BLOOMBERG_PORT` - blpapi port (default `8194`)

## Architecture Overview

Symtools is a FastAPI-based financial analytics server providing portfolio optimization and risk analysis for block trading.

### Directory Structure

- `app/main.py` - FastAPI application entry point
- `app/server/` - HTTP layer (router.py endpoints, cache.py in-memory storage)
- `app/services/` - Business logic (prices, quotes, refs, hist, cost, baskets/, alerts/)
- `app/models/` - Pydantic request/response models
- `app/mds/` - Market data provider layer (see MDS Provider Architecture below)
  - `provider.py` - `MarketDataProvider` Protocol (abstract contract)
  - `client.py` - `get_provider()` factory (single swap point)
  - `polygon/` - Polygon.io provider (`massive` RESTClient)
  - `bloomberg/` - Bloomberg provider stub (`blpapi`)
- `app/utils/` - Helpers (store.py for parquet caching, trie.py for symbol search, groups.py for ETF lists and scenario defs)
- `data/` - Parquet cache files (date-stamped: `*.YYYYMMDD.parquet`)

### Core Data Flow

1. **Startup** - Async loading with concurrency pool (see Startup Architecture below)
2. **Symbol snapshot** (`/snapshot`) - SSE stream via `stream_symbol()`. Fires quote, PriceService, and analytics in parallel; yields cold Y/M hists (with synthetic today bar), then loops per-template: re-yields Y/M with real today bar, fetches stale W/D basket-sym hists, yields basket_hists. See `docs/snapshot-sse.md`.
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
| 3. Hists | Prefetch Y template for top 5000 by mkt_cap + ETFs | `hists.parquet` | every 50 |
| 4. ETF Hists | Prefetch M/W/D templates for ETF symbols | (in hists) | every 20 |

**Note:** Startup-loaded hists do not set `_hist_loaded_at` timestamps. The stream pre-fetch (step 6) treats unknown-age W/D data as stale (`hist_age()` returns `inf`), ensuring ETF intraday hists are refreshed on first snapshot request.

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

### MDS Provider Architecture

The market data layer uses a Protocol-based adapter pattern. All providers implement the same `MarketDataProvider` interface; swapping providers requires changing only `app/mds/client.py:get_provider()`.

**Protocol** (`app/mds/provider.py`):
```python
class MarketDataProvider(Protocol):
    def list_tickers(max_count) -> pl.DataFrame
    def get_details(symbol) -> dict | None
    def get_quote(symbol) -> SymbolQuote
    def get_hist(symbol, timespan, multiplier, unit, scale, ...) -> pl.DataFrame
    def get_hist_template(symbol, template, ...) -> pl.DataFrame
    def get_float(symbol, ...) -> dict | None
    def get_short_interest(symbol, ...) -> dict | None
```

All provider methods are **sync**. Callers wrap in `asyncio.to_thread()` for async usage.

**Polygon provider** (`app/mds/polygon/`) — active:
- `__init__.py` — `PolygonProvider` facade, delegates to sub-modules
- `quote.py` — `fetch_quote()` via `massive` REST snapshots + minute bars for session detection
- `hist.py` — `fetch_hist()` / `fetch_hist_template()` via aggs endpoint; shared OHLCV schemas (`OHLCV_BASE_SCHEMA`, `CLOSE_SCHEMA`, `OPEN_CLOSE_SCHEMA`)
- `refs.py` — `list_tickers()` / `fetch_ticker_details()` via tickers endpoint; shared `TICKER_SCHEMA`, `REF_SCHEMA`
- `float.py` — `fetch_free_float()` via vX shares/float endpoint
- `short_interest.py` — `fetch_short_interest()` via vX short-interest endpoint

**Bloomberg provider** (`app/mds/bloomberg/`) — stub:
- `__init__.py` — `BloombergProvider` facade, delegates to sub-modules with `self._session`
- `session.py` — `create_session()`, `collect()` event loop yielding `msg.toPy()` dicts, `sec()` ticker → `"AAPL US Equity"` mapping
- `quote.py` — `ReferenceDataRequest` snapshot; pre/post detection via `PRE_MKT_LAST_PRICE`/`AFTER_MKT_LAST_PRICE` fields (vs Polygon's timestamp-based approach)
- `hist.py` — daily via `HistoricalDataRequest`, intraday via `IntradayBarRequest`; normalizes to shared OHLCV schemas
- `refs.py` — ticker bootstrapping via `INDX_MEMBERS` on `RAY Index`; `fetch_details`/`fetch_float`/`fetch_short_interest` via batched `ReferenceDataRequest`

**Key provider differences:**

| Aspect | Polygon | Bloomberg |
|--------|---------|-----------|
| Ticker universe | `/v3/reference/tickers` endpoint | Index membership (`INDX_MEMBERS`) bootstrapping |
| Quote session | Timestamp-based from minute bars | Dedicated `PRE_MKT_LAST_PRICE` / `AFTER_MKT_LAST_PRICE` fields |
| Intraday hist | Multi-symbol via aggs endpoint | Single-security `IntradayBarRequest` |
| VWAP (daily) | Direct `vw` field | Direct `VWAP` field |
| VWAP (intraday) | Direct `vw` field | Derived: `value / volume` |
| Field scaling | Raw values | `EQY_SH_OUT`, `CUR_MKT_CAP`, `EQY_FLOAT` in millions (×1e6) |
| Short interest | Dedicated SI endpoint | `ReferenceDataRequest` with SI fields |
| Auth | API key (`POLYGON_API_KEY`) | B-PIPE/Terminal session (`BLOOMBERG_HOST`:`BLOOMBERG_PORT`) |

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

#### Model Strategy (`app/services/baskets/config.py`)

Two factor models for candidate screening and covariance estimation, toggled by `ModelChoice` (`'emp'` | `'barra'`) in `config.py`. `BasketService` reads `MODEL_CHOICE` at init and only builds the selected model. See `docs/MODELS.md` for full details.

**Empirical** (`'emp'`, default) — PCA-based (`EmpModel` in `factors.py`). Two factors (SMB + turnover) from principal components. Candidate screen: L1 distance on factor loadings, refined by correlation. Optimizer uses skfolio `EmpiricalPrior` (default).

**Barra** (`'barra'`) — Structured multi-factor (`BarraModel` in `barra.py`). 7 style factors + sector factors from Q5-Q1 factor-mimicking portfolios. Candidate screen: L1 distance on 6 z-scored style exposures with same-sector bonus. Optimizer uses skfolio `FactorModel` prior (B'FB + D covariance) with sector floor/cap constraints.

**Pipeline**: `BasketService` → `build_baskets(model_choice)` → `get_scenarios(emp_model=... | barra_model=...)` → `run_opts(...)` with model-specific prior/constraints → `calc_stats()`.

**Comparison tool**: `tools/barra.py` runs both models side-by-side (`uv run python tools/barra.py AAPL`).

### QuoteService (`app/services/quotes.py`)

Owns all quote fetching with TTL-based caching. Quotes are the single source of truth for `end_price` across the entire snapshot and hist pipeline.

```python
QUOTE_TTL = 300  # 5 min

class QuoteService:
    async def get(symbol) -> SymbolQuote
    async def get_many(symbols) -> dict[str, SymbolQuote]
```

- `get()` — returns cached quote if within TTL, otherwise fetches via `asyncio.to_thread(mds.get_quote, symbol)`
- `get_many()` — batch fetch, identifies stale symbols and fetches them in parallel via `asyncio.gather`
- Initialized once on `Cache.__init__` as `cache.quote_svc`
- `cache.get_quote(symbol)` delegates to `quote_svc.get(symbol)`

### PriceService (`app/services/prices.py`)

Owns daily (Y) hist data, template routing, and SymbolHist response building. Stateless — no mutable data, no refresh logic. Y daily data is treated as immutable T-1 data; live pricing comes from the quote.

**Factory**: `PriceService.create(cache, symbol)` — async classmethod that loads Y hist from cache. Returns `None` if no Y data exists. No refresh, no TTL tracking.

**Template routing** via `prices.hist(symbol, template)`:
- `Y` → stored daily bars (`self._daily`)
- `M` → sliced from Y (`slice_hist(daily, 'months', 6)`) — no separate API fetch
- `W`/`D` → delegated to `cache.get_hist_async`

**Response building**: `prices.build_response(symbol, template, end_price, scale)` → `SymbolHist`. Takes `end_price` from the caller (derived from quote). Computes per-scale stats (1 through max_scale), daily_aggs for intraday, and bar data at max_scale resolution.

**`end_price_from_quote(quote)`** — module-level function, returns `quote.close`. This is the single source of truth for `end_price`. Callers (stream.py, router.py) fetch the quote first, extract `end_price`, and pass it to `build_response()`.

**Intraday refresh**: W/D hists are re-fetched by `cache.get_hist_async` when age > `INTRADAY_TTL` (120s). This is transparent — callers get fresh data without any code change. Y/M daily data is T-1; today's partial bar is fetched separately via `cache.fetch_today_bars_async()` (single-day API call) and appended to Y/M in cache.hists.

**Today-bar helpers** on `PriceService`:
- `append_quote_bar(end_price)` — appends synthetic today bar (OHLC=close, vol=0) for immediate cold yield
- `replace_today_bar(bar)` — replaces synthetic bar with real API data (real OHL, volume)

**Price helpers** (module-level, operate on a `daily: pl.DataFrame` argument):
- `_close(daily, date_str)` — daily close for a date
- `_prev_close(daily, before)` → `(prev_date, prev_close)` for last bar before a date
- `_vwap(daily, start, end)` — volume-weighted avg price; caps range to available daily data
- `_daily_aggs(daily, start, end)` — daily-bar slice for intraday overlay with prev close anchor

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

**Intraday stats date logic** (inline in `PriceService._build_hist`):
- `end_date` = today if market has opened (>= 9:30 AM ET), else prior trading day (clock-driven)
- `end_price` = `quote.close` via `end_price_from_quote()` (passed in from caller)
- `start_date` = `weekdays_back(end, scale - 1)` for D, `weekdays_back(end, scale * 5 - 1)` for W
- `prev_date` = `prev_weekday(start_date)`
- Bar data may include pre-session bars beyond `end_date`; stats reflect the quote's live price.

**end_price by session** (via `quote.close`):
- Pre-market: `quote.close` = prev close → `end_date` = prev trading day. Stats show T-1 returns.
- Market hours: `quote.close` = running close → `end_date` = today. Stats show live intraday return.
- Post-market: `quote.close` = official close → `end_date` = today. Stats show finalized daily return.
- Daily templates (Y/M): bars are T-1 data from cache. During market hours, a today-bar is appended (first synthetic from quote close, then real from single-day API fetch). `end_price` from quote is always the source of truth for stats.

**Market Sessions** (`app/utils/market.py`):
| Session | Window (ET) |
|---|---|
| `closed` | before 4:30 AM |
| `pre` | 4:30 AM – 9:30 AM |
| `market` | 9:30 AM – 4:00 PM |
| `post` | 4:00 PM – 11:59 PM |

Session classification via `get_session(ts_ms)` on the quote timestamp (Polygon uses trade timestamps; Bloomberg uses dedicated pre/post fields). Hist responses have no session field — session awareness is embedded in date range logic (see intraday stats above).

**Quote Fields by Session** (`app/mds/polygon/quote.py`):

| Field | closed | pre | market | post |
|---|---|---|---|---|
| `prev` | prev close | prev close | prev close | prev close |
| `close` | =prev | =prev | today running | today final |
| `last` | =prev | pre-mkt px | ≈close | post-mkt px |
| `volume` | prev vol | prev vol | today vol | today vol |
| `chg`/`pctChg` | from API | from API | from API | from API |
| `session` | "closed" | "pre" | null | "post" |
| `sessionLast` | null | =last | null | =last |
| `sessionChg` | null | last - prev | null | last - close |
| `sessionVolume` | null | min.accum_vol | null | min.accum_vol |

Price resolution: `last_trade > min.close > prev_day.close`
Volume resolution: `day.volume > prev_day.volume`
Close resolution: `day.close > prev_day.close`

`sessionLast` duplicates `last` for convenience — lets clients grab all session-specific fields without checking session type.

**Basket Tracking** (`app/services/tracking.py:compute_tracking_for_template`):
1. For each scenario, join basket symbol closes from unified `cache.hists` to symbol hist on date/timestamp
2. Forward-fill + backward-fill nulls
3. Compute pct_change per basket symbol
4. Per-symbol `pct_return` columns stored in `TrackingResult.symbol_series` (dict[scenario, DataFrame])
5. Weighted average return per bar → returns `TrackingResult(series, scenarios, symbol_series)`
6. `build_basket_hists` splits into `BasketHist` per scenario with per-scale `stats` (dates from parent, `end_price`/`range_pct_return` cumulated from weighted returns, `range_vwap` = null), `weighted` (weighted-average tracking bars), and `symbols` (per-hedge-symbol return bars)
7. Series is rebased against `prev_close` from hist stats, so first bar has a non-zero return

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
- `quote_svc: QuoteService` — TTL-cached quote fetching, initialized on `Cache.__init__`
- `get_quote(symbol)` — delegates to `quote_svc.get(symbol)`
- Unified hists: `self.hists` — single Polars DataFrame with `symbol`/`template` columns, all templates concatenated
- `get_hist(symbol, template)` — sync, filters unified hists, drops metadata columns
- `get_hist_async(symbol, template)` — async, fetches from API if not cached or stale, adds to unified hists
- Intraday TTL: W/D hists tracked via `_hist_loaded_at` timestamps; re-fetched when age > `INTRADAY_TTL` (120s). `hist_age()` returns `float('inf')` for unknown load times (e.g. data loaded at startup without timestamps), so startup-loaded ETF intraday hists are always refreshed on first use. Double-checked inside the per-symbol lock to avoid redundant concurrent fetches. Empty API responses preserve old cached data.
- Today bar: `fetch_today_bars_async(symbols)` fetches a single-day daily bar for each symbol and appends to Y/M data in hists. Used by stream to get real OHL for today without re-fetching full Y/M series.
- Parquet: `data/hists.YYYYMMDD.parquet` — persisted on startup, loaded on cached startup
- Baskets: `data/baskets.YYYYMMDD.parquet` — cached optimizer weights, rebuilt on load
- During snapshot stream, today's daily bar is fetched for target + basket constituent symbols (single-day call), and W/D intraday hists are fetched on-demand if not cached or stale

## Code Style

- Line length: 70 characters
- Quote style: single quotes
- Uses Polars for DataFrame operations (some Pandas in legacy paths)

### Service Return Types

Services return Pydantic models directly — no `model_validate` at call sites:
- `build_analytics()` → `SymbolAnalytics`
- `fetch_quote()` → `SymbolQuote`
- `QuoteService.get()` → `SymbolQuote` (TTL-cached)
- `calc_costs()` → `SymbolCostCalcs | None`
- `BasketService.build/get()` → `SymbolBaskets | None`
- `cache.search_token()` → `list[SearchResult]`
- `cache.get_analytics()` → `SymbolAnalytics | None`
- `cache.get_quote()` → `SymbolQuote` (delegates to QuoteService)
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
- `app/mds/polygon/refs.py` - `list_tickers()`, `fetch_ticker_details()` partial types
- `app/server/cache.py` - `get_refs()` → `list[dict]`, `get_ref()` → `dict | None` (not yet models)

### Bloomberg Provider

- `blpapi` is not installed in the project — Bloomberg modules are stubs only and cannot be type-checked or run
- Bloomberg schemas import from `app.mds.polygon.hist` / `app.mds.polygon.refs` — could be extracted to a shared location if both providers become active
