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

ADV is the 30-day trailing average dollar volume at trade
date, taken from `backtest_trades.parquet`. With these defaults
the average target notional is **$26.5M** (median $13.9M; ~39%
of trades hit the floor for small-ADV names, ~7% hit the cap).

The sweep tool (see `tools/portfolio_sizing_sweep.py`) shows the
strategy is capacity-insensitive across a 4× notional range —
annualized return stays at ~15% from `pct_adv=0.10` through
`pct_adv=0.40` — so the default is set on the small end for
realism rather than max scale.

### Trade mechanics

- **Target leg entry**: full position at `offer_price` on T0.
- **Hedge leg entry**: short `β × hedge_ratio × notional` of
  combined basket, executed at basket close on T0.
- **Planned exit (both legs)**: ramp 1/3 per day at close on
  T+(w−2), T+(w−1), T+w. The buyer doesn't dump in one print
  to avoid impact.
- **Stop loss**: if target close ≤ `offer × (1 − stop_pct)` on
  any day before the ramp completes, exit *all remaining*
  position (both legs) at next day's close. Default is **−8%**
  (compromise between drawdown protection and recovery
  capture). See `stop-loss-analysis.md` for the full sweep
  comparison.
- **Hedge ratio**: 0.85 × β (haircut from the regime-break
  calibration in `block-alpha-drivers.md`).
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
  $20-100M sizing. No global capital base in v0.
- **Gross GMV**: each position contributes
  `long_notional + |hedge_notional|` to GMV — both the long
  target and the short basket leg count, since both consume
  buying power. Average hedge notional is `β × hedge_ratio ×
  long_notional` ≈ 0.77 × long.
- **Daily exposure expansion**: each position contributes
  full gross notional to GMV each trading day from T+1
  through the exit date (slight overstatement during the
  3-day ramp).
- **VaR aggregation**: each position's hedged VaR is summed
  across open positions for the day. No cross-position
  diversification is applied; portfolio VaR is the
  conservative independent-positions bound.
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

n=331 hedgeable trades across 28-29 months (Jan-2024 → Apr-2026
on the curated alt block-trades dataset).

### Per-trade summary

| window | n | avg notional | avg ret unhedged | avg ret hedged | avg exp hedged | hedged hit | n stops |
|---|---|---|---|---|---|---|---|
| 5d | 331 | $43M | +0.07% | −0.03% | +2.84% | 49% | 14 |
| 10d | 331 | $43M | +0.93% | +0.74% | +2.84% | 51% | 53 |
| 20d | 327 | $43M | +1.62% | +1.35% | +2.83% | 50% | 87 |

Notional averages $43M — between the $20M floor and $100M cap,
so the sizer is rarely clipping. ADV-scaled is the binding cut.

### Monthly rollup (window=20d)

Headline numbers across 29 months (net of 10 bps × 4 sides
transaction costs):

| metric | value |
|---|---|
| avg trade size (target) | $26.5M |
| avg daily long GMV | $244M |
| avg daily hedge GMV | $192M |
| **avg daily gross GMV** | **$436M** |
| peak daily gross GMV | ~$1.1B |
| avg daily VaR (hedged) | $37M |
| VaR / Gross | 8.4% |
| avg monthly P&L hedged | **+$4.8M** |
| avg monthly ret on gross | +1.21% |
| **annualized return on gross** | **+14.5%** |
| Sharpe (hedged, annualized) | **+1.61** |
| total transaction cost over 29mo | $31M (18% of gross P&L) |

By window (GMV is **gross** = long target + |short basket
hedge|; P&L is net of 40 bps round-trip costs):

| window | avg trade size | avg daily pos | avg daily gross GMV | peak daily gross GMV | avg daily VaR (hed) | VaR/Gross | avg monthly P&L hedged | avg monthly ret | annualized |
|---|---|---|---|---|---|---|---|---|---|
| 5d | $26.5M | 3.1 | $159M | ~$510M | $7M | 4.7% | −$1.0M | −0.61% | **−7.3%** |
| 10d | $26.5M | 5.0 | $258M | ~$795M | $16M | 6.1% | +$1.6M | +0.40% | **+4.8%** |
| **20d** | $26.5M | **8.5** | **$436M** | **$1.37B** | **$37M** | **8.4%** | **+$4.8M** | **+1.21%** | **+14.5%** |

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
- **Survivorship bias.** The source file holds 440 deals; 59
  trades (13.4%, $21.5B notional) are dropped because their
  tickers are not in our current refs universe. These are
  mostly M&A targets (CIVI, DNB, BMBL), foreign ADRs we
  filter out (BILI, BEKE, JD, FUTU), and small-caps below
  our mkt_cap threshold. Run `tools/survivorship_check.py`
  to enumerate. The remaining 327 trades are implicitly
  conditioned on surviving to today; outcomes on the
  dropped cohort are unknown.
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
