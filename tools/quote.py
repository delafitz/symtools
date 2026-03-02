"""Fetch and print a quote table for one or more symbols.

Usage:
    uv run python tools/quote.py <symbol> [symbol ...]

Examples:
    uv run python tools/quote.py AAPL
    uv run python tools/quote.py AAPL SPY NVDA
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.mds.client import get_provider


def main() -> None:
    if len(sys.argv) < 2:
        print('usage: quote.py <symbol> [symbol ...]')
        sys.exit(1)

    mds = get_provider()
    symbols = [s.upper() for s in sys.argv[1:]]

    for sym in symbols:
        try:
            quote = mds.get_quote(sym)
            print(
                json.dumps(
                    quote.model_dump(by_alias=True), indent=2
                )
            )
        except Exception as e:
            print(f'{sym}: error — {e}', file=sys.stderr)


if __name__ == '__main__':
    main()
