"""Validate block trade discounts against market closes.

Rebuilds discount from hists per the convention:
  - PxDt <  TradeDt: pre_close = close on PxDt
                     (block priced after Pxdt close, trades T+1)
  - PxDt == TradeDt: pre_close = close on weekday before PxDt
                     (block priced and traded same day; use prior
                     session as reference)

  discount = offer_price / pre_close - 1

Compares three sources:
  - file_disc:   raw `Disc` field from block_trades.json
  - net_disc:    derived from NetPx / OfferPx - 1 (if NetPx exists)
  - model_disc:  hists-derived using the rules above

Usage:
    uv run python tools/check_discount.py
    uv run python tools/check_discount.py --symbols vsat,qxo,sa
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl


def parse_dt(s: str) -> str:
    return datetime.strptime(s, '%d-%b-%Y').strftime('%Y-%m-%d')


def latest(name: str) -> Path | None:
    files = sorted(Path('data').glob(f'{name}.*.parquet'))
    return files[-1] if files else None


def close_at_or_before(
    sym_hist: pl.DataFrame, on_date: str
) -> float | None:
    """Most recent close on or before `on_date`."""
    rows = sym_hist.filter(pl.col('date') <= on_date).tail(1)
    if rows.is_empty():
        return None
    return rows.get_column('close').item()


def close_strictly_before(
    sym_hist: pl.DataFrame, on_date: str
) -> float | None:
    """Most recent close strictly before `on_date`."""
    rows = sym_hist.filter(pl.col('date') < on_date).tail(1)
    if rows.is_empty():
        return None
    return rows.get_column('close').item()


def main() -> None:
    args = sys.argv[1:]
    syms_filter: set[str] | None = None
    while args:
        flag = args.pop(0)
        if flag == '--symbols':
            syms_filter = {s.lower() for s in args.pop(0).split(',')}
        else:
            print(f'unknown arg: {flag}', file=sys.stderr)
            sys.exit(1)

    bt_files = (
        sorted(Path('data').glob('block_trades*.json'))
        + sorted(Path('data').glob('block_trades*.csv'))
    )
    if not bt_files:
        print('no block_trades file', file=sys.stderr)
        sys.exit(1)
    bt_path = bt_files[-1]
    with open(bt_path) as f:
        raw = json.load(f)

    hists_path = latest('hists')
    if hists_path is None:
        print('no hists parquet', file=sys.stderr)
        sys.exit(1)
    hists = pl.read_parquet(hists_path).filter(
        pl.col('template') == 'Y'
    ).select(['symbol', 'date', 'close']).sort(
        ['symbol', 'date']
    )

    rows: list[dict] = []
    n_skip = 0
    for r in raw:
        sym = (r.get('Ticker') or '').lower()
        if not sym:
            n_skip += 1
            continue
        if syms_filter and sym not in syms_filter:
            continue
        offer = r.get('OfferPx')
        if not offer:
            n_skip += 1
            continue

        px_dt = parse_dt(r['PxDt'])
        td_dt = parse_dt(r['TradeDt'])

        sym_hist = hists.filter(pl.col('symbol') == sym)
        if sym_hist.is_empty():
            n_skip += 1
            continue

        if px_dt < td_dt:
            pre_close = close_at_or_before(sym_hist, px_dt)
            ref_label = f'close({px_dt})'
        else:
            pre_close = close_strictly_before(sym_hist, px_dt)
            ref_label = f'close(<{px_dt})'

        if pre_close is None or pre_close <= 0:
            n_skip += 1
            continue

        model_disc = offer / pre_close - 1
        file_disc = r.get('Disc')
        net_px = r.get('NetPx')
        net_disc = (
            net_px / offer - 1 if net_px else None
        )

        rows.append({
            'symbol': sym,
            'px_dt': px_dt,
            'td_dt': td_dt,
            'same_day': px_dt == td_dt,
            'offer': offer,
            'net_px': net_px,
            'pre_close': pre_close,
            'ref': ref_label,
            'file_disc': file_disc,
            'net_disc': net_disc,
            'model_disc': model_disc,
            'file_minus_model': (
                file_disc - model_disc
                if file_disc is not None else None
            ),
            'net_minus_model': (
                net_disc - model_disc
                if net_disc is not None else None
            ),
        })

    if not rows:
        print('no comparable rows', file=sys.stderr)
        sys.exit(1)

    df = pl.DataFrame(rows)
    print(f'\n{len(df)} comparable trades '
          f'({n_skip} skipped: no hist / no offer)\n')

    print('== distribution of differences ==')
    print(df.select([
        pl.col('file_minus_model').abs().mean()
            .alias('|file-model| mean'),
        pl.col('file_minus_model').abs().median()
            .alias('|file-model| med'),
        pl.col('net_minus_model').abs().mean()
            .alias('|net-model| mean'),
        pl.col('net_minus_model').abs().median()
            .alias('|net-model| med'),
    ]))

    # File-vs-model bucketed by magnitude of discrepancy
    print('\n== file vs model: discrepancy buckets ==')
    bucketed = df.with_columns(
        pl.when(
            pl.col('file_minus_model').abs() < 0.005
        ).then(pl.lit('<0.5%'))
        .when(pl.col('file_minus_model').abs() < 0.02)
            .then(pl.lit('0.5-2%'))
        .when(pl.col('file_minus_model').abs() < 0.1)
            .then(pl.lit('2-10%'))
        .otherwise(pl.lit('>10%'))
        .alias('bucket')
    )
    print(
        bucketed.group_by('bucket').len().sort('bucket')
    )

    # Worst file-vs-model
    print('\n== top 15 file_disc anomalies (vs model) ==')
    worst = (
        df.filter(pl.col('file_minus_model').is_not_null())
        .with_columns(
            pl.col('file_minus_model').abs().alias('absdiff')
        )
        .sort('absdiff', descending=True)
        .select([
            'symbol', 'px_dt', 'td_dt', 'offer',
            'pre_close', 'file_disc', 'net_disc',
            'model_disc', 'file_minus_model',
        ])
        .head(15)
    )
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=200):
        print(worst)

    # Net vs model: should be tiny if data is consistent
    print('\n== top 10 net_disc vs model anomalies ==')
    net_worst = (
        df.filter(pl.col('net_minus_model').is_not_null())
        .with_columns(
            pl.col('net_minus_model').abs().alias('absdiff')
        )
        .sort('absdiff', descending=True)
        .select([
            'symbol', 'px_dt', 'td_dt', 'offer', 'net_px',
            'pre_close', 'net_disc', 'model_disc',
            'net_minus_model',
        ])
        .head(10)
    )
    with pl.Config(tbl_rows=-1, tbl_cols=-1, tbl_width_chars=200):
        print(net_worst)


if __name__ == '__main__':
    main()
