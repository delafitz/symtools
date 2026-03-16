import blpapi
import polars as pl

from app.mds.bloomberg.session import (
    REFDATA_SVC,
    collect,
    sec,
)
from app.mds.polygon.refs import TICKER_SCHEMA
from app.utils.corp import strip_name
from app.utils.logger import get_logger

log = get_logger(__name__)

# Broad indices for universe bootstrapping
UNIVERSE_INDICES = ['RAY Index']

MIN_MKT_CAP = 1e9

DETAIL_FIELDS = [
    'CIK_NUMBER',
    'SIC_CODE',
    'GICS_SECTOR_NAME',
    'EQY_SH_OUT',   # millions
    'CUR_MKT_CAP',  # millions
]

FLOAT_FIELDS = [
    'EQY_FLOAT',  # millions
    'EQY_FREE_FLOAT_PCT',
]

SI_FIELDS = [
    'SHORT_INT',
    'DAYS_TO_COVER_RATIO',
    'AVG_DAILY_VOL_20D',
    'SHORT_INT_DATE',
]


def list_tickers(
    session: blpapi.Session,
    max_count: int,
) -> pl.DataFrame:
    """Bootstrap ticker universe from indices."""
    svc = session.getService(REFDATA_SVC)

    # Step 1: index members
    members: set[str] = set()
    for index in UNIVERSE_INDICES:
        req = svc.createRequest('ReferenceDataRequest')
        req.fromPy(
            {
                'securities': [index],
                'fields': ['INDX_MEMBERS'],
            }
        )
        session.sendRequest(req)
        for data in collect(session):
            for s in data['securityData']:
                fd = s.get('fieldData', {})
                for m in fd.get('INDX_MEMBERS', []):
                    t = m.get(
                        'Member Ticker and Exchange Code',
                        '',
                    )
                    if t:
                        members.add(t.strip())

    symbols = sorted(members)[:max_count]
    log.info(f'list_tickers: {len(symbols)} from indices')

    # Step 2: basic ref fields
    batch = [sec(s) for s in symbols]
    req = svc.createRequest('ReferenceDataRequest')
    req.fromPy(
        {
            'securities': batch,
            'fields': [
                'TICKER',
                'EXCH_CODE',
                'NAME',
                'CRNCY',
            ],
        }
    )
    session.sendRequest(req)

    rows = []
    for data in collect(session):
        for s in data['securityData']:
            fd = s.get('fieldData', {})
            rows.append(
                [
                    fd.get('TICKER', '').lower(),
                    fd.get('EXCH_CODE', ''),
                    strip_name(fd.get('NAME', '')),
                    fd.get('CRNCY', 'USD'),
                ]
            )

    log.info(f'list_tickers: returning {len(rows)} tickers')
    return pl.DataFrame(
        data=rows,
        schema=TICKER_SCHEMA,
        orient='row',
    )


def fetch_details(
    session: blpapi.Session,
    symbol: str,
) -> dict | None:
    """Fetch ticker details."""
    svc = session.getService(REFDATA_SVC)
    req = svc.createRequest('ReferenceDataRequest')
    req.fromPy(
        {
            'securities': [sec(symbol)],
            'fields': DETAIL_FIELDS,
        }
    )
    session.sendRequest(req)

    for data in collect(session):
        for s in data['securityData']:
            fd = s.get('fieldData', {})
            mkt_cap = fd.get('CUR_MKT_CAP', 0)
            if mkt_cap and mkt_cap > MIN_MKT_CAP:
                shares = fd.get('EQY_SH_OUT', 0)
                return {
                    'cik': str(fd.get('CIK_NUMBER', '')),
                    'sic': str(fd.get('SIC_CODE', '')),
                    'shares_out': (
                        int(shares * 1e6) if shares else 0
                    ),
                    'mkt_cap': float(mkt_cap) * 1e6,
                    'g_sector': fd.get(
                        'GICS_SECTOR_NAME', ''
                    ),
                }
    return None


def fetch_float(
    session: blpapi.Session,
    symbol: str,
    quiet: bool = False,
) -> dict | None:
    """Fetch free float data."""
    svc = session.getService(REFDATA_SVC)
    req = svc.createRequest('ReferenceDataRequest')
    req.fromPy(
        {
            'securities': [sec(symbol)],
            'fields': FLOAT_FIELDS,
        }
    )
    session.sendRequest(req)

    for data in collect(session):
        for s in data['securityData']:
            fd = s.get('fieldData', {})
            ff = fd.get('EQY_FLOAT')
            if ff:
                pct = fd.get('EQY_FREE_FLOAT_PCT', 0.0)
                if not quiet:
                    log.info(
                        f'{symbol.upper()} free_float: {pct:.1f}%'
                    )
                return {
                    'ticker': symbol.upper(),
                    'effective_date': '',
                    'free_float': int(ff * 1e6),
                    'free_float_percent': float(pct),
                }
    return None


def fetch_short_interest(
    session: blpapi.Session,
    symbol: str,
    quiet: bool = False,
) -> dict | None:
    """Fetch short interest data."""
    svc = session.getService(REFDATA_SVC)
    req = svc.createRequest('ReferenceDataRequest')
    req.fromPy(
        {
            'securities': [sec(symbol)],
            'fields': SI_FIELDS,
        }
    )
    session.sendRequest(req)

    for data in collect(session):
        for s in data['securityData']:
            fd = s.get('fieldData', {})
            si = fd.get('SHORT_INT')
            if si:
                dtc = fd.get('DAYS_TO_COVER_RATIO', 0.0)
                adv = fd.get('AVG_DAILY_VOL_20D', 0)
                si_date = fd.get('SHORT_INT_DATE', '')
                if not quiet:
                    log.info(
                        f'{symbol.upper()} '
                        f'short_interest: '
                        f'{si:,} '
                        f'({dtc:.1f} DTC)'
                    )
                return {
                    'ticker': symbol.upper(),
                    'settlement_date': str(si_date),
                    'short_interest': int(si),
                    'days_to_cover': float(dtc),
                    'avg_daily_volume': int(adv),
                }
    return None
