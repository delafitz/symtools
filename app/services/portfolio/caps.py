"""Portfolio-level capacity caps applied to the trade list.

Walks the trade list chronologically by `trade_date`. Tracks
the currently-open book and applies one or both caps:
  * `max_pos` — maximum simultaneously-open positions
  * `max_gmv_usd` — maximum aggregate gross notional (long + |hedge|)

Two policies:

* `apply_caps` — binary skip. Any trade that would breach a cap
  is dropped entirely.
* `apply_caps_scaled` — partial fill. When the GMV cap binds,
  take the new trade at a reduced size (= remaining capacity /
  full-size gross). Position-count cap remains binary (slots
  are integer). Existing positions are untouched.

The scale-down policy is the production default because it
captures most of the alpha that would be lost to a hard skip
during heavy issuance bursts, at a smaller dollar contribution.

P&L scales linearly with notional under this framework:
  target_pnl = shares × (exit_px − offer)        (shares ∝ notional)
  hedge_pnl  = −hedge_notional × basket_return   (linear)
  costs      = 10 bps × 4 sides × gross_notional (linear)
so a 50%-size trade is just every dollar field multiplied by 0.5.
No re-pricing required.
"""

from dataclasses import dataclass

import polars as pl


# Dollar-denominated fields that scale linearly with notional.
DOLLAR_COLS = [
    'notional_usd',
    'shares',
    'hedge_notional_usd',
    'target_pnl_usd',
    'hedge_pnl_usd',
    'pnl_unhedged_usd',
    'pnl_hedged_usd',
    'expected_pnl_unhedged_usd',
    'expected_pnl_hedged_usd',
    'var99_unhedged_usd',
    'var99_hedged_usd',
    'cost_target_usd',
    'cost_hedge_usd',
]


# Default cap on aggregate gross notional. Calibrated on the 283-trade
# cleaned dataset: $500M cap with scale-down compresses max GMV
# 47% ($937M → $500M), max VaR 47% ($133M → $76M), worst-month DD
# 21% (−$34M → −$27M) while keeping monthly h20 P&L within 3% of
# uncapped baseline. See docs/basic-scenario-analysis.md GMV-cap
# sweep for the analysis.
DEFAULT_MAX_GMV_USD: float | None = 500_000_000

# Default cap on simultaneously-open positions. None = unlimited.
# In practice the GMV cap is the binding governor; pos cap is
# left off so the book can dilute large positions into many small
# ones during heavy bursts.
DEFAULT_MAX_POS: int | None = None


@dataclass(frozen=True)
class CapStats:
    kept: int
    full: int
    partial: int
    zero_capacity: int
    skipped_pos: int
    total: int


def apply_caps(
    positions: pl.DataFrame,
    max_pos: int | None = None,
    max_gmv_usd: float | None = None,
) -> tuple[pl.DataFrame, dict]:
    """Binary skip: any trade that would breach a cap is dropped."""
    df = positions.sort('trade_date')
    open_positions: list[dict] = []
    kept_rows: list[dict] = []
    n_skipped_pos = 0
    n_skipped_gmv = 0
    for r in df.iter_rows(named=True):
        td = r['trade_date']
        open_positions = [
            p for p in open_positions if p['exit_date'] > td
        ]
        cur_count = len(open_positions)
        cur_gmv = sum(p['gross'] for p in open_positions)
        new_gross = (
            r['notional_usd'] + abs(r['hedge_notional_usd'])
        )
        if max_pos is not None and cur_count + 1 > max_pos:
            n_skipped_pos += 1
            continue
        if (
            max_gmv_usd is not None
            and cur_gmv + new_gross > max_gmv_usd
        ):
            n_skipped_gmv += 1
            continue
        kept_rows.append(r)
        open_positions.append({
            'exit_date': r['exit_date'],
            'gross': new_gross,
        })
    return pl.DataFrame(kept_rows, schema=df.schema), {
        'kept': len(kept_rows),
        'skipped_pos': n_skipped_pos,
        'skipped_gmv': n_skipped_gmv,
        'total': len(df),
    }


def apply_caps_scaled(
    positions: pl.DataFrame,
    max_pos: int | None = None,
    max_gmv_usd: float | None = None,
) -> tuple[pl.DataFrame, dict]:
    """Partial fill on the GMV cap; binary on the position cap.

    For each trade in chronological order:
      1. Drop already-exited positions from the open book.
      2. If `max_pos` would be breached: skip (no fractional slots).
      3. If `max_gmv_usd` would be breached: compute
         `scale = max(0, max_gmv − cur_gmv) / new_gross` and
         multiply every dollar field of the trade by `scale`.
         If `scale == 0` (book full), skip.
      4. Otherwise take the trade at full size.

    Returns the scaled DataFrame and a stats dict with counts of
    full/partial/zero/skipped_pos.
    """
    df = positions.sort('trade_date')
    open_positions: list[dict] = []
    scaled_rows: list[dict] = []
    n_skipped_pos = 0
    n_full = 0
    n_partial = 0
    n_zero = 0
    for r in df.iter_rows(named=True):
        td = r['trade_date']
        open_positions = [
            p for p in open_positions if p['exit_date'] > td
        ]
        cur_count = len(open_positions)
        cur_gmv = sum(p['gross'] for p in open_positions)
        new_gross = (
            r['notional_usd'] + abs(r['hedge_notional_usd'])
        )
        if new_gross <= 0:
            continue

        # Position-count cap: binary (no fractional slots)
        if max_pos is not None and cur_count + 1 > max_pos:
            n_skipped_pos += 1
            continue

        # GMV cap: partial fill
        scale = 1.0
        if max_gmv_usd is not None:
            available = max(0.0, max_gmv_usd - cur_gmv)
            if available < new_gross:
                scale = available / new_gross

        if scale <= 0.0:
            n_zero += 1
            continue
        elif scale < 1.0:
            n_partial += 1
        else:
            n_full += 1

        new_row = dict(r)
        for col in DOLLAR_COLS:
            if col in new_row and new_row[col] is not None:
                new_row[col] = new_row[col] * scale
        scaled_rows.append(new_row)
        open_positions.append({
            'exit_date': r['exit_date'],
            'gross': new_gross * scale,
        })

    return pl.DataFrame(scaled_rows, schema=df.schema), {
        'kept': len(scaled_rows),
        'full': n_full,
        'partial': n_partial,
        'zero_capacity': n_zero,
        'skipped_pos': n_skipped_pos,
        'total': len(df),
    }
