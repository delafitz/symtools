"""One-shot fixer for hists parquet `date` column.

Existing parquets were written with UTC-derived dates (off by
one calendar day from the ET trading day). After fixing
`ts_to_date` to use ET, rewrite the `date` column from the
existing `timestamp` column so we don't need to re-fetch.

Usage:
    uv run python tools/fix_hists_dates.py
    uv run python tools/fix_hists_dates.py --dry-run

Idempotent — running twice produces the same result.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from app.utils.dates import ts_to_date


def fix_file(path: Path, dry_run: bool) -> None:
    df = pl.read_parquet(path)
    if 'timestamp' not in df.columns or 'date' not in df.columns:
        print(f'  skip (missing cols)')
        return

    new_date = pl.Series(
        'date',
        [ts_to_date(t) for t in df.get_column('timestamp').to_list()],
        dtype=pl.Utf8,
    )
    diff = (df.get_column('date') != new_date).sum()
    print(
        f'  rows: {len(df):>10}   '
        f'date changed: {diff:>10}   '
        f'unchanged: {len(df) - diff:>10}'
    )
    if dry_run or diff == 0:
        return

    backup = path.with_suffix(path.suffix + '.bak')
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f'  backup -> {backup.name}')

    df = df.with_columns(new_date)
    df.write_parquet(path, compression='zstd')
    print(f'  wrote -> {path.name}')


def main() -> None:
    dry_run = '--dry-run' in sys.argv
    paths = sorted(Path('data').glob('hists.*.parquet'))
    if not paths:
        print('no hists.*.parquet in data/', file=sys.stderr)
        sys.exit(1)

    for p in paths:
        print(f'\n{p.name}:')
        fix_file(p, dry_run)


if __name__ == '__main__':
    main()
