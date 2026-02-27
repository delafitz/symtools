"""Download GICS sector/industry classifications for refs symbols.

Source: Yahoo Finance assetProfile (sector, industry).

Usage:
    uv run python tools/gics.py [symbols...]
    uv run python tools/gics.py --top 500
    uv run python tools/gics.py --refresh

Reads symbols from the latest data/refs.*.csv.
If no symbols given, fetches all refs not already cached.

Results written to data/gics.csv.

Flags:
    --top N     Limit to top N symbols by market cap
    --refresh   Re-fetch all (ignore cached gics.csv)

Examples:
    uv run python tools/gics.py AAPL MSFT NVDA
    uv run python tools/gics.py --top 500
    uv run python tools/gics.py
"""

import asyncio
import csv
import json
import random
import sys
import time
import urllib.error
import urllib.request
from glob import glob
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / 'data'
OUT_FILE = DATA_DIR / 'gics.csv'
FIELDS = ['symbol', 'sector', 'industry']

CONCURRENCY = 8  # parallel workers
JITTER_S = (0.05, 0.4)  # random per-request delay range (seconds)
TIMEOUT_S = 12
MAX_RETRIES = 3
RETRY_BASE_S = 2.0  # base for exponential backoff on 429

# Yahoo Finance assetProfile endpoint
YAHOO_URL = (
    'https://query2.finance.yahoo.com/v11/finance/quoteSummary/'
    '{symbol}?modules=assetProfile'
)
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
        ' AppleWebKit/537.36 (KHTML, like Gecko)'
        ' Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json',
}


def load_refs() -> list[dict]:
    """Load latest refs CSV, sorted by mkt_cap descending."""
    csvs = sorted(glob(str(DATA_DIR / 'refs.*.csv')))
    if not csvs:
        print('no refs CSV found in data/')
        sys.exit(1)
    path = csvs[-1]
    rows = []
    with open(path) as f:
        for row in csv.DictReader(f):
            rows.append(row)
    rows.sort(
        key=lambda r: float(r.get('mkt_cap') or 0), reverse=True
    )
    print(f'loaded {len(rows)} refs from {path}')
    return rows


def load_existing() -> dict[str, dict]:
    """Load already-fetched gics.csv into {symbol: row}."""
    if not OUT_FILE.exists():
        return {}
    with open(OUT_FILE) as f:
        rows = {r['symbol']: r for r in csv.DictReader(f)}
    print(f'loaded {len(rows)} cached entries from {OUT_FILE}')
    return rows


def _fetch_sync(symbol: str) -> tuple[str, str] | None:
    """Blocking fetch with retry/backoff. Runs in a thread."""
    url = YAHOO_URL.format(symbol=symbol.upper())
    req = urllib.request.Request(url, headers=HEADERS)

    for attempt in range(MAX_RETRIES):
        # Jitter before every attempt (including first)
        time.sleep(random.uniform(*JITTER_S))
        try:
            with urllib.request.urlopen(
                req, timeout=TIMEOUT_S
            ) as resp:
                data = json.loads(resp.read())
            result = data.get('quoteSummary', {}).get('result') or []
            if not result:
                return None
            profile = result[0].get('assetProfile') or {}
            sector = profile.get('sector') or ''
            industry = profile.get('industry') or ''
            return (sector, industry) if sector else None

        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                backoff = RETRY_BASE_S * (2**attempt)
                print(
                    f'  {symbol}: 429 rate-limited, '
                    f'retry {attempt + 1}/{MAX_RETRIES} '
                    f'in {backoff:.1f}s'
                )
                time.sleep(backoff)
            else:
                print(f'  {symbol}: HTTP {e.code}')
                return None
        except Exception as e:
            print(f'  {symbol}: {e}')
            return None

    print(f'  {symbol}: gave up after {MAX_RETRIES} retries')
    return None


async def _fetch(
    sem: asyncio.Semaphore,
    symbol: str,
    counter: list[int],
    total: int,
) -> tuple[str, tuple[str, str] | None]:
    async with sem:
        result = await asyncio.to_thread(_fetch_sync, symbol)
        counter[0] += 1
        if counter[0] % 50 == 0:
            print(f'  {counter[0]}/{total}')
        return symbol, result


def write_csv(rows: dict[str, dict]) -> None:
    all_rows = sorted(rows.values(), key=lambda r: r['symbol'])
    with open(OUT_FILE, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    print(f'wrote {len(all_rows)} rows to {OUT_FILE}')


async def run(symbols: list[str], existing: dict[str, dict]) -> None:
    sem = asyncio.Semaphore(CONCURRENCY)
    counter = [0]
    total = len(symbols)

    tasks = [_fetch(sem, sym, counter, total) for sym in symbols]
    results = await asyncio.gather(*tasks)

    rows: dict[str, dict] = dict(existing)
    errors = 0
    for sym, result in results:
        if result:
            sector, industry = result
            rows[sym] = {
                'symbol': sym,
                'sector': sector,
                'industry': industry,
            }
        else:
            errors += 1

    print(
        f'\nfetched {total - errors}/{total} '
        f'({errors} errors/not found)'
    )
    write_csv(rows)


def main() -> None:
    args = sys.argv[1:]

    refresh = '--refresh' in args
    if refresh:
        args = [a for a in args if a != '--refresh']

    top_n: int | None = None
    if '--top' in args:
        idx = args.index('--top')
        top_n = int(args[idx + 1])
        args = args[:idx] + args[idx + 2 :]

    refs = load_refs()
    existing = {} if refresh else load_existing()

    if args:
        # Specific symbols — always fetch fresh
        symbols = [s.lower() for s in args]
    else:
        ordered = [r['symbol'].lower() for r in refs]
        if top_n is not None:
            ordered = ordered[:top_n]
            print(f'limiting to top {top_n} by mkt_cap')
        symbols = [s for s in ordered if s not in existing]
        skipped = len(ordered) - len(symbols)
        if skipped:
            print(f'skipping {skipped} already cached')

    if not symbols:
        print('nothing to fetch')
        write_csv(existing)
        return

    print(
        f'fetching {len(symbols)} symbols ({CONCURRENCY} workers)...'
    )
    asyncio.run(run(symbols, existing))


if __name__ == '__main__':
    main()
