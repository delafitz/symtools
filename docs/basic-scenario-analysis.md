# Basic Scenario Analysis

Realistic trading simulation over the full block trade
population. Builds on `basic-return-analysis.md` and
`block-alpha-drivers.md` by adding position sizing, entry/exit
mechanics, stop-loss, ex-ante predictions, and monthly
portfolio rollup.

## Methodology

### Position sizing

For each trade:

```
notional_$ = clip(pct_adv × adv_usd_30d, floor, cap)
```

| param | default |
|---|---|
| `pct_adv` | 0.15 |
| `floor` | $10M |
| `cap` | $100M |
| `var_cap_usd` | **$50M** (soft cap on hedged 99% VaR @ 20d) |
| `deal_pct` | **0.30** (max position as fraction of deal size) |

ADV is the 30-day trailing average dollar volume at trade
date, taken from `backtest_trades.parquet`. With these defaults
the average target notional is **$20.5M** (median $13.5M; ~40%
of trades hit the floor for small-ADV names, ~1% hit the
$100M cap; ~4% are clipped by the 30%-of-deal cap).

The **$50M VaR cap** is a soft risk-mgmt guardrail. It clips
notional further only on positions whose implied hedged
20d VaR exceeds $50M — typically extreme-vol names
(vol_90d > 90%). On the cleaned population the cap is
nearly inactive; tightening below $30M starts to materially
erode return.

The **30% deal-size cap** reflects that you can't take more
of the block than the broker is selling. It binds on ~12
trades in the current population (notably RDDT at 79%
pre-cap, WBD at 75%, PM at 60%).

The sweep tool (see `tools/portfolio_sizing_sweep.py`) shows the
strategy is capacity-insensitive across a 4× notional range —
annualized return stays near ~11% from `pct_adv=0.10` through
`pct_adv=0.40` — so the default is set on the small end for
realism rather than max scale.

The $50M default is a soft guardrail — clips only the 2 most
extreme-vol positions (rddt 2024-11-22 vol 93%, app 2024-11-22
vol 98%) and costs ~20bps annualized. Useful as a hard risk
limit for capital allocation or regulatory single-name
concentration — *not* a return-improvement lever.

### Trade mechanics

- **Target leg entry**: full position at `offer_price` on T0.
- **Hedge leg entry**: short `β × hedge_ratio × notional` of
  combined basket, executed at basket close on T0.
- **Planned exit (both legs)**: ramp 1/3 per day at close on
  T+(w−2), T+(w−1), T+w. The buyer doesn't dump in one print
  to avoid impact.
- **Stop loss**: triggers when the **daily-marked hedged
  P&L** falls to `stop_pct × notional`. Both legs liquidate at
  next day's close. Default `stop_pct = −8%` on a hedged
  basis. Hedged-P&L basis (vs. target-price basis) means the
  stop only fires when the hedge has *also* failed to protect
  the position — see `stop-loss-analysis.md` for the full
  sweep + comparison.
- **Hedge ratio**: **0.60 × β** (portfolio-Sharpe optimum from
  the hedge-ratio sweep below). Earlier 0.85 came from a
  single-trade min-var analysis (`block-alpha-drivers.md`) —
  the portfolio-aware sweep finds 0.60 is the Pareto best on
  the cleaned 296-trade population. Note: this is the
  *average*-optimum; sparse months (avg < 5 open positions)
  prefer heavier hedging — see hedge-ratio sweep below.
- **Transaction costs**: 10 bps per execution side, applied
  on all four sides (target entry, target exit, hedge entry,
  hedge exit) → 40 bps round-trip on gross. P&L is reported
  *net* of costs throughout.

### Tradeout windows

Tested at 5, 10, 20 trading days. Each window is scored
independently as a separate strategy.

### Ex-ante expected return + VaR

Both are computed at entry using only data available before T0.

**Expected return (v0 placeholder)**:
```
E[r_hedged] = E[r_unhedged] = −actual_discount
```
The buyer's edge at offer entry, *assuming* the target reverts
fully to pre-block close. We know empirically this overshoots
the realized hedged P&L by ~2x (the post-trade drift consumes
half the discount cushion). A regression/decile-conditional
predictor is the natural next iteration.

**99% VaR (parametric, with daily time decay)**:

At entry, the **horizon VaR** for a position is:
```
VaR_unhedged_entry = notional × σ_daily × √window × z_99
VaR_hedged_entry   = notional × σ_daily × √(1−ρ²) × √window × z_99
```
- `σ_daily = vol_90d / (√252 × 100)` from target's trailing vol
- `ρ` = combined basket-target correlation
- `z_99 = 2.326`

