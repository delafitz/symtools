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
from app.services.baskets.config import ModelChoice
from app.utils.logger import get_logger

log = get_logger(__name__)

# Module-level worker state (set once per process)
_w_refs: pl.DataFrame | None = None
_w_hists: pl.DataFrame | None = None
_w_emp = None
_w_barra = None
_w_model_choice: ModelChoice = 'emp'


def _init_worker(
    refs_path: str,
    hists_path: str,
    emp_bytes: bytes | None,
    barra_bytes: bytes | None,
    model_choice: ModelChoice,
) -> None:
    """Runs once per worker process."""
    global _w_refs, _w_hists, _w_emp
    global _w_barra, _w_model_choice
    _w_refs = pl.read_ipc(refs_path, memory_map=True)
    _w_hists = pl.read_ipc(hists_path, memory_map=True)
    _w_emp = pickle.loads(emp_bytes) if emp_bytes else None
    _w_barra = pickle.loads(barra_bytes) if barra_bytes else None
    _w_model_choice = model_choice


def _build_one(
    args: tuple[str, bytes],
) -> tuple[str, dict | None, float]:
    """Runs per symbol in worker process."""
    symbol, hist_ipc = args
    start = perf_counter()
    try:
        hist = pl.read_ipc(BytesIO(hist_ipc))
        baskets = build_baskets(
            symbol,
            hist,
            _w_refs,
            _w_hists,
            emp_model=_w_emp,
            barra_model=_w_barra,
            model_choice=_w_model_choice,
        )
        return symbol, baskets, perf_counter() - start
    except Exception:
        import traceback

        traceback.print_exc()
        return symbol, None, perf_counter() - start


def run_batch(
    symbols_hists: list[tuple[str, pl.DataFrame]],
    refs: pl.DataFrame,
    hists: pl.DataFrame,
    emp_model=None,
    barra_model=None,
    model_choice: ModelChoice = 'emp',
) -> dict[str, tuple[dict, float]]:
    """Write shared data to temp IPC, run pool.

    Returns dict of symbol -> (baskets, elapsed).
    """
    tmpdir = tempfile.mkdtemp(prefix='symtools_')
    refs_path = os.path.join(tmpdir, 'refs.arrow')
    hists_path = os.path.join(tmpdir, 'hists.arrow')

    try:
        refs.write_ipc(refs_path)
        hists.write_ipc(hists_path)
        emp_bytes = pickle.dumps(emp_model) if emp_model else None
        barra_bytes = (
            pickle.dumps(barra_model) if barra_model else None
        )

        items = []
        for sym, hist in symbols_hists:
            buf = BytesIO()
            hist.write_ipc(buf)
            items.append((sym, buf.getvalue()))

        results: dict[str, tuple[dict, float]] = {}
        with ProcessPoolExecutor(
            initializer=_init_worker,
            initargs=(
                refs_path,
                hists_path,
                emp_bytes,
                barra_bytes,
                model_choice,
            ),
        ) as pool:
            futures = {
                pool.submit(_build_one, item): item[0]
                for item in items
            }
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    _, baskets, elapsed = future.result()
                    if baskets:
                        results[sym] = (
                            baskets,
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
