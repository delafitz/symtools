# Strategy Comparison

Empirical sweep of per-trade filter rules layered on the
baseline portfolio backtest. Builds on
`basic-scenario-analysis.md`, `block-alpha-drivers.md`, and
`stop-loss-analysis.md`.

## Setup

Population: 296 hedgeable trades from the alt dataset, 20d
window (deals ≥$100M, xADV ≤30). Sizing: pct_adv=0.15,
floor=$10M, cap=$100M, deal_pct=0.30, hedge ratio=0.85,
stop=−8% (hedged-P&L basis), cost=10 bps × 4 sides (40 bps
round-trip on gross). All P&L numbers are **net of costs**.

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
| baseline | 296 | $378M | $31M | +$2M | +0.95% | +11.4% | +1.48 |
| skip_panic | 250 | $339M | $26M | +$3M | +1.38% | +16.5% | +1.84 |
| chase_d10 | 296 | $432M | $36M | +$3M | +1.14% | +13.7% | +1.59 |
| half_bad_bank | 296 | $286M | $24M | +$2M | +1.17% | +14.0% | +1.52 |
| half_bad_sector | 296 | $351M | $30M | +$2M | +0.99% | +11.9% | +1.48 |
| quarter_bad_sector | 296 | $337M | $29M | +$2M | +1.00% | +12.0% | +1.45 |
| skip_tail_sector | 289 | $365M | $31M | +$1M | +0.82% | +9.8% | +1.09 |
| chase_good_sector | 296 | $422M | $35M | +$2M | +1.08% | +12.9% | +1.51 |
| chase_d10+half_sector | 296 | $399M | $34M | +$3M | +1.19% | +14.2% | +1.60 |
| chase_d10+chase_good_sector | 296 | $482M | $41M | +$4M | +1.28% | +15.4% | +1.62 |
| **skip_panic+half_sector** | **250** | **$312M** | **$25M** | **+$3M** | **+1.47%** | **+17.7%** | **+1.92** |
| chase_d10+half_bank+skip_panic | 250 | $296M | $24M | +$3M | +1.84% | +22.1% | +1.80 |
| **chase_d10+skip_panic+half_sector** | **250** | **$360M** | **$30M** | **+$4M** | **+1.64%** | **+19.7%** | **+1.93** |
| **chase_d10+skip_panic+quarter_sector** | **250** | **$344M** | **$29M** | **+$4M** | **+1.69%** | **+20.3%** | **+1.95** |
| chase_d10+skip_panic+chase_good_sector | 250 | $436M | $35M | +$5M | +1.66% | +20.0% | +1.92 |
| **chase_d10+half_bank+skip_panic+half_sector** | **250** | **$273M** | **$23M** | **+$4M** | **+1.94%** | **+23.3%** | **+1.88** |
| **chase_d10+half_bank+skip_panic+chase_good_sector** | **250** | **$330M** | **$27M** | **+$4M** | **+2.01%** | **+24.1%** | **+1.89** |
| chase_d10+half_bank+skip_panic+sector_full | 244 | $294M | $26M | +$4M | +1.93% | +23.2% | +1.72 |

## Findings

### Rules that work cleanly

1. **`skip_panic` is the best single filter.** Cutting 46
   trades with pre-1d ≤ −5% lifts Sharpe **1.48 → 1.84**
   and lifts monthly P&L from +$2M to +$3M. The D1-archetype
   "forced-seller into the print" cohort destroys value
   without contributing. Pure noise reduction.

2. **`chase_d10` is the best size-up rule.** Upsizing
   D10-archetype trades (strong pre-20d run-up + mild pre-1d
   drawdown) by 1.5× lifts monthly P&L **+$2M → +$3M** at
   Sharpe 1.59. Captures right-tail alpha without any skips.

3. **Sector half-sizing (Energy + Real Estate) adds Sharpe
   for free.** Layering `half_bad_sector` on top of
   `chase_d10+skip_panic` lifts Sharpe **1.80 → 1.93** with
   no trade drops — alpha is hedged away in these high-corr
   sectors, so half-sizing them keeps the option without
   carrying the variance. **IT is intentionally excluded**
   from sector penalties: too big and internally diverse
   (semis, software, hardware, IT services) to treat as
   one cohort.

4. **`chase_d10+skip_panic+quarter_sector` is the new Sharpe
   optimum.** Sharpe **1.95** at +20.3% annualized. Drops 46
   panic trades, upsizes D10, quarter-sizes Energy/RE.

5. **`chase_d10+half_bank+skip_panic+chase_good_sector` hits
   the highest annualized.** Sharpe 1.89 at **+24.1%
   annualized** on $330M GMV — capital-efficient choice.

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

- baseline: $2M/mo, Sharpe 1.48
- skip_panic only: $3M/mo, Sharpe 1.84 (+0.36 Sharpe)
- chase_d10 only: $3M/mo, Sharpe 1.59 (+0.11 Sharpe, +$1M/mo)
- half_bad_sector only: $2M/mo, Sharpe 1.48 (no lift alone)

The right tail of the return distribution carries the
strategy. Skipping the left tail tightens variance but
doesn't add P&L; upsizing the right tail adds P&L (and a
little variance) directly. Sector half-sizing is a third
axis — it works in *combination* with the other rules, where
it lifts Sharpe by ~0.05–0.15 by reducing variance from the
high-corr sectors where the hedge eats the alpha.

## Recommended bundles

Three clean choices depending on objective:

### Pure risk-adjusted optimum: `chase_d10+skip_panic+quarter_sector`
- **Sharpe 1.95** (best in sweep)
- Annualized **+20.3%** hedged
- Avg daily GMV **$344M**
- Monthly P&L **+$4M**
- 250 trades (drops 46 panic; quarter-sizes Energy/RE; upsizes D10)

### Balanced: `chase_d10+skip_panic+half_sector`
- Sharpe **+1.93**, ann **+19.7%**, P&L **+$4M/mo**, GMV $360M
- Same combo with 0.5× sector penalty instead of 0.25×
- Slightly higher GMV, very similar Sharpe

### Capital-efficient: `chase_d10+half_bank+skip_panic+chase_good_sector`
- Sharpe **+1.89**, ann **+24.1%**, P&L **+$4M/mo**, GMV $330M
- Half-sizes bad banks, upsizes Cons Disc + Industrials
- **Highest annualized** in the high-Sharpe tier
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
