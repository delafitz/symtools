"""Survivorship audit for the block-trade backtest.

Reports the trades dropped from the source file because their
tickers are not in the current refs universe — these are
typically delisted, acquired, foreign-ADR-filtered, or below
our mkt_cap threshold. The backtest's reported returns are
implicitly conditioned on this survivor set.

Usage:
    uv run python tools/survivorship_check.py
"""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from app.utils.logger import get_logger

log = get_logger(__name__)


def latest(name: str, ext: str = 'parquet') -> Path | None:
    files = sorted(Path('data').glob(f'{name}.*.{ext}'))
    return files[-1] if files else None


def find_source_json() -> Path | None:
    files = sorted(
        Path('data').glob('block_trades_alt*.json')
    ) + sorted(Path('data').glob('block_trades*.json'))
    files = [f for f in files if not f.name.endswith('.bak')]
    return files[-1] if files else None


def main() -> None:
    refs_path = latest('refs')
    src = find_source_json()
    if refs_path is None or src is None:
        print('missing refs or block trades source', file=sys.stderr)
        sys.exit(1)

    refs = pl.read_parquet(refs_path)
    known = set(refs['symbol'].to_list())

    with open(src) as f:
        doc = json.load(f)
    deals = (
        doc['deals'] if isinstance(doc, dict) and 'deals' in doc
        else doc
    )
    n_src = len(deals)

    dropped: list[dict] = []
    for d in deals:
        sym = (d.get('ticker') or d.get('Ticker') or '').lower()
        if not sym:
            continue
        if sym not in known:
            dropped.append(d)

    pct = (len(dropped) / n_src * 100) if n_src else 0
    print(f'Source: {src.name}, {n_src} deals')
    print(
        f'Dropped (ticker not in current refs): '
        f'{len(dropped)} ({pct:.1f}%)'
    )

    if not dropped:
        return

    # Year distribution
    print('\nYear distribution of dropped trades:')
    years = Counter(
        (d.get('trade_date', '') or '')[:4] for d in dropped
    )
    for y, n in sorted(years.items()):
        print(f'  {y}: {n}')

    # Evidence (alt schema only)
    if any('evidence' in d for d in dropped):
        print('\nEvidence distribution of dropped trades:')
        ev = Counter(d.get('evidence', '') for d in dropped)
        for e, n in sorted(ev.items(), key=lambda x: -x[1]):
            print(f'  {e}: {n}')

    # Notional
    total_notional = sum(d.get('notional', 0) or 0 for d in dropped)
    if total_notional:
        print(
            f'\nTotal source notional in dropped trades: '
            f'${total_notional / 1e9:.1f}B'
        )

    # Unique tickers
    unique = sorted(
        {(d.get('ticker') or d.get('Ticker') or '').upper()
         for d in dropped}
    )
    print(f'\nUnique dropped tickers ({len(unique)}):')
    print('  ' + ', '.join(unique))


if __name__ == '__main__':
    main()