On each holding day, the VaR contribution decays by
`√(remaining_days / window)`. On day 1 (remaining=window) the
position carries 100% of horizon VaR; on the last day
(remaining=1) it carries `1/√window` of it. The portfolio's
**avg daily VaR** is the mean across open-position-days of
these decayed values. For a position that runs the full
window the average comes out to ~67% of horizon VaR.

### Portfolio aggregation

- **Independent positions**: each trade gets its own
  $10-100M sizing. No global capital base in v0.
- **Gross GMV**: each position contributes
  `long_notional + |hedge_notional|` to GMV — both the long
  target and the short basket leg count, since both consume
  buying power. Average hedge notional is `β × hedge_ratio ×
  long_notional` ≈ 0.77 × long.
- **Daily exposure expansion**: each position contributes
  full gross notional to GMV each trading day from T+1
  through the exit date (slight overstatement during the
  3-day ramp).
- **VaR aggregation** — two values reported:
  1. *sum-of-VaRs* (ρ=1): Σ single-position hedged VaRs.
     Conservative ceiling assuming all positions move together.
  2. *portfolio VaR* with constant pairwise correlation ρ:
     `√((1-ρ)·Σvᵢ² + ρ·(Σvᵢ)²)`. Default **ρ=0.3** as the
     headline; **ρ=0.1 and ρ=0.5 reported as a band**. Hedged
     residuals are theoretically near-orthogonal after the
     basket removes common factor exposure; ρ=0.3 leaves
     headroom for stress regimes where residual correlations
     spike.
- **Monthly rollup**:
  - `avg_daily_positions` = mean count of open positions per
    trading day
  - `avg_daily_gmv` = mean gross GMV per trading day
  - `avg_daily_var_hedged/unhedged` = mean summed VaR
  - `pnl_*` = sum of realized P&L from positions that exited
    in the month
  - `ret = pnl / avg_daily_gmv` (return on gross)
  - `annualized = ret × 12` (simple monthly annualization)

## Results — all blocks

n=296 hedgeable trades across 28-29 months (Jan-2024 → Apr-2026
on the curated alt block-trades dataset). Sub-$100M deals and
xADV>30 outliers are filtered (see `tools/backtest.py` for the
MIN_DEAL_SIZE / MAX_XADV thresholds). Positions are capped at
30% of deal size (you can't take more of the block than the
broker is selling).

### Summary feature — Population Returns

Headline life-cycle of a block from T−1 (the day before the
print) through T+20 (final exit). Raw and hedged means and
medians at each horizon.

| stat | T−1 | T (disc) | T+1 | T+5 | T+10 | T+20 |
|---|---|---|---|---|---|---|
| raw mean | −1.66% | **−2.85%** | +0.04% | −0.07% | +0.98% | **+1.58%** |
| raw median | −1.24% | −2.52% | −0.03% | −0.08% | +0.94% | +1.40% |
| hedged mean | −1.72% | — | −0.04% | −0.16% | +0.72% | **+0.82%** |
| hedged median | −1.24% | — | −0.16% | −0.21% | +0.56% | +0.32% |

*T−1 = close-to-close raw return one day before trade; T =
discount (offer_price / pre_close − 1); T+N = forward return
from offer_price over N trading days. Hedged uses 0.60 × β
basket short, combined scenario.*

**Reading the table**:
- **Sellers exploit pre-trade strength**: typical stock drops
  −1.7% into the print (median), gets sold at a −2.5% discount
  to that already-weakened close.
- **Recovery is gradual**: T+1 / T+5 effectively flat. The
  alpha builds T+8 → T+15 (mean peaks in this zone, see
  daily-path data in `backtest_scores.parquet`).
- **Mean-median gap at T+20 hedged** (+0.82% vs +0.32%):
  right-tail-driven strategy. The median trade gives back
  most of the discount cushion after the hedge; the mean is
  positive because the right tail compensates.

### Monthly rollup (window=20d)

Headline numbers across 29 months (net of 10 bps × 4 sides
transaction costs, with hedged-P&L stop at −8%, $50M VaR cap,
30% deal-size cap, hedge ratio 0.60, portfolio VaR at ρ=0.3):

