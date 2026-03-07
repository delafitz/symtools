from __future__ import annotations

from time import perf_counter

import polars as pl

from app.models.baskets import Basket, BasketSummaryRow, SymbolBaskets
from app.services.prices import HIST_TEMPLATE_DEFAULT
from app.services.baskets.barra import (
    BarraModel,
    build_barra_model,
)
from app.services.baskets.builder import (
    build_baskets,
    rebuild_from_weights,
)
from app.services.baskets.config import (
    MODEL_CHOICE,
    ModelChoice,
)
from app.services.baskets.factors import build_emp_model
from app.services.baskets.worker import run_batch
from app.utils.groups import SCENARIOS
from app.utils.logger import get_logger
from app.utils.store import get_store, write_store

log = get_logger(__name__)

BATCH_SIZE = 200


class BasketService:
    """Owns model, basket optimization, and basket cache.

    Receives refs + hists at init (read-only snapshots).
    Builds emp or barra model based on model_choice,
    runs batch optimization or restores from cached
    weights on startup.
    """

    def __init__(
        self,
        refs: pl.DataFrame,
        hists: pl.DataFrame,
        model_choice: ModelChoice = MODEL_CHOICE,
    ) -> None:
        self.refs = refs
        self.hists = hists
        self.model_choice = model_choice
        self.emp_model = (
            build_emp_model(refs, hists)
            if model_choice == 'emp'
            else None
        )
        self.barra_model: BarraModel | None = (
            build_barra_model(refs, hists)
            if model_choice == 'barra'
            else None
        )
        self.baskets: dict[str, SymbolBaskets] = {}

    def startup(self) -> None:
        self._load_cached()

    def build(self, symbol: str) -> SymbolBaskets | None:
        """Build basket for a single symbol."""
        hist = self._get_hist(symbol)
        if hist is None or hist.is_empty():
            return None

        start = perf_counter()
        baskets = build_baskets(
            symbol,
            hist,
            self.refs,
            self.hists,
            emp_model=self.emp_model,
            barra_model=self.barra_model,
            model_choice=self.model_choice,
        )
        elapsed = perf_counter() - start

        if not baskets:
            return None

        summary = self._build_summary(baskets)
        result = SymbolBaskets(
            symbol=symbol, baskets=baskets, summary=summary
        )
        self.baskets[symbol] = result
        self._save_weights()
        self._log_summary(symbol, baskets, summary, elapsed)
        return result

    def get(self, symbol: str) -> SymbolBaskets | None:
        return self.baskets.get(symbol)

    # -- private ---------------------------------------------------

    @staticmethod
    def _summary_rows(
        symbol: str,
        baskets: dict[str, Basket],
        elapsed: float,
    ) -> list[dict]:
        rows: list[dict] = []
        first = True
        for name, (label, _) in SCENARIOS.items():
            b = baskets.get(name)
            secs = round(elapsed, 1) if first else None
            first = False
            if not b:
                rows.append(
                    {
                        'symbol': symbol.upper(),
                        'scenario': name,
                        'weights': '',
                        'corr': None,
                        'beta': None,
                        'vol_red': None,
                        'secs': secs,
                    }
                )
                continue
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

    @staticmethod
    def _build_summary(
        baskets: dict[str, Basket],
    ) -> list[BasketSummaryRow]:
        rows = []
        for name, b in baskets.items():
            syms = ', '.join(
                s.upper()
                for s, _ in sorted(
                    b.weights.items(),
                    key=lambda x: x[1],
                    reverse=True,
                )
            )
            rows.append(
                BasketSummaryRow(
                    basket=name,
                    symbols=syms,
                    weight=b.stats.weight,
                    beta=b.stats.beta,
                    corr=b.stats.corr,
                    reduce=b.stats.vol_reduce,
                )
            )
        return rows

    def _log_summary(
        self,
        symbol: str,
        baskets: dict[str, Basket],
        summary: list[BasketSummaryRow],
        elapsed: float,
    ) -> None:
        rows = self._summary_rows(symbol, baskets, elapsed)
        summary_rows = [
            {
                'basket': r.basket,
                'symbols': r.symbols,
                'weight': round(r.weight, 3),
                'beta': round(r.beta, 3),
                'corr': round(r.corr, 3),
                'reduce': round(r.reduce, 3),
            }
            for r in summary
        ]
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
            if summary_rows:
                log.yellow(
                    f'basket summary {symbol}:\n'
                    f'{pl.DataFrame(summary_rows)}'
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

        count = 0
        for symbol, scenarios in weights_map.items():
            hist = self._get_hist(symbol)
            if hist is None or hist.is_empty():
                continue

            baskets = rebuild_from_weights(
                symbol, hist, self.hists, scenarios
            )
            if baskets:
                summary = self._build_summary(baskets)
                self.baskets[symbol] = SymbolBaskets(
                    symbol=symbol,
                    baskets=baskets,
                    summary=summary,
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
            emp_model=self.emp_model,
            barra_model=self.barra_model,
            model_choice=self.model_choice,
        )

        rows = []
        for symbol, hist in symbols_hists:
            result = batch_results.get(symbol)
            if not result:
                continue

            baskets, elapsed = result
            summary = self._build_summary(baskets)
            self.baskets[symbol] = SymbolBaskets(
                symbol=symbol,
                baskets=baskets,
                summary=summary,
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
