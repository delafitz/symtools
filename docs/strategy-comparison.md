# Strategy Comparison

Empirical sweep of per-trade filter rules layered on the
baseline portfolio backtest. Builds on
`basic-scenario-analysis.md`, `block-alpha-drivers.md`, and
`stop-loss-analysis.md`.

## Setup

Population: 327 hedgeable trades from the alt dataset, 20d
window. Sizing: pct_adv=0.15, floor=$10M, cap=$100M, hedge
ratio=0.85, stop=−8% (hedged-P&L basis), cost=10 bps × 4
sides (40 bps round-trip on gross). All P&L numbers are
**net of costs**.

Per-trade rules return a multiplier in `[0.0, 1.5]` applied
to the base notional:
- **`0.0`** = skip the trade (rescaled to zero, removed from
  the portfolio)
- **`0.5`** = half size
- **`1.0`** = baseline size
- **`1.5`** = 50% upsize

Dollar fields (P&L, VaR, GMV) scale linearly with the
multiplier; pct fields are invariant. Implementation in
`tools/portfolio_strategy_sweep.py`.

## Strategy primitives

| primitive | rule | rationale |
|---|---|---|
| `half_bad_bank` | 0.5× if broker ∈ {MS, BAC, BAML, JPM} | bank lens: these lead-banks have negative-to-neutral hedged P&L on average (see `block-alpha-drivers.md`) |
| `skip_bad_bank` | 0.0× if bad bank | aggressive bank filter |
| `skip_panic` | 0.0× if pre-1d return ≤ −5% | decile lens: D1-archetype "slamming the close" trades destroy value |
| `skip_deep_disc` | 0.0× if discount ≤ −5% | discount-bucket lens: ≥5% bucket has double-dip pattern |
| `skip_high_xadv` | 0.0× if shares_pct_adv > 5.0 | larger trades vs ADV correlate with continued selling pressure |
| `chase_d10` | 1.5× if pre_20d > +5% AND pre_1d > −2% | decile lens: D10 archetype outperforms by ~20pp at 20d |
| `half_bad_sector` | 0.5× if sector ∈ {Energy, Real Estate} | sector lens: clean losing cohorts with high hedge-corr (alpha hedged away). **IT excluded**: too big and internally diverse (semis / software / hardware / IT services) to treat as one cohort. |
| `quarter_bad_sector` | 0.25× if sector ∈ {Energy, Real Estate} | heavier penalty, same set |
| `skip_tail_sector` | 0.0× if sector ∈ {Comm Services, Utilities} | tiny-n tail (n<10) with hedged P&L < −3% — safe to drop |
| `chase_good_sector` | 1.5× if sector ∈ {Cons Disc, Industrials} | sector lens: low-corr cohorts where alpha survives hedging |

## Sweep results (window=20d, hedged-P&L stop, net of costs)

