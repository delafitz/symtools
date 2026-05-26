"""Manual GICS sector overrides for tickers where Polygon's
SIC code doesn't map cleanly to a g_sector value.

Applied during refs load to fill the "unknown" sector bucket.
Symbols are lowercase to match the rest of the system.

Rationale: ~31 tickers in the block-trade backtest were
landing in the "(unknown)" sector bucket because either
(a) Polygon doesn't return a SIC for foreign-domicile US
listings (BEP, GFL, VIK, etc.) or (b) the SIC code is
present but our SIC→GICS map has a gap (COHR, GEHC, etc.).
All 31 are well-known US-listed companies with obvious GICS
classifications.
"""

SECTOR_OVERRIDES: dict[str, str] = {
    # Industrials
    'nxt':   'Industrials',
    'rdw':   'Industrials',
    'carr':  'Industrials',
    'vnt':   'Industrials',
    'blbd':  'Industrials',
    'eslt':  'Industrials',
    'cnm':   'Industrials',
    'gfl':   'Industrials',
    'trmd':  'Industrials',
    'zim':   'Industrials',
    # Health Care
    'gehc':  'Health Care',
    'cor':   'Health Care',
    'brkr':  'Health Care',
    # Consumer Staples
    'pm':    'Consumer Staples',
    'ccep':  'Consumer Staples',
    'bg':    'Consumer Staples',
    'calm':  'Consumer Staples',
    'dole':  'Consumer Staples',
    # Materials
    'uec':   'Materials',
    'meoh':  'Materials',
    'eqx':   'Materials',
    'vzla':  'Materials',
    # Tech+Comm (IT and Communication Services merged into a
    # single bucket — too entangled in cross-sector betas to
    # treat separately for block-hedge analysis)
    'cohr':  'Tech+Comm',
    'fox':   'Tech+Comm',
    'fwonk': 'Tech+Comm',
    'geni':  'Tech+Comm',
    # Consumer Discretionary
    'sn':    'Consumer Discretionary',
    'as':    'Consumer Discretionary',
    'vik':   'Consumer Discretionary',
    # Utilities
    'bep':   'Utilities',
    # Financials
    'mrx':   'Financials',
}


def apply_sector_overrides(symbol: str, current: str | None) -> str:
    """Return the override sector if defined, else the current
    value (or empty string if None). Symbol is lowercased."""
    s = symbol.lower() if symbol else ''
    if s in SECTOR_OVERRIDES:
        return SECTOR_OVERRIDES[s]
    return current or ''
