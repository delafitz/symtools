import asyncio
from typing import TYPE_CHECKING, AsyncGenerator

from pydantic import BaseModel

from app.models.inputs import SymbolOverrides
from app.services.cost import calc_costs
from app.services.hist import build_basket_hists
from app.services.prices import (
    HIST_TEMPLATES,
    PriceService,
    end_price_from_quote,
)
from app.services.tracking import (
    compute_tracking_for_template,
)
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.server.cache import Cache

from app.server.cache import INTRADAY_TTL

log = get_logger(__name__)


async def stream_symbol(
    symbol: str,
    cache: 'Cache',
) -> AsyncGenerator[tuple[str, BaseModel], None]:
    """Stream symbol data via SSE.

    Fires parallel tasks on entry, yields cold Y/M
    hists with a synthetic today bar, then loops
    per-template to yield real hists + basket_hists.
    """
    if not cache.get_ref(symbol):
        return

    # Fire independent tasks in parallel
    quote_task = asyncio.create_task(cache.get_quote(symbol))
    prices_task = asyncio.create_task(
        PriceService.create(cache, symbol)
    )
    analytics_task = asyncio.create_task(cache.get_analytics(symbol))

    # 1. Quote
    quote = await quote_task
    yield ('quote', quote)
    end_price = end_price_from_quote(quote)

    # 2. PriceService + cold Y/M with synthetic today bar
    prices = await prices_task
    if prices is None:
        return
    prices.append_quote_bar(end_price)

    y_resp = await prices.build_response(symbol, 'Y', end_price)
    if y_resp is None:
        return
    log.yellow(f'{symbol} hist Y (cold) bars={len(y_resp.bars)}')
    yield ('hist', y_resp)

    m_resp = await prices.build_response(symbol, 'M', end_price)
    if m_resp:
        log.yellow(f'{symbol} hist M (cold) bars={len(m_resp.bars)}')
        yield ('hist', m_resp)

    # 3. Analytics
    analytics = await analytics_task
    if analytics:
        yield ('analytics', analytics)

    # 4. Cost (1 ADV default)
    if analytics and analytics.adv > 0:
        overrides = SymbolOverrides(
            symbol=symbol,
            shares=analytics.adv / 1e6,
        )
        cost = await calc_costs(cache, overrides)
        if cost:
            yield ('cost', cost)

    # 5. Baskets (cached or on-demand)
    basket_svc = cache.basket_svc
    baskets = cache.get_baskets(symbol)
    if not baskets and basket_svc:
        baskets = await asyncio.to_thread(basket_svc.build, symbol)
    if baskets:
        yield ('baskets', baskets)

    # Collect basket syms once
    basket_syms: set[str] = set()
    if baskets:
        for sc in baskets.baskets.values():
            basket_syms.update(sc.weights.keys())

    # 7. Alerts (all non-cost categories)
    from app.services.alerts import AlertContext, evaluate

    ctx = AlertContext(
        symbol=symbol,
        ref=cache.get_ref(symbol),
        analytics=analytics,
        baskets=baskets,
        daily=prices.daily,
    )
    alerts = evaluate(ctx)
    if alerts:
        yield ('alerts', alerts)

    # 8. Fetch real today bars for target + basket syms
    today_bars = await cache.fetch_today_bars_async(
        {symbol} | basket_syms
    )
    if symbol in today_bars:
        prices.replace_today_bar(today_bars[symbol])

    # 9. Per-template: hist → basket_hists
    for template in HIST_TEMPLATES:
        if template in ('Y', 'M'):
            # Re-yield with real today bar
            resp = await prices.build_response(
                symbol, template, end_price
            )
            if resp is None:
                continue
            log.yellow(
                f'{symbol} hist {resp.template} bars={len(resp.bars)}'
            )
            yield ('hist', resp)
        else:
            # W/D: fetch stale basket-sym hists
            if basket_syms and cache.hists is not None:
                fetch_tasks = [
                    cache.get_hist_async(sym, template)
                    for sym in basket_syms
                    if cache.get_hist(sym, template) is None
                    or cache.hist_age(sym, template) > INTRADAY_TTL
                ]
                if fetch_tasks:
                    await asyncio.gather(*fetch_tasks)

            resp = await prices.build_response(
                symbol, template, end_price
            )
            if resp is None:
                continue
            log.yellow(
                f'{symbol} hist {resp.template} '
                f'aggs='
                f'{len(resp.daily_aggs) if resp.daily_aggs else 0} '
                f'bars={len(resp.bars)}'
            )
            yield ('hist', resp)

        # Basket_hists
        if baskets and cache.hists is not None:
            hist = await prices.hist(symbol, template)
            if hist is None:
                log.yellow(
                    f'{symbol} {template} basket_hist: hist None'
                )
                continue
            _, _, _, _, t_max = HIST_TEMPLATES[template]
            t_prev = (
                resp.stats[t_max].prev_date
                if t_max in resp.stats
                else None
            )
            tracking = compute_tracking_for_template(
                symbol,
                hist,
                template,
                t_max,
                baskets,
                cache.hists,
                prev_date=t_prev,
            )
            if tracking:
                for bh in build_basket_hists(
                    symbol,
                    template,
                    tracking,
                    resp.stats,
                    baskets,
                    cache.hists,
                ):
                    log.yellow(
                        f'{symbol} basket_hist '
                        f'{bh.template} '
                        f'{bh.basket} '
                        f'bars={len(bh.weighted)}'
                    )
                    yield ('basket_hist', bh)
            else:
                log.yellow(f'{symbol} {template} tracking: None')

