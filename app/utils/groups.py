# Symbol type constants
TYPE_STOCK = 'stock'
TYPE_ETF_INDEX = 'etf_index'
TYPE_ETF_FACTOR = 'etf_factor'

# ETF symbol lists (lowercase)
INDEX_ETFS = 'spy,qqq,iwm'
SECTOR_ETFS = (
    'xlf,xlk,xlv,xlc,xle,xli,xly,xrt,xlu,xlp,kre,xbi,xme,xop,xhb,xlb'
)
ISHARES_ETFS = 'mtum,soxx,igv,iyr,itb,ibb'
OTHER_ETFS = 'gld,uso,ibit'

# Group keys (used for scenario building)
INDICES = 'indices'
FACTORS = 'factors'
SINGLES = 'singles'

# Group key for combined scenario
COMBINED = 'combined'

# Scenario definitions: name -> (label, groups)
# Groups are filtered from unified hists by type
SCENARIOS = {
    'indices': ('Indices', [INDICES]),
    'factors': ('Factors', [FACTORS]),
    'singles': ('Singles', [SINGLES]),
    'combined': ('Combined', [INDICES, FACTORS, SINGLES]),
}


def build_group(etfs):
    return [sym for syms in etfs for sym in syms.split(',')]


def get_etf_groups():
    """Get ETF groups for loading and type assignment."""
    return {
        INDICES: build_group([INDEX_ETFS]),
        FACTORS: build_group([SECTOR_ETFS, ISHARES_ETFS]),
    }


def get_all_etf_symbols() -> list[str]:
    """Get all ETF symbols (indices + factors)."""
    groups = get_etf_groups()
    return groups[INDICES] + groups[FACTORS]


def get_symbol_type(symbol: str) -> str:
    """Get the type of a symbol."""
    groups = get_etf_groups()
    if symbol in groups[INDICES]:
        return TYPE_ETF_INDEX
    if symbol in groups[FACTORS]:
        return TYPE_ETF_FACTOR
    return TYPE_STOCK