| metric | value |
|---|---|
| **avg daily gross GMV** | **$330M** |
| avg daily sum-of-VaRs (hedged, ρ=1) | $31.1M |
| avg daily portfolio VaR (ρ=0.30) | **$21.0M** |
| portfolio VaR / gross GMV | 6.4% |
| diversification benefit at ρ=0.3 | ~33% of sum-of-VaR |
| avg monthly P&L hedged | **+$2.6M** |
| avg monthly ret on gross | +1.30% |
| **annualized return on gross** | **+13.9%** |
| Sharpe (hedged, annualized) | **+1.52** |
| n_stops triggered (20d) | ~52 |

**Diversification band**: at ρ=0.1 (highly orthogonal
hedged residuals) portfolio VaR is **$16.9M (54% of sum)**;
at ρ=0.5 (correlated stress regime) it is **$24.4M (78% of
sum)**. The default ρ=0.3 sits between these as the
conservative-realistic middle.

### Hedge ratio sweep (portfolio level)

Sharpe across hedge ratios at 20d window. Sharpe peaks in
[0.60, 0.70] band; the prior 0.85 default is on the wrong
side of the curve.

| hr | avg GMV | sum VaR | PnL hedged | ann hedged | **Sharpe_h** |
|---|---|---|---|---|---|
| 0.00 (no hedge) | $200M | $30M | +$79M | +15.5% | +0.73 |
| 0.30 | $267M | $31M | +$89M | +17.1% | +1.26 |
| 0.50 | $308M | $31M | +$75M | +14.3% | +1.37 |
| **0.60** (default) | $330M | $31M | **+$75M** | **+13.9%** | **+1.52** |
| 0.65 | $340M | $31M | +$72M | +13.3% | +1.53 |
| 0.70 | $350M | $31M | +$67M | +12.9% | **+1.54** ← peak |
| 0.75 | $360M | $31M | +$60M | +12.0% | +1.48 |
| 0.85 (prior) | $378M | $31M | +$53M | +11.4% | +1.48 |
| 1.00 | $401M | $31M | +$40M | +10.8% | +1.35 |
| 1.20 | $436M | $31M | +$39M | +10.9% | +1.30 |

**0.60 vs 0.85**: +0.04 Sharpe, +2.5pp ann, +$22M total PnL,
−13% GMV. The earlier 0.85 calibration came from a
*per-trade* min-var bin analysis (`block-alpha-drivers.md`)
that doesn't account for cross-position diversification.
At the portfolio level the lighter hedge keeps the alpha
that the per-trade analysis was suppressing.

### Hedge ratio × portfolio breadth

The optimum hedge ratio varies with the count of
simultaneous open positions. Comparing hr=0.60 vs 0.85
monthly Sharpe by portfolio breadth bucket:

| bucket | months | avg pos | PnL_60 | PnL_85 | std_60 | std_85 | Sharpe_60 | Sharpe_85 |
|---|---|---|---|---|---|---|---|---|
| **sparse** (<5) | 7 | 3.3 | +$5.0M | **+$10.6M** | 3.77% | 3.05% | +1.85 | **+2.79** |
| medium (5-10) | 13 | 7.9 | **+$51.1M** | +$34.2M | 2.33% | 1.75% | **+1.66** | +1.26 |
| dense (≥10) | 9 | 14.9 | **+$19.2M** | +$8.2M | 2.14% | 1.76% | **+0.90** | +0.45 |

**Sparse months want more hedge** — no diversification cushion
means single-name residual vol pumps through to portfolio
vol. Medium/dense months benefit from the diversification
math and prefer lighter hedging. A breadth-aware dynamic
hedge ratio (use 0.85 when pos<5, 0.60 otherwise) would
recover ~$5M from sparse-month underperformance without
giving up the medium/dense win. Not implemented yet —
listed as future work.

By window (GMV is **gross** = long target + |short basket
hedge|; P&L is net of 40 bps round-trip costs; hedged-P&L
stop at −8%):

| window | avg trade size | avg daily pos | avg daily gross GMV | peak daily gross GMV | avg daily VaR (hed) | VaR/Gross | avg monthly P&L hedged | avg monthly ret | annualized |
|---|---|---|---|---|---|---|---|---|---|
| 5d | $23.9M | 2.9 | $108M | ~$505M | $6M | 5.3% | −$1.4M | −1.44% | **−17.3%** |
| 10d | $23.9M | 5.0 | $182M | ~$707M | $13M | 7.0% | +$0.7M | +0.56% | **+6.7%** |
| **20d** | $23.9M | **9.0** | **$330M** | **~$941M** | **$31M** | **9.4%** | **+$2.6M** | **+1.16%** | **+13.9%** |

