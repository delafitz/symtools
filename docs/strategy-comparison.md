# Strategy Comparison

Empirical sweep of per-trade filter rules layered on the
baseline portfolio backtest. Builds on
`basic-scenario-analysis.md`, `block-alpha-drivers.md`, and
`stop-loss-analysis.md`.

## Setup

Population: 327 hedgeable trades from the alt dataset, 20d
window. Sizing: pct_adv=0.15, floor=$10M, cap=$100M, hedge
ratio=0.85, stop=−8%, cost=10 bps × 4 sides (40 bps
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

## Sweep results (window=20d, net of 10 bps × 4 sides costs)

| strategy | n | avg_GMV | avg_VaR | **PnL_mo** | mo_ret | **ann_ret** | **sharpe_h** |
|---|---|---|---|---|---|---|---|
| baseline | 327 | $436M | $37M | +$5M | +1.21% | +14.5% | +1.61 |
| half_bad_bank | 327 | $330M | $29M | +$4M | +1.36% | +16.3% | +1.57 |
| skip_bad_bank | 156 | $228M | $21M | +$4M | +1.62% | +19.4% | +1.39 |
| skip_panic | 278 | $406M | $33M | +$5M | +1.39% | +16.6% | **+1.77** |
| skip_deep_disc | 287 | $388M | $31M | +$3M | +0.89% | +10.7% | +1.34 |
| skip_high_xadv | 206 | $352M | $31M | +$5M | +1.25% | +15.0% | +1.40 |
| chase_d10 | 327 | $499M | $43M | **+$7M** | +1.44% | +17.3% | +1.71 |
| half_bank+skip_panic | 278 | $307M | $25M | +$4M | +1.57% | +18.8% | +1.76 |
| **chase_d10+skip_panic** | **278** | **$471M** | **$39M** | **+$7M** | **+1.61%** | **+19.3%** | **+1.83** |
| chase_d10+half_bad_bank | 327 | $377M | $33M | +$6M | +1.57% | +18.8% | +1.64 |
| **chase_d10+half_bank+skip_panic** | **278** | **$355M** | **$30M** | **+$6M** | **+1.77%** | **+21.2%** | **+1.81** |
| chase_d10+skip_bad_bank | 156 | $258M | $24M | +$5M | +1.78% | +21.4% | +1.44 |
| all_skips | 73 | $176M | $14M | +$3M | +1.40% | +16.7% | +1.07 |

## Findings

### Three rules that work cleanly

1. **`skip_panic` is the best single filter.** Cutting 49
   trades with pre-1d ≤ −5% lifts Sharpe **1.92 → 2.06**
   while preserving the $6M/mo P&L. The D1-archetype
   "forced-seller into the print" cohort destroys value
   without contributing. Pure noise reduction.

2. **`chase_d10` is the best size-up rule.** Upsizing 56
   D10-archetype trades (strong pre-20d run-up + mild pre-1d
   drawdown) by 1.5× lifts monthly P&L **$6M → $8M (+33%)**
   with Sharpe still strong at 1.98. Captures right-tail
   alpha without any skips.

3. **Combining the two is the headline result.**
   `chase_d10+skip_panic` hits **Sharpe 2.08** (best in
   sweep) on **$8M/mo**, **+22.4% annualized**. Filtering
   the worst pre-trade signal AND upsizing the best gives
   the cleanest single play.

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

- baseline: $6M/mo, Sharpe 1.92
- skip_panic only: $6M/mo, Sharpe 2.06 (+0.14 Sharpe)
- chase_d10 only: $8M/mo, Sharpe 1.98 (+0.06 Sharpe but +$2M/mo)

The right tail of the return distribution carries the
strategy. Skipping the left tail tightens variance but
doesn't add P&L; upsizing the right tail adds P&L (and a
little variance) directly. Both rules complement: skip the
losers AND upsize the winners.

## Recommended bundles

Two clean choices depending on objective:

### Risk-adjusted optimum: `chase_d10+skip_panic`
- **Sharpe 1.83** (best in sweep)
- Annualized **+19.3%** hedged
- Avg daily GMV **$471M** (close to baseline)
- Monthly P&L **+$7M** (baseline level + chase_d10 upsize)
- 278 trades (drops 49 panic-day trades; upsizes 56 D10 to 1.5×)

### Capital-efficient at scale: `chase_d10+half_bank+skip_panic`
- Sharpe **+1.81**
- Annualized **+21.2%** hedged (highest of the high-Sharpe set)
- Avg daily GMV **$355M** (18% smaller than baseline)
- Monthly P&L **+$6M**
- Same 278 trades plus 0.5× on 140 bad-bank trades

The second uses less gross capital for slightly less P&L
($7M vs $8M/mo), so return-on-gross is higher. Choose based
on whether the limiting constraint is capital (use second)
or P&L scale (use first).

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
  base notional (clamped by $20-100M). Lower-vol names are
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
