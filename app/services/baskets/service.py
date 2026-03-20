from __future__ import annotations

from time import perf_counter

import polars as pl

from app.models.baskets import Basket, SymbolBaskets
from app.services.prices import HIST_TEMPLATE_DEFAULT
from app.services.baskets.barra import (
    BarraModel,
    build_barra_model,
)
from app.services.baskets.builder import (
    build_baskets,
    rebuild_from_weights,
)
from app.services.baskets.worker import run_batch
from app.utils.logger import get_logger
from app.utils.store import get_store, write_store

log = get_logger(__name__)

BATCH_SIZE = 200


class BasketService:
    """Owns Barra model, basket optimization, and basket cache.

    Receives refs + hists at init (read-only snapshots).
    Builds Barra model on init, runs batch optimization or
    restores from cached weights on startup.
    """

    def __init__(
        self,
        refs: pl.DataFrame,
        hists: pl.DataFrame,
    ) -> None:
        self.refs = refs
        self.hists = hists
        self.barra_model: BarraModel | None = build_barra_model(
            refs, hists
        )
        self.baskets: dict[str, SymbolBaskets] = {}
        self.reports: dict[str, str] = {}

    def startup(self) -> None:
        self._load_cached()

    def build(self, symbol: str) -> SymbolBaskets | None:
        """Build basket for a single symbol."""
        hist = self._get_hist(symbol)
        if hist is None or hist.is_empty():
            return None

        start = perf_counter()
        build_result = build_baskets(
            symbol,
            hist,
            self.refs,
            self.hists,
            barra_model=self.barra_model,
        )
        elapsed = perf_counter() - start

        if not build_result:
            return None

        baskets_dict, report = build_result
        self.reports[symbol] = report
        log.info(report)

        result = SymbolBaskets(
            symbol=symbol, baskets=baskets_dict, report=report
        )
        self.baskets[symbol] = result
        self._save_weights()
        self._save_reports()
        self._log_summary(symbol, baskets_dict, elapsed)
        return result

    def get(self, symbol: str) -> SymbolBaskets | None:
        return self.baskets.get(symbol)

    def get_report(self, symbol: str) -> str | None:
        return self.reports.get(symbol)

    # -- private ---------------------------------------------------

    @staticmethod
    def _summary_rows(
        symbol: str,
        baskets: dict[str, Basket],
        elapsed: float,
    ) -> list[dict]:
        rows: list[dict] = []
        first = True
        for name, b in baskets.items():
            secs = round(elapsed, 1) if first else None
            first = False
            wt_str = ' '.join(
                f'{s}:{w:.0%}'
                for s, w in sorted(
                    b.weights.items(),
                    key=lambda x: x[1],
                    reverse=True,
                )
            )
            rows.append(
                {
                    'symbol': symbol.upper(),
                    'scenario': name,
                    'weights': wt_str,
                    'corr': round(b.stats.corr, 3),
                    'beta': round(b.stats.beta, 3),
                    'vol_red': round(b.stats.vol_reduce, 3),
                    'secs': secs,
                }
            )
        return rows

    def _log_summary(
        self,
        symbol: str,
        baskets: dict[str, Basket],
        elapsed: float,
    ) -> None:
        rows = self._summary_rows(symbol, baskets, elapsed)
        with pl.Config(
            tbl_rows=-1,
            tbl_width_chars=160,
            fmt_str_lengths=60,
        ):
            if rows:
                log.yellow(
                    f'basket {symbol} '
                    f'({elapsed:.1f}s):\n'
                    f'{pl.DataFrame(rows)}'
                )

    def _get_hist(
        self, symbol: str, template: str = HIST_TEMPLATE_DEFAULT
    ) -> pl.DataFrame | None:
        hist = self.hists.filter(
            (pl.col('symbol') == symbol)
            & (pl.col('template') == template)
        ).drop(['symbol', 'template'])
        return hist if not hist.is_empty() else None

    def _load_cached(self) -> bool:
        cached = get_store('baskets')
        if cached is None:
            return False

        start = perf_counter()

        # Group: symbol -> scenario -> {hedge: weight}
        weights_map: dict[str, dict[str, dict[str, float]]] = {}
        for row in cached.iter_rows(named=True):
            sym = row['symbol']
            sc = row['scenario']
            if sym not in weights_map:
                weights_map[sym] = {}
            if sc not in weights_map[sym]:
                weights_map[sym][sc] = {}
            weights_map[sym][sc][row['hedge_symbol']] = row['weight']

        # Load cached reports
        cached_reports = get_store('basket_reports')
        if cached_reports is not None:
            for row in cached_reports.iter_rows(named=True):
                self.reports[row['symbol']] = row['report']

        count = 0
        for symbol, scenarios in weights_map.items():
            hist = self._get_hist(symbol)
            if hist is None or hist.is_empty():
                continue

            baskets = rebuild_from_weights(
                symbol, hist, self.hists, scenarios
            )
            if baskets:
                self.baskets[symbol] = SymbolBaskets(
                    symbol=symbol,
                    baskets=baskets,
                    report=self.reports.get(symbol),
                )
                count += 1

        elapsed = perf_counter() - start
        log.info(
            f'rebuilt {count} baskets from cache ({elapsed:.1f}s)'
        )
        return count > 0

    def _run_batch(self) -> None:
        stocks = (
            self.refs.filter(pl.col('type') == 'stock')
            .sort('mkt_cap', descending=True)
            .get_column('symbol')
            .to_list()
        )

        symbols_hists = []
        for symbol in stocks:
            hist = self._get_hist(symbol)
            if hist is not None and not hist.is_empty():
                symbols_hists.append((symbol, hist))
                if len(symbols_hists) >= BATCH_SIZE:
                    break

        if not symbols_hists:
            return

        total_start = perf_counter()
        batch_results = run_batch(
            symbols_hists,
            self.refs,
            self.hists,
            barra_model=self.barra_model,
        )

        rows = []
        for symbol, hist in symbols_hists:
            result = batch_results.get(symbol)
            if not result:
                continue

            baskets, report, elapsed = result
            self.reports[symbol] = report
            self.baskets[symbol] = SymbolBaskets(
                symbol=symbol,
                baskets=baskets,
                report=report,
            )
            rows.extend(self._summary_rows(symbol, baskets, elapsed))

        total_elapsed = perf_counter() - total_start
        if rows:
            with pl.Config(
                tbl_rows=-1,
                tbl_width_chars=160,
                fmt_str_lengths=60,
            ):
                log.yellow(
                    f'baskets '
                    f'({len(symbols_hists)} symbols, '
                    f'{total_elapsed:.1f}s):\n'
                    f'{pl.DataFrame(rows)}'
                )

        self._save_weights()
        self._save_reports()

    def _save_weights(self) -> None:
        rows: list[dict] = []
        for symbol, data in self.baskets.items():
            for scenario, basket in data.baskets.items():
                for hedge_sym, weight in basket.weights.items():
                    rows.append(
                        {
                            'symbol': symbol,
                            'scenario': scenario,
                            'hedge_symbol': hedge_sym,
                            'weight': weight,
                        }
                    )
        if rows:
            write_store(pl.DataFrame(rows), 'baskets')
            log.info(f'saved {len(rows)} basket weights')

    def _save_reports(self) -> None:
        if not self.reports:
            return
        rows = [
            {'symbol': sym, 'report': rep}
            for sym, rep in self.reports.items()
        ]
        write_store(pl.DataFrame(rows), 'basket_reports')
        log.info(f'saved {len(rows)} basket reports')