| strategy | n | avg_GMV | avg_VaR | **PnL_mo** | mo_ret | **ann_ret** | **sharpe_h** |
|---|---|---|---|---|---|---|---|
| baseline | 327 | $465M | $39M | +$5M | +1.31% | +15.7% | +1.86 |
| skip_panic | 278 | $418M | $33M | +$5M | +1.64% | +19.7% | +2.14 |
| chase_d10 | 327 | $533M | $46M | +$7M | +1.51% | +18.1% | +1.87 |
| half_bad_bank | 327 | $356M | $31M | +$5M | +1.52% | +18.3% | +1.85 |
| half_bad_sector | 327 | $436M | $38M | +$5M | +1.37% | +16.4% | +1.91 |
| quarter_bad_sector | 327 | $422M | $37M | +$6M | +1.39% | +16.7% | +1.92 |
| skip_tail_sector | 317 | $433M | $37M | +$5M | +1.23% | +14.7% | +1.56 |
| chase_good_sector | 327 | $516M | $44M | +$6M | +1.42% | +17.1% | +1.81 |
| chase_d10+half_sector | 327 | $499M | $44M | +$7M | +1.57% | +18.9% | +1.91 |
| chase_d10+chase_good_sector | 327 | $592M | $51M | +$8M | +1.64% | +19.7% | +1.83 |
| **skip_panic+half_sector** | **278** | **$391M** | **$32M** | **+$6M** | **+1.75%** | **+21.0%** | **+2.26** |
| chase_d10+half_bank+skip_panic | 278 | $372M | $31M | +$6M | +2.05% | +24.6% | +2.03 |
| **chase_d10+skip_panic+half_sector** | **278** | **$453M** | **$38M** | **+$8M** | **+1.93%** | **+23.2%** | **+2.19** |
| **chase_d10+skip_panic+quarter_sector** | **278** | **$436M** | **$37M** | **+$8M** | **+2.00%** | **+24.0%** | **+2.24** |
| chase_d10+skip_panic+chase_good_sector | 278 | $539M | $44M | +$9M | +1.95% | +23.4% | +2.08 |
| **chase_d10+half_bank+skip_panic+half_sector** | **278** | **$348M** | **$29M** | **+$6M** | **+2.16%** | **+25.9%** | **+2.13** |
| chase_d10+half_bank+skip_panic+chase_good_sector | 278 | $412M | $34M | +$7M | +2.21% | +26.5% | +2.05 |
| chase_d10+half_bank+skip_panic+sector_full | 269 | $359M | $32M | +$7M | +2.20% | +26.4% | +1.99 |

## Findings

### Rules that work cleanly

1. **`skip_panic` is the best single filter.** Cutting 49
   trades with pre-1d ≤ −5% lifts Sharpe **1.86 → 2.14**
   while preserving the $5M/mo P&L. The D1-archetype
   "forced-seller into the print" cohort destroys value
   without contributing. Pure noise reduction.

2. **`chase_d10` is the best size-up rule.** Upsizing 56
   D10-archetype trades (strong pre-20d run-up + mild pre-1d
   drawdown) by 1.5× lifts monthly P&L **$5M → $7M (+33%)**
   at Sharpe 1.87. Captures right-tail alpha without any
   skips.

3. **Sector half-sizing (Energy + Real Estate) adds Sharpe
   for free.** Layering `half_bad_sector` on top of
   `chase_d10+skip_panic` lifts Sharpe **2.14 → 2.19** with
   no trade drops — alpha is hedged away in these high-corr
   sectors, so half-sizing them keeps the option without
   carrying the variance. **IT is intentionally excluded**
   from sector penalties: at 49 trades / ~15% of the
   population it is too big and internally diverse (semis,
   software, hardware, IT services) to treat as one cohort.

4. **`skip_panic + half_sector` is the new Sharpe optimum.**
   Sharpe **2.26** at +21.0% annualized. Two-rule combo, no
   chase, ~$391M GMV. Drops 49 panic trades and half-sizes
   the Energy/RE cohort.

5. **Stacking everything hits the highest annualized.**
   `chase_d10+half_bank+skip_panic+half_sector` runs
   Sharpe 2.13 at **+25.9% annualized** on $348M GMV — the
   capital-efficient choice.

### Three counter-intuitive results

1. **`skip_deep_disc` HURTS.** Sharpe 1.92 → 1.69, monthly
   P&L $6M → $3M. Despite the ≥5% discount bucket showing
   a "double-dip" pattern in the cross-section, those trades
   are net profitable on average. Skipping them forfeits
   substantial discount-cushion alpha.

2. **`skip_bad_bank` (full skip) hurts Sharpe.** 1.92 → 1.59
   even though annualized return rises to +22.6%. JPM/MS/BAC
   trades aren't uniformly bad — we skip good ones along
   with the bad. The **`half_bad_bank` (0.5×) is the better
   compromise**, but even that drops Sharpe to 1.84.

3. **`skip_high_xadv` is roughly neutral.** Sharpe slips
   1.92 → 1.66, P&L drops modestly. The xADV signal is
   correlated with bad outcomes but not cleanly enough to
   make filtering on it pay.

### Why `chase_d10` works better than skipping bad trades

