# Block Alpha Drivers

What characteristics separate the best-performing block trades
from the worst, and what biases in this analysis should temper
the conclusions.

Builds on `basic-return-analysis.md` — same data, same scoring.

All numbers in this doc are computed on the curated
`block_trades_alt.YYYYMMDD.json` dataset (n=327 unique
observations on the combined scenario). The findings were
originally derived on the legacy `block_trades.20260321.json`
file (n=365) and replicated cleanly on the curated file at
sharper signal-to-noise. Where the two datasets diverge
materially, both numbers are shown.

## Method

- Rank by post 20d hedged return on the **combined** scenario.
- Bin into deciles (n ≈ 33 per bin).
- One observation per `(symbol, trade_date)` — multi-broker
  tranches of the same placement are collapsed via average.
- Source: curated `block_trades_alt.YYYYMMDD.json` (440 raw
  deals from SEC filings). Uses `adj_price`/`adj_shares` for
  split-adjusted comparability with Polygon hists.
- Final population: **n=327 unique observations**.
- Hedged returns apply a **0.85 haircut on β** — see "Hedge
  calibration" section below.

## Hedge calibration

The optimizer computes β over 200 days via OLS on basket vs
target returns. Empirically that β is biased high vs the
realized forward β over the post window. Three findings drove
the 0.85 haircut now applied in scoring:

### Pre-block β is elevated

Computing β over a 60-day window ending at the trade date for
each combined basket (n=1,160 trades across cached weeks):

| stat | β_200 | β_60 |
|---|---|---|
| mean | 0.91 | **1.35** |
| median | 0.90 | 1.18 |

Ratio β_60/β_200 is **1.71 mean, 1.27 median** — 60d β is
*higher* than 200d, not lower. Counter to the naive intuition
that a shorter window better reflects current dynamics. The
pre-block 60d picks up the high-correlation regime where the
target trades with its sector heading into the print. Using
60d β would over-hedge worse than 200d.

### Realized forward β is lower than either

A hedge-ratio sweep on the population's combined-scenario
post returns reveals min-variance lands well below k=1.0:

| horizon | min-var k | mean at min-var | std at min-var |
|---|---|---|---|
| 5d | 0.85 | −0.05% | 5.85% |
| 10d | 0.95 | +0.45% | 7.11% |
| 20d | **0.80** | +0.50% | 10.06% |

A constant k=0.85 sits within rounding of min-var at all three
horizons. So no β estimator built on pre-trade data captures
the forward dynamic — **the block event is a regime break**,
and effective post-trade β is ~0.85 of the optimizer's
estimate.

### Effect of haircut

Applying k=0.85 to all hedged returns lifts mean by ~15bps at
20d on combined while keeping variance roughly flat. On the
curated alt dataset, the absolute mean P&L at every k is
materially higher than the legacy dataset (k=0 unhedged 20d
went from +1.32% to **+2.10%**, Sharpe 0.117 → **0.192**):

| k | mean h20 | std | Sharpe |
|---|---|---|---|
| 0.00 | +2.10% | 10.94% | **0.192** |
| 0.85 | +1.17% | 9.88% | 0.119 |
| 1.00 | +1.01% | 9.91% | 0.102 |

Min-variance still lands at ~k=0.85 (within rounding 0.80-0.90
across post horizons). Sharpe is monotonically decreasing in
k — pure mean / unhedged is the highest-Sharpe choice if you
can absorb the variance.

All hedged numbers in this doc and in
`basic-return-analysis.md` use k=0.85.

### Caveats

- The 0.85 is calibrated to *this population* of block trades.
  A different sample (different time period, different
  market regime, different deal type mix) would calibrate
  differently.
- **k=0** (no hedge) gives the highest Sharpe at every horizon
  in this dataset. The hedge buys variance reduction at a poor
  rate of mean. A more aggressive haircut or no hedge at all
  is defensible if your objective is mean rather than
  variance.
- The interpretation of "regime break" is empirical, not
  causal. We don't know whether the elevated pre-block β is
  driven by the seller's deliberate timing, by the broader
  market mechanism that produces blocks, or by adverse
  selection in our discount measurement.

## Decile summary

