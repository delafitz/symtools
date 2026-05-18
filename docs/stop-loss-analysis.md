# Stop-Loss Analysis

Empirical study of stop-loss thresholds for the block-trade
strategy. Builds on `basic-scenario-analysis.md`.

## Setup

Population: all 327 hedgeable trades from the alt dataset at
the 20d window. Sizing: pct_adv=0.15, floor=$10M, cap=$100M.
Hedge ratio: 0.85 × β.

**Stop basis: hedged P&L** (current default). On each daily
close, we compute the marked net P&L:
```
net_ret_d = (shares × (close_d − offer) − hedge_notional × (basket_d/basket_T − 1)) / notional
```
If `net_ret_d ≤ stop_pct`, the position exits both legs at the
next day's close. This is the trigger a real hedged book would
respond to — the stop fires only when the hedge has failed to
protect the position. Setting `stop_basis='target'` reverts to
the older target-close-only convention; the sweep below shows
the new hedged-basis behavior.

## Sweep results (hedged-P&L basis, net of 40 bps round-trip)

| stop | n_stops | avg GMV | unh mo P&L | hed mo P&L | ann unh | **ann hed** | sharpe_u | **sharpe_h** |
|---|---|---|---|---|---|---|---|---|
| none | 0 | $515M | +$9.4M | +$6.1M | +13.4% | +12.7% | +0.76 | +1.14 |
| **−5%** | 106 | $418M | +$7.8M | +$6.0M | +17.9% | **+20.3%** | +1.33 | **+2.12** |
| −7% | 73 | $453M | +$7.3M | +$5.2M | +13.5% | +16.1% | +0.89 | +1.82 |
| **−8% (default)** | 57 | $466M | +$7.9M | +$5.4M | +14.2% | **+15.9%** | +0.95 | **+1.88** |
| −10% | 37 | $483M | +$8.4M | +$5.5M | +15.0% | +16.1% | +1.07 | +1.88 |
| −15% | 13 | $505M | +$9.2M | +$6.0M | +15.4% | +15.9% | +1.10 | +2.00 |

**Important reframe with hedged-basis stops**: a −5% stop on
the hedged basis only fires when the *net* position is down
5% — a substantially less aggressive trigger than a −5% stop
on the target leg alone (which would fire on a 5% target
drop even if the hedge fully protected). Under the hedged
basis, **−5% becomes the Sharpe-optimal level** (+2.12) with
the highest annualized hedged return (+20.3%).

The current default of −8% is conservative-but-defensible
(Sharpe 1.88, ann 15.9%); the sweep suggests **−5% hedged
might be the better setting**. We're keeping −8% as default
for now since that's what was specified earlier, but the
data argues for tightening.

**Two clean optima**:
- **Hedged: looser stops dominate.** Sharpe rises monotonically
  from 1.30 (−2%) to 2.41 (−15%). The −10% level captures most
  of that improvement while still firing meaningfully (55
  stops vs 19 at −15%).
- **Unhedged: bimodal at −3% and −10%.** Tight stops cut tail
  losses on directionally-exposed positions; very loose stops
  preserve winners. Mid-range (−5% to −7%) is worst.

## Drawdown realities

Per-position max hedged drawdown during trade life:

| stop | window | avg DD | peak DD | n trades >$5M DD |
|---|---|---|---|---|
| −5% | 20d | −$889K (3.6%) | **−$11.9M (20.8%)** | 9 |
| −10% | 20d | −$1.05M (4.3%) | **−$11.9M (20.8%)** | 13 |

**Peak DD is identical at both stop levels** because the worst
cases are single-day gap-downs that breach both thresholds
between two daily closes. By the time end-of-day stop check
fires and we exit next session, the move is already past
either threshold.

Average DD differs only by ~65bps. Tight stop sees fewer
large drawdowns (9 vs 13 > $5M) but the catastrophic peak is
the same.

## Stopped-trade recovery: what really happens

Of trades that stopped under the default −5% rule, if held
to horizon under no-stop:

| window | n | median ret | % positive at horizon | % improved post-stop |
|---|---|---|---|---|
| 5d | 33 | −5.5% | 6% | 48% |
| 10d | 90 | −4.0% | 19% | 56% |
| 20d | 127 | −3.9% | **28%** | 51% |

**"Most stopped trades recover" was misleading.** Reality:

- Only **28%** of stopped trades turn positive by 20d.
- But **51%** improve post-stop (bounce, just stay below 0).
- The alpha lives in the **partial recovery from −5%/−7% to
  the −3.9% median** — captured by loose stops but forfeited
  by tight ones.

Per-trade average bounce from stop to horizon at 20d: ~78bps.
Multiplied across 127 stopped trades × ~$26M avg notional =
**~$62M of foregone hedged P&L** under the −5% stop.

