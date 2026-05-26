"""Per-trade strategy filters that scale intended notional based
on pre-trade characteristics. Multipliers are passed to
`sizer.size_position(..., pre_clip_mult=...)` so the position
cap ($100M), deal_pct cap (30%), and VaR cap ($50M) bind on
upsized trades.

Convention: each filter takes one piece of pre-trade context
(broker for bank filters, GICS sector for sector filters) and
returns a multiplier in [0.0, 2.0]. A multiplier of 0 means
"skip this trade entirely"; 1.0 is "take at full intended
size"; >1 is an upsize.

Default stack: `bank_filter` + `sector_filter`. On the cleaned
283-trade population they lift Sharpe +1.61 → +1.68 (bank)
→ +1.75 (bank + sector).
"""

# Bank cohort definitions (h20 means on 283-trade clean data).
BAD_BANKS: set[str] = {'JPM', 'MS'}   # JPM h20 −0.9%, MS h20 0.0%
GOOD_BANKS: set[str] = {'C'}          # Citi h20 +3.8% (n=37)

# Sector cohorts. Three negative/near-flat cohorts whose
# elimination clears GMV-cap room for the remaining (positive-
# cohort) population without admitting net-negative marginal
# trades.
BAD_SECTORS: set[str] = {
    'Real Estate',   # n=23, h20 −1.19%
    'Health Care',   # n=14, h20 −1.30%
    'Utilities',     # n=4,  h20 −0.29% (small-n; included for
                     # categorical cleanliness — adds −0.02
                     # Sharpe vs RE+HC alone)
}


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


def sector_filter(sector: str | None) -> float:
    """Default sector multiplier: skip Real Estate, Health Care,
    and Utilities.

    Three negative/near-flat cohorts. Skipping them frees GMV-
    cap room for the remaining (positive-cohort) population
    without admitting net-negative marginal trades (which is
    what downsize-only variants do — the cap re-fills with
    cap-binding-day trades, partially offsetting the savings).
    """
    return 0.0 if sector in BAD_SECTORS else 1.0


def no_sector_filter(sector: str | None) -> float:
    return 1.0


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
