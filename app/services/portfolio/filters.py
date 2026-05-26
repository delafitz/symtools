"""Per-trade strategy filters that scale intended notional based
on pre-trade characteristics. Applied BEFORE the GMV cap so that
the cap operates on the strategy-adjusted intended book size.

Convention: each filter takes a trade context dict (with keys
like `broker`, `sector`, etc.) and returns a multiplier in
[0.0, 2.0]. A multiplier of 0 means "skip this trade entirely";
1.0 is "take the trade at full intended size"; >1 is an upsize.

The default `bank_filter` combines `half_bad_bank` and
`chase_citi`. On the cleaned 283-trade population it lifts
portfolio Sharpe from +1.61 (defaults-only) to +1.77 at $235M
avg GMV (up from $265M baseline because chase_citi adds size
back). The single-axis `quarter_bad_bank` is slightly better
on Sharpe (+1.86) but at lower total participation; `half +
chase` is the Pareto-balanced choice.
"""

# Bank cohort definitions (h20 means on 283-trade clean data).
BAD_BANKS: set[str] = {'JPM', 'MS'}   # JPM h20 −0.9%, MS h20 0.0%
GOOD_BANKS: set[str] = {'C'}          # Citi h20 +3.8% (n=37)


def half_bad_bank(broker: str | None) -> float:
    """0.5× for JPM and MS."""
    return 0.5 if broker in BAD_BANKS else 1.0


def chase_citi(broker: str | None) -> float:
    """1.5× for Citi."""
    return 1.5 if broker in GOOD_BANKS else 1.0


def bank_filter(broker: str | None) -> float:
    """Default bank multiplier: `half_bad_bank` × `chase_citi`.

    JPM/MS: 0.5 · Citi: 1.5 · everything else: 1.0.
    """
    return half_bad_bank(broker) * chase_citi(broker)


def no_filter(broker: str | None) -> float:
    return 1.0


def half_jpm_only(broker: str | None) -> float:
    return 0.5 if broker == 'JPM' else 1.0


def skip_jpm_only(broker: str | None) -> float:
    return 0.0 if broker == 'JPM' else 1.0


def quarter_bad_bank(broker: str | None) -> float:
    return 0.25 if broker in BAD_BANKS else 1.0


def skip_bad_bank(broker: str | None) -> float:
    return 0.0 if broker in BAD_BANKS else 1.0


# Variant registry keyed by short name. Used by the bank-filter
# sweep in `tools/portfolio_bank_sweep.py`.
BANK_FILTER_VARIANTS: dict[str, object] = {
    'baseline': no_filter,
    'half_jpm_only': half_jpm_only,
    'skip_jpm_only': skip_jpm_only,
    'half_bad_bank': half_bad_bank,
    'quarter_bad_bank': quarter_bad_bank,
    'skip_bad_bank': skip_bad_bank,
    'chase_citi': chase_citi,
    'half_bad_bank+chase_citi': bank_filter,
}