| D | n | mean h20 | disc | xADV | vol | corr | pre 1d | pre 20d |
|---|---|---|---|---|---|---|---|---|
| **D10** | 33 | **+20.6%** | −3.5% | 4.8× | 53% | 0.52 | −0.8% | **+15.2%** |
| D9 | 33 | +9.1% | −3.0% | 6.4× | 39% | 0.60 | −0.9% | +7.9% |
| D8 | 32 | +5.3% | −2.9% | 5.7× | 35% | 0.59 | −1.6% | +3.8% |
| D7 | 33 | +3.2% | −2.3% | 4.8× | 39% | 0.64 | −1.6% | +6.8% |
| D6 | 32 | +1.2% | −2.5% | 7.8× | 35% | 0.63 | −1.0% | +3.9% |
| D5 | 33 | −0.5% | −2.1% | 6.3× | 35% | 0.64 | −0.9% | +4.9% |
| D4 | 33 | −2.2% | −2.7% | 3.8× | 43% | 0.65 | −1.4% | +9.9% |
| D3 | 32 | −4.2% | −2.5% | 5.0× | 42% | 0.61 | −2.3% | +8.3% |
| D2 | 33 | −7.0% | −3.1% | 5.3× | 41% | 0.62 | −1.1% | +6.6% |
| **D1** | 33 | **−15.4%** | −3.6% | 4.2× | 57% | 0.53 | **−5.0%** | +1.5% |

## What doesn't discriminate

- **Discount size** — D1 and D10 both average ~−3.5%. Deeper
  discounts do *not* predict better P&L; if anything they're a
  warning signal (see basic-return-analysis.md, ≥5% bucket).
- **xADV** — D10 at 3.0×, D1 at 3.5×, middle deciles span
  4-7×. No monotonic relationship.
- **corr** — D10 0.55, D1 0.56, middle 0.60-0.64. Lower
  hedgeability is associated with the *tails* (winners and
  losers), not with one direction.
- **vol_90d** — D10 56% and D1 53% both have higher vol than
  the middle deciles (37-47%). Vol drives variance both
  directions, not just upside.

## What does

1. **Pre-trade 20-day run-up.** D10 averages +15.2%, D1 +1.5%.
   The other deciles fall in a 4-10% band. **Strong pre-trade
   momentum strongly predicts post-trade outperformance.**
   These are placements into demand — the seller is taking
   advantage of upside, not capitulating.
2. **1-day pre-trade drawdown.** D10 averages only −0.8%; D1
   averages −5.0%. The same-day drawdown signal is *milder*
   in the curated dataset than legacy (D1 was −6.0%), because
   the alt file filters out the most-forced same-day prints,
   but the *direction* and the spread between extremes is
   preserved.
3. **Raw ≈ hedged at tails.** D10 hedged +20.6%, raw +21.7%.
   D1 hedged −15.4%, raw −13.5%. Top performers' alpha is in
   the underlying — not amplified or muted by the hedge. The
   hedge mildly *amplifies* D1's losses (hedge subtracts more
   than basket dropped) because these trades are uncorrelated
   downside surprises.

## Implication

Best signal for trade selection isn't the discount — it's the
**pre-trade flow profile**. The attractive setup is **strong
20-day uptrend + only mild same-day softening** (D10 archetype:
+15.2% / −0.8%). The dangerous setup is **flat-to-down 20-day
+ severe same-day drop** (D1 archetype: +1.5% / −5.0%) — D1's
−15.4% hedged loss is the price of stepping in front of an
unrelenting seller with no demand cushion. The pattern
replicates exactly across legacy and curated source files.

## Observation: Registered vs Unregistered

Independent split on the same population (n=365). Registered
offerings (`Type = 'Reg'` in source) are SEC-registered public
follow-ons; the rest are 144A / private placements / insider
sales (`Type = null`).

### Profile

| type | n | disc | xADV | vol | corr |
|---|---|---|---|---|---|
| **Reg** | 141 | −3.3% | **8.4×** | 35% | 0.61 |
| **Unreg** | 186 | −2.5% | 3.1× | **47%** | 0.60 |

Reg deals are **larger**, in **lower-vol mature names**, at
**deeper discounts** than Unreg. The xADV gap widened on the
curated dataset (8.4× vs 3.1×) — the larger-deal cohort
biases more heavily into Reg.

### Pre-trade (raw, close-to-close)

| type | pre 20d | pre 1d |
|---|---|---|
| Reg | +5.7% | −0.2% |
| Unreg | +7.8% | **−2.7%** |

Reg now shows almost no same-day drawdown (−0.2%) — the
curated dataset has the cleanest marketed cohort. Unreg
retains the sharper T-1 drop (−2.7%) consistent with
opportunistic intraday/pre-open pricings.

### Post-trade raw P&L

