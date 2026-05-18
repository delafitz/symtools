"""Backfill Y hists to the new max_scale depth.

Re-fetches the Y template only for symbols already in the
existing hists.parquet, then replaces the Y rows. W/D/M
intraday data (anchored to recent dates, not affected by
depth) is preserved.

Usage:
    uv run python tools/backfill_hists.py
    uv run python tools/backfill_hists.py --concurrency 20
    uv run python tools/backfill_hists.py --dry-run

Idempotent — running again after a successful run is a no-op
in cost (re-fetches, replaces).
"""

import asyncio
import shutil
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl

from app.mds.client import get_provider
from app.services.prices import HIST_TEMPLATE_DEFAULT, HIST_TEMPLATES
from app.utils.logger import get_logger

log = get_logger(__name__)

DEFAULT_CONCURRENCY = 15


def latest(name: str) -> Path | None:
    files = sorted(Path('data').glob(f'{name}.*.parquet'))
    return files[-1] if files else None


async def backfill(concurrency: int, dry_run: bool) -> None:
    hists_path = latest('hists')
    if hists_path is None:
        log.warning('no hists parquet to backfill')
        return

    log.info(f'reading {hists_path.name}')
    hists = pl.read_parquet(hists_path)
    y_syms = (
        hists.filter(pl.col('template') == HIST_TEMPLATE_DEFAULT)
        .get_column('symbol')
        .unique()
        .to_list()
    )
    log.info(
        f'will re-fetch {HIST_TEMPLATE_DEFAULT} for '
        f'{len(y_syms)} symbols (max_scale='
        f'{HIST_TEMPLATES[HIST_TEMPLATE_DEFAULT][4]} '
        f'{HIST_TEMPLATES[HIST_TEMPLATE_DEFAULT][2]})'
    )
    if dry_run:
        return

    mds = get_provider()
    sem = asyncio.Semaphore(concurrency)
    new_rows: list[pl.DataFrame] = []
    done = 0
    found = 0
    total = len(y_syms)

    async def fetch(sym: str) -> None:
        nonlocal done, found
        async with sem:
            try:
                hist = await asyncio.wait_for(
                    asyncio.to_thread(
                        mds.get_hist_template,
                        sym, HIST_TEMPLATE_DEFAULT, True,
                    ),
                    timeout=60.0,
                )
                if not hist.is_empty():
                    found += 1
                    new_rows.append(
                        hist.with_columns(
                            pl.lit(sym).alias('symbol'),
                            pl.lit(HIST_TEMPLATE_DEFAULT)
                            .alias('template'),
                        )
                    )
            except Exception as e:
                log.warning(f'  {sym}: {type(e).__name__}: {e}')
            finally:
                done += 1
                if done % 100 == 0:
                    log.info(
                        f'  {done}/{total} ({found} loaded)'
                    )

    t0 = perf_counter()
    await asyncio.gather(*[fetch(s) for s in y_syms])
    log.green(
        f'fetched {found}/{total} Y series in '
        f'{perf_counter() - t0:.1f}s'
    )

    if not new_rows:
        log.warning('no new Y data, aborting')
        return

    new_y = pl.concat(new_rows, how='vertical_relaxed')
    other = hists.filter(
        pl.col('template') != HIST_TEMPLATE_DEFAULT
    )

    # Align schemas
    for col in other.columns:
        if col not in new_y.columns:
            new_y = new_y.with_columns(
                pl.lit(None).alias(col).cast(other.schema[col])
            )
    new_y = new_y.select(other.columns)

    combined = pl.concat(
        [new_y, other], how='vertical_relaxed'
    )

    stamp = datetime.now().strftime('%Y%m%d')
    out = Path('data') / f'hists.{stamp}.parquet'
    if out.exists() and out != hists_path:
        shutil.copy2(out, out.with_suffix('.parquet.bak'))
    combined.write_parquet(out, compression='zstd')
    log.green(
        f'wrote {len(combined)} rows '
        f'({len(new_y)} Y, {len(other)} other) -> {out.name}'
    )


def main() -> None:
    args = sys.argv[1:]
    concurrency = DEFAULT_CONCURRENCY
    dry_run = False
    while args:
        flag = args.pop(0)
        if flag == '--concurrency':
            concurrency = int(args.pop(0))
        elif flag == '--dry-run':
            dry_run = True
        else:
            print(f'unknown arg: {flag}', file=sys.stderr)
            sys.exit(1)
    asyncio.run(backfill(concurrency, dry_run))


if __name__ == '__main__':
    main()