Avg trade size (target leg) is ~$43M and avg hedge notional
is ~$34M, so each position runs **~$77M gross** ($43M long
+ $34M short basket). Longer windows have more positions
open at once (8.5 at 20d vs 3.1 at 5d) → both legs scale,
gross GMV at 20d averages **$651M**.

**Hedge benefit on VaR**: avg ρ across the population is
~0.65, so single-position hedged VaR is `√(1−ρ²) = 0.76` of
unhedged. The table reflects this: hedged VaR is ~24% lower
than unhedged at every horizon. **No cross-position
correlation diversification is applied** — VaR is summed
across positions as if each were independent worst-cases,
which is the conservative bound. Real portfolio VaR would
be lower after factoring in pairwise correlation across the
single-name residuals; that's a follow-up.

VaR is computed daily with **time-to-exit decay**: a position
on its first holding day carries `σ × √window` × notional of
risk; on its last day it carries `σ × √1` × notional. The avg
daily VaR shown is the mean across all open-position days,
weighting longer-remaining positions more heavily. For a
position that runs the full window with no stop, this
averages to ~67% of the entry-time horizon VaR.

**Return on gross**: with gross GMV included, the 20d
annualized hedged return is **+13.5%** on $651M of average
gross exposure (vs +24% on long-only $370M). The Sharpe is
unchanged at **+1.23** since both the return numerator and
the GMV denominator scale by the same factor.

### Monthly detail (20d, gross GMV)

Selected months showing realized vs expected divergence
(GMV = long + |hedge|, returns on gross):

| month | n_pos | gross GMV | VaR hed | V/G | PnL hed | exp PnL | ret hed | annual hed |
|---|---|---|---|---|---|---|---|---|
| 2024-04 | 8.7 | $824M | $38M | 4.6% | **+$19.2M** | +$23.9M | +2.33% | +28.0% |
| 2024-12 | 10.6 | $810M | $99M | 12.2% | **−$10.9M** | +$22.9M | −1.35% | −16.2% |
| 2025-01 | 1.4 | $146M | $19M | 13.2% | **+$13.2M** | +$2.0M | +9.04% | +108.5% |
| 2025-09 | 14.4 | $887M | $71M | 8.0% | **+$69.4M** | +$17.9M | +7.82% | +93.9% |
| 2025-11 | 8.8 | $768M | $63M | 8.2% | **−$4.1M** | +$23.0M | −0.54% | −6.4% |

## Findings

1. **20d hedged Sharpe ≈ 1.2 (annualized) with ~$370M GMV.**
   Real signal at meaningful scale. Path is choppy — about
   one-third of months are negative — but the average is
   solidly positive.

2. **5d window is unprofitable.** Hedged P&L is −0.75%
   per month, annualized −9%. Window is too short to capture
   the post-print drift recovery; you're stopped out or sold
   into the worst of the drift before the rebound.

3. **10d is a reasonable middle ground** (+12.9% annualized,
   Sharpe 1.18) but 20d dominates on every metric.

4. **Realized hedged P&L is ~50% of discount-based expected.**
   Predictor `E[r] = −discount` averages +2.84%; realized at
   20d averages +1.35%. Confirms the per-trade finding that
   post-trade drift eats half the discount cushion. This is
   the v0 baseline — a conditional / decile-based predictor
   should narrow the gap.

5. **Predictor under-shoots big winners.** When a month
   realizes +14% (e.g., 2025-09), the predictor was at +3.6%.
   The unconditional `−discount` predictor cannot capture
   right-tail months driven by D10-style trades.

6. **VaR / GMV ratio runs 13-31% depending on
   diversification.** Months with fewer positions have
   concentrated risk. 2025-09 hit 30% with 14 positions; 2024-04
   was 15% with 9. More positions → more diversification.

7. **Stop-loss triggers scale with window.** 14 stops at 5d,
   53 at 10d, 87 at 20d. Whether the stop is net-positive on
   P&L isn't yet measured — easy follow-up.

## Caveats

- **Predictor is unconditional.** `E[r] = −discount` doesn't
  use any of the discriminators we found (pre 1d / pre 20d
  flow, sector, bank). A conditional predictor would shrink
  the realized-vs-expected gap and produce a more useful
  scorecard.
