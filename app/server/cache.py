import asyncio
import os
from time import monotonic

import polars as pl

from app.mds.client import get_provider
from app.utils.market import DT_FMT, last_trading_day
from app.models.analytics import SymbolAnalytics
from app.services.quotes import QuoteService
from app.models.baskets import SymbolBaskets
from app.models.cost import SymbolCostCalcs
from app.models.inputs import SymbolOverrides
from app.models.results import SearchResult, SymbolQuote
from app.services.analytics.build import build_analytics
from app.services.baskets.service import BasketService
from app.services.cost import calc_costs
from app.services.hist import (
    load_symbol_series,
    load_symbol_template,
)
from app.services.prices import HIST_TEMPLATE_DEFAULT
from app.models.blocks import SymbolBlocks
from app.services.block_trades import (
    load_block_trades,
    get_symbol_blocks,
)
from app.services.refs import (
    get_cached_refs,
    get_cached_hists,
    load_refs_async,
)
from app.utils.logger import get_logger
from app.utils.trie import Trie

log = get_logger(__name__)

SYM = 'symbol'
SEARCH_LEN = 7
MAX_SYMBOLS = int(os.getenv('MAX_SYMBOLS', '1000'))
INTRADAY_TTL = 120  # seconds


class Cache:
    """
    Shared application state and data storage.
    Business logic lives in services; this is just the data layer.
    """

    def __init__(self) -> None:
        self.mds = get_provider()
        self.quote_svc = QuoteService(self.mds)
        self.refs: pl.DataFrame | None = None
        self.hists: pl.DataFrame | None = None
        self.tickers: pl.DataFrame | None = None
        self.symbols: Trie | None = None
        self.analytics: dict[str, SymbolAnalytics] = {}
        self.block_trades: pl.DataFrame | None = None
        self.basket_svc: BasketService | None = None
        self._refs_task: asyncio.Task | None = None
        self._loading_symbols: set[str] = set()
        self._hist_locks: dict[str, asyncio.Lock] = {}
        self._hist_loaded_at: dict[tuple[str, str], float] = {}

    def _log_refs_summary(
        self,
        source: str,
        details_count: int = 0,
        details_elapsed: float = 0.0,
        floats_count: int = 0,
        floats_elapsed: float = 0.0,
        hists_count: int = 0,
        hists_elapsed: float = 0.0,
    ) -> None:
        """Log summary of loaded refs."""
        if self.refs is None:
            return
        total = len(self.refs)
        stocks = self.refs.filter(pl.col('type') == 'stock')
        above_1b = stocks.filter(pl.col('mkt_cap') >= 1e9).height
        sorted_stocks = (
            stocks.sort('mkt_cap', descending=True)
            .select(['symbol', 'name', 'mkt_cap'])
            .with_columns(
                (pl.col('mkt_cap') / 1e9).round(1).alias('mkt_cap_B')
            )
            .drop('mkt_cap')
        )
        top5 = sorted_stocks.head(5)
        bottom5 = sorted_stocks.tail(5)
        log.info(
            f'refs ({source}): {total} total, '
            f'{above_1b} stocks above $1B mkt cap'
        )
        log.info(f'top 5:\n{top5}')
        log.info(f'bottom 5:\n{bottom5}')

        # Short interest / float ratio
        has_si = stocks.filter(
            (pl.col('short_interest') > 0)
            & (pl.col('free_float') > 0)
        )
        if has_si.height > 0:
            si_report = (
                has_si.with_columns(
                    (
                        pl.col('short_interest')
                        / pl.col('free_float')
                        * 100
                    )
                    .round(1)
                    .alias('si_pct_float')
                )
                .sort('si_pct_float', descending=True)
                .head(10)
                .select(
                    'symbol',
                    'name',
                    'si_pct_float',
                    'short_interest',
                    'days_to_cover',
                )
            )
            log.cyan(f'top 10 SI/float %:\n{si_report}')

        # Lowest free float as % of shares out
        has_ff = stocks.filter(
            (pl.col('free_float') > 0) & (pl.col('shares_out') > 0)
        )
        if has_ff.height > 0:
            ff_report = (
                has_ff.sort('free_float_pct')
                .head(10)
                .select(
                    'symbol',
                    'name',
                    'free_float_pct',
                    'free_float',
                    'shares_out',
                )
            )
            log.cyan(f'lowest 10 float %:\n{ff_report}')

        # Log symbols without SIC
        no_sic = stocks.filter(
            (pl.col('sic').is_null()) | (pl.col('sic') == '')
        )
        if no_sic.height > 0:
            no_sic_syms = no_sic.get_column('symbol').to_list()
            log.warning(
                f'symbols without SIC ({len(no_sic_syms)}): '
                f'{", ".join(no_sic_syms[:20])}'
                f'{"..." if len(no_sic_syms) > 20 else ""}'
            )
        if source == 'loaded':
            total_elapsed = (
                details_elapsed + floats_elapsed + hists_elapsed
            )
            summary = pl.DataFrame(
                {
                    'phase': [
                        'tickers',
                        'details',
                        'floats',
                        'hists',
                        'total',
                    ],
                    'count': [
                        total,
                        details_count,
                        floats_count,
                        hists_count,
                        total,
                    ],
                    'seconds': [
                        0.0,
                        round(details_elapsed, 1),
                        round(floats_elapsed, 1),
                        round(hists_elapsed, 1),
                        round(total_elapsed, 1),
                    ],
                }
            )
            log.yellow(f'startup summary:\n{summary}')

    def _load_block_trades(self) -> None:
        """Load block trades file and cross-check against refs.

        `hists` is passed in so discount can be rebuilt from
        the pre-block close (source `Disc` field is unreliable).
        """
        if self.refs is None:
            return
        self.block_trades = load_block_trades(self.refs, self.hists)

    def _init_baskets(self) -> None:
        """Create basket service, pre-build analytics."""
        if self.refs is None or self.hists is None:
            return

        self.basket_svc = BasketService(self.refs, self.hists)
        self.basket_svc.startup()

        spy_hist = self.get_hist('spy', HIST_TEMPLATE_DEFAULT)
        for symbol in self.basket_svc.baskets:
            hist = self.get_hist(symbol, HIST_TEMPLATE_DEFAULT)
            if hist is not None:
                self.analytics[symbol] = build_analytics(
                    symbol,
                    hist,
                    ref=self.get_ref(symbol),
                    spy_hist=spy_hist,
                )

    async def startup(self) -> None:
        """Initialize cache on application startup."""
        # Try to load from cache
        cached_refs = get_cached_refs()
        cached_hists = get_cached_hists()

        if cached_refs is not None and cached_hists is not None:
            self.refs = cached_refs
            self.hists = cached_hists
            self.symbols = Trie(cached_refs.get_column(SYM).to_list())
            self._log_refs_summary('cached')
            log.info(f'hists (cached): {len(cached_hists)} rows')
            self._load_block_trades()
            self._init_baskets()
            return

        # Fresh load needed
        self.tickers = self.mds.list_tickers(MAX_SYMBOLS)
        self.symbols = Trie(self.tickers.get_column(SYM).to_list())
        self._refs_task = asyncio.create_task(
            self._load_refs_background()
        )

    async def _load_refs_background(self) -> None:
        if self.tickers is None:
            return

        def on_refs_update(refs: pl.DataFrame) -> None:
            self.refs = refs
            self.symbols = Trie(refs.get_column(SYM).to_list())

        def on_hists_update(hists: pl.DataFrame) -> None:
            self.hists = hists

        stats = await load_refs_async(
            self.mds,
            self.tickers,
            on_refs_update,
            on_hists_update,
        )
        self._log_refs_summary('loaded', **stats)
        self._load_block_trades()
        self._init_baskets()

    def is_ready(self) -> bool:
        return self.refs is not None

    def get_refs(self) -> list[dict]:
        if self.refs is not None:
            return self.refs.to_dicts()
        if self.tickers is not None:
            return self.tickers.to_dicts()
        return []

    def get_ref(self, symbol: str) -> dict | None:
        if self.refs is not None:
            ref = self.refs.filter(pl.col(SYM) == symbol)
            return ref.to_dicts()[0] if not ref.is_empty() else None
        if self.tickers is not None:
            ref = self.tickers.filter(pl.col(SYM) == symbol)
            return ref.to_dicts()[0] if not ref.is_empty() else None
        return None

    def get_hist(
        self, symbol: str, template: str = HIST_TEMPLATE_DEFAULT
    ) -> pl.DataFrame | None:
        """Get hist for symbol/template from unified hists."""
        if self.hists is None:
            return None

        hist = self.hists.filter(
            (pl.col('symbol') == symbol)
            & (pl.col('template') == template)
        ).drop(['symbol', 'template'])

        return hist if not hist.is_empty() else None

    def hist_age(self, symbol: str, template: str) -> float:
        loaded = self._hist_loaded_at.get((symbol, template))
        if loaded is None:
            return float('inf')
        return monotonic() - loaded

    async def get_hist_async(
        self, symbol: str, template: str = HIST_TEMPLATE_DEFAULT
    ) -> pl.DataFrame | None:
        """Get hist for symbol/template, loading if needed."""
        hist = self.get_hist(symbol, template)
        stale = (
            hist is not None
            and template in ('W', 'D')
            and self.hist_age(symbol, template) > INTRADAY_TTL
        )
        if hist is not None and not stale:
            return hist

        if symbol not in self._hist_locks:
            self._hist_locks[symbol] = asyncio.Lock()

        async with self._hist_locks[symbol]:
            # Re-check after lock
            hist = self.get_hist(symbol, template)
            age = self.hist_age(symbol, template)
            if hist is not None and (
                template not in ('W', 'D') or age <= INTRADAY_TTL
            ):
                return hist

            fresh = await load_symbol_template(
                self.mds, symbol, template
            )
            if not fresh.is_empty():
                hist_with_meta = fresh.with_columns(
                    pl.lit(symbol).alias('symbol'),
                    pl.lit(template).alias('template'),
                )
                if self.hists is None:
                    self.hists = hist_with_meta
                else:
                    self.hists = self.hists.filter(
                        ~(
                            (pl.col('symbol') == symbol)
                            & (pl.col('template') == template)
                        )
                    )
                    self.hists = pl.concat(
                        [self.hists, hist_with_meta]
                    )
                self._hist_loaded_at[(symbol, template)] = monotonic()
                return fresh

            # API returned empty — keep old data
            return hist

    async def fetch_today_bars_async(
        self, symbols: set[str]
    ) -> dict[str, pl.DataFrame]:
        """Fetch today's daily bar for symbols.

        Appends to Y and M data in cache.hists.
        Returns symbol → today-bar DataFrame.
        """
        today = last_trading_day().strftime(DT_FMT)

        need: set[str] = set()
        for sym in symbols:
            hist = self.get_hist(sym, 'Y')
            if hist is None:
                continue
            last = hist.select('date').tail(1).item()
            if last < today:
                need.add(sym)

        if not need:
            return {}

        async def _fetch(
            sym: str,
        ) -> tuple[str, pl.DataFrame]:
            bar = await asyncio.to_thread(
                self.mds.get_hist,
                sym,
                'day',
                1,
                'days',
                0,
                quiet=True,
            )
            return sym, bar

        results = await asyncio.gather(*[_fetch(s) for s in need])

        bars: dict[str, pl.DataFrame] = {}
        for sym, bar in results:
            if bar.is_empty():
                continue
            today_bar = bar.filter(pl.col('date') == today)
            if today_bar.is_empty():
                continue
            bars[sym] = today_bar
            for tmpl in ('Y', 'M'):
                if self.get_hist(sym, tmpl) is not None:
                    self._append_hist_bar(sym, tmpl, today_bar)
        return bars

    def _append_hist_bar(
        self,
        symbol: str,
        template: str,
        bar: pl.DataFrame,
    ) -> None:
        """Append/replace a bar in hists."""
        if self.hists is None:
            return
        bar_date = bar.select('date').head(1).item()
        self.hists = self.hists.filter(
            ~(
                (pl.col('symbol') == symbol)
                & (pl.col('template') == template)
                & (pl.col('date') == bar_date)
            )
        )
        bar_with_meta = bar.with_columns(
            pl.lit(symbol).alias('symbol'),
            pl.lit(template).alias('template'),
        )
        self.hists = pl.concat([self.hists, bar_with_meta])

    async def _load_symbol_series_background(
        self, symbol: str
    ) -> None:
        def on_update(
            sym: str, template: str, hist: pl.DataFrame
        ) -> None:
            hist_with_meta = hist.with_columns(
                pl.lit(sym).alias('symbol'),
                pl.lit(template).alias('template'),
            )
            if self.hists is None:
                self.hists = hist_with_meta
            else:
                # Remove old entry if exists
                self.hists = self.hists.filter(
                    ~(
                        (pl.col('symbol') == sym)
                        & (pl.col('template') == template)
                    )
                )
                self.hists = pl.concat([self.hists, hist_with_meta])

        try:
            existing = {}
            if self.hists is not None:
                for tmpl in ['Y', 'M', 'W', 'D']:
                    hist = self.get_hist(symbol, tmpl)
                    if hist is not None:
                        existing[tmpl] = hist
            await load_symbol_series(
                self.mds, symbol, existing, on_update
            )
        finally:
            self._loading_symbols.discard(symbol)

    async def get_analytics(
        self, symbol: str
    ) -> SymbolAnalytics | None:
        """Get analytics, building if not cached."""
        if symbol in self.analytics:
            return self.analytics[symbol]

        if not self.get_ref(symbol):
            return None

        hist = await self.get_hist_async(
            symbol, HIST_TEMPLATE_DEFAULT
        )
        if hist is None or hist.is_empty():
            return None

        analytics = build_analytics(
            symbol,
            hist,
            ref=self.get_ref(symbol),
            spy_hist=self.get_hist('spy', HIST_TEMPLATE_DEFAULT),
        )
        self.analytics[symbol] = analytics
        return analytics

    def get_block_trades(
        self, symbol: str
    ) -> SymbolBlocks | None:
        if self.block_trades is None:
            return None
        return get_symbol_blocks(symbol, self.block_trades)

    def get_baskets(self, symbol: str) -> SymbolBaskets | None:
        """Get baskets if cached."""
        if self.basket_svc is None:
            return None
        return self.basket_svc.get(symbol)

    async def get_costs(
        self, overrides: SymbolOverrides
    ) -> SymbolCostCalcs | None:
        return await calc_costs(self, overrides)

    async def get_quote(self, symbol: str) -> SymbolQuote:
        return await self.quote_svc.get(symbol)

    def search_token(
        self, token: str, length: int = SEARCH_LEN
    ) -> list[SearchResult]:
        if self.symbols is None:
            return []
        results = self.symbols.prefix_search(token)
        if results:
            return [
                SearchResult(symbol=symbol, score=score)
                for [symbol, score] in results
            ][:length]
        return []