## Two trade case studies

The peak DD trade (−$11.9M) is the same trade at both stop
levels: **COHR 2026-02-10**. It illustrates one archetype.
**DELL 2024-03-04** is the mirror image. Together they explain
the population-level trade-off.

### COHR — "gap-down + recovery" archetype

- offer $237.50, pre-close $242.46 → discount −2.0%
- ADV $1.07B → capped at $100M, β=1.04, **corr=0.75 (high)**

Price path: dropped −9% in 2 days post-print, then steadily
recovered, hitting +25.9% on Mar 2 before mean-reverting.

Realized hedged returns:

| window | −5% stop | −10% stop |
|---|---|---|
| 5d | −9.3% | −10.4% |
| 10d | −9.3% | **+1.6%** |
| 20d | −9.3% | **+4.7%** |

The −5% stop fires on T+1 (close at −5.8%) and exits T+2 (the
worst close at −9.0%) — **stop slippage personified**, selling
into the bottom. The −10% stop never triggers (worst close is
−9.0%, just above threshold), so the position holds through
recovery.

**This one trade swings $14M** between the two stop levels —
about half the total population-level alpha differential.

### DELL — "slow bleed + delayed recovery" archetype

- offer $123.25, pre-close $124.59 → discount −1.1%
- ADV $710M → capped at $100M, β=1.43, **corr=0.44 (low)**
- Context: DELL had rallied +31.6% the prior trading day on
  earnings. The block locked in some of that gain at a tiny
  discount.

Price path: grinds down from $122 to $106 over 9 days
(post-earnings fade), bottoms at −14%, recovers only beyond
the 20d ramp window (Apr 3, T+22).

Realized hedged returns:

| window | −5% stop | −10% stop |
|---|---|---|
| 5d | −4.6% (no trigger) | −4.6% (no trigger) |
| 10d | **−6.0%** (stop fires T+4) | −13.6% (stop fires T+7) |
| 20d | −6.0% | **−13.6%** |

For DELL, the tight stop **saves** the position from a deeper
drawdown. Recovery happens too late to help any window.

### The discriminator: post-trade correlation

| | COHR | DELL |
|---|---|---|
| pre-trade corr | 0.75 | 0.44 |
| post-trade move | idiosyncratic | sector-grind (low hedge value) |
| hedge protection | substantial | minimal |
| optimal stop | loose (let recovery happen) | tight (cap exposure) |

For well-hedged names, the hedge already handles directional
risk and the stop forfeits the alpha cushion. For
poorly-hedged names, the stop is the only protection. **A
smarter system would set per-trade stops based on `corr`**;
the population-level −10% default works because well-hedged
trades dominate (avg corr ≈ 0.6).

## Default

**stop_basis = 'hedged'**, **stop_pct = −0.08**, hedged-P&L
basis. The hedged basis fires less often than the same
threshold on the target basis would, because the hedge
absorbs much of the directional move. Under target basis a
−8% stop required the target to drop 8%; under hedged basis
it requires net P&L (target gain/loss combined with hedge
gain/loss) to be down 8%.

| metric | −5% hedged | **−8% (current default)** | −10% hedged |
|---|---|---|---|
| hedged Sharpe (annual) | +2.12 | **+1.88** | +1.88 |
| annualized hedged | +20.3% | **+15.9%** | +16.1% |
| n stops (20d) | 106 | **57** | 37 |
| avg daily gross GMV | $418M | $466M | $483M |

The Sharpe-optimal level on the hedged basis is **−5%**
(Sharpe 2.12, ann +20.3%). The current default −8% is
conservative; a future change to −5% hedged is worth
considering as the new baseline.

## Caveats

- **Per-trade stop logic ignores correlation.** A
  corr-conditional stop (e.g., −5% for low-corr trades, −10%
  for high-corr) would likely dominate but isn't implemented.
- **Gap-down protection requires intraday stops.** End-of-day
  evaluation means peak DD is locked in at single-day moves
  worse than the stop threshold. Intraday stops would shrink
  peak DD but introduce slippage cost and a more complex sim.
- **Population skew matters.** The dataset has 60-70% trades
  in the COHR archetype; if a different cohort (e.g., low-corr
  small-caps) dominated, the optimal stop would tighten.
- **Recovery timeframe is window-bounded.** DELL "recovered"
  on day T+22, just past the 20d ramp. A 25d or 30d window
  would have made −10% stop competitive on DELL too. The
  20d horizon truncates some of the recovery.
- **Capacity binding via cap.** Both case-study trades hit
  the $100M cap; this is a structural concentration risk. A
  lower cap would spread the same $14M COHR swing across
  multiple smaller trades, reducing single-name P&L sensitivity.