- **Hedge execution is idealized.** Assumes the basket trades
  at exact close on T0 with no slippage and no transaction
  cost. Real-world hedge-leg implementation would lose 5-20bps
  per trade.
- **Ramp exit ignores impact.** Selling 1/3 per day at close
  is more realistic than a single dump, but we don't model
  market impact of the seller's own exit.
- **VaR is parametric.** Assumes normal returns and stable
  σ, ρ. Likely understates fat tails — months like 2024-12
  show realized worse than parametric VaR would have implied.
- **Avg daily GMV uses simple ramp.** Position is at full
  notional from T+1 through exit_date — a ~5% overstatement
  during the 3-day ramp window. Doesn't materially affect
  return ratios.
- **Independent positions, no shared capital.** v0
  assumption. Real portfolio would have a capital base that
  caps deployment, possibly skipping trades when overlimit.
  When added, expect avg_daily_GMV to compress and
  return-on-capital to clarify.
- **Survivorship + data-quality filters.** Source file
  has 440 deals. The drops break down as:
  - **59 deals / $21.5B notional** dropped at the refs
    layer (ticker not in current refs). This splits into
    three distinct categories — only the last two are
    actual survivorship:
    - **Foreign ADRs (19 deals / $12.7B = 59% of dropped
      notional)**: Chinese (JD $3.6B, FUTU $1.7B, TCOM
      $1.2B, BILI, BEKE, GDS, XPEV), Brazilian (LTM $3.7B —
      LATAM Airlines, 6 deals), and others. These are a
      *structural* refs filter, not survivorship.
    - **Known M&A / private completions (16 deals / $3.3B)**:
      ALIT, CYBR, DNB, PYCR, MLNK, CIVI, PTLO, ODD, OS.
      True survivorship — these existed at trade time but
      are gone now.
    - **Small / delisted (24 deals / $5.5B)**: CCCS (8 deals,
      $2.7B), FWRG, QDEL, BMBL, etc. Mostly fell below the
      $1B mkt-cap floor or were quietly delisted.
  - **35 deals** dropped by block_trades sanity filters
    (no pre-close, premium prices, |discount| > 15%).
  - **33 deals** dropped by `MIN_DEAL_SIZE` ($100M floor —
    many are clear data errors with `shares` mis-recorded
    by 10× / 100×).
  - **2 deals** exceed `MAX_XADV` (CMPR 56×, IAUX 51×).

  **True survivorship is ~5-6% of source notional** (the
  M&A + small/delisted cohorts = $8.8B / $164B), not the
  13.4% headline number from `survivorship_check.py`.
  Foreign-ADR exclusion is a structural design choice and
  shouldn't be counted as survivorship. 296 trades remain
  at the 20d window after all filters.
- **P&L concentration in right-tail outliers.** Top 10
  trades = 104% of total P&L (top 20 = 141%). Removing top
  10 collapses Sharpe from 1.61 → ~0.4. Half of trades have
  *negative* hedged return (median −0.09%); the strategy
  works through right-tail wins, not breadth. A practical
  trader would not see consistent monthly P&L — 1 in 3
  months is negative.
- **Tradeout windows are mechanical.** Real exits respond to
  the position's P&L path; we don't trail stops or harvest at
  intermediate targets.
- **Annualization is simple ×12.** Compounding makes
  practically no difference at this scale but for very high
  monthly returns the difference grows.

## Reproducing

```bash
# Prereq: backtest scores exist (see basic-return-analysis.md)
uv run python tools/portfolio_backtest.py --mode all
uv run python tools/portfolio_rolling.py --window 20
uv run python tools/portfolio_rolling.py --window 10
uv run python tools/portfolio_rolling.py --window 5
```

Strategy and random samples:

```bash
uv run python tools/portfolio_backtest.py --mode strategy --strategy d10
uv run python tools/portfolio_backtest.py --mode random --n 100
```

Sensitivity sweeps:

```bash
uv run python tools/portfolio_backtest.py --pct-adv 0.4 --stop -0.10
```

## Code layout

```
app/services/portfolio/
  sizer.py       SizeParams, size_position
  position.py    score_position with ramp + stop
  expected.py    compute_expected (discount-based E[r] + VaR99)
  sampler.py     all / random / strategy filters
  aggregator.py  entry-date monthly rollup
  rolling.py     daily expansion + trading-day monthly rollup
tools/
  portfolio_backtest.py    orchestrator, writes parquets
  portfolio_rolling.py     reads parquet, prints rolling view
```
