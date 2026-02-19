"""Look up recent S-3, 424B, and 144 filings on EDGAR.

Usage:
    uv run python tools/edgar.py [symbols...]

Reads CIK from the latest data/refs.*.csv.
If no symbols given, scans all refs.

For 144 filings, parses the HTML to extract seller,
shares, market value, and date of sale. Only retains
144s with aggregate market value >= $50M.

Results written to data/filings.csv.

Examples:
    uv run python tools/edgar.py COHR CRCL MGY
    uv run python tools/edgar.py
"""

import csv
import json
import re
import sys
import time
import urllib.request
from datetime import date, timedelta
from glob import glob
from pathlib import Path

EDGAR_URL = 'https://data.sec.gov/submissions/CIK{cik}.json'
FILING_URL = (
    'https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}'
)
USER_AGENT = 'symtools dev@symtools.dev'
FORM_PREFIXES = ('S-3', '424B', '144')
LOOKBACK_DAYS = 180
MIN_144_VALUE = 50_000_000
DATA_DIR = Path(__file__).parent.parent / 'data'


def load_refs() -> dict[str, dict]:
    """Load latest refs CSV into {symbol: {name, cik}}."""
    csvs = sorted(glob(str(DATA_DIR / 'refs.*.csv')))
    if not csvs:
        print('no refs CSV found in data/')
        sys.exit(1)
    path = csvs[-1]
    refs = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            refs[row['symbol'].lower()] = row
    print(f'loaded {len(refs)} refs from {path}')
    return refs


_last_fetch = 0.0


def _fetch_url(url: str) -> bytes | None:
    global _last_fetch
    # SEC rate limit: 10 req/s
    elapsed = time.time() - _last_fetch
    if elapsed < 0.12:
        time.sleep(0.12 - elapsed)
    req = urllib.request.Request(
        url, headers={'User-Agent': USER_AGENT}
    )
    try:
        _last_fetch = time.time()
        return urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f'  error: {url}: {e}')
        return None


def fetch_filings(cik: str) -> list[dict]:
    """Fetch recent filings index from EDGAR."""
    raw = _fetch_url(EDGAR_URL.format(cik=cik))
    if not raw:
        return []

    data = json.loads(raw)
    recent = data['filings']['recent']
    cutoff = (
        date.today() - timedelta(days=LOOKBACK_DAYS)
    ).isoformat()
    results = []
    for i, form in enumerate(recent['form']):
        if recent['filingDate'][i] < cutoff:
            continue
        if not any(form.startswith(p) for p in FORM_PREFIXES):
            continue
        accession = recent['accessionNumber'][i].replace('-', '')
        cik_num = cik.lstrip('0')
        results.append(
            {
                'date': recent['filingDate'][i],
                'form': form,
                'doc': recent['primaryDocument'][i],
                'url': FILING_URL.format(
                    cik=cik_num,
                    accession=accession,
                    doc=recent['primaryDocument'][i],
                ),
            }
        )
    return results


def parse_144(url: str) -> dict | None:
    """Parse a 144 filing HTML for seller, shares,
    market value, sale date.

    Returns None if mkt_value < MIN_144_VALUE.
    """
    raw = _fetch_url(url)
    if not raw:
        return None
    html = raw.decode('latin-1')

    # Extract account (seller)
    seller = ''
    idx = html.find('Account the Securities')
    if idx > 0:
        m = re.search(r'fakeBox">(.*?)</div>', html[idx : idx + 500])
        if m:
            seller = m.group(1).strip()

    # Extract securities table cells
    idx2 = html.find('Securities Information')
    if idx2 < 0:
        return None
    chunk = html[idx2 : idx2 + 2000]
    tds = re.findall(
        r'<td[^>]*class="tableClass"[^>]*>(.*?)</td>',
        chunk,
        re.DOTALL,
    )
    # cells: [0] title, [1] broker, [2] shares,
    #        [3] mkt_value, [4] outstanding,
    #        [5] sale_date, [6] exchange
    if len(tds) < 6:
        return None

    def clean(s: str) -> str:
        return re.sub(r'<[^>]+>', ' ', s).strip()

    try:
        shares = int(clean(tds[2]).replace(',', ''))
        mkt_value = float(clean(tds[3]).replace(',', ''))
    except (ValueError, IndexError):
        return None

    if mkt_value < MIN_144_VALUE:
        return None

    sale_date = clean(tds[5])

    return {
        'seller': seller,
        'shares': shares,
        'mkt_value': mkt_value,
        'sale_date': sale_date,
    }