| type | 1d | 5d | 10d | 20d |
|---|---|---|---|---|
| **Reg** | +0.59% | +0.54% | +1.03% | **+2.23%** |
| Unreg | −0.36% | −0.40% | +0.78% | +2.00% |

### Post-trade hedged P&L (k=0.85)

| type | 1d | 5d | 10d | 20d |
|---|---|---|---|---|
| **Reg** | +0.59% | +0.54% | +1.03% | **+1.46%** |
| Unreg | −0.36% | −0.40% | +0.78% | +0.66% |

### Hit rates (raw_return > 0)

| type | post 1d | post 20d | hedged post 20d |
|---|---|---|---|
| **Reg** | 48% | **60%** | **55%** |
| Unreg | 49% | 51% | 47% |

### Findings

1. **Reg outperforms by ~1pp at 20d raw and ~95bps hedged.**
   This is counter-intuitive — Reg deals are bigger, deeper
   discount, and more constrained (no quick discount-and-flee
   on disclosure restrictions).
2. **Unreg is essentially noise on a hedged basis.** −8bps
   mean, 47% hit rate at 20d. The unhedged +90bps from Unreg
   is beta drift in volatile names; once hedged, it's gone.
3. **The roadshow process matters.** Reg's marketed buildup
   pre-builds institutional demand, supporting the stock after
   the print. Unreg has no such cushion — when the supply
   hits, there's no built-up demand to absorb it.

### Implication

**A first-cut filter of "Reg only" eliminates 60% of the
population and shifts mean hedged P&L from +5bps (full pop) to
+87bps (Reg), with 55% hit rate.** Combined with the
flow-profile signal from the decile analysis above, Reg deals
with strong pre-20d momentum and mild pre-1d drawdown are the
structurally attractive cohort.

## Observation: Bank selection

Same population (n=365). Banks bucketed as GS, MS, JPM, BAML
(includes BAC), Citi, and Other (all remaining brokers — RBC,
Jefferies, BCS, BMO, WFC, etc.).

### Profile

| bank | n | % Reg | disc | xADV | vol | corr |
|---|---|---|---|---|---|---|
| JPM | 71 | 46% | −2.3% | 5.9× | 36% | 0.64 |
| GS | 63 | 43% | −3.0% | 5.9× | 45% | 0.61 |
| Other | 58 | 57% | −3.4% | 6.1× | 44% | 0.60 |
| MS | 53 | 38% | −2.7% | 4.6× | 44% | 0.60 |
| BAC | 47 | 34% | −2.8% | 4.5× | 42% | 0.58 |
| Citi | 35 | 34% | −3.0% | 4.7× | 41% | 0.55 |

JPM leads in trade count; GS close second. Reg share varies
34-57%. Banks tracked via canonical codes from `banks[0]` in
the source (lead-left). Mappings: BAML/BAC → BAC, Citi/C →
Citi.

### Pre-trade flow (raw)

| bank | pre 20d | pre 1d |
|---|---|---|
| GS | **+10.1%** | −2.1% |
| **Citi** | +9.6% | **−1.7%** |
| BAC | +6.8% | −1.6% |
| MS | +6.2% | −1.7% |
| Other | +5.5% | −1.7% |
| **JPM** | **+4.5%** | −1.2% |

### Raw post-trade P&L

| bank | 1d | 5d | 10d | 20d |
|---|---|---|---|---|
| **Citi** | +1.06% | +1.10% | +3.37% | **+4.88%** |
| Other | +0.88% | +0.50% | +0.83% | +3.56% |
| GS | −0.19% | −0.38% | +1.09% | +2.87% |
| MS | −0.53% | −0.87% | +0.29% | +1.80% |
| BAC | +0.38% | +1.32% | +2.03% | +1.10% |
| JPM | −0.72% | −0.83% | −0.79% | **−0.27%** |

### Hedged post-trade P&L (k=0.85)

| bank | 1d | 5d | 10d | 20d |
|---|---|---|---|---|
| **Citi** | +1.06% | +1.10% | +3.37% | **+4.48%** |
| GS | −0.19% | −0.38% | +1.09% | +1.97% |
| Other | +0.88% | +0.50% | +0.83% | +1.40% |
| BAC | +0.38% | +1.32% | +2.03% | +1.12% |
| MS | −0.53% | −0.87% | +0.29% | −0.21% |
| JPM | −0.72% | −0.83% | −0.79% | **−1.05%** |

