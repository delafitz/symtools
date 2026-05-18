# Basic Return Analysis

First pass over the block-trade backtest. Goal: characterize the
raw and hedged P&L profile of block trades by discount bucket.

## Data

- **Trades**: `data/block_trades_alt.YYYYMMDD.json` — curated
  schema sourced from SEC 424B/144/Form 4 filings, reconciled
  against bootstrap curation, plus a small legacy-only set of
  foreign-issuer deals. 440 raw deals; filtered to larger
  trades only relative to the legacy file.
  - Uses `adj_price` and `adj_shares` (split-adjusted to
    today's basis) so cross-deal comparisons stay apples-to-
    apples with Polygon hists.
  - Explicit `intraday` boolean disambiguates same-day prints
    from after-close prints; intraday deals use prior session
    close as the discount reference.
  - Explicit `'Reg'` / `'Unreg'` `type` (no nulls).
  - `banks` is a list of canonical codes; lead-left is
    `banks[0]`.
  - After dropping out-of-hist-range, |discount| > 15%, and
    positive (premium) discounts, **346 scored trades**.
    After collapsing same-day multi-broker tranches to one
    observation per `(symbol, trade_date)`, **327 unique
    observations** on the combined scenario.
- **Hists**: `data/hists.YYYYMMDD.parquet` — 5y daily closes,
  fetched at `Y` template maxScale=5.
- **Baskets**: built per `(symbol, week)` via Barra factor model
  with point-in-time hist filtering (see `tools/backtest.py`).

### Dedup note

A single block placement often appears as multiple rows when
multiple brokers are syndicated. The raw scoring treats each
broker entry as a trade, but for cross-trade aggregation that
inflates concentrated names. **All cross-trade statistics here
use one row per `(symbol, trade_date)`**, averaging across
tranches.

### Schema history

A prior dataset (`block_trades.20260321.json`, 491 entries → 365
unique observations) was used earlier in this analysis. The
findings replicate cleanly on the curated alt file at sharper
signal-to-noise; the canonical source is now the alt file. The
legacy loader is preserved in `app/services/block_trades.py`
for backward compatibility.

## Discount construction

Source `Disc` field is unreliable (mixed units, occasional
errors). Discount is rebuilt from the pre-block close:

- `price_date < trade_date` → `pre_close = close(price_date)`
- `price_date == trade_date` → `pre_close = close(prev session)`

`discount = offer_price / pre_close - 1`. Trades with
`discount > 0` (premium) or `|discount| > 0.15` are dropped — see
`app/services/block_trades.py:_rebuild_discount`.

## Return scoring

For each trade × scenario × period × window:

- **pre period** — close-to-close drift INTO the print:
  `target_pre(N) = close(T) / close(T-N) - 1`
- **post period** — buyer's P&L from offer entry:
  `target_post(N) = close(T+N) / offer_price - 1`
- **basket leg** (post) — established at TradeDt close, so
  `basket_post(N) = close_basket(T+N) / close_basket(T) - 1`
- **hedged** — `target - β × basket` for the matching period

Different denominators for target and basket in the post case
are intentional: the buyer enters the block at `offer_price` but
puts on the basket hedge at TradeDt close. That asymmetry is the
discount capture.

Windows: 1, 5, 10, 20 trading days.

### Hedge ratio

Hedged returns apply a **0.85 multiplier** on the basket's
`β` (`HEDGE_RATIO` in `tools/backtest.py`). The optimizer's
200d β is empirically ~15% high vs the realized forward β
over the post window — block events are a regime break, and
no backward-looking β captures the post-trade dynamic well.
The haircut hits min-variance at the 5/10/20d post horizons
and recovers ~15-30bps of mean across scenarios. See the
"Hedge calibration" section in `block-alpha-drivers.md` for
the 60d vs 200d β analysis behind the choice.

## Population by discount bucket

n=327 unique observations, combined scenario.

| bucket | n | avg disc | avg xADV | avg vol_90d |
|---|---|---|---|---|
| <2% | 138 | −1.2% | 3.9× | 36% |
| 2-5% | 149 | −3.3% | 5.9× | 42% |
| ≥5% | 40 | −6.8% | 8.7× | 61% |

Deeper discount correlates with larger trade size relative to
ADV and higher target volatility — sellers pay more to clear
harder names. The curated alt dataset has a smaller ≥5% bucket
(n=40 vs 52 in the legacy file), consistent with filtering to
larger / better-marketed deals.

## Pre-trade drift

Average target raw return (close-to-close) leading into the
print:

| bucket | −20d | −1d |
|---|---|---|
| <2% | +4.8% | **−1.3%** |
| 2-5% | +6.9% | **−1.4%** |
| ≥5% | +13.8% | **−3.9%** |

Same broad pattern as the legacy data — 20-day run-up scales
with discount magnitude, last-day drawdown also scales. The
alt-dataset pre 1d numbers are materially milder than legacy
(legacy ≥5% was −7.2% vs new −3.9%) because the curated cohort
filters out the most-forced same-day prints.

20-day run-up is similar across buckets (+5 to +8%); the
**1-day drawdown into the print scales 1:1 with the discount**.
The discount is priced into the final intraday move — not
generously offered above where the stock is already trading.

## Post-trade raw P&L (offer → close)

| bucket | 1d | 5d | 10d | 20d |
|---|---|---|---|---|
| <2% | −0.06% | +0.59% | +1.32% | +1.77% |
| 2-5% | +0.49% | +0.35% | **+1.88%** | **+2.34%** |
| ≥5% | −0.52% | −1.68% | −0.91% | **+2.34%** |

Two findings (largely preserved from the legacy data):

1. **Mid-discount (2-5%) is the sweet spot** — best raw P&L at
   every horizon ≥10d (+2.34% at 20d, beating <2% by +57bps).
2. **Deep-discount (≥5%) double-dips** — buyer is −52bps at
   1d, drops to **−168bps at 5d**, recovers to **−91bps at
   10d**, then rallies to **+234bps at 20d**. The selling
   pressure that *caused* the deep discount keeps grinding for
   ~2 weeks before reverting. The reversion is sharper in the
   curated dataset than legacy — fewer broken trades drag it.

The mean-population pattern: discount of −3% is fully erased by
T+1 (avg post-print drift −2.95% ≈ −avg discount). Net P&L is
flat at 1d, recovers between T+5 and T+10, and stabilizes around
+1-2% by 20d.

## Hedged P&L (post, combined basket, k=0.85)

| bucket | 1d | 5d | 10d | 20d |
|---|---|---|---|---|
| <2% | −0.15% | +0.33% | +0.73% | +0.63% |
| 2-5% | +0.43% | +0.21% | **+1.46%** | **+1.49%** |
| ≥5% | −0.70% | −1.92% | −0.71% | +0.49% |

Headline: **biggest discounts have worst hedged P&L.**

- <2% bucket alpha mostly disappears under hedging — was
  riding market beta.
- 2-5% bucket retains **+93bps at 20d** — real residual block
  alpha after beta is hedged out.
- ≥5% bucket is negative across all horizons — these trades
  are idiosyncratic selling pressure the hedge can't capture,
  so the hedge subtracts more than the basket move and the
  buyer is left with the drift.

The discount is a danger signal, not a value signal in the deep
bucket.

## Caveats

- **Pre-trade hedge uses the trade-date basket retrospectively.**
  Not a pre-positioning backtest — would require re-optimizing
  as-of T-N.
- **Refs (mkt_cap, free_float, type) are latest-snapshot, not
  point-in-time.** Candidate universe is forward-looking and
  survivorship-biased.
- **n=52 in ≥5% bucket** — the deep-discount findings have wider
  CIs than the headline averages imply.
- **Hedgeable selection bias.** Trades where Barra failed (older
  trades with thin universe) are excluded from scored rows but
  remain in `backtest_trades.parquet` with null basket fields.
- **Source data integrity.** 12 trades initially showed positive
  discounts due to PxDt/TradeDt off-by-one errors in the source
  JSON (see prior investigation); these are now dropped by the
  >0 filter rather than corrected. Manual edits to source dates
  could recover them.

## Reproducing

```bash
# Fresh data fetch (5y depth, ~30s)
uv run python tools/backfill_hists.py

# Full backtest (~5 min cold; instant on cache after that)
rm -f data/backtest_baskets.parquet
uv run python tools/backtest.py

# Outputs:
#   data/backtest_trades.parquet  — per-trade meta + characteristics
#   data/backtest_scores.parquet  — per (trade × scenario × period × window)
```

Slicing by other dimensions (xADV, vol_90d, hedgeability) uses
the same join pattern on `(symbol, trade_date)`.