def main():
    refs = load_refs()
    args = sys.argv[1:]

    # Parse --top N flag
    top_n = None
    if '--top' in args:
        idx = args.index('--top')
        top_n = int(args[idx + 1])
        args = args[:idx] + args[idx + 2 :]

    if args:
        symbols = [s.lower() for s in args]
    elif top_n:
        # Sort refs by mkt_cap descending, take top N
        by_cap = sorted(
            refs.items(),
            key=lambda kv: float(kv[1].get('mkt_cap', 0)),
            reverse=True,
        )
        symbols = [k for k, _ in by_cap[:top_n]]
        print(f'scanning top {top_n} by mkt_cap')
    else:
        symbols = list(refs.keys())

    blocks: list[dict] = []
    found = 0
    total = len(symbols)
    for idx, sym in enumerate(symbols):
        if (idx + 1) % 25 == 0:
            print(f'... {idx + 1}/{total}')
        ref = refs.get(sym)
        if not ref:
            print(f'{sym.upper()}: not in refs')
            continue
        cik = ref['cik']
        if not cik:
            continue
        filings = fetch_filings(cik)
        if not filings:
            continue

        sym_printed = False
        # Group 424B filings by form type for summary
        b424_counts: dict[str, int] = {}
        s3_filings = []
        f144_filings = []
        for f in filings:
            if f['form'].startswith('144'):
                f144_filings.append(f)
            elif f['form'].startswith('S-3'):
                s3_filings.append(f)
            elif f['form'].startswith('424B'):
                b424_counts[f['form']] = (
                    b424_counts.get(f['form'], 0) + 1
                )

        # Skip if nothing interesting
        if not s3_filings and not f144_filings and not b424_counts:
            continue

        def print_header():
            nonlocal sym_printed, found
            if not sym_printed:
                found += 1
                print(f'\n{sym.upper()} ({ref["name"]}) CIK={cik}')
                sym_printed = True

        # Print S-3 filings individually
        for f in s3_filings:
            print_header()
            print(f'  {f["date"]}  {f["form"]:12s}  {f["url"]}')

        # Summarize 424B filings by type
        if b424_counts:
            print_header()
            parts = [
                f'{form}={n}'
                for form, n in sorted(b424_counts.items())
            ]
            print(f'  424B: {", ".join(parts)}')

        # Process 144 filings (fetch + parse each)
        for f in f144_filings:
            detail = parse_144(f['url'])
            if not detail:
                continue
            print_header()
            print(
                f'  {f["date"]}  {f["form"]:12s}'
                f'  {detail["shares"]:>12,} shares'
                f'  ${detail["mkt_value"]:>15,.0f}'
                f'  {detail["seller"]}'
            )
            blocks.append(
                {
                    'symbol': sym,
                    'date': detail['sale_date'],
                    'shares': detail['shares'],
                    'mkt_value': detail['mkt_value'],
                    'seller': detail['seller'],
                }
            )

    print(f'\n{found} symbols with filings')

    if blocks:
        out = DATA_DIR / 'filings.csv'
        with open(out, 'w', newline='') as fh:
            w = csv.DictWriter(
                fh,
                fieldnames=[
                    'symbol',
                    'date',
                    'shares',
                    'mkt_value',
                    'seller',
                ],
            )
            w.writeheader()
            w.writerows(blocks)
        print(f'wrote {len(blocks)} blocks to {out}')


if __name__ == '__main__':
    main()
