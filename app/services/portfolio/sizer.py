"""Position sizing for the portfolio backtest.

Notional size is a fixed fraction of trailing ADV ($), clamped
to a global floor and cap. The fraction is parameterized so we
can sweep across strategies.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SizeParams:
    pct_adv: float = 0.15
    floor_usd: float = 10_000_000
    cap_usd: float = 100_000_000


def size_position(adv_usd: float, params: SizeParams) -> float:
    """Notional $ for one position. Returns 0 if adv_usd is
    missing or non-positive."""
    if not adv_usd or adv_usd <= 0:
        return 0.0
    raw = params.pct_adv * adv_usd
    return max(params.floor_usd, min(params.cap_usd, raw))