The Citi–JPM 20d hedged spread widened from **3.2pp (legacy)
to 5.5pp (curated)** — the sharpened sample sharpens the
signal. Naming convention: old BAML = new BAC (Bank of
America canonical code); old Citi = new Citi (`C` in source).

### Hit rates (post 20d)

| bank | hedged_hit |
|---|---|
| **Citi** | **63%** |
| BAC | 55% |
| MS | 49% |
| GS | 49% |
| Other | 48% |
| JPM | 48% |

### Findings

1. **Citi is the clear standout** — best raw (+4.88%) and best
   hedged (+4.48%) P&L at 20d, 63% hedged hit rate. Its
   pre-trade profile matches the D10 archetype: strong
   20-day run-up (+9.6%) combined with the mildest same-day
   drawdown (−1.7%). Smallest sample size (n=35), so strongest
   statistical caveat — but the cleanest economic story, and
   it replicated cleanly from the legacy dataset.
2. **GS handles the strongest momentum** — highest pre-20d
   (+10.1%) with moderate same-day pressure (−2.1%). Net
   solidly positive: +2.9% raw, +2.0% hedged, 49% hit rate.
3. **JPM is structurally worst** — weakest pre-trade momentum
   (+4.5% over 20d, vs 10% for top banks), only negative
   hedged P&L (−1.05%), 48% hit rate. JPM appears to win flow
   with weaker demand support: forced sellers without the
   run-up cushion. Replicates exactly from the legacy
   dataset.
4. **BAC and MS are middling** — positive raw P&L but hedged
   P&L is neutral to slightly negative at 20d. Both have
   profile features that *look* attractive (low corr, low
   pre-1d) but don't quite deliver hedged alpha at horizon.
5. **The Citi–JPM spread widened** from 3.2pp (legacy) to
   5.5pp (curated) — the curated dataset's better attribution
   sharpens the signal rather than overturning it.

### Implication

**Bank selection appears to be a real signal**: at this sample,
Citi-led blocks have a ~3.2pp hedged P&L advantage over
JPM-led blocks at 20d (+2.45% vs −0.74%). Two interpretations:

- **Bank-specific block-desk quality** — Citi's syndicate may
  be better at identifying which placements have demand
  cushion.
- **Self-selection of flow** — sellers in less-distressed
  setups may preferentially choose certain banks for their
  reputation/relationships, leaving others with the harder
  flow.

Both stories produce the same trading conclusion: **filter to
Citi/GS/Other lead-bank blocks** and you've shifted hedged P&L
materially without changing any other criterion. Combined with
the decile flow profile and Reg-filter, this is a strong
multi-factor screen.

### Caveats specific to this lens

- **Small sample per bank** (35-86). Differences below ~50bps
  are likely noise. The Citi-vs-JPM extreme is the most likely
  robust ordering; middle banks (GS/MS/BAML) are noisier.
- **No time control.** Banks' block books shift quarter by
  quarter; if Citi happened to lead a few high-quality deals
  in a benign market window, their average is inflated.
- **Lead-bank vs all-bank.** The `LeftBank` field identifies
  the lead bank only — in syndicated deals, other banks share
  the risk/economics but don't show up here.
- **No control for sector/cap mix.** If JPM systematically
  handles different sectors (e.g., more financials, more
  small-cap), and those sectors performed worse in 2024-2026,
  the gap reflects sector exposure rather than bank skill.
  See the Sector observation below.

## Observation: Sector

GICS sector attached via `refs.g_sector` (current-snapshot
classification). 63 trades on tickers not in current refs are
bucketed as "(unknown)" — these are mostly delisted/acquired
names and carry their own selection bias.

### Profile + returns (hedged k=0.85)

