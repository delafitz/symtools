"""One block-trade position with full daily-path mechanics.

Trade mechanics:
  - Target leg: enter $notional at offer_price on T0 (full
    position immediately).
  - Hedge leg: short β * hedge_ratio * $notional of basket,
    entered at basket close on T0.
  - Planned exit: 1/3 of position each day at close on
    T+(w-2), T+(w-1), T+w. Both legs exit on the same schedule.
  - Stop loss: two bases supported via `stop_basis`:
      'hedged' (default): trigger when daily-marked NET
        position P&L (target + hedge) ≤ stop_pct × notional.
        Reflects what the trader actually sees on a marked
        hedged book.
      'target': trigger when target close alone ≤
        offer × (1 + stop_pct). Older convention; doesn't
        credit hedge offset.
    Exit semantics unchanged: full liquidation of both legs at
    next day's close after the stop day.

Returns are reported per-window: a single Position with three
PositionResults (one per tradeout horizon) is the unit.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


DEFAULT_HEDGE_RATIO = 0.85
DEFAULT_STOP_PCT = -0.08
DEFAULT_STOP_BASIS = 'hedged'  # 'hedged' or 'target'
# Round-trip transaction cost per side (bps). Charged on
# target entry, target exit, hedge entry, hedge exit — so the
# total round-trip cost is 4 × bps × notional. 10 bps/side
# captures realistic basket/block execution (spread, slippage,
# small borrow drag).
DEFAULT_COST_BPS = 10.0
RAMP_DAYS = 3


@dataclass(frozen=True)
class PositionResult:
    symbol: str
    trade_date: str
    window_d: int
    notional_usd: float
    shares: float
    offer_price: float
    beta: float
    hedge_ratio: float
    hedge_notional_usd: float

    # Realized exit details
    target_avg_exit_px: float
    basket_avg_exit_px: float
    basket_entry_px: float
    exit_date: str  # the LAST day a piece was sold

    # P&L (USD) — net of transaction costs
    target_pnl_usd: float
    hedge_pnl_usd: float
    pnl_unhedged_usd: float
    pnl_hedged_usd: float

    # Returns (net P&L / notional)
    return_unhedged: float
    return_hedged: float

    # Transaction cost diagnostics
    cost_bps_per_side: float
    cost_target_usd: float
    cost_hedge_usd: float

    # Stop diagnostics
    stop_basis: str
    stop_triggered: bool
    stop_day: str | None


def _ramp_dates(
    daily: pl.DataFrame, trade_date: str, window_d: int
) -> list[str]:
    """Planned ramp-exit dates: last RAMP_DAYS trading days
    of the window. Returns date strings in order [T+w-2, T+w-1,
    T+w]. Returns empty if insufficient bars."""
    fwd = daily.filter(pl.col('date') > trade_date).head(window_d)
    if len(fwd) < window_d:
        return []
    dates = fwd.get_column('date').to_list()
    return dates[-RAMP_DAYS:]


def _close_on(daily: pl.DataFrame, date_str: str) -> float | None:
    rows = daily.filter(pl.col('date') == date_str)
    if rows.is_empty():
        return None
    return rows.get_column('close').item()


def _next_close(
    daily: pl.DataFrame, after: str
) -> tuple[str, float] | None:
    rows = daily.filter(pl.col('date') > after).head(1)
    if rows.is_empty():
        return None
    return (
        rows.get_column('date').item(),
        rows.get_column('close').item(),
    )


def score_position(
    symbol: str,
    trade_date: str,
    offer_price: float,
    notional_usd: float,
    beta: float,
    target_daily: pl.DataFrame,
    basket_close: pl.DataFrame,
    window_d: int,
    hedge_ratio: float = DEFAULT_HEDGE_RATIO,
    stop_pct: float = DEFAULT_STOP_PCT,
    cost_bps_per_side: float = DEFAULT_COST_BPS,
    stop_basis: str = DEFAULT_STOP_BASIS,
) -> PositionResult | None:
    """Score one trade at one tradeout horizon.

    `target_daily` / `basket_close` are (date, close) frames
    covering at least the window past trade_date. `basket_close`
    is the synthetic weighted-basket close series (see
    backtest.py:basket_close_series). Returns None if any
    required price is missing.

    `cost_bps_per_side` charges a transaction cost on each of
    the four execution sides (target entry, target exit, hedge
    entry, hedge exit). With the default 10 bps/side the total
    round-trip drag is 40 bps × gross_notional.
    """
    if notional_usd <= 0 or offer_price <= 0:
        return None

    shares = notional_usd / offer_price
    hedge_notional = beta * hedge_ratio * notional_usd
    basket_entry = _close_on(basket_close, trade_date)
    if basket_entry is None:
        # Trade-date close may not exist (e.g., intraday print);
        # fall back to most-recent-on-or-before.
        rows = basket_close.filter(
            pl.col('date') <= trade_date
        ).tail(1)
        if rows.is_empty():
            return None
        basket_entry = rows.get_column('close').item()

    if basket_entry <= 0:
        return None

    planned_ramp = _ramp_dates(target_daily, trade_date, window_d)
    if not planned_ramp:
        return None

    ramp_start = planned_ramp[0]

    # Walk T+1 .. T+(w-3) looking for stop trigger
    pre_ramp = (
        target_daily.filter(
            (pl.col('date') > trade_date)
            & (pl.col('date') < ramp_start)
        )
        .sort('date')
    )

    stop_triggered = False
    stop_day: str | None = None
    exit_dates: list[str]   # dates at which we sell
    exit_target_pxs: list[float]
    exit_basket_pxs: list[float]

    if stop_basis == 'target':
        # Target-only stop: target close ≤ offer × (1 + stop_pct)
        stop_threshold = offer_price * (1.0 + stop_pct)
        for r in pre_ramp.iter_rows(named=True):
            if r['close'] <= stop_threshold:
                stop_day = r['date']
                stop_triggered = True
                break
    else:
        # Hedged-P&L stop: daily-marked net return ≤ stop_pct
        # Net P&L_d = target_pnl_d + hedge_pnl_d (pre-cost; the
        # round-trip cost is paid at exit, not relevant for
        # the trigger).
        shares_marked = notional_usd / offer_price
        for r in pre_ramp.iter_rows(named=True):
            d = r['date']
            t_close = r['close']
            b_close = _close_on(basket_close, d)
            if b_close is None:
                continue
            t_pnl = shares_marked * (t_close - offer_price)
            b_ret = b_close / basket_entry - 1
            h_pnl = -hedge_notional * b_ret
            net_ret = (t_pnl + h_pnl) / notional_usd
            if net_ret <= stop_pct:
                stop_day = d
                stop_triggered = True
                break

    if stop_triggered:
        # Full exit at next session's close after stop_day
        nxt = _next_close(target_daily, stop_day)
        if nxt is None:
            return None
        ex_date, ex_t_px = nxt
        ex_b = _close_on(basket_close, ex_date)
        if ex_b is None:
            # Fall back to last available basket close on/before
            rows = basket_close.filter(
                pl.col('date') <= ex_date
            ).tail(1)
            if rows.is_empty():
                return None
            ex_b = rows.get_column('close').item()
        exit_dates = [ex_date]
        exit_target_pxs = [ex_t_px]
        exit_basket_pxs = [ex_b]
    else:
        # Planned ramp: 1/3 each of last RAMP_DAYS
        exit_dates = []
        exit_target_pxs = []
        exit_basket_pxs = []
        for d in planned_ramp:
            t_px = _close_on(target_daily, d)
            b_px = _close_on(basket_close, d)
            if t_px is None or b_px is None:
                return None
            exit_dates.append(d)
            exit_target_pxs.append(t_px)
            exit_basket_pxs.append(b_px)

    # Equal-weighted avg across ramp pieces (1/3 if not stopped,
    # 1.0 if stopped — both reduce to mean of the exit prices)
    avg_t_exit = sum(exit_target_pxs) / len(exit_target_pxs)
    avg_b_exit = sum(exit_basket_pxs) / len(exit_basket_pxs)
    exit_date = exit_dates[-1]

    # P&L — gross
    target_pnl_gross = shares * (avg_t_exit - offer_price)
    basket_ret = avg_b_exit / basket_entry - 1
    hedge_pnl_gross = -hedge_notional * basket_ret

    # Transaction costs: bps × 2 sides × notional per leg
    cost_frac = cost_bps_per_side / 10000.0
    cost_target = notional_usd * 2 * cost_frac
    cost_hedge = hedge_notional * 2 * cost_frac

    # Net of costs
    target_pnl = target_pnl_gross - cost_target
    hedge_pnl = hedge_pnl_gross - cost_hedge
    pnl_unhedged = target_pnl
    pnl_hedged = target_pnl + hedge_pnl

    return PositionResult(
        symbol=symbol,
        trade_date=trade_date,
        window_d=window_d,
        notional_usd=notional_usd,
        shares=shares,
        offer_price=offer_price,
        beta=beta,
        hedge_ratio=hedge_ratio,
        hedge_notional_usd=hedge_notional,
        target_avg_exit_px=avg_t_exit,
        basket_avg_exit_px=avg_b_exit,
        basket_entry_px=basket_entry,
        exit_date=exit_date,
        target_pnl_usd=target_pnl,
        hedge_pnl_usd=hedge_pnl,
        pnl_unhedged_usd=pnl_unhedged,
        pnl_hedged_usd=pnl_hedged,
        return_unhedged=pnl_unhedged / notional_usd,
        return_hedged=pnl_hedged / notional_usd,
        cost_bps_per_side=cost_bps_per_side,
        cost_target_usd=cost_target,
        cost_hedge_usd=cost_hedge,
        stop_basis=stop_basis,
        stop_triggered=stop_triggered,
        stop_day=stop_day,
    )
