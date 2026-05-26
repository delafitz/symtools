"""Position sizing for the portfolio backtest.

Notional size is a fixed fraction of trailing ADV ($), clamped
to a global floor and cap. An optional VaR cap further scales
the position down so that hedged 99% VaR (at the configured
horizon) does not exceed `var_cap_usd`.
"""

from dataclasses import dataclass

Z_99 = 2.3263478740408408  # one-sided 99% normal
DAILY_ANN = 252 ** 0.5 * 100  # vol stored as annualized %


@dataclass(frozen=True)
class SizeParams:
    pct_adv: float = 0.15
    floor_usd: float = 10_000_000
    cap_usd: float = 75_000_000
    # Soft cap on hedged 99% VaR per position. Set to None
    # to disable. Default $50M is permissive — caps only the
    # extreme-vol outliers (~5% of trades) at a tiny return
    # cost while bounding single-position tail risk.
    var_cap_usd: float | None = 50_000_000
    var_horizon_days: int = 20
    # Max position as a fraction of the actual block size.
    # You can't buy more than what the broker is selling; in
    # practice institutional allocations rarely exceed 30% of
    # a deal. Default 0.30. Caps ~4% of trades in the current
    # population (notably RDDT at 79%, WBD at 75%, PM at 60%).
    # Pass deal_size_usd to size_position() to apply.
    deal_pct: float = 0.30


def size_position(
    adv_usd: float,
    params: SizeParams,
    *,
    vol_90d_annual_pct: float | None = None,
    corr: float | None = None,
    deal_size_usd: float | None = None,
    pre_clip_mult: float = 1.0,
) -> float:
    """Notional $ for one position.

    Returns 0 if adv_usd is missing/non-positive. When
    `params.var_cap_usd` is set, also requires `vol_90d` (and
    optionally `corr`) to apply the VaR-based cap; if those
    are missing, the VaR cap is silently skipped.

    When `deal_size_usd` is provided, the position is also
    capped at `params.deal_pct × deal_size_usd` — you can't
    take more of the block than the broker is selling.

    `pre_clip_mult` scales the raw `pct_adv × ADV` target
    BEFORE the global cap/floor/VaR/deal-pct clips are
    applied, so strategy filters (e.g. bank or sector
    multipliers) cannot push a position past the hard caps.
    """
    if not adv_usd or adv_usd <= 0:
        return 0.0
    if pre_clip_mult <= 0:
        return 0.0
    raw = params.pct_adv * adv_usd * pre_clip_mult
    notional = max(params.floor_usd, min(params.cap_usd, raw))

    if (
        params.var_cap_usd
        and vol_90d_annual_pct
        and vol_90d_annual_pct > 0
    ):
        sig_daily = vol_90d_annual_pct / DAILY_ANN
        rho = max(0.0, min(0.99, corr or 0.0))
        sig_hedged = sig_daily * ((1 - rho * rho) ** 0.5)
        if sig_hedged > 0:
            max_n = params.var_cap_usd / (
                sig_hedged
                * (params.var_horizon_days ** 0.5)
                * Z_99
            )
            notional = min(notional, max_n)

    if deal_size_usd and deal_size_usd > 0:
        notional = min(notional, params.deal_pct * deal_size_usd)

    return max(0.0, notional)