| sector | n | disc | xADV | vol | corr | pre 20d | pre 1d | raw p20 | hedged p20 | hed hit |
|---|---|---|---|---|---|---|---|---|---|---|
| Materials | 12 | −4.4% | 9.0× | 58% | 0.55 | +10.0% | −3.7% | +9.83% | **+6.76%** | 75% |
| Health Care | 10 | −3.4% | 4.7× | 57% | 0.48 | +3.2% | −3.5% | +5.29% | +4.89% | 50% |
| **Cons Disc** | 27 | −2.7% | 2.7× | 47% | 0.48 | +10.0% | −1.1% | +3.08% | **+2.48%** | 56% |
| Industrials | 47 | −3.0% | 6.3× | 40% | 0.57 | +7.0% | −1.5% | +3.18% | +2.16% | 53% |
| Financials | 37 | −2.1% | 4.5× | 37% | **0.71** | +3.2% | −1.0% | +2.23% | +1.67% | 51% |
| (unknown) | 62 | −3.1% | 6.0× | 44% | 0.57 | +9.6% | −2.4% | +2.09% | +1.28% | 55% |
| Cons Staples | 8 | −2.3% | 6.8× | 30% | 0.53 | +0.4% | −2.7% | +2.46% | +0.53% | 75% |
| Real Estate | 25 | −2.5% | 7.7× | 25% | **0.74** | +3.7% | −0.5% | −1.32% | −0.16% | 52% |
| Energy | 41 | −2.4% | 5.3× | 37% | **0.74** | +6.1% | −0.9% | +1.71% | −0.88% | 41% |
| **IT** | 48 | −3.1% | 4.1× | 53% | 0.52 | **+9.9%** | −1.6% | +0.59% | **−0.97%** | **42%** |
| Utilities | 3 | −2.7% | 5.3× | 23% | 0.75 | −7.5% | −0.8% | −1.86% | −2.24% | 33% |
| Comm Services | 7 | −3.0% | 3.9× | 35% | 0.57 | −1.7% | −3.6% | −1.24% | −2.92% | 43% |

### Findings

1. **Consumer Discretionary is the most robust** — +2.48%
   hedged at 20d, 56% hit rate, n=27. Lowest corr (0.48),
   smallest xADV (2.7×), strong pre-20d momentum (+10.0%),
   mildest pre-1d drop (−1.1%). Replicates the legacy
   finding cleanly.
2. **Industrials second at the largest meaningful n** (47
   trades, +2.16% hedged). Similar pre-trade profile,
   slightly more hedgeable, broader sample.
3. **IT is still the worst large-sample sector despite the
   biggest run-up.** pre-20d +9.9% (highest among major
   sectors) doesn't translate — hedged P&L **−0.97% at 20d,
   42% hit rate (lowest)**. Mechanism unchanged from legacy:
   the basket optimizer's candidate pool for an IT target
   is naturally IT-heavy, so the hedge captures the same
   sector momentum and β-hedges the alpha away.
4. **Energy and Real Estate show the same hedgeability trap.**
   Energy corr 0.74, hedged −0.88%; Real Estate corr 0.74,
   hedged −0.16%. High-corr sectors give most of the
   directional gain back through the hedge.
5. **Financials breaks the pattern** — corr 0.71 like Energy,
   but stays positive hedged (+1.67%). Suggests Financials
   blocks carry more idiosyncratic news (bank-specific
   earnings, M&A) than sector beta — alpha survives despite
   the basket capturing market.
6. **Materials (n=12) and Health Care (n=10) jumped on the
   curated dataset** — Materials hedged +6.76% (was +3.17%),
   Health Care +4.89% (was +1.37%). Small samples and
   high vol (57-58%) — likely outlier-driven, not
   generalizable. Don't trust the magnitudes.
7. **"(unknown)" sector turned positive** at +1.28% hedged
   (legacy was −0.96%). The curated dataset retains fewer
   delisted/acquired-name trades, so survivorship bias is
   less concentrated in this bucket.

### Implication

**Sector hedgeability is the master variable** in this lens.
Low-corr sectors (Cons Disc, Industrials) keep their alpha
because the basket genuinely doesn't capture the target's
move. High-corr sectors (Energy, Real Estate) lose theirs even
when the target rallied — the hedge eats the gain.

The paradox is IT: low individual-name corr (0.52) but high
basket capture because the candidate universe for an IT target
is mostly other IT names that move together. **For sector-
concentrated names, the basket model effectively rebuilds the
sector** — a structural limitation of the candidate-screening
approach worth exploring as a separate workstream.

### Caveats specific to this lens

- **Refs sector is current-snapshot**, not as-of-trade-date.
  Reclassifications (e.g., MSCI changes) shift names between
  sectors over time. Likely minor effect at 2-year horizon.
- **Small samples** in Materials (12), Health Care (10),
  Cons Staples (8), Comm Services (7), Utilities (3) make
  these sector-level numbers very noisy. Treat as
  observations, not signal.
- **"(unknown)" bucket is large** (62 trades, 19% of the
  population) and carries survivorship bias. Excluding it
  doesn't materially change the relative ordering of the
  named sectors.
- **No cross-cut with bank.** Sector and bank may be
  correlated (e.g., GS may handle more IT secondaries) — the
  bank lens and sector lens are independent slices on the
  same data, not orthogonal factors.