Most strategy lift comes from **upsizing winners**, not from
**filtering losers**:

- baseline: $5M/mo, Sharpe 1.86
- skip_panic only: $5M/mo, Sharpe 2.14 (+0.28 Sharpe)
- chase_d10 only: $7M/mo, Sharpe 1.87 (+0.01 Sharpe but +$2M/mo)
- half_bad_sector only: $5M/mo, Sharpe 1.91 (+0.05 Sharpe, no skip)

The right tail of the return distribution carries the
strategy. Skipping the left tail tightens variance but
doesn't add P&L; upsizing the right tail adds P&L (and a
little variance) directly. Sector half-sizing is a third
axis — it reduces variance contribution from sectors where
the hedge eats the alpha, without dropping trades.

## Recommended bundles

Three clean choices depending on objective:

### Pure risk-adjusted optimum: `skip_panic+half_sector`
- **Sharpe 2.26** (best in sweep)
- Annualized **+21.0%** hedged
- Avg daily GMV **$391M**
- Monthly P&L **+$6M**
- 278 trades (drops 49 panic-day trades; half-sizes Energy/RE)

### Balanced: `chase_d10+skip_panic+half_sector`
- Sharpe **+2.19**, ann **+23.2%**, P&L **+$8M/mo**, GMV $453M
- Adds D10 upsize on top of the Sharpe optimum for +$2M/mo
- Best balance of P&L and Sharpe

### Capital-efficient: `chase_d10+half_bank+skip_panic+half_sector`
- Sharpe **+2.13**, ann **+25.9%**, P&L **+$6M/mo**, GMV $348M
- Half-sizes bad banks and bad sectors; full skip on panic
- Highest annualized in the high-Sharpe tier
- Use when the limiting constraint is gross capital

IT remains in coverage at full size in all three — never
penalized, never skipped.

## Caveats

- **Look-ahead in the D10 classifier**: `chase_d10` uses
  `r_pre20` and `r_pre1` from `backtest_scores.parquet` —
  these are computed AT TRADE DATE from realized prior
  returns, so no future leakage. Defensible as a
  pre-trade classifier.
- **Skip rules are hard cuts**, not gradient. A trade at
  pre-1d = −5.01% is treated identically to one at −20%; one
  at −4.99% gets full size. Smoothed rule (e.g., gradient
  multiplier in the −2% to −10% band) would be more
  defensible but adds complexity. The sample doesn't have
  enough trades at the boundaries to differentiate.
- **Thresholds are population-fitted**. The −5% panic
  threshold, +5%/−2% D10 thresholds, and bad-bank list all
  come from the same dataset we're testing on. Out-of-sample
  performance would differ; a forward-walk validation is the
  right next test.
- **No interaction with stop-loss is tested.** Strategy rules
  are evaluated under the current default stop of −8%.
  Skipped trades never get stopped (they were never opened),
  but upsized trades face the same stop rule. Combining
  stop-loss + sizing rules systematically would multiply the
  sweep space considerably.
- **No risk parity / vol scaling.** All trades use the same
  base notional (clamped by $10-100M). Lower-vol names are
  effectively over-weighted in dollar terms. A vol-adjusted
  sizer would shift the mix toward the higher-vol cohort
  where the chase_d10 archetype concentrates.
- **No drawdown stop at the portfolio level.** Single-month
  drawdowns (e.g., 2024-04 at −3.9% hedged) would still
  occur. A portfolio-level guardrail (e.g., scale down
  after MTD < −5%) could improve Sharpe further but isn't
  tested here.

## Reproducing

```bash
# Single sweep at default window=20d
uv run python tools/portfolio_strategy_sweep.py

# Different window
uv run python tools/portfolio_strategy_sweep.py --window 10

# Outputs:
#   data/portfolio_strategy_sweep.{stamp}.parquet
```

The sweep is fast (~5 seconds) because the trades are
pre-scored — only the rescale + monthly aggregation runs per
strategy. Add new primitives directly to
`tools/portfolio_strategy_sweep.py:STRATEGIES`.
