"""Ex-ante expected return and 99% VaR for one position.

Placeholder model:
  - Expected return (both hedged and unhedged): −discount.
    The buyer's edge at offer entry, assuming target reverts
    to pre-block close on average.
  - 99% VaR parametric:
      unhedged: notional × σ_daily × √horizon × z_99
      hedged:   notional × σ_daily × √(1 − ρ²) × √horizon × z_99
    where σ is the target's 90d realized vol (annualized, %)
    and ρ is the basket-target correlation stored on the basket.

This is the v0 / discount-based predictor. We'll iterate
toward decile-conditional and/or regression-based predictors
later.
"""

from dataclasses import dataclass

Z_99 = 2.3263478740408408  # one-sided 99% normal critical value
DAILY_ANN = 252 ** 0.5 * 100  # vol stored as annualized %, like cost.py


@dataclass(frozen=True)
class Expected:
    expected_return_unhedged: float
    expected_return_hedged: float
    expected_pnl_unhedged_usd: float
    expected_pnl_hedged_usd: float
    var99_unhedged_usd: float
    var99_hedged_usd: float
    sigma_daily: float
    rho_used: float


def compute_expected(
    actual_discount: float,
    vol_90d_annual_pct: float,
    basket_target_corr: float,
    notional_usd: float,
    window_d: int,
) -> Expected | None:
    """Build the ex-ante expected struct. Returns None if vol
    is missing/invalid."""
    if not vol_90d_annual_pct or vol_90d_annual_pct <= 0:
        return None
    sig_daily = vol_90d_annual_pct / DAILY_ANN
    rho = max(0.0, min(0.99, basket_target_corr or 0.0))
    sig_hedged = sig_daily * ((1 - rho * rho) ** 0.5)

    er = -actual_discount  # = +discount magnitude
    exp_pnl_unh = er * notional_usd
    exp_pnl_hed = er * notional_usd  # placeholder: same expectation

    var_unh = (
        notional_usd * sig_daily * (window_d ** 0.5) * Z_99
    )
    var_hed = (
        notional_usd * sig_hedged * (window_d ** 0.5) * Z_99
    )

    return Expected(
        expected_return_unhedged=er,
        expected_return_hedged=er,
        expected_pnl_unhedged_usd=exp_pnl_unh,
        expected_pnl_hedged_usd=exp_pnl_hed,
        var99_unhedged_usd=var_unh,
        var99_hedged_usd=var_hed,
        sigma_daily=sig_daily,
        rho_used=rho,
    )
