# Entry-Timing Analysis (Ramp Entry on Low-Quality Blocks)

Research question: for lower-quality blocks (bad-bank, panic
pre-trade move, high xADV, bad sector), would entering at full
size on T (offer_price) be improved by ramping entry 1/3 each
over T, T+1, T+2 at close?

Prototype: `tools/portfolio_ramp_entry.py` — target-only ramp
(long leg blends offer_price + close_T+1 + close_T+2 / 3;
hedge unchanged at T close). This isolates the post-T target
drift question. Costs and hedge timing held constant.

## Setup

- 331 trades (5d), 327 trades (20d), `combined` scenario
- Entry drift = `(avg_entry_px / offer_price − 1)`. Negative
  drift = ramp paid less than offer.
- "Hedged ret" expressed per-trade as `pnl_hedged / notional`.

Quality buckets:
| flag | rule |
|---|---|
| `bad_bank` | broker ∈ {MS, BAC, BAML, JPM} |
| `panic` | r_pre1 ≤ −5% |
| `high_xadv` | shares_pct_adv > 5 |
| `bad_sector` | sector ∈ {Energy, Real Estate} |
| `any_low_qual` | OR of the above |

## Result (window = 20d)

| cohort | n | drift (bps) | retH (offer) | retH (ramp) | Δ ($M) |
|---|---|---:|---:|---:|---:|
| **all trades** | 327 | +6 | +0.46% | +0.44% | −8.8 |
| **panic** | 49 | **−166** | −0.85% | **+0.81%** | **+27.8** |
| bad_bank | 171 | −17 | −0.05% | +0.12% | +6.1 |
| bad_sector | 66 | −2 | −0.84% | −0.81% | +1.8 |
| high_xadv | 121 | +29 | +0.79% | +0.50% | −6.8 |
| any_low_qual | 260 | −9 | +0.28% | +0.37% | +14.5 |
| **non-panic** | 278 | +37 | +0.97% | +0.61% | −36.5 |
| **none-of-flags** | 67 | +67 | +2.33% | +1.66% | −23.3 |

5d and 10d show the same directional pattern with smaller
magnitudes.

## Findings

1. **Post-T drift is panic-specific.** Only the `r_pre1 ≤ −5%`
   cohort shows meaningful negative drift (−1.66% over T+1/T+2
   on average). The other "low quality" flags (bank, sector,
   xADV) don't have post-T price drift — their P&L issues come
   from hedge correlation or unrelated structural factors.

2. **Typical blocks drift slightly UP** after T. Non-panic
   trades average +37bps over T+1/T+2 — the post-print rebound
   off the discount. Ramping into these forfeits the bounce.

3. **High-quality trades are hurt most by indiscriminate
   ramp**: the 67 trades with no low-quality flag have +67bps
   post-T drift and lose −$23.3M of P&L under ramp entry.

4. **Net of blanket ramp is negative** at the all-trade level
   (−$8.8M at 20d): the panic-cohort gain (+$27.8M) doesn't
   offset losses on everything else.

## Strategic implication

Ramp-entry is not a generic "low-quality" treatment — it's
specifically a **panic-cohort treatment**. Compared to today's
default `skip_panic` (which drops the 49 panic trades), an
alternative `ramp_panic` rule would:

- Convert 49 panic trades from −$6.2M to +$21.5M over the
  dataset (target-only ramp; hedge unchanged).
- Preserve coverage instead of skipping.
- Keep the rest of the book at offer_price (where the
  post-print bounce is on our side).

## Caveats

- **Hedge leg is unchanged in this prototype** (entered fully
  at T close). If panic days also drift the basket down,
  ramping the hedge would lock in worse short prices,
  partially offsetting the long-side gain. The clean version
  needs basket reconstruction at T+1/T+2 from
  `backtest_baskets.parquet`. Directional sign should hold,
  but the magnitude could be 30–60% smaller.
- **Costs unchanged** — total notional is the same across
  ramp vs offer entry, so the 4-side 10bps cost applies
  identically.
- **Sample size on panic = 49 trades.** The +$27.8M lift is
  driven by a handful of large drifts. A bootstrap of the
  cohort would show wide confidence bands.
- **No stop interaction tested.** A ramp-entry trade carries
  smaller notional on day 1, so the 8% hedged-P&L stop is
  proportionally less likely to fire during the ramp window.
  This favors ramp entry mechanically.
- **No volume / liquidity check.** Ramp days assume close-on
  execution at full 1/3 fill. Real implementation needs
  liquidity confirmation on T+1, T+2 — typically not a problem
  for blocks (where notional is small vs daily volume) but
  worth verifying for size > $50M.

## Reproducing

```bash
uv run python tools/portfolio_ramp_entry.py
uv run python tools/portfolio_ramp_entry.py --window 20
```

Outputs a per-cohort table for the 5d, 10d, and 20d windows.

## Next steps (not implemented)

If we want to operationalize ramp_panic as a strategy primitive:

1. Rebuild basket levels on T+1/T+2 from
   `backtest_baskets.basket_json` so hedge entry can ramp too.
2. Recompute `pnl_hedged_usd` for the panic subset with
   both-legs ramp.
3. Add `ramp_panic` as a new primitive in
   `tools/portfolio_strategy_sweep.py` (replacement for
   `skip_panic`, not a multiplier — needs a recomputed P&L
   column).
4. Compare `ramp_panic` vs `skip_panic` head-to-head on
   Sharpe and ann return.
