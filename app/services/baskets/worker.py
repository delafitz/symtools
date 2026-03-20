from __future__ import annotations

import os
import pickle
import tempfile
from concurrent.futures import (
    ProcessPoolExecutor,
    as_completed,
)
from io import BytesIO
from time import perf_counter

import polars as pl

from app.services.baskets.builder import (
    build_baskets,
)
from app.utils.logger import get_logger

log = get_logger(__name__)

# Module-level worker state (set once per process)
_w_refs: pl.DataFrame | None = None
_w_hists: pl.DataFrame | None = None
_w_barra = None


def _init_worker(
    refs_path: str,
    hists_path: str,
    barra_bytes: bytes | None,
) -> None:
    """Runs once per worker process."""
    global _w_refs, _w_hists, _w_barra
    _w_refs = pl.read_ipc(refs_path, memory_map=True)
    _w_hists = pl.read_ipc(hists_path, memory_map=True)
    _w_barra = pickle.loads(barra_bytes) if barra_bytes else None


def _build_one(
    args: tuple[str, bytes],
) -> tuple[str, tuple | None, float]:
    """Runs per symbol in worker process."""
    symbol, hist_ipc = args
    start = perf_counter()
    try:
        hist = pl.read_ipc(BytesIO(hist_ipc))
        result = build_baskets(
            symbol,
            hist,
            _w_refs,
            _w_hists,
            barra_model=_w_barra,
        )
        return symbol, result, perf_counter() - start
    except Exception:
        import traceback

        traceback.print_exc()
        return symbol, None, perf_counter() - start


def run_batch(
    symbols_hists: list[tuple[str, pl.DataFrame]],
    refs: pl.DataFrame,
    hists: pl.DataFrame,
    barra_model=None,
) -> dict[str, tuple[dict, str, float]]:
    """Write shared data to temp IPC, run pool.

    Returns dict of symbol -> (baskets, elapsed).
    """
    tmpdir = tempfile.mkdtemp(prefix='symtools_')
    refs_path = os.path.join(tmpdir, 'refs.arrow')
    hists_path = os.path.join(tmpdir, 'hists.arrow')

    try:
        refs.write_ipc(refs_path)
        hists.write_ipc(hists_path)
        barra_bytes = (
            pickle.dumps(barra_model) if barra_model else None
        )

        items = []
        for sym, hist in symbols_hists:
            buf = BytesIO()
            hist.write_ipc(buf)
            items.append((sym, buf.getvalue()))

        results: dict[str, tuple[dict, str, float]] = {}
        with ProcessPoolExecutor(
            initializer=_init_worker,
            initargs=(
                refs_path,
                hists_path,
                barra_bytes,
            ),
        ) as pool:
            futures = {
                pool.submit(_build_one, item): item[0]
                for item in items
            }
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    _, result, elapsed = future.result()
                    if result:
                        baskets, report = result
                        results[sym] = (
                            baskets,
                            report,
                            elapsed,
                        )
                except Exception as e:
                    log.warning(f'worker: {sym} failed: {e}')

        return results
    finally:
        for f in (refs_path, hists_path):
            try:
                os.unlink(f)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
