"""SIC description -> GICS sector mapping.

Keyword-based mapper from Polygon SIC descriptions
to 11 GICS sectors. Checked in order against
lowercased description; first match wins.

When real GICS data arrives (via tools/gics.py),
swap the source of g_sector — consumers unchanged.
"""

GICS_SECTORS = [
    'Energy',
    'Materials',
    'Industrials',
    'Consumer Discretionary',
    'Consumer Staples',
    'Health Care',
    'Financials',
    'Information Technology',
    'Communication Services',
    'Utilities',
    'Real Estate',
]

# (keyword_substring, gics_sector) — checked in order
SIC_TO_GICS: list[tuple[str, str]] = [
    # Energy
    ('crude petroleum', 'Energy'),
    ('petroleum refin', 'Energy'),
    ('natural gas', 'Energy'),
    ('oil & gas', 'Energy'),
    ('pipeline', 'Energy'),
    ('drilling oil', 'Energy'),
    ('bituminous coal', 'Energy'),
    # Health Care
    ('pharmaceutical', 'Health Care'),
    ('biological', 'Health Care'),
    ('surgical', 'Health Care'),
    ('electromedical', 'Health Care'),
    ('dental', 'Health Care'),
    ('hospital', 'Health Care'),
    ('medical instrument', 'Health Care'),
    ('in vitro', 'Health Care'),
    ('orthopedic', 'Health Care'),
    ('health service', 'Health Care'),
    # Real Estate
    ('real estate', 'Real Estate'),
    # Utilities
    ('electric service', 'Utilities'),
    ('gas distribution', 'Utilities'),
    ('water supply', 'Utilities'),
    ('combination utility', 'Utilities'),
    ('cogeneration', 'Utilities'),
    ('sewerage system', 'Utilities'),
    # Information Technology
    ('semiconductor', 'Information Technology'),
    ('prepackaged software', 'Information Technology'),
    ('computer', 'Information Technology'),
    ('electronic component', 'Information Technology'),
    ('printed circuit', 'Information Technology'),
    ('calculating & accounting', 'Information Technology'),
    ('electronic connector', 'Information Technology'),
    # Communication Services
    ('radio & tv broadcast', 'Communication Services'),
    ('cable & other pay tv', 'Communication Services'),
    ('telephone', 'Communication Services'),
    ('communication', 'Communication Services'),
    ('book', 'Communication Services'),
    ('newspaper', 'Communication Services'),
    ('motion picture', 'Communication Services'),
    # Financials
    (
        'national commercial bank',
        'Financials',
    ),
    ('state commercial bank', 'Financials'),
    ('savings institution', 'Financials'),
    ('insurance', 'Financials'),
    ('security broker', 'Financials'),
    ('investment', 'Financials'),
    ('finance', 'Financials'),
    ('loan', 'Financials'),
    ('bank', 'Financials'),
    ('blank checks', 'Financials'),
    # Consumer Staples
    ('food', 'Consumer Staples'),
    ('beverage', 'Consumer Staples'),
    ('tobacco', 'Consumer Staples'),
    ('soap', 'Consumer Staples'),
    ('perfume', 'Consumer Staples'),
    ('grocery', 'Consumer Staples'),
    ('grain mill', 'Consumer Staples'),
    # Consumer Discretionary
    ('retail', 'Consumer Discretionary'),
    ('motor vehicle', 'Consumer Discretionary'),
    ('eating place', 'Consumer Discretionary'),
    ('hotel', 'Consumer Discretionary'),
    ('apparel', 'Consumer Discretionary'),
    ('footwear', 'Consumer Discretionary'),
    ('furniture', 'Consumer Discretionary'),
    ('hobby', 'Consumer Discretionary'),
    ('amusement', 'Consumer Discretionary'),
    ('home furnishing', 'Consumer Discretionary'),
    ('household audio', 'Consumer Discretionary'),
    ('catalog', 'Consumer Discretionary'),
    # Materials
    ('chemical', 'Materials'),
    ('plastic', 'Materials'),
    ('steel', 'Materials'),
    ('aluminum', 'Materials'),
    ('metal mining', 'Materials'),
    ('copper', 'Materials'),
    ('gold', 'Materials'),
    ('paper', 'Materials'),
    ('lumber', 'Materials'),
    ('glass', 'Materials'),
    ('cement', 'Materials'),
    ('adhesive', 'Materials'),
    # Industrials
    ('aerospace', 'Industrials'),
    ('aircraft', 'Industrials'),
    ('railroad', 'Industrials'),
    ('trucking', 'Industrials'),
    ('air transport', 'Industrials'),
    ('freight', 'Industrials'),
    ('industrial', 'Industrials'),
    ('construction', 'Industrials'),
    ('machinery', 'Industrials'),
    ('farm machinery', 'Industrials'),
    ('engine', 'Industrials'),
    ('electrical', 'Industrials'),
    ('measuring', 'Industrials'),
    ('refuse system', 'Industrials'),
    ('services-misc', 'Industrials'),
    ('service', 'Industrials'),
]


def sic_to_sector(sic_desc: str) -> str:
    """Map SIC description to GICS sector.

    Returns '' if no match.
    """
    lower = sic_desc.lower()
    for keyword, sector in SIC_TO_GICS:
        if keyword in lower:
            return sector
    return ''