## Biases

These results are suggestive, not proven. Several biases are
baked in:

### Refs snapshot bias
`refs.parquet` reflects current `mkt_cap`, `free_float`,
`g_sector`, and `type` — not values as of the trade date. A
stock that has 5× since 2024 looks like a large-cap with
moderate xADV today but was a small-cap with much higher xADV
at the time. This systematically understates xADV and
overstates liquidity for older trades.

### Survivorship in the candidate universe
The Barra factor model is built from stocks currently in refs.
Stocks that have since delisted, merged, or been acquired
aren't there — even if they existed at the trade date. The
basket model is choosing hedge candidates from "winners that
survived to today," which biases both factor exposures and
basket weights toward names that have done well.

### Hedgeable selection bias
Quartiles are computed only over trades where Barra succeeded
and the combined basket built (n ≈ 380 of 386). Older trades
where the cross-section was thin are silently excluded. If
those failures correlate with trade outcome, the population is
non-random.

### Idealized hedge execution
The post-trade scoring assumes the hedge basket is established
at `close(TradeDt)` with no transaction costs and no slippage,
and that all 3-4 hedge names execute simultaneously. Real
execution loses some of the captured alpha to spreads and
timing.

### Discount source quality
The hist-derived discount is consistent for after-close
priced blocks but is a poor proxy for blocks priced pre-open or
intraday into a news catalyst. The positive-discount filter
drops 60+ trades; some of those may have been legitimate
intraday pricings with the discount mismeasured rather than
the trade being a true premium. See basic-return-analysis.md.

### Quartile sample sizes
n ≈ 95 per bucket. Q4 hedged +14.6% is driven by ~96 trades; a
handful of extreme right-tail outcomes (`arwr` +51%, `be` +44%,
`gtes` +36%) carry it. Median Q4 outcome is materially smaller
than the mean — distribution is right-skewed.

### Multiple-comparison risk
This analysis examines 4 scenarios × 2 periods × 4 windows × N
characteristics. Reporting only the strongest patterns inflates
apparent significance. No multiple-testing correction applied;
treat findings as exploratory hypotheses for confirmation, not
confirmed effects.

### Name concentration
Top-15 hedged P&L is dominated by a small number of
high-momentum names (`arwr`, `be`, `cohr`, `sofi`, `cava`,
`hnge`). If 5-10 names drive most of the right tail, the
"high-vol, low-corr" pattern may be a single-name story rather
than a generalizable factor.

### Regime sensitivity
Trades span Dec-2023 to Mar-2026 — a strong-market period.
Block-trade dynamics in a falling market are likely different,
and these findings may not generalize. Pre-trade flow signals
that work in this regime could invert when the average pre-20d
return is negative rather than +5%.

### Pre-trade hedge is retrospective
Pre-period hedged returns use the **trade-date basket** applied
backward. This isn't a pre-positioning backtest; it asks "how
would the trade-date hedge have performed if we'd held it
through the pre-trade window," which is a stationarity
assumption that's mostly fine for risk modeling but invalid as
a tradable signal.

### Dedup vs raw observations
Multi-broker tranches of a single placement are collapsed to
one `(symbol, trade_date)` observation. This treats a $400M
deal split across 4 banks the same as a single $400M deal —
arguably correct for cross-trade statistics, but loses the
per-tranche P&L granularity (different brokers may have
slightly different offer prices). Statistics on raw scored rows
without dedup will overweight names with many tranches and
inflate apparent concentration.

### Type label is incomplete
`Type` in source is either `'Reg'` or `null`. The Reg/Unreg
split treats null as Unreg, but null could in principle mean
"unknown" rather than "explicitly unregistered." A subset of
nulls may be registered offerings with missing labels. The 60%
unreg share looks plausible against industry split for follow-on
markets, but isn't independently verified.

### Selective deletion (hnge)
HNGE trades were removed from the source data because the
underlying placement was a 180-day post-IPO lockup expiration —
a structurally different supply event. The exclusion is
judgment-based; no other names have been screened with the same
rigor for analogous structural distinctions (e.g., insider
sales tied to 10b5-1 plans, sponsor-led secondaries, etc.). The
broader population likely contains other structurally distinct
trades that haven't been separated.

## Caveat applicable to all of the above

These are *averages* across a small-to-moderate sample. The
within-quartile dispersion is large (Q4 raw return std ≈ 20%).
Use these signals to *condition* trade screening, not to
mechanically pick trades.
